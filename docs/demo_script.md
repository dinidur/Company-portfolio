# Demo script (45 minutes)

| Time | Part |
|---|---|
| 0-5 | Problem + assumptions (fictional bank, free-tier stack), architecture diagram |
| 5-10 | Code tour: `orchestrator.py` graph, `state.py` ownership rule, `config/roles.yaml` |
| 10-15 | Login as **alice**: runbook question → activity panel (supervisor plan, hybrid scores d/s/h/r, validation) → open LangSmith trace |
| 15-23 | Login as **bob**: RLM question (payment outages last year) → explore / python plan / batches / recursion / counts. Follow-up "which were SEV1?" (memory) |
| 23-28 | bob: on-call + service status (MCP) and "how many incidents by root cause" (python analysis). Same as alice → tools dropped by RBAC |
| 28-33 | Security: injection prompt blocked, poisoned doc cleaned, hallucinated citation retry (test), 429 rate limit |
| 33-37 | carol: reindex → human approval → approve; audit log |
| 37-41 | Failure demo: stop MCP server, set wrong PINECONE key, set bad GOOGLE key → graceful answers |
| 41-45 | Trade-offs + what next, feedback 👍/👎 in LangSmith |

LangSmith: filter by metadata `session_id` to see one conversation as a thread; each turn is a `chat_turn`
run with child runs per node, `hybrid_search` (retriever), `execute_tool` (tool), `rlm_*` spans.
