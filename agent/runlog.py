"""Per-run JSON log: transcript, LLM calls, actions and every API request/response."""
from __future__ import annotations

import json
from pathlib import Path

from .config import now_ist


class RunLog:
    def __init__(self, runs_dir: Path, candidate_id: str):
        self.path = runs_dir / f"{candidate_id}-{now_ist().strftime('%Y%m%d-%H%M%S')}.json"
        self.events: list[dict] = []
        self.meta: dict = {"candidate_id": candidate_id, "started_at": now_ist().isoformat()}

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append({"at": now_ist().isoformat(), "kind": kind, **data})

    def save(self, **summary) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {**self.meta, **summary, "events": self.events}
        self.path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return self.path
