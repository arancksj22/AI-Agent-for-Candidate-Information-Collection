"""Client for the Candidate DB API: safe, minimal, idempotent writes."""
from __future__ import annotations

import hashlib
import os
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from agent.config import Config

RETRYABLE = {429, 500, 502, 503, 504}
RATE_LIMIT_PER_MIN = 120


class ApiError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"API error {status}: {message}")
        self.status = status


@dataclass
class PatchResult:
    record: dict                      # latest record after the write (or after re-reading)
    applied: dict = field(default_factory=dict)    # fields actually written
    conflicts: dict = field(default_factory=dict)  # field -> {"ours": .., "theirs": .., "was": ..}
    skipped_noop: list = field(default_factory=list)


def idempotency_key(candidate_id: str, version: int, body: dict) -> str:
    """Same logical write → same key, even across a crash, a replay from the DLQ, or the Lambda."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{candidate_id}|{version}|{canonical}".encode()).hexdigest()[:32]


class RateLimiter:
    """Sliding window: at most `limit` requests in any 60 s (the API allows 120/min)."""

    def __init__(self, limit: int = RATE_LIMIT_PER_MIN):
        self.limit, self.stamps, self.lock = limit, deque(), threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            while self.stamps and now - self.stamps[0] > 60:
                self.stamps.popleft()
            if len(self.stamps) >= self.limit:
                time.sleep(60 - (now - self.stamps[0]) + 0.05)
            self.stamps.append(time.monotonic())


class CandidateRepo:
    def __init__(self, cfg: Config, log: Callable[[str, dict], None] | None = None, outbox=None):
        self.base = cfg.api_base_url
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0),
            headers={"Authorization": f"Bearer {cfg.api_key}"},
        )
        self.log = log or (lambda kind, data: None)
        self.outbox = outbox
        self.limiter = RateLimiter()

    # ------------------------------------------------------------ transport

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 headers: dict | None = None, attempts: int = 3) -> httpx.Response:
        url = f"{self.base}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            self.limiter.wait()
            self.log("api_request", {"method": method, "path": path, "headers": headers,
                                     "body": body, "attempt": attempt})
            try:
                resp = self.client.request(method, url, json=body, headers=headers)
            except httpx.TransportError as e:
                last_exc = e
                self.log("api_error", {"method": method, "path": path, "error": repr(e), "attempt": attempt})
            else:
                self.log("api_response", {"method": method, "path": path, "status": resp.status_code,
                                          "body": _json_or_text(resp)})
                if resp.status_code not in RETRYABLE:
                    return resp
                last_exc = ApiError(resp.status_code, resp.text[:300])
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(float(retry_after), 10))
                    continue
            if attempt < attempts:
                time.sleep(0.5 * 2 ** (attempt - 1))
        if isinstance(last_exc, ApiError):
            raise last_exc
        raise ApiError(None, f"network failure: {last_exc!r}")

    # ------------------------------------------------------------ reads

    def get(self, candidate_id: str) -> dict:
        resp = self._request("GET", f"/v1/candidates/{candidate_id}")
        if resp.status_code == 404:
            raise ApiError(404, f"candidate {candidate_id} not found")
        if resp.status_code != 200:
            raise ApiError(resp.status_code, resp.text[:300])
        return resp.json()

    def list(self) -> list[dict]:
        resp = self._request("GET", "/v1/candidates")
        if resp.status_code != 200:
            raise ApiError(resp.status_code, resp.text[:300])
        data = resp.json()
        return data.get("candidates", data) if isinstance(data, dict) else data

    def reset(self) -> None:
        resp = self._request("POST", "/v1/reset")
        if resp.status_code >= 300:
            raise ApiError(resp.status_code, resp.text[:300])

    # ------------------------------------------------------------ writes

    def patch(self, base: dict, changes: dict) -> PatchResult:
        """Write `changes` on top of `base` (the record as we last read it).

        - Fields whose value already equals the stored value are dropped (no no-op writes).
        - If-Match carries the version; the Idempotency-Key is derived from (id, version, body), so a
          retry or a later replay of the same write reuses it and can't apply twice.
        - On 409 (three-way merge): re-read; a field the other writer didn't touch (theirs == base)
          is re-applied on the new version; a field they changed is NOT overwritten (conflict).
        - The intent goes to the outbox before sending; if it still fails after retries it's
          dead-lettered (local DLQ, plus SQS when configured) and the error is re-raised.
        """
        result = PatchResult(record=base)
        body = {}
        for k, v in changes.items():
            if base.get(k) == v:
                result.skipped_noop.append(k)
            else:
                body[k] = v
        if not body:
            return result

        current = base
        for _ in range(3):
            key = idempotency_key(current["id"], current["version"], body)
            intent = {"key": key, "candidate_id": current["id"], "base_version": current["version"],
                      "was": {k: base.get(k) for k in body}, "body": body}
            if self.outbox:
                self.outbox.begin(intent)
            headers = {"If-Match": f'"{current["version"]}"', "Idempotency-Key": key,
                       "Content-Type": "application/json"}
            try:
                if os.environ.get("SIMULATE_WRITE_FAILURE") == "1":   # demo/test switch: API "down" for writes
                    raise ApiError(None, "simulated outage (SIMULATE_WRITE_FAILURE=1)")
                resp = self._request("PATCH", f"/v1/candidates/{current['id']}", body=body, headers=headers)
            except ApiError as e:
                if self.outbox:
                    where = self.outbox.dead_letter(intent, str(e))
                    self.log("dead_lettered", {"key": key, "where": where, "error": str(e)})
                raise
            if resp.status_code in (200, 201):
                if self.outbox:
                    self.outbox.done(key)
                result.record = resp.json()
                result.applied.update(body)
                return result
            if self.outbox:
                self.outbox.done(key, status=f"http_{resp.status_code}")
            if resp.status_code == 409:
                fresh = self.get(current["id"])
                rebased = {}
                for k, ours in body.items():
                    theirs, was = fresh.get(k), base.get(k)
                    if theirs == ours:
                        result.applied[k] = ours          # already has our value (e.g. replayed)
                    elif theirs == was:
                        rebased[k] = ours                 # untouched by the other writer → still ours
                    else:
                        result.conflicts[k] = {"ours": ours, "theirs": theirs, "was": was}
                self.log("conflict", {"rebased": list(rebased), "conflicts": result.conflicts,
                                      "new_version": fresh.get("version")})
                result.record = fresh
                if not rebased:
                    return result
                body, current = rebased, fresh
                continue
            if resp.status_code == 422:
                # Our request is wrong (unknown/read-only field). Retrying can't help.
                raise ApiError(422, resp.text[:500])
            raise ApiError(resp.status_code, resp.text[:300])
        raise ApiError(409, "record kept changing during the write")

    def replay(self, intent: dict) -> PatchResult:
        """Re-send a stored intent (crash recovery / DLQ). Rebuilds the base it was computed on, so
        the same Idempotency-Key is used and the three-way merge still protects newer edits."""
        base = {"id": intent["candidate_id"], "version": intent["base_version"], **intent["was"]}
        return self.patch(base, intent["body"])


def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text[:500]
