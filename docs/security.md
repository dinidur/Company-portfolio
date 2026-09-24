# Security approach

## Layers

1. **Authentication** - Option A: hardcoded users in `config/roles.yaml` with PBKDF2 password hashes,
   HS256 JWT. Role is read from the signed token + config, never from the chat.
2. **RBAC**
   - tools: `can_use_tool()` is checked twice - before tools are shown to the LLM (planner only sees allowed
     tools) and again inside `execute_tool()`. The LLM can't bypass it, even with a perfect injection.
   - documents: `access_level $in <role levels>` filter is built server side for every Pinecone query,
     the RLM catalog and fetches. The LLM can add topic filters but can't remove this one.
   - admin-only tools are listed separately (`admin_only_tools`) as a second safety net.
   - data minimisation: phone numbers masked for non-admins in MCP results.
3. **Prompt injection**
   - *Instruction override*: input rules (ignore/override instructions, "you are now", DAN, pretend admin).
   - *Data exfiltration*: "send ... to URL/email", "all passwords / phone numbers", dump database;
     output validator also blocks external URLs and secret patterns (API keys, JWT, private keys).
   - *Tool abuse*: shell/SQL patterns, "call X 100 times"; tools are allow-listed, max 3 calls per turn,
     parameters validated with pydantic, python code AST-checked, sensitive tools need human approval,
     plus the rate limiter.
   - *Indirect injection*: retrieved chunks are scanned; instruction-like lines are removed before the LLM
     sees them (demo: `MTG-2026-07`). Documents are wrapped in `<document>` tags and the system prompt says
     document text is data.
4. **Input validation** - user request (length, empty, control / zero-width chars, NFKC), tool parameters
   (pydantic schemas, regex for dates/severity), retrieved content (sanitiser), API bodies (pydantic, 422).
5. **Output guardrails** - hallucinated citations (`[n]` not in sources), unknown document IDs, secrets,
   external URLs, prompt leak, brand rules (banned phrases, competitor names from `brand.yaml`), empty answer.
   One retry with the issue list, then a safe extractive answer.
6. **Rate limiting** - token bucket per user, capacity/refill per role, `429` with `Retry-After`.
7. **Audit log** - logins, blocked inputs, every tool call (args + result status) in SQLite; admins can read it.

## Why rules and not an LLM judge?

Rules are fast, free, deterministic and explainable in the activity panel. An LLM judge can itself be
injected. The real protection is architectural (server-side RBAC, allow-listed tools, validated params,
data-not-instructions). Trade-off: paraphrased injections can pass the regex - that is OK because the
injected text still can't give itself permissions or tools. Next step: add a small classifier
(e.g. Prompt Guard) as one more score in `check_user_input`.
