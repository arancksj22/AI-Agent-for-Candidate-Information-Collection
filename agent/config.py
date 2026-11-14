"""Configuration loaded from .env / environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


@dataclass(frozen=True)
class Config:
    api_base_url: str
    api_key: str
    gemini_api_key: str
    gemini_model: str
    gemini_thinking_budget: int
    nudge_after_s: float
    give_up_after_s: float
    max_turns: int
    runs_dir: Path


def load_config() -> Config:
    def need(name: str) -> str:
        val = os.environ.get(name, "").strip()
        if not val:
            raise SystemExit(f"Missing {name}. Copy .env.example to .env and fill it in.")
        return val

    return Config(
        api_base_url=need("CANDIDATE_API_BASE_URL").rstrip("/"),
        api_key=need("CANDIDATE_API_KEY"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        gemini_thinking_budget=int(os.environ.get("GEMINI_THINKING_BUDGET", "512")),
        nudge_after_s=float(os.environ.get("NUDGE_AFTER_SECONDS", "60")),
        give_up_after_s=float(os.environ.get("GIVE_UP_AFTER_SECONDS", "120")),
        max_turns=int(os.environ.get("MAX_TURNS", "30")),
        runs_dir=ROOT / "runs",
    )
