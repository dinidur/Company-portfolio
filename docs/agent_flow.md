# Agent flow and state management

## Agents

| Agent | Job | Writes to state |
|---|---|---|
| input_guard | validate + prompt injection check | `guard`, `blocked`, resets per-turn keys |
| memory_load | long-term profile + related past interactions | `user_profile`, `past_interactions` |
| supervisor | intent, rewrite follow-up to standalone question, plan (max 2 steps), filters | `intent`, `plan`, `sub_tasks`, `filters` |
| retrieval | hybrid RAG, sanitise retrieved content | `retrieval` |
| research | RLM (see architecture.md) | `research` |
| tool_planner | pick tools from the role's allowed list only | `tool_calls` |
| human_approval | `interrupt()` for sensitive tools | `approval`, `tool_calls` |
| tool_executor | run tools in parallel, python analysis after | `tool_results` |
| response | numbered sources, streaming answer | `answer`, `sources` |
| validator | citations / leaks / brand; 1 retry then safe answer | `validation` |
| memory_save | add answer to session, save long-term | `messages` |

## How agents share state (and why failures don't spread - "butterfly effect")

1. **One owner per key.** Each agent writes only its own keys. Retrieval can't overwrite research results.
2. **Append-only error list.** `errors` and `path` use a reducer (`operator.add`). Agents add, never replace.
3. **Every node is wrapped** (`@agent_node`). If a node throws, the wrapper records the error, returns a safe
   default for that agent's keys, and the graph goes on. A broken MCP server makes `tool_results` contain
   `ok: false` - the response agent still answers from documents and says what was not available.
4. **Every external call has a fallback:**

| Failure | What happens |
|---|---|
| LLM (Gemini) down | fallback provider (Ollama) → then rule-based routing / default RLM plan / extractive answer |
| Pinecone down | local index with the same chunks + same filter syntax, `degraded=true` shown in UI |
| MCP down | tool result `mcp_unavailable`, answer continues without it |
| Tool slow | `asyncio.wait_for` timeout → `tool_timeout` (try `service_catalog` with `legacy-core-reports` health check) |
| One RLM batch fails | `gather(return_exceptions=True)`; summary marked partial |
| Bad answer | validator retry once, then safe extractive answer |
| Invalid request | pydantic → 422, guardrail → refusal, rate limit → 429 + Retry-After |

5. **Validation between agents.** Supervisor output is checked in code (unknown steps dropped, tools removed
   if the role has none, filters allow-listed). RLM plan can only select docs in the user's catalog.
   Sub-agent findings with doc IDs that were not in the batch are dropped.
6. **Per-turn reset.** `input_guard` clears the per-turn keys so an old turn's results never leak into the next
   answer; only `messages` (conversation) carries over.
