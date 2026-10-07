"""`ocp idle-timeout` / `ocp ignore-cache-expiry` (T52): config.yaml's global
default for how long an idle agent may sit before the daemon closes it, and
whether a resume may ignore its prompt cache's expiry - plus, with --agent,
one agent's own override of either. `ocp auto-accept` (T64): whether dialogs
are answered for a working agent."""
from __future__ import annotations

import argparse
import sys

from ..infra import protected, timefmt
from ..session import autoaccept, idle_close


def _proposed(switch: str, value, args) -> int | None:
    """From an agent's session, setting a global switch is a `global` HR proposal; None otherwise."""
    if value is None or getattr(args, "agent", None) or not protected.in_agent_session():
        return None
    from .hr import propose_for_session
    return propose_for_session("global", switch, {"value": str(value)}, args.why)


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


def span_arg(text: str) -> int:
    """An argparse type: a duration with its unit (`90m`), as seconds."""
    try:
        return timefmt.parse_span(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _describe_timeout(seconds: int) -> str:
    return ("never closes while idle" if seconds == idle_close.NEVER
            else f"closes after {seconds}s idle")


def cmd_idle_timeout(args) -> int:
    proposed = _proposed("idle-timeout", None if args.seconds is None else ("never" if args.seconds == idle_close.NEVER else args.seconds), args)
    if proposed is not None:
        return proposed
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


def cmd_idle_background(args) -> int:
    """T424: when an idle agent that still runs background work is told to finish or stop
    it, and how long after that the work is stopped. Global, or one agent's own."""
    if args.clear and not args.agent:
        print("--clear only applies with --agent", file=sys.stderr)
        return 1
    try:
        for key, value in (("bg_notice_s", args.notice), ("bg_grace_s", args.grace),
                           ("bg_check_s", args.check)):
            if value is not None or args.clear:
                idle_close.set_bg_seconds(key, None if args.clear else value, args.agent)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    who = args.agent or "default"
    notice = idle_close.bg_notice_s(args.agent) if args.agent else idle_close.config_seconds(
        "bg_notice_s", idle_close.default_close_timeout_s())
    grace = idle_close.bg_grace_s(args.agent) if args.agent else idle_close.config_seconds(
        "bg_grace_s", idle_close.BG_GRACE_S)
    check = idle_close.bg_check_s(args.agent) if args.agent else idle_close.config_seconds(
        "bg_check_s", idle_close.BG_CHECK_S)
    print(f"{who}: asked which background work to stop every {timefmt.ago(check)} while any runs")
    closes = idle_close.close_timeout_s(args.agent) if args.agent else idle_close.default_close_timeout_s()
    if closes == idle_close.NEVER:
        print(f"{who}: never told - its idle window is 'never close'")
        return 0
    print(f"{who}: told to finish or stop its background work after {timefmt.ago(notice)} of nothing "
          f"shown, and it is stopped {timefmt.ago(grace)} after that")
    return 0


def cmd_notice_digest(args) -> int:
    """How long after its first waiting result an agent is woken once with a digest of them."""
    proposed = _proposed("notice-digest", None if args.span is None else f"{args.span}s", args)
    if proposed is not None:
        return proposed
    if args.clear and not args.agent:
        print("--clear only applies with --agent", file=sys.stderr)
        return 1
    try:
        if args.clear or args.span is not None:
            idle_close.set_notice_digest_s(None if args.clear else args.span, args.agent)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    seconds = (idle_close.notice_digest_s(args.agent) if args.agent
               else idle_close.config_seconds("notice_digest_s", idle_close.NOTICE_DIGEST_S))
    print(f"{args.agent or 'default'}: results of work it opened are told in one digest "
          f"{timefmt.ago(seconds)} after the first (sooner with any message); a failure, block or FATAL at once")
    return 0


def cmd_auto_accept(args) -> int:
    proposed = _proposed("auto-accept", args.state, args)
    if proposed is not None:
        return proposed
    if args.state is not None:
        try:
            autoaccept.set_enabled(args.state == "on")
        except Exception as e:  # noqa: BLE001
            print(f"error: {e}", file=sys.stderr)
            return 1
    print("auto-accept: " + ("on - browser tool calls are said yes to once, and a declared "
                            "folder's trust screen is accepted" if autoaccept.enabled()
                            else "off - every dialog is left for a person"))
    return 0


def _describe_cache(on: bool) -> str:
    return ("a resume may reuse its session however long it has been closed"
            if on else "a resume starts fresh once its prompt cache has expired (today's behaviour)")


def cmd_ignore_cache_expiry(args) -> int:
    proposed = _proposed("ignore-cache-expiry", args.state, args)
    if proposed is not None:
        return proposed
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
