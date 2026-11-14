"""In-memory state of one conversation. This, not the chat history, is the agent's source of truth."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.config import now_ist
from agent.domain.diagnose import Diagnosis, Issue


@dataclass
class Session:
    candidate_id: str
    record: dict                               # latest server copy (updated after every write)
    diagnosis: Diagnosis                       # what the record needed at the start
    identity: str = "unknown"                  # unknown | confirmed | denied
    saved: dict = field(default_factory=dict)  # field -> value written this chat
    refused: dict = field(default_factory=dict)   # field -> candidate's words
    failed_attempts: dict = field(default_factory=dict)  # field -> rejected values count
    held: list = field(default_factory=list)   # saves proposed before identity was confirmed
    escalations: list = field(default_factory=list)
    team_notes: list = field(default_factory=list)       # off-topic questions, extra details
    followup: dict | None = None               # {"text": .., "iso": ..}
    conflicts: dict = field(default_factory=dict)
    write_errors: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    turns: int = 0
    llm_calls: int = 0
    llm_failed: bool = False
    end_reason: str = ""
    started_at: str = field(default_factory=lambda: now_ist().isoformat())
    gave_up: dict = field(default_factory=dict)   # field -> why we stopped asking (attempt cap)
    soft_warned: set = field(default_factory=set)  # cross-field rules already raised once
    save_events: int = 0                       # bumps on every applied write (read-back trigger)
    readback_sent: bool = False
    idle_turns: int = 0                        # consecutive turns with no progress
    hostile_turns: int = 0                     # turns with abusive words + angry sentiment
    forced_end: str = ""                       # set when code (not the LLM) ends the chat
    timeline: list = field(default_factory=list)   # one compact code-written line per turn
    signal_counts: dict = field(default_factory=dict)  # signal -> how many turns it fired

    @property
    def issues(self) -> dict[str, Issue]:
        return {i.field: i for i in self.diagnosis.invalid}

    @property
    def needed(self) -> list[str]:
        return self.diagnosis.needed

    @property
    def remaining(self) -> list[str]:
        return [f for f in self.needed
                if f not in self.saved and f not in self.refused and f not in self.gave_up]

    @property
    def last_agent_message(self) -> str:
        return next((t["text"] for t in reversed(self.transcript) if t["role"] == "agent"), "")

    def progress_signature(self) -> tuple:
        return (self.save_events, len(self.refused), bool(self.followup), self.identity,
                len(self.escalations))

    @property
    def candidate_messages(self) -> list[str]:
        return [t["text"] for t in self.transcript if t["role"] == "candidate"]

    @property
    def candidate_replied(self) -> bool:
        return any(m.strip() for m in self.candidate_messages)

    def add(self, role: str, text: str) -> None:
        self.transcript.append({"role": role, "text": text, "at": now_ist().isoformat()})
