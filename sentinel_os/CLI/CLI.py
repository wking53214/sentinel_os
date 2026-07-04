# Row Count: 167

"""
cli.py
------

Deterministic command-line interface for Iceberg Dashboard Server.

This CLI wraps client.py and provides:
- Snapshot inspection
- Queue + caller views
- Telemetry stream access
- Replay execution
- RL episode execution

Best‑in‑Class Notes:
- Deterministic: No randomness.
- Governance‑Safe: JSON‑safe output.
- Replay‑Friendly: Identical server → identical CLI output.
"""

from __future__ import annotations
import argparse
import json
from client import IcebergClient


def pretty(obj):
    """Deterministic pretty-print."""
    print(json.dumps(obj, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(
        description="Iceberg Dashboard CLI"
    )

    parser.add_argument(
        "--url",
        type=str,
        default="http://localhost:8000",
        help="Dashboard server base URL",
    )

    parser.add_argument(
        "command",
        type=str,
        choices=[
            "snapshot",
            "hash",
            "queues",
            "callers",
            "telemetry",
            "replay",
            "replay-events",
            "rl",
            "rl-episode",
        ],
        help="Command to execute",
    )

    args = parser.parse_args()
    client = IcebergClient(args.url)

    if args.command == "snapshot":
        pretty(client.snapshot())

    elif args.command == "hash":
        print(client.structural_hash())

    elif args.command == "queues":
        pretty(client.queues())

    elif args.command == "callers":
        pretty(client.callers())

    elif args.command == "telemetry":
        pretty(client.telemetry())

    elif args.command == "replay":
        pretty(client.replay())

    elif args.command == "replay-events":
        pretty(client.replay_events())

    elif args.command == "rl":
        pretty(client.rl())

    elif args.command == "rl-episode":
        pretty(client.rl_episode())


if __name__ == "__main__":
    main()