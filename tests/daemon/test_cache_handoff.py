"""Tests: the prompt cache's clock is read off the session's last response, and its life is the nearest TTL that wrote
the cache (an hour, or five minutes after an overage).
Run on its own: tools/test.sh tests/daemon/test_cache_handoff.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import cachettl  # noqa: E402

H1, M5 = cachettl.LONG_TTL_S, cachettl.SHORT_TTL_S


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _response(at: float, ttl: int | None, sidechain: bool = False, pad: int = 0) -> str:
    """One assistant line of a Claude transcript, written at `at`, whose request wrote the cache with
    life `ttl` (None: it only read the cache and wrote nothing)."""
    made = {"ephemeral_1h_input_tokens": 500 if ttl == H1 else 0,
            "ephemeral_5m_input_tokens": 500 if ttl == M5 else 0}
    line = {"type": "assistant", "timestamp": _iso(at), "isSidechain": sidechain,
            "message": {"id": f"m{at}", "usage": {"input_tokens": 2, "cache_read_input_tokens": 90000,
                                                  "cache_creation": made}}}
    if pad:
        line["pad"] = "x" * pad
    return json.dumps(line)


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def test_the_cache_clock_is_the_last_responses_time_and_the_ttl_the_nearest_that_wrote_the_cache(home: Path) -> None:
    use_home(home)
    t = time.time()
    one = _write(home / "a.jsonl", _response(t - 900, H1), json.dumps({"type": "user", "timestamp": _iso(t - 800)}))
    c = cachettl.read(str(one))
    check("ttl: a response that wrote an hour's cache is an hour, clocked from that response",
          c is not None and c.ttl == H1 and abs(c.at - (t - 900)) < 2, str(c))
    five = _write(home / "b.jsonl", _response(t - 100, M5))
    check("ttl: one that wrote a five-minute cache is five minutes (an account over its limit)",
          cachettl.read(str(five)).ttl == M5)
    reads = _write(home / "c.jsonl", _response(t - 3000, H1), _response(t - 2000, None), _response(t - 60, None))
    c = cachettl.read(str(reads))
    check("ttl: responses that only read the cache restart the clock and leave the ttl to the last writer",
          c.ttl == H1 and abs(c.at - (t - 60)) < 2, str(c))
    mixed = _write(home / "d.jsonl", _response(t - 3000, H1), _response(t - 500, M5))
    check("ttl: the drop to five minutes is followed as soon as a response shows it", cachettl.read(str(mixed)).ttl == M5)
    side = _write(home / "e.jsonl", _response(t - 3000, H1), _response(t - 10, M5, sidechain=True))
    c = cachettl.read(str(side))
    check("ttl: a subagent's response is another conversation's cache and does not move this one's",
          c.ttl == H1 and abs(c.at - (t - 3000)) < 2, str(c))
    check("ttl: no clock where there is no transcript or nothing in it",
          cachettl.read(None) is None and cachettl.read(str(home / "none.jsonl")) is None
          and cachettl.read(str(_write(home / "f.jsonl", "not json", json.dumps({"type": "user"})))) is None)
    nowrite = _write(home / "g.jsonl", *[_response(t - 100 + i, None) for i in range(cachettl._LOOK_BACK + 3)])
    c = cachettl.read(str(nowrite))
    check("ttl: a clock with no ttl when no recent response says one", c is not None and c.ttl is None and c.left(t) is None)
    big = _write(home / "h.jsonl", _response(t - 2500, H1), _response(t - 30, None, pad=400_000))
    c = cachettl.read(str(big))
    check("ttl: a last response larger than the first read is still found, with the ttl behind it",
          c is not None and c.ttl == H1 and abs(c.at - (t - 30)) < 2, str(c))
    with open(one, "a") as f:
        f.write(_response(t - 5, M5) + "\n")
    check("ttl: an appended response is seen (the answer is kept only while the file is unchanged)",
          cachettl.read(str(one)).ttl == M5)
    check("ttl: its left is the life less the age, negative once it has lapsed",
          abs(cachettl.Clock(t - 600, H1).left(t) - 3000) < 1 and cachettl.Clock(t - 4000, H1).left(t) < 0
          and cachettl.margin(H1) == 300 and cachettl.margin(M5) == 25)


def main() -> int:
    def tests(td):
        run(lambda: test_the_cache_clock_is_the_last_responses_time_and_the_ttl_the_nearest_that_wrote_the_cache(Path(td) / "c1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
