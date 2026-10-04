"""The prompt each agent's screen shows now (T395): see `prompt_blocks` in the schema."""
from __future__ import annotations

import json


def open_prompt(con, agent: str):
    return con.execute("SELECT * FROM prompt_blocks WHERE agent = ? AND gone_at IS NULL", (agent,)).fetchone()


def open_prompts(con) -> list:
    return con.execute("SELECT * FROM prompt_blocks WHERE gone_at IS NULL ORDER BY first_seen, id").fetchall()


def get_prompt(con, block_id: int):
    return con.execute("SELECT * FROM prompt_blocks WHERE id = ?", (block_id,)).fetchone()


def prompt_open(con, agent: str, kind: str, fingerprint: str, title: str, panel: list, detail: dict,
                wait_id: int | None, answerable: bool, now: int) -> int:
    cur = con.execute(
        "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, wait_id, answerable,"
        " first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (agent, kind, fingerprint, title, json.dumps(panel), json.dumps(detail), wait_id,
         1 if answerable else 0, now, now))
    return cur.lastrowid


def prompt_seen(con, block_id: int, now: int) -> None:
    con.execute("UPDATE prompt_blocks SET last_seen = ? WHERE id = ?", (now, block_id))


def prompt_close(con, block_id: int, now: int) -> None:
    con.execute("UPDATE prompt_blocks SET gone_at = ? WHERE id = ? AND gone_at IS NULL", (now, block_id))


def prompt_answered(con, block_id: int, ok: bool, said: str, now: int) -> None:
    con.execute("UPDATE prompt_blocks SET answered_at = ?, answer_ok = ?, answer_said = ? WHERE id = ?",
                (now, 1 if ok else 0, said, block_id))
