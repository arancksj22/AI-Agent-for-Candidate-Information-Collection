"""Thin Gemini wrapper: structured JSON output, retries, a fallback model, and call counting."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Callable

from google import genai
from google.genai import errors, types

from agent.config import Config

RETRYABLE = {429, 500, 502, 503, 504}


class LLMUnavailable(Exception):
    pass


class LLMStreamBroken(LLMUnavailable):
    """The stream started and then failed: some output may already have been used."""


class LLM:
    def __init__(self, cfg: Config, system_prompt: str, log: Callable[[str, dict], None] | None = None):
        if not cfg.gemini_api_key:
            raise SystemExit("Missing GEMINI_API_KEY in .env")
        keys = [("primary", cfg.gemini_api_key)]
        if cfg.gemini_fallback_api_key:
            keys.append(("fallback", cfg.gemini_fallback_api_key))
        models = [cfg.gemini_model] + ([cfg.gemini_fallback_model] if cfg.gemini_fallback_model else [])
        # System instructions and JSON schemas need the v1beta API surface.
        clients = {label: genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))
                   for label, key in keys}
        # Each (key, model) pair has its own free-tier quota. Order: best model first on every key,
        # then the lighter model on every key.
        self.routes = [(label, clients[label], model) for model in models for label, _ in keys]
        self.dead: set = set()                  # (key, model) pairs out of daily quota / not usable
        self.cooling: dict = {}                 # (key, model) -> monotonic time it may be retried
        self.models = models
        self.system_prompt = system_prompt
        self.thinking_budget = cfg.gemini_thinking_budget
        self.log = log or (lambda kind, data: None)
        self.calls = 0
        self.cache_dir = cfg.state_dir / "llm_cache" if cfg.llm_cache else None

    def _cache_path(self, prompt: str, schema: dict) -> Path | None:
        """Dev-only response cache (LLM_CACHE=1): re-running a scripted scenario costs no quota.
        The clock line is masked so the same conversation hits the cache on a later run."""
        if not self.cache_dir:
            return None
        stable = re.sub(r"today: [^\n|]*", "today: <masked>", prompt)
        key = hashlib.sha256(json.dumps([self.models[0], self.system_prompt, stable, schema],
                                        sort_keys=True).encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def _config(self, schema: dict) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.3,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
        )

    def json_call(self, prompt: str, schema: dict, purpose: str) -> dict:
        cache = self._cache_path(prompt, schema)
        if cache and cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            self.log("llm_call", {"purpose": purpose, "model": "cache", "prompt": prompt, "response": data})
            return data
        data = self._call(prompt, schema, purpose)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def _call(self, prompt: str, schema: dict, purpose: str) -> dict:
        """Try each (key, model) route once; a rate-limited route cools down for a minute, a route out
        of daily quota (or unusable) is skipped for the rest of the run. Two passes in total."""
        last_err = None
        for pass_no in range(2):
            for label, client, model in self.routes:
                route = (label, model)
                if route in self.dead or self.cooling.get(route, 0) > time.monotonic():
                    continue
                self.calls += 1
                started = time.time()
                try:
                    resp = client.models.generate_content(model=model, contents=prompt,
                                                          config=self._config(schema))
                    data = json.loads(resp.text)
                    self.log("llm_call", {"purpose": purpose, "model": model, "key": label, "prompt": prompt,
                                          "response": data, "seconds": round(time.time() - started, 2),
                                          "usage": _usage(resp)})
                    return data
                except errors.APIError as e:
                    last_err = e
                    self._mark(route, e, purpose)
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    last_err = e                      # malformed output: just try the next route
                    self.log("llm_error", {"purpose": purpose, "model": model, "key": label,
                                           "error": f"bad JSON: {e}"})
                except Exception as e:                # network errors from the SDK's transport
                    last_err = e
                    self.log("llm_error", {"purpose": purpose, "model": model, "key": label,
                                           "error": repr(e)[:300]})
            if pass_no == 0:
                self.cooling.clear()                  # second pass: give rate-limited routes one more go
                time.sleep(3)
        raise LLMUnavailable(str(last_err))


    # ------------------------------------------------------------ streaming

    def _mark(self, route: tuple, e: errors.APIError, purpose: str) -> None:
        self.log("llm_error", {"purpose": purpose, "model": route[1], "key": route[0], "code": e.code,
                               "error": str(e)[:300]})
        if e.code == 429 and "PerDay" in str(e) or e.code not in RETRYABLE:
            self.dead.add(route)                  # daily quota gone, bad key, unknown model...
        else:
            self.cooling[route] = time.monotonic() + 60

    def stream_json(self, prompt: str, schema: dict, purpose: str):
        """Same request as json_call, but yields the raw JSON text as it arrives (one request, same quota).
        A route that fails before sending anything falls through to the next one. If a stream breaks
        midway, LLMStreamBroken is raised so the caller can decide what to do with what it already used."""
        cache = self._cache_path(prompt, schema)
        if cache and cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            self.log("llm_call", {"purpose": purpose, "model": "cache", "prompt": prompt, "response": data})
            yield json.dumps(data, ensure_ascii=False)
            return
        last_err = None
        for label, client, model in self.routes:
            route = (label, model)
            if route in self.dead or self.cooling.get(route, 0) > time.monotonic():
                continue
            self.calls += 1
            started, parts, last = time.time(), [], None
            try:
                for chunk in client.models.generate_content_stream(model=model, contents=prompt,
                                                                   config=self._config(schema)):
                    last = chunk
                    piece = chunk.text or ""
                    if piece:
                        parts.append(piece)
                        yield piece
                data = json.loads("".join(parts))
            except errors.APIError as e:
                last_err = e
                self._mark(route, e, purpose)
                if parts:
                    raise LLMStreamBroken(str(e)) from e
                continue
            except Exception as e:                    # bad JSON or a network drop
                last_err = e
                self.log("llm_error", {"purpose": purpose, "model": model, "key": label,
                                       "error": f"stream: {e!r}"[:300]})
                if parts:
                    raise LLMStreamBroken(str(e)) from e
                continue
            self.log("llm_call", {"purpose": purpose, "model": model, "key": label, "prompt": prompt,
                                  "response": data, "seconds": round(time.time() - started, 2),
                                  "usage": _usage(last), "streamed": True})
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return
        # Every route was skipped or failed up front: one last try through the retrying path.
        data = self._call(prompt, schema, purpose)
        yield json.dumps(data, ensure_ascii=False)


def _usage(resp) -> dict | None:
    u = getattr(resp, "usage_metadata", None)
    if not u:
        return None
    return {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
            "thinking": getattr(u, "thoughts_token_count", None)}
