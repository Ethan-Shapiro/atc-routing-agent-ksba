"""Runs every scenario in scenarios.py against the matching agent and prints a transcript.

    python run_scenarios.py            # run all scenarios
    python run_scenarios.py --id tower_go_around   # run just one, by id
"""

import argparse
import sys

from agents import call_agent
from scenarios import SCENARIOS


def run_one(scenario: dict) -> None:
    print("=" * 88)
    print(f"[{scenario['id']}]  role={scenario['role']}")
    print(f"Scenario: {scenario['description']}")
    print(f"Expected: {scenario['expect']}")
    print("-" * 88)
    print("INPUT:")
    print(scenario["input"])
    print("-" * 88)
    response = call_agent(scenario["role"], scenario["input"])
    print("AGENT RESPONSE:")
    print(response)
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Run only the scenario with this id")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.id:
        scenarios = [s for s in SCENARIOS if s["id"] == args.id]
        if not scenarios:
            print(f"No scenario with id={args.id!r}. Known ids:", file=sys.stderr)
            for s in SCENARIOS:
                print(f"  {s['id']}", file=sys.stderr)
            sys.exit(1)

    for scenario in scenarios:
        run_one(scenario)


if __name__ == "__main__":
    main()
