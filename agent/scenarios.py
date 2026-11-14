"""Scripted-candidate regression runner: `python -m agent --scenarios [name ...]`.

Each scenario resets the workspace, plays fixed candidate replies into the *real* agent (real LLM,
real API), then checks the final record. Run logs land in runs/, so this also produces the sample
transcripts. It costs LLM quota (~3-5 calls per chatty scenario); set LLM_CACHE=1 to make re-runs free.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.adapters.candidate_api import CandidateRepo
from agent.adapters.outbox import Outbox
from agent.adapters.runlog import RunLog
from agent.adapters.terminal import EOF
from agent.config import Config
from agent.conversation.agent import Agent

SILENCE = "<silence>"


@dataclass
class Scenario:
    name: str
    candidate_id: str
    replies: list[str]
    status: str
    fields: dict = field(default_factory=dict)        # field -> expected final value


SCENARIOS = [
    Scenario("complete_record", "c003", [], "completed"),
    Scenario("silent", "c002", [SILENCE, SILENCE], "no_answer"),
    Scenario("wrong_person", "c007", ["No, this is his brother. He's not around."], "needs_human",
             {"email": None}),
    Scenario("happy_path", "c001",
             ["yes, this is Aarav", "sure, it's aarav.mehta@gmail.com and my CGPA is 8.45", "yes that's right"],
             "completed", {"email": "aarav.mehta@gmail.com", "cgpa": 8.45}),
    Scenario("percentage_then_cgpa", "c001",
             ["yes", "aarav.m@gmail.com, and cgpa is 85", "oh sorry, it's 8.5 out of 10", "yes"],
             "completed", {"cgpa": 8.5}),
    Scenario("refuses_cgpa", "c001",
             ["yes", "my email is aarav.m@gmail.com",
              "I'd rather not share my CGPA", "no, I really don't want to share it"],
             "refused", {"email": "aarav.m@gmail.com", "cgpa": None}),
    Scenario("continue_later", "c007",
             ["yes I'm Vikram", "I'm at DJ Sanghvi doing BE in computer engineering",
              "I'm busy right now, can we continue tomorrow at 6pm?"],
             "in_progress"),
    Scenario("insists_on_2031", "c008",
             ["yes", "no, it really is 2031, the form is correct", "I'm sure, it's 2031"],
             "needs_human", {"graduation_year": 2031}),
    Scenario("fix_bad_email", "c005",
             ["yes", "oh yes, kabir.shah@gmail.com is right", "Mumbai please", "yes"],
             "completed", {"email": "kabir.shah@gmail.com", "preferred_location": "Mumbai"}),
]


class ScriptedTerminal:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.show_actions = True

    def say(self, text: str) -> None:
        print(f"Agent: {text}")

    def info(self, text: str) -> None:
        print(f"  · {text}")

    def ask(self, timeout_s: float):
        if not self.replies:
            print("You: /quit  (script finished)")
            return EOF
        reply = self.replies.pop(0)
        if reply == SILENCE:
            print("You: (silence)")
            return None
        print(f"You: {reply}")
        return reply


def run_scenarios(cfg: Config, names: list[str]) -> int:
    chosen = [s for s in SCENARIOS if not names or s.name in names]
    if not chosen:
        print("Unknown scenario. Available: " + ", ".join(s.name for s in SCENARIOS))
        return 2
    repo = CandidateRepo(cfg)
    rows = []
    for sc in chosen:
        print(f"\n{'=' * 70}\nSCENARIO {sc.name} ({sc.candidate_id})\n{'=' * 70}")
        repo.reset()
        log = RunLog(cfg.runs_dir, f"{sc.candidate_id}-{sc.name}")
        agent = Agent(cfg, CandidateRepo(cfg, log=log, outbox=Outbox(cfg)), ScriptedTerminal(sc.replies), log)
        status = agent.run(sc.candidate_id)
        final = repo.get(sc.candidate_id)
        problems = []
        if final.get("collection_status") != sc.status:
            problems.append(f"status {final.get('collection_status')} != {sc.status}")
        for k, v in sc.fields.items():
            if final.get(k) != v:
                problems.append(f"{k}={final.get(k)!r} != {v!r}")
        rows.append((sc.name, sc.status, status, problems))
    repo.reset()

    print(f"\n{'=' * 70}\nRESULTS\n{'=' * 70}")
    for name, expected, got, problems in rows:
        mark = "PASS" if not problems else "FAIL"
        print(f"{mark}  {name:<22} expected {expected:<12} got {got:<12} {'; '.join(problems)}")
    return 0 if all(not p for *_, p in rows) else 1
