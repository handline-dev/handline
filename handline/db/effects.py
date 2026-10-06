"""What a daemon write did, kept a day under its request_id and written in the transaction of the write."""
from __future__ import annotations

import json
import time


def record_effect(con, caller: str, request_id: str, method: str, result: dict) -> None:
    con.execute("INSERT OR REPLACE INTO write_effects(caller, request_id, method, result_json, created_at) VALUES(?,?,?,?,?)",
                (caller, request_id, method, json.dumps(result), int(time.time())))
