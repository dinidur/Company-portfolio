"""
Long-term memory (bonus) - survives across sessions, per user, in SQLite.

What we remember (small and explainable, no raw chat dumps):
- profile facts: topics the user asks about most (document types / departments)
- past interactions: question + short answer summary -> "relevant historical interactions"

Retrieval of past interactions = simple keyword overlap. Enough for a POC; in
production this would be an embedding search in its own Pinecone namespace.
"""
import json
import re
import time

from src.memory import store

_WORDS = re.compile(r"[a-z0-9-]{4,}")
_STOP = {"what", "which", "when", "where", "about", "there", "their", "please", "summarize", "tell", "with",
         "from", "that", "this", "have", "does", "show", "give", "last", "year"}


def _keywords(text: str) -> set[str]:
    return {w for w in _WORDS.findall((text or "").lower()) if w not in _STOP}


async def load_profile(username: str) -> dict:
    rows = await store.run("SELECT key, value FROM user_memory WHERE username=?", (username,))
    return {r["key"]: json.loads(r["value"]) for r in rows}


async def relevant_interactions(username: str, question: str, session_id: str, limit: int = 3) -> list[dict]:
    rows = await store.run("SELECT session_id, question, answer_summary, ts FROM interactions WHERE username=? "
                           "ORDER BY id DESC LIMIT 50", (username,))
    q = _keywords(question)
    scored = []
    for r in rows:
        if r["session_id"] == session_id:
            continue  # this session is already in short-term memory
        overlap = len(q & _keywords(r["question"]))
        if overlap:
            scored.append((overlap, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


async def save_interaction(username: str, session_id: str, question: str, answer: str, topics: list[str]):
    await store.run("INSERT INTO interactions (username, session_id, question, answer_summary, topics, ts) "
                    "VALUES (?,?,?,?,?,?)", (username, session_id, question[:500], answer[:400],
                                             json.dumps(topics), time.time()))
    profile = await load_profile(username)
    counts = profile.get("topic_counts", {})
    for t in topics:
        counts[t] = counts.get(t, 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:3]
    for key, value in (("topic_counts", counts), ("frequent_topics", top)):
        await store.run("INSERT OR REPLACE INTO user_memory (username, key, value, updated) VALUES (?,?,?,?)",
                        (username, key, json.dumps(value), time.time()))
    return top
