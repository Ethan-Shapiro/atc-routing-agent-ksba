"""Runs a sequential handoff chain — one aircraft through multiple KSBA roles in order,
each agent's real response feeding the next agent's input — and prints the transcript.

    python run_chain.py               # run both chains (departure, then arrival)
    python run_chain.py --chain departure
    python run_chain.py --chain arrival
"""

import argparse

from agents import call_agent
from chains import CHAINS


def run_chain(name: str, steps: list[dict]) -> None:
    print("#" * 88)
    print(f"# CHAIN: {name}")
    print("#" * 88)
    ctx: dict = {}
    for step in steps:
        print("=" * 88)
        print(f"[{step['id']}]  role={step['role']}  — {step['label']}")
        print("-" * 88)
        agent_input = step["build_input"](ctx)
        print("INPUT:")
        print(agent_input)
        print("-" * 88)
        response = call_agent(step["role"], agent_input)
        print("AGENT RESPONSE:")
        print(response)
        print()
        ctx[step["id"]] = response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", choices=list(CHAINS), help="Run only this chain")
    args = parser.parse_args()

    chains = {args.chain: CHAINS[args.chain]} if args.chain else CHAINS
    for name, steps in chains.items():
        run_chain(name, steps)


if __name__ == "__main__":
    main()
