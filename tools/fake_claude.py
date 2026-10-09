#!/usr/bin/env python3
"""A stand-in for the claude CLI's stream-json mode, for the conformance run: it answers --version and every control request, echoes
each user message and runs a turn that ends at once, except a message naming fake-hold (it ends at an interrupt) or fake-ask (it ends when its permission prompt is answered). The version comes from FAKE_CLAUDE_VERSION (tools/test-go.sh reads the pinned
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


def end_turn(text):
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok " + text}]}})
    emit({"type": "result", "subtype": "success"})
    state("idle")


def turn(uid, text):
    emit({"type": "user", "uuid": uid, "isReplay": True, "message": {"role": "user", "content": text}})
    state("running")
    if "fake-hold" in text:  # a turn that runs until an interrupt
        return
    if "fake-ask" in text:  # a turn that waits on a permission prompt, and ends when it is answered
        emit({"type": "control_request", "request_id": "fake-perm", "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                                                                                "tool_use_id": "fake-tool", "input": {"command": "ls"}}})
        return
    end_turn(text)


for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("type") == "control_request":
        emit({"type": "control_response", "response": {"subtype": "success", "request_id": m.get("request_id"), "response": {}}})
        if m.get("request", {}).get("subtype") == "interrupt":
            end_turn("interrupted")
    elif m.get("type") == "control_response":  # the answer to the permission prompt
        end_turn("asked")
    elif m.get("type") == "user":
        threading.Thread(target=turn, args=(m["uuid"], m["message"]["content"]), daemon=True).start()
