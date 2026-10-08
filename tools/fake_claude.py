#!/usr/bin/env python3
"""A stand-in for the claude CLI's stream-json mode, for the conformance run: it answers --version and every control request, echoes
each user message and runs a turn that ends at once. The version comes from FAKE_CLAUDE_VERSION (tools/test-go.sh reads the pinned
one out of the Go source), so the daemon's version check passes."""
import json
import os
import sys
import threading

if sys.argv[1:2] == ["--version"]:
    print(f"{os.environ['FAKE_CLAUDE_VERSION']} (Claude Code)")
    sys.exit(0)

lock = threading.Lock()


def emit(v):
    with lock:
        sys.stdout.write(json.dumps(v) + "\n")
        sys.stdout.flush()


def state(s):
    emit({"type": "system", "subtype": "session_state_changed", "state": s, "uuid": "s", "session_id": "x"})


def turn(uid, text):
    emit({"type": "user", "uuid": uid, "isReplay": True, "message": {"role": "user", "content": text}})
    state("running")
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok " + text}]}})
    emit({"type": "result", "subtype": "success"})
    state("idle")


for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("type") == "control_request":
        emit({"type": "control_response", "response": {"subtype": "success", "request_id": m.get("request_id"), "response": {}}})
    elif m.get("type") == "user":
        threading.Thread(target=turn, args=(m["uuid"], m["message"]["content"]), daemon=True).start()
