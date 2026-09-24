"""
Supervisor agent - intent understanding, task decomposition, agent routing.

The LLM proposes a plan, then we CHECK it in code:
- unknown steps are dropped
- "tools" is dropped if the role has no tools except search (RBAC before routing)
- filters are validated (only known keys/values)
If the LLM is down we use simple keyword rules, so routing still works.
"""
import re
from datetime import date, timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base_agent import agent_node, today, user_from_state
from src.llm.client import ainvoke, parse_json
from src.memory.short_term import format_history
from src.prompts.system_prompts import SUPERVISOR_PROMPT
from src.rag.loader import NAMESPACES
from src.utils.events import emit

VALID_STEPS = {"retrieval", "research", "tools"}
DOC_TYPES = {"policy", "architecture", "runbook", "incident", "product_spec", "meeting_notes"}
TOOL_NAMES = {"employee_directory", "service_catalog", "incident_records", "python_analysis",
              "reindex_knowledge_base", "view_audit_log"}


def rule_based_plan(question: str) -> dict:
    q = question.lower()
    plan, filters = [], {}
    if re.fullmatch(r"\W*(hi|hello|hey|thanks|thank you|good (morning|evening)|who are you)\W*", q):
        return {"intent": "chit_chat", "plan": [], "filters": {}, "reason": "greeting (rule)"}
    if re.search(r"summari[sz]e all|recurring|trend|across|all (the )?(incidents|outages)|root causes|"
                 r"last (year|12 months)|patterns?", q):
        plan.append("research")
    if re.search(r"who is|on[- ]call|owner of|contact|service status|status of|how many incidents|"
                 r"employee|audit log|reindex|analy[sz]e the (data|records)", q):
        plan.append("tools")
    if not plan:
        plan.append("retrieval")
    for t in DOC_TYPES:
        if t.replace("_", " ").rstrip("s") in q or (t == "incident" and re.search(r"outage|incident", q)):
            filters["document_type"] = t
    if re.search(r"last (year|12 months)", q):
        filters["created_after"] = (date.today() - timedelta(days=365)).isoformat()
    return {"intent": "rule_based", "plan": plan, "filters": filters, "reason": "keyword rules (LLM unavailable)"}


def _clean_filters(raw: dict | None) -> dict:
    out = {}
    raw = raw or {}
    if raw.get("document_type") in DOC_TYPES:
        out["document_type"] = raw["document_type"]
    if raw.get("department") in NAMESPACES:
        out["department"] = raw["department"]
    if isinstance(raw.get("created_after"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["created_after"]):
        out["created_after"] = raw["created_after"]
    return out


@agent_node("supervisor")
async def supervisor_node(state: dict) -> dict:
    user = user_from_state(state)
    question = state["question"]
    user_tools = [t for t in (TOOL_NAMES if "*" in user.tools else user.tools) if t in TOOL_NAMES]
    history = format_history(state.get("messages", []))

    source = "llm"
    try:
        prompt = SUPERVISOR_PROMPT.format(role=user.role, tools=user_tools or "none (search only)",
                                          today=today(), history=history)
        raw = await ainvoke([SystemMessage(prompt), HumanMessage(question)], purpose="supervisor")
        decision = parse_json(raw)
    except Exception as e:
        source = "rules"
        decision = rule_based_plan(question)
        emit("agent_state", agent="supervisor", note=f"LLM routing failed ({type(e).__name__}), used rules")

    plan = [s for s in decision.get("plan", []) if s in VALID_STEPS][:2]
    dropped = []
    if "tools" in plan and not user_tools:
        plan.remove("tools")
        dropped.append("tools (role has no tool permission)")
        if not plan:
            plan = ["retrieval"]
    if decision.get("intent") in ("chit_chat",) and not plan:
        plan = []
    plan = list(dict.fromkeys(plan))  # remove duplicates, keep order

    standalone = decision.get("standalone_question") or question
    update = {
        "intent": str(decision.get("intent", "question"))[:60],
        "standalone_question": standalone[:1000],
        "plan": plan,
        "plan_index": 0,
        "sub_tasks": [str(s)[:200] for s in decision.get("sub_tasks", [])][:5],
        "filters": _clean_filters(decision.get("filters")),
        "plan_reason": str(decision.get("reason", ""))[:300],
    }
    emit("agent_state", agent="supervisor", source=source, intent=update["intent"], plan=plan or ["direct"],
         sub_tasks=update["sub_tasks"], filters=update["filters"], reason=update["plan_reason"], dropped=dropped,
         standalone_question=standalone)
    return update


def route_next(state: dict) -> str:
    """Conditional edge: go to the next planned agent, or to the response agent."""
    if state.get("blocked"):
        return "response"
    plan, i = state.get("plan", []), state.get("plan_index", 0)
    return plan[i] if i < len(plan) else "response"
