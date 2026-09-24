"""
Tool agent - plans tool calls, waits for human approval when needed, executes them.

tool_planner   -> LLM picks tools, but only from the role's allowed list
human_approval -> LangGraph interrupt() for sensitive tools (e.g. reindex) - bonus HITL
tool_executor  -> runs calls through execute_tool() (RBAC again + validation + timeout)
"""
import asyncio
import json
import re
from datetime import date, timedelta

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from src.agents.base_agent import agent_node, today, user_from_state
from src.llm.client import ainvoke, parse_json
from src.prompts.system_prompts import TOOL_PLANNER_PROMPT
from src.security.rbac import allowed_tools, needs_approval
from src.tools.registry import TOOLS, describe_tools, execute_tool
from src.utils.events import emit


def rule_based_calls(question: str, allowed: list[str]) -> list[dict]:
    q = question.lower()
    calls = []
    if "employee_directory" in allowed and re.search(r"on[- ]call|who is|employee|contact", q):
        calls.append({"tool": "employee_directory", "args": {"on_call_only": "on call" in q or "on-call" in q},
                      "reason": "rule"})
    if "service_catalog" in allowed and re.search(r"service|owner|status|slo", q):
        m = re.search(r"([a-z]+(?:-[a-z]+)+)", q)
        calls.append({"tool": "service_catalog", "args": {"name_or_id": m.group(1) if m else ""},
                      "reason": "rule"})
    if "incident_records" in allowed and re.search(r"incident|outage|root cause|how many", q):
        since = (date.today() - timedelta(days=365)).isoformat() if "last year" in q else ""
        calls.append({"tool": "incident_records", "args": {"since": since}, "reason": "rule"})
        if "python_analysis" in allowed and re.search(r"how many|count|most|by (root )?(service|cause)", q):
            calls.append({"tool": "python_analysis", "reason": "rule", "args": {"code":
                          "result = dict(Counter([d['root_cause_category'] for d in data "
                          "if 'root_cause_category' in d]).most_common())"}})
    if "view_audit_log" in allowed and "audit" in q:
        calls.append({"tool": "view_audit_log", "args": {}, "reason": "rule"})
    if "reindex_knowledge_base" in allowed and "reindex" in q:
        calls.append({"tool": "reindex_knowledge_base", "args": {}, "reason": "rule"})
    return calls[:3]


@agent_node("tool_planner", fallback={"tool_calls": []})
async def tool_planner_node(state: dict) -> dict:
    user = user_from_state(state)
    # knowledge_search is handled by the retrieval agent; here only "action" tools
    allowed = [t for t in allowed_tools(user, list(TOOLS)) if t != "knowledge_search"]
    question = state.get("standalone_question") or state["question"]
    source = "llm"
    try:
        raw = await ainvoke([HumanMessage(TOOL_PLANNER_PROMPT.format(
            question=question, sub_tasks=state.get("sub_tasks", []), today=today(),
            tools=describe_tools(allowed)))], purpose="tool_planner")
        calls = parse_json(raw)
        if not isinstance(calls, list):
            raise ValueError("expected list")
    except Exception as e:
        source = f"rules ({type(e).__name__})"
        calls = rule_based_calls(question, allowed)

    clean, rejected = [], []
    for c in calls[:3]:
        if not isinstance(c, dict) or c.get("tool") not in allowed:
            rejected.append(c.get("tool") if isinstance(c, dict) else str(c))
            continue
        clean.append({"tool": c["tool"], "args": c.get("args") or {}, "reason": str(c.get("reason", ""))[:200],
                      "needs_approval": needs_approval(c["tool"])})
    emit("agent_state", agent="tool_planner", source=source, calls=clean, rejected=rejected)
    update = {"tool_calls": clean}
    if rejected:
        update["errors"] = [{"agent": "tool_planner", "error": f"blocked tool request(s): {rejected}"}]
    return update


def route_after_planner(state: dict) -> str:
    if any(c.get("needs_approval") for c in state.get("tool_calls", [])):
        return "human_approval"
    return "tool_executor"


@agent_node("human_approval")
async def human_approval_node(state: dict) -> dict:
    pending = [c for c in state["tool_calls"] if c.get("needs_approval")]
    emit("approval", status="waiting", calls=pending)
    # graph pauses here. The API streams an "approval_required" event; UI calls /chat/resume
    decision = interrupt({"type": "approval_required", "calls": pending,
                          "message": "These tools change the system. Approve?"})
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    emit("approval", status="approved" if approved else "rejected", by=state["user"]["username"])
    calls = state["tool_calls"] if approved else [c for c in state["tool_calls"] if not c.get("needs_approval")]
    return {"tool_calls": calls, "approval": {"approved": approved, "calls": pending}}


@agent_node("tool_executor", fallback={"tool_results": []})
async def tool_executor_node(state: dict) -> dict:
    user = user_from_state(state)
    calls = state.get("tool_calls", [])
    first = [c for c in calls if c["tool"] != "python_analysis"]
    later = [c for c in calls if c["tool"] == "python_analysis"]

    # independent tools run in parallel (async tool execution)
    results = list(await asyncio.gather(*[execute_tool(user, c["tool"], c["args"]) for c in first]))

    # python analysis runs on data we already fetched this turn
    data = []
    for r in results:
        if r["ok"] and isinstance(r["result"], list):
            data.extend(x for x in r["result"] if isinstance(x, dict))
        elif r["ok"] and isinstance(r["result"], dict):
            data.append(r["result"])
    for c in later:
        results.append(await execute_tool(user, c["tool"], c["args"], ctx={"analysis_data": data}))

    if state.get("approval") and not state["approval"].get("approved"):
        results.append({"tool": "human_approval", "ok": False, "error": "rejected by the user", "ms": 0})

    update = {"tool_results": results, "plan_index": state.get("plan_index", 0) + 1}
    failed = [r for r in results if not r["ok"]]
    if failed:
        update["errors"] = [{"agent": "tools", "error": f"{r['tool']}: {r['error']}"} for r in failed]
    emit("agent_state", agent="tool_executor", ok=len(results) - len(failed), failed=len(failed),
         summary=json.dumps([{"tool": r["tool"], "ok": r["ok"]} for r in results]))
    return update
