# Assumptions, trade-offs and model choice

## Assumptions
- The bot's own company is a **commercial bank**; I used a fictional name "Nova Commercial Bank"
  (`config/brand.yaml`) so the demo does not use a real bank's brand. Change one file to rename.
- Documents are markdown with a YAML header (department, type, access level, date). Real sources
  (Confluence, SharePoint, PDF) would need loaders, the rest of the pipeline is the same.
- 4 access levels (public / internal / confidential / restricted) mapped to the 3 roles.
- Mock data is small (32 docs); the design (namespaces, catalog, RLM batching) is for thousands.
- "Last year" = last 365 days from today.

## Model selection
| Need | Choice | Reason |
|---|---|---|
| Main LLM | **Gemini 2.5 Flash** | free tier, fast, strong JSON / instruction following, long context, cheap for many RLM sub-calls |
| Fallback | **Ollama (local)** | works offline / when quota ends; same local setup as my SupermarketAI project |
| Embeddings | bge-small (fastembed) | free, CPU, no API cost, same model for ingest + query |
| Sparse | BM25 (fastembed) | exact keyword and ID match |
| Rerank | MiniLM cross-encoder | small, CPU, clear quality gain |
Model names are env variables, so switching to OpenAI / Anthropic is a small change in `src/llm/client.py`.

## Trade-offs
| Decision | Trade-off |
|---|---|
| Prompt → JSON parsing instead of provider tool-calling | works the same on Gemini and Ollama, easy to show; less strict than native function calling (we validate in code) |
| Regex guardrails | explainable + fast, but can miss paraphrases (architecture is the real guard) |
| In-memory checkpointer + token bucket | simple for one instance; use Postgres checkpointer + Redis bucket for scale |
| Python sandbox in a thread | AST allow-list + timeout is OK for a POC, production needs an isolated container |
| Pinecone client in `asyncio.to_thread` | sync client but non-blocking; could use the async client |
| Local fallback index | keeps the app answering when Pinecone is down, but uses more memory |
| RLM plan written by LLM | flexible; if the code is bad we use a default plan (logged in the activity panel) |
| Validator retry once | better answers, but one more LLM call on failure |
| Hardcoded users (Option A) | fast for the POC; `auth.py` is the only file to change for Keycloak/OIDC |

## What I would do next
Keycloak (OIDC), Postgres checkpointer, Redis rate limiter, evaluation set in LangSmith (RAGAS-style
faithfulness / citation accuracy), streaming loaders for Confluence/SharePoint, a real prompt-injection
classifier, per-tenant Pinecone indexes.
