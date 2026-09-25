"""`ocp idle-timeout` / `ocp ignore-cache-expiry` (T52): config.yaml's global
default for how long an idle agent may sit before the daemon closes it, and
whether a resume may ignore its prompt cache's expiry - plus, with --agent,
one agent's own override of either."""
from __future__ import annotations

import argparse
import sys

from ..session import idle_close


def seconds_or_never(text: str) -> int:
    if text.strip().lower() == "never":
        return idle_close.NEVER
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("an integer number of seconds, or 'never'")
    if n < 0:
        raise argparse.ArgumentTypeError("a timeout is at least 0 (0 = never close)")
    return n


def _describe_timeout(seconds: int) -> str:
    return ("never closes while idle" if seconds == idle_close.NEVER
            else f"closes after {seconds}s idle")


def cmd_idle_timeout(args) -> int:
    if args.clear and not args.agent:
        print("--clear only applies with --agent", file=sys.stderr)
        return 1
    try:
        if args.agent and args.clear:
            idle_close.set_agent_close_timeout(args.agent, None)
        elif args.agent and args.seconds is not None:
            idle_close.set_agent_close_timeout(args.agent, args.seconds)
        elif not args.agent and args.seconds is not None:
            idle_close.set_close_timeout(args.seconds)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.agent:
        print(f"{args.agent}: {_describe_timeout(idle_close.close_timeout_s(args.agent))}")
    else:
        print(f"default: {_describe_timeout(idle_close.default_close_timeout_s())}")
    return 0


def _describe_cache(on: bool) -> str:
    return ("a resume may reuse its session however long it has been closed"
            if on else "a resume starts fresh once its prompt cache has expired (today's behaviour)")


def cmd_ignore_cache_expiry(args) -> int:
    if args.clear and not args.agent:
        print("--clear only applies with --agent", file=sys.stderr)
        return 1
    try:
        if args.agent and args.clear:
            idle_close.set_agent_ignore_cache_expiry(args.agent, None)
        elif args.agent and args.state is not None:
            idle_close.set_agent_ignore_cache_expiry(args.agent, args.state == "on")
        elif not args.agent and args.state is not None:
            idle_close.set_ignore_cache_expiry(args.state == "on")
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.agent:
        print(f"{args.agent}: {_describe_cache(idle_close.ignore_cache_expiry(args.agent))}")
    else:
        print(f"default: {_describe_cache(idle_close.default_ignore_cache_expiry())}")
    return 0
