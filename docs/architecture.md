# Architecture

## 1. System view

```mermaid
flowchart LR
    U[Bank staff] --> UI[Streamlit UI<br/>chat + Agent Activity panel]
    UI -- "SSE stream (events + tokens)" --> API[FastAPI<br/>JWT auth · token bucket · error handlers]
    API --> G[LangGraph<br/>multi-agent graph]
    G --> LLM[Gemini 2.5 Flash<br/>fallback: Ollama]
    G --> R[Hybrid retriever]
    R --> E[fastembed<br/>bge-small dense · BM25 sparse · MiniLM rerank]
    R --> P[(Pinecone<br/>namespaces = departments<br/>dense+sparse dotproduct)]
    R -. Pinecone down .-> L[(Local fallback index)]
    G --> T[Tool registry<br/>RBAC · validation · timeout · audit]
    T --> M[MCP server<br/>employees · services · incidents]
    T --> PY[Python sandbox]
    G --> CP[(Checkpointer<br/>short-term memory)]
    G --> DB[(SQLite<br/>long-term memory · feedback · audit)]
    G -. traces .-> LS[LangSmith]
    API -. JSON logs .-> LOG[stdout / log platform]
```

## 2. LangGraph

```mermaid
flowchart TD
    S((START)) --> IG[input_guard]
    IG -- blocked --> RESP
    IG --> ML[memory_load] --> SUP[supervisor]
    SUP -- plan step --> RET[retrieval]
    SUP -- plan step --> RES[research - RLM]
    SUP -- plan step --> TP[tool_planner]
    SUP -- direct --> RESP[response]
    RET -- next step / done --> RESP
    RES -- next step / done --> RESP
    RET -.-> TP
    RES -.-> TP
    TP -- sensitive tool --> HA[human_approval<br/>interrupt]
    TP --> TE[tool_executor]
    HA --> TE
    TE --> RESP
    RESP --> VAL[validator]
    VAL -- invalid, 1 retry --> RESP
    VAL --> MS[memory_save] --> E((END))
```

The supervisor returns a plan of max 2 steps (for example `research` then `tools`). After each agent
`route_next()` moves to the next step, then to `response`.

## 3. RLM (research agent)

```mermaid
flowchart LR
    Q[question] --> X[1 explore<br/>catalog stats, no text]
    X --> S[2 hybrid search<br/>relevance per doc]
    S --> PL[3 LLM writes python plan<br/>run in sandbox → doc ids]
    PL --> D[4 decompose<br/>batches of 3]
    D --> B1[sub-agent B1]
    D --> B2[sub-agent B2]
    D --> B3[sub-agent B3]
    B1 -- too big --> B1a[B1.a depth 2]
    B1 -- too big --> B1b[B1.b depth 2]
    B1a & B1b & B2 & B3 --> AG[6 aggregate<br/>python Counter + LLM summary]
```

Each sub-agent retrieves only the **targeted sections** of its documents (hybrid search with a
`doc_id $in [...]` filter), never full documents. If the sections are larger than
`RLM_MAX_CHARS_PER_CALL`, the sub-agent splits the batch and calls itself (depth + 1, max `RLM_MAX_DEPTH`).

## 4. Retrieval

| Part | Choice | Why |
|---|---|---|
| Chunking | markdown headings, then 900 chars / 120 overlap | a section (e.g. "Root cause") stays together |
| Dense | `BAAI/bge-small-en-v1.5` (384) | free, CPU, good quality |
| Sparse | `Qdrant/bm25` | exact IDs (`INC-2026-0114`), product names |
| Store | Pinecone serverless, `dotproduct`, dense + sparse in one index | native hybrid query |
| Hybrid | `alpha * dense + (1 - alpha) * sparse_norm` (alpha 0.6) | scores shown in UI |
| Namespaces | one per department, queried in parallel with `asyncio.gather` | isolation + parallel |
| Filters | `access_level $in role_levels` always + type/department/date | RBAC at retrieval |
| Rerank | `ms-marco-MiniLM-L-6-v2` cross-encoder on top 15 | better top 6 |
| Attribution | doc_id, title, section, date, source path in metadata | citations |
