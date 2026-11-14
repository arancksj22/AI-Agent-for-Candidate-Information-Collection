"""CLI entry point.

  python -m agent --candidate-id c001     chat with one candidate
  python -m agent --diagnose-all          show what every candidate needs (no LLM, no writes)
  python -m agent --reset                 restore the workspace to its original data
"""
from __future__ import annotations

import argparse
import sys

from .api_client import ApiError, CandidateRepo
from .config import load_config


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m agent", description="ACME candidate information agent")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate-id", help="candidate record id, e.g. c001")
    g.add_argument("--diagnose-all", action="store_true", help="print what each candidate needs (no LLM)")
    g.add_argument("--reset", action="store_true", help="POST /v1/reset (restore original data)")
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
        from .diagnose import describe, diagnose
        for record in CandidateRepo(cfg).list():
            print(describe(record, diagnose(record)))
        return 0

    from .agent import Agent
    from .chat_io import Terminal
    from .runlog import RunLog

    log = RunLog(cfg.runs_dir, args.candidate_id)
    repo = CandidateRepo(cfg, log=log)
    agent = Agent(cfg, repo, Terminal(show_actions=not args.quiet), log)
    print(f"Chatting as the agent for {args.candidate_id}. Type your replies as the candidate; "
          f"/quit to leave.\n")
    try:
        agent.run(args.candidate_id)
    except ApiError as e:
        print(f"Candidate API error: {e}")
        log.save(status="error", error=str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
