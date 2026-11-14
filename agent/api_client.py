"""Client for the Candidate DB API: safe, minimal, idempotent writes."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .config import Config

RETRYABLE = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"API error {status}: {message}")
        self.status = status


@dataclass
class PatchResult:
    record: dict                      # latest record after the write (or after re-reading)
    applied: dict = field(default_factory=dict)    # fields actually written
    conflicts: dict = field(default_factory=dict)  # field -> {"ours": .., "theirs": ..}
    skipped_noop: list = field(default_factory=list)


class CandidateRepo:
    def __init__(self, cfg: Config, log: Callable[[str, dict], None] | None = None):
        self.base = cfg.api_base_url
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0),
            headers={"Authorization": f"Bearer {cfg.api_key}"},
        )
        self.log = log or (lambda kind, data: None)

    # ------------------------------------------------------------ transport

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 headers: dict | None = None, attempts: int = 3) -> httpx.Response:
        url = f"{self.base}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            self.log("api_request", {"method": method, "path": path, "headers": _safe_headers(headers),
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
        - If-Match carries base["version"]; one Idempotency-Key per logical write, reused on retries.
        - On 409, re-read: a field still equal to what we based our change on is re-applied on the
          new version; a field someone else changed is NOT overwritten and is reported as a conflict.
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
            key = str(uuid.uuid4())
            headers = {"If-Match": f'"{current["version"]}"', "Idempotency-Key": key,
                       "Content-Type": "application/json"}
            resp = self._request("PATCH", f"/v1/candidates/{current['id']}", body=body, headers=headers)
            if resp.status_code in (200, 201):
                result.record = resp.json()
                result.applied.update(body)
                return result
            if resp.status_code == 409:
                fresh = self.get(current["id"])
                rebased = {}
                for k, ours in body.items():
                    theirs, was = fresh.get(k), base.get(k)
                    if theirs == ours:
                        continue                          # already has our value
                    if theirs == was:
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


def _safe_headers(headers: dict | None) -> dict | None:
    if not headers:
        return None
    return {k: v for k, v in headers.items() if k.lower() != "authorization"}


def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text[:500]
