"""CLI entry point.

  python -m agent --candidate-id c001     chat with one candidate
  python -m agent --diagnose-all          show what every candidate needs (no LLM, no writes)
  python -m agent --reset                 restore the workspace to its original data
  python -m agent --replay-dlq            re-send writes that failed earlier (local DLQ)
  python -m agent --scenarios             scripted-candidate regression run (uses the LLM)
"""
from __future__ import annotations

import argparse
import sys

from agent.adapters.candidate_api import ApiError, CandidateRepo
from agent.adapters.outbox import Outbox
from agent.config import load_config

# Emojis/box characters must never crash a Windows console or a redirected output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BANNER = "\n".join([
    "",
    "═" * 62,
    "   ACME Candidate Information Agent",
    "   AI chat agent that completes internship applications",
    "   Made with ❤️  by Aranck",
    "═" * 62,
])


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m agent", description="ACME candidate information agent")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate-id", help="candidate record id, e.g. c001")
    g.add_argument("--diagnose-all", action="store_true", help="print what each candidate needs (no LLM)")
    g.add_argument("--reset", action="store_true", help="POST /v1/reset (restore original data)")
    g.add_argument("--replay-dlq", action="store_true", help="re-send dead-lettered writes")
    g.add_argument("--scenarios", nargs="*", metavar="NAME",
                   help="run scripted-candidate scenarios against the real agent (uses the LLM)")
    p.add_argument("--nudge-after", type=float, help="seconds of silence before a nudge")
    p.add_argument("--give-up-after", type=float, help="total seconds of silence before ending the chat")
    p.add_argument("--quiet", action="store_true", help="hide the agent's action log lines")
    args = p.parse_args()

    cfg = load_config()
    if args.nudge_after or args.give_up_after:
        from dataclasses import replace
        cfg = replace(cfg, nudge_after_s=args.nudge_after or cfg.nudge_after_s,
                      give_up_after_s=args.give_up_after or cfg.give_up_after_s)

    if args.reset:
        CandidateRepo(cfg).reset()
        print("Workspace reset to original data.")
        return 0

    if args.diagnose_all:
        from agent.domain.diagnose import describe, diagnose
        for record in CandidateRepo(cfg).list():
            print(describe(record, diagnose(record)))
        return 0

    if args.replay_dlq:
        return replay_dlq(cfg)

    if args.scenarios is not None:
        from agent.scenarios import run_scenarios
        return run_scenarios(cfg, args.scenarios)

    from agent.adapters.runlog import RunLog
    from agent.adapters.terminal import Terminal
    from agent.conversation.agent import Agent

    candidate_id = args.candidate_id.strip()
    log = RunLog(cfg.runs_dir, candidate_id)
    repo = CandidateRepo(cfg, log=log, outbox=Outbox(cfg))
    agent = Agent(cfg, repo, Terminal(show_actions=not args.quiet), log)
    print(BANNER)
    print(f"Chatting as the agent for {candidate_id}. Type your replies as the candidate; "
          f"/quit to leave.\n")
    try:
        agent.run(candidate_id)
    except ApiError as e:
        print(f"Candidate API error: {e}")
        log.save(status="error", error=str(e))
        return 1
    return 0


def replay_dlq(cfg) -> int:
    outbox = Outbox(cfg)
    repo = CandidateRepo(cfg)                  # no outbox here: a failed replay stays in the DLQ
    items = outbox.dlq_open()
    if not items:
        print("DLQ is empty.")
        return 0
    failed = 0
    for intent in items:
        try:
            res = repo.replay(intent)
        except ApiError as e:
            failed += 1
            print(f"  {intent['candidate_id']} {list(intent['body'])}: still failing ({e})")
            continue
        status = "replayed" if not res.conflicts else "conflict"
        outbox.dlq_resolve(intent["key"], status)
        print(f"  {intent['candidate_id']} {list(intent['body'])}: {status}"
              + (f" (not overwritten: {list(res.conflicts)})" if res.conflicts else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
