"""
SQLite store for things that must survive a restart:
- long-term user memory (facts / interests learned from past sessions)
- past interactions (for "relevant historical interactions")
- feedback (thumbs up/down) and the audit log

sqlite3 is sync, so every call goes through asyncio.to_thread (keeps the API async).
"""
import asyncio
import json
import sqlite3
import time
from pathlib import Path

from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_memory (username TEXT, key TEXT, value TEXT, updated REAL,
                                        PRIMARY KEY (username, key));
CREATE TABLE IF NOT EXISTS interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, session_id TEXT,
                                         question TEXT, answer_summary TEXT, topics TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, username TEXT,
                                     score INTEGER, comment TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT,
                                      detail TEXT, ts REAL);
"""


def _conn():
    Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _exec(sql: str, params: tuple = ()) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


async def run(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(_exec, sql, params)


async def audit(username: str, action: str, detail: dict) -> None:
    await run("INSERT INTO audit_log (username, action, detail, ts) VALUES (?,?,?,?)",
              (username, action, json.dumps(detail, default=str)[:4000], time.time()))


async def recent_audit(limit: int = 20) -> list[dict]:
    return await run("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


async def save_feedback(run_id: str, username: str, score: int, comment: str) -> None:
    await run("INSERT INTO feedback (run_id, username, score, comment, ts) VALUES (?,?,?,?,?)",
              (run_id, username, score, comment, time.time()))
