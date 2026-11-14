"""Thin Gemini wrapper: structured JSON output, retries, a fallback model, and call counting."""
from __future__ import annotations

import json
import time
from typing import Callable

from google import genai
from google.genai import errors, types

from agent.config import Config

RETRYABLE = {429, 500, 502, 503, 504}


class LLMUnavailable(Exception):
    pass


class LLM:
    def __init__(self, cfg: Config, system_prompt: str, log: Callable[[str, dict], None] | None = None):
        if not cfg.gemini_api_key:
            raise SystemExit("Missing GEMINI_API_KEY in .env")
        # System instructions and JSON schemas need the v1beta API surface.
        self.client = genai.Client(api_key=cfg.gemini_api_key,
                                   http_options=types.HttpOptions(api_version="v1beta"))
        self.models = [cfg.gemini_model] + ([cfg.gemini_fallback_model] if cfg.gemini_fallback_model else [])
        self.system_prompt = system_prompt
        self.thinking_budget = cfg.gemini_thinking_budget
        self.log = log or (lambda kind, data: None)
        self.calls = 0

    def _config(self, schema: dict) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.3,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
        )

    def json_call(self, prompt: str, schema: dict, purpose: str) -> dict:
        last_err = None
        for model in self.models:
            for attempt in range(2):
                self.calls += 1
                started = time.time()
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=prompt, config=self._config(schema))
                    data = json.loads(resp.text)
                    self.log("llm_call", {"purpose": purpose, "model": model, "prompt": prompt,
                                          "response": data, "seconds": round(time.time() - started, 2),
                                          "usage": _usage(resp)})
                    return data
                except errors.APIError as e:
                    last_err = e
                    self.log("llm_error", {"purpose": purpose, "model": model, "code": e.code,
                                           "error": str(e)[:300]})
                    if e.code not in RETRYABLE:
                        break
                    if e.code == 429 and "quota" in str(e).lower() and "PerDay" in str(e):
                        break                                 # daily quota: retrying won't help
                    time.sleep(2 + 3 * attempt)
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    last_err = e
                    self.log("llm_error", {"purpose": purpose, "model": model, "error": f"bad JSON: {e}"})
                except Exception as e:                        # network errors from the SDK's transport
                    last_err = e
                    self.log("llm_error", {"purpose": purpose, "model": model, "error": repr(e)[:300]})
                    time.sleep(2)
        raise LLMUnavailable(str(last_err))


def _usage(resp) -> dict | None:
    u = getattr(resp, "usage_metadata", None)
    if not u:
        return None
    return {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
            "thinking": getattr(u, "thoughts_token_count", None)}
