"""
Tool registry + the ONE function that executes tools: execute_tool().

Every tool call goes through here, so all tools get the same:
  RBAC check  ->  parameter validation (pydantic)  ->  timeout  ->  audit log  ->  trace + UI event
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langsmith import traceable
from pydantic import BaseModel, Field, ValidationError, field_validator

from config.settings import settings
from src.memory import store
from src.rag.hybrid_search import hybrid_search
from src.security.auth import User
from src.security.rbac import can_use_tool
from src.tools.mcp_tools import call_mcp_tool
from src.tools.python_analysis import run_analysis
from src.utils.errors import AppError, InvalidRequestError, PermissionDeniedError, ToolTimeoutError
from src.utils.events import emit
from src.utils.helpers import Timer
from src.utils.logger import get_logger

log = get_logger("tools")


# ------------------------------------------------------------------ parameter schemas
class KnowledgeSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    document_type: str | None = None
    department: str | None = None

    @field_validator("document_type")
    @classmethod
    def _doc_type(cls, v):
        allowed = {"policy", "architecture", "runbook", "incident", "product_spec", "meeting_notes"}
        if v and v not in allowed:
            raise ValueError(f"document_type must be one of {sorted(allowed)}")
        return v


class EmployeeArgs(BaseModel):
    query: str = Field(default="", max_length=100)
    department: str = Field(default="", max_length=50)
    on_call_only: bool = False


class ServiceArgs(BaseModel):
    name_or_id: str = Field(default="", max_length=60)
    status: str = Field(default="", pattern=r"^(|operational|degraded|slow)$")
    health_check: bool = False


class IncidentArgs(BaseModel):
    service: str = Field(default="", max_length=60)
    severity: str = Field(default="", pattern=r"^(|SEV1|SEV2|SEV3|sev1|sev2|sev3)$")
    since: str = Field(default="", pattern=r"^(|\d{4}-\d{2}-\d{2})$")


class AnalysisArgs(BaseModel):
    code: str = Field(min_length=5, max_length=3000)


class NoArgs(BaseModel):
    pass


# ------------------------------------------------------------------ implementations
async def _knowledge_search(user: User, args: KnowledgeSearchArgs, ctx: dict):
    filters = {"document_type": args.document_type, "department": args.department}
    res = await hybrid_search(args.query, user.access_levels, filters=filters)
    return [{"id": h["id"], "title": h["metadata"]["title"], "text": h["text"][:500]} for h in res["hits"]]


def _mask_phone(rows, user: User):
    # data minimisation: only admin sees phone numbers
    if user.role == "admin":
        return rows
    for r in rows if isinstance(rows, list) else [rows]:
        if isinstance(r, dict):
            for key in ("phone", "noc_phone"):
                if r.get(key):
                    r[key] = r[key][:6] + " *** ****"
    return rows


async def _employee_directory(user: User, args: EmployeeArgs, ctx: dict):
    rows = await call_mcp_tool("search_employees", args.model_dump())
    return _mask_phone(rows, user)


async def _service_catalog(user: User, args: ServiceArgs, ctx: dict):
    if args.health_check and args.name_or_id:
        return await call_mcp_tool("get_service_health", {"name": args.name_or_id})
    if args.name_or_id:
        return _mask_phone(await call_mcp_tool("get_service", {"name_or_id": args.name_or_id}), user)
    return _mask_phone(await call_mcp_tool("list_services", {"status": args.status}), user)


async def _incident_records(user: User, args: IncidentArgs, ctx: dict):
    return await call_mcp_tool("get_incident_records", args.model_dump())


async def _python_analysis(user: User, args: AnalysisArgs, ctx: dict):
    # the analysis runs on data already fetched in this turn (tool results + retrieved chunks)
    data = ctx.get("analysis_data", [])
    return await run_analysis(args.code, {"data": data})


async def _reindex(user: User, args: NoArgs, ctx: dict):
    from src.rag.loader import load_all_chunks, save_chunk_cache
    chunks = await asyncio.to_thread(load_all_chunks)
    await asyncio.to_thread(save_chunk_cache, chunks)
    if settings.PINECONE_API_KEY:
        from src.rag.vector_store import PineconeStore
        ps = PineconeStore()
        await asyncio.to_thread(ps.ensure_index)
        await asyncio.to_thread(ps.upsert_chunks, chunks)
    return {"chunks": len(chunks), "pinecone": bool(settings.PINECONE_API_KEY)}


async def _audit_log(user: User, args: NoArgs, ctx: dict):
    return await store.recent_audit(15)


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[User, Any, dict], Awaitable[Any]]
    category: str


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("knowledge_search", "Search indexed internal documents (hybrid search).", KnowledgeSearchArgs,
         _knowledge_search, "search"),
    Tool("employee_directory", "MCP: find employees by name/title/department, or who is on call.", EmployeeArgs,
         _employee_directory, "mcp"),
    Tool("service_catalog", "MCP: service owner, tier, SLO, status, dependencies. health_check=true for live "
         "status.", ServiceArgs, _service_catalog, "mcp"),
    Tool("incident_records", "MCP: structured incident records (service, severity, date, root cause category, "
         "failed transactions). since=YYYY-MM-DD.", IncidentArgs, _incident_records, "mcp"),
    Tool("python_analysis", "Run a small python snippet on `data` (list of dicts from earlier tools). Must set "
         "`result`. Counter, sorted, sum, mean available. No imports.", AnalysisArgs, _python_analysis,
         "analytics"),
    Tool("reindex_knowledge_base", "ADMIN: re-chunk and re-index all documents.", NoArgs, _reindex, "admin"),
    Tool("view_audit_log", "ADMIN: show recent audit log entries.", NoArgs, _audit_log, "admin"),
]}


def describe_tools(names: list[str]) -> str:
    lines = []
    for n in names:
        t = TOOLS[n]
        params = {k: str(v.annotation).replace("typing.", "") for k, v in t.args_model.model_fields.items()}
        lines.append(f"- {n}: {t.description} params={params}")
    return "\n".join(lines)


@traceable(run_type="tool", name="execute_tool")
async def execute_tool(user: User, name: str, raw_args: dict, ctx: dict | None = None) -> dict:
    """Returns {"tool", "ok", "result"|"error", "ms"}. Never raises for tool errors."""
    ctx = ctx or {}
    emit("tool_call", tool=name, status="started", args=raw_args)
    with Timer() as t:
        try:
            if name not in TOOLS:
                raise InvalidRequestError(f"unknown tool '{name}'")
            if not can_use_tool(user, name):  # second RBAC check - the LLM can't bypass it
                raise PermissionDeniedError(f"role '{user.role}' cannot use tool '{name}'")
            try:
                args = TOOLS[name].args_model(**(raw_args or {}))
            except ValidationError as e:
                raise InvalidRequestError(f"invalid parameters: {e.errors()[0]['msg']}") from e
            result = await asyncio.wait_for(TOOLS[name].fn(user, args, ctx),
                                            timeout=settings.TOOL_TIMEOUT_SECONDS + 1)
            out = {"tool": name, "ok": True, "result": result}
        except asyncio.TimeoutError:
            out = {"tool": name, "ok": False, "error": ToolTimeoutError.user_message, "code": "tool_timeout"}
        except AppError as e:
            out = {"tool": name, "ok": False, "error": str(e), "code": e.code}
        except Exception as e:  # unknown bug in a tool -> still don't crash the graph
            log.exception("tool crashed", extra={"tool": name})
            out = {"tool": name, "ok": False, "error": f"tool failed: {type(e).__name__}", "code": "tool_error"}
    out["ms"] = t.ms
    emit("tool_call", tool=name, status="done" if out["ok"] else "failed", ms=t.ms,
         error=out.get("error"), preview=str(out.get("result", ""))[:300])
    await store.audit(user.username, f"tool:{name}", {"args": raw_args, "ok": out["ok"], "error": out.get("error")})
    log.info("tool executed", extra={"tool": name, "ok": out["ok"], "ms": t.ms, "code": out.get("code")})
    return out
