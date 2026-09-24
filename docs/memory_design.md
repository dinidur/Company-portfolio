# Memory design

| Layer | What | Where | Lifetime |
|---|---|---|---|
| Short-term | full conversation (`messages`) + last turn state | LangGraph checkpointer, `thread_id = user:session` | the session |
| Working | per-turn keys (plan, hits, tool results) | graph state, reset every turn | one turn |
| Long-term | frequent topics per user, past Q&A summaries | SQLite (`user_memory`, `interactions`) | across sessions |

**Decisions**

- **Checkpointer as short-term memory.** It saves state after every node, so memory survives all turns in
  a session and also makes human-in-the-loop `interrupt()` possible (the graph resumes from the saved state).
  `InMemorySaver` for the POC; swap to `SqliteSaver`/`PostgresSaver` for restarts / many API instances.
- **Only the last N turns go to the LLM** (`SHORT_TERM_TURNS=6`). Full history stays in the checkpoint.
  Cheaper, less noise, and the supervisor rewrites follow-ups ("and which were SEV1?") into a standalone
  question so retrieval does not need the whole history.
- **thread_id includes the username.** One user can never load another user's session even if they guess
  the session id.
- **Long-term memory is small and explainable:** topic counts + short answer summaries, not raw chats.
  "Relevant historical interactions" = keyword overlap with past questions from *other* sessions.
  Production: embed them into a per-user Pinecone namespace.
- **Memory is data, not instructions.** Past interactions go into the prompt as context only; the security
  rules are in the system prompt and can't be changed by memory content.
- **Privacy:** no documents or tool outputs are stored in long-term memory, only the user's own
  question and a 400 char answer summary.
