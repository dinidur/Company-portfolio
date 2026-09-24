"""
Response agent (final answer) + Validator agent.

response  -> builds a numbered source list, streams the answer token by token
validator -> checks citations / leaks / brand rules. If the answer fails, the
             response agent gets ONE retry with the list of problems. If it still
             fails we send a safe extractive answer instead of a wrong one.
"""
import json

from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import load_yaml
from src.agents.base_agent import agent_node, user_from_state
from src.llm.client import astream
from src.memory.short_term import format_history
from src.prompts.system_prompts import BANK, DIRECT_PROMPT, RESPONSE_PROMPT
from src.security.guardrails import DOC_ID, validate_answer
from src.utils.errors import LLMError
from src.utils.events import emit
from src.utils.helpers import short

BLOCKED_ANSWER = ("I can't help with that request. It looks like it asks me to ignore my rules, reveal "
                  "internal configuration, or move data outside the bank. If you think this is a mistake, "
                  "please rephrase your question or contact the IT service desk.")


def build_sources(state: dict) -> list[dict]:
    """One numbered source per document (retrieval hits first, then research findings)."""
    sources: dict[str, dict] = {}
    for h in (state.get("retrieval") or {}).get("hits", []):
        m = h["metadata"]
        s = sources.setdefault(m["doc_id"], {"doc_id": m["doc_id"], "title": m["title"],
                                             "document_type": m["document_type"], "created_date": m["created_date"],
                                             "access_level": m["access_level"], "sections": [], "text": ""})
        s["sections"].append(m.get("section", ""))
        s["text"] += h["text"] + "\n"
    research = state.get("research") or {}
    for f in research.get("findings", []):
        s = sources.setdefault(f["doc_id"], {"doc_id": f["doc_id"], "title": f.get("title", f["doc_id"]),
                                             "document_type": "research_finding", "created_date": f.get("date", ""),
                                             "access_level": "", "sections": ["finding"], "text": ""})
        s["text"] += f"Finding: {f.get('finding')} | Root cause: {f.get('root_cause')} | Impact: {f.get('impact')}\n"
    out = []
    for i, s in enumerate(sources.values(), start=1):
        s["n"] = i
        s["text"] = s["text"][:1800]
        out.append(s)
    return out[:12]


def _sources_block(sources: list[dict]) -> str:
    if not sources:
        return "(no document sources)"
    return "\n\n".join(f'[{s["n"]}] <document id="{s["doc_id"]}" title="{s["title"]}" date="{s["created_date"]}">\n'
                       f'{s["text"]}\n</document>' for s in sources)


def extractive_answer(sources: list[dict], tool_results: list, research: dict, reason: str) -> str:
    """Safe answer with no LLM: show what we found, with citations."""
    lines = [f"I couldn't generate a full answer right now ({reason}). Here is what I found:"]
    if research.get("summary"):
        lines.append(research["summary"])
    for s in sources[:4]:
        lines.append(f"- {s['title']}: {short(s['text'], 220)} [{s['n']}]")
    for r in tool_results or []:
        if r.get("ok"):
            lines.append(f"- {r['tool']} (from the enterprise system): {short(json.dumps(r['result'], default=str), 220)}")
    if len(lines) == 1:
        lines.append("I couldn't find anything relevant in the knowledge base.")
    return "\n".join(lines)


async def _stream_answer(messages, purpose: str) -> str:
    parts = []
    async for token in astream(messages, purpose=purpose):
        parts.append(token)
        emit("token", text=token)
    return "".join(parts)


@agent_node("response")
async def response_node(state: dict) -> dict:
    user = user_from_state(state)
    attempts = state.get("attempts", 0) + 1
    if attempts > 1:
        emit("answer_reset", reason="validator asked for a retry")

    if state.get("blocked"):
        emit("token", text=BLOCKED_ANSWER)
        return {"answer": BLOCKED_ANSWER, "sources": [], "attempts": attempts}

    history = format_history(state.get("messages", []))
    plan = state.get("plan") or []

    if not plan:  # direct / chit-chat
        extra = ", live enterprise data" if len(user.tools) > 1 else ""
        prompt = DIRECT_PROMPT.format(bank=BANK, extra=extra, history=history)
        try:
            answer = await _stream_answer([SystemMessage(prompt), HumanMessage(state["question"])], "response_direct")
        except LLMError:
            answer = (f"Hello! I'm {load_yaml('brand.yaml')['assistant_name']}, the {BANK} knowledge assistant. "
                      "Ask me about policies, runbooks, incidents, architecture or product specs.")
            emit("token", text=answer)
        return {"answer": answer, "sources": [], "attempts": attempts}

    sources = build_sources(state)
    research = state.get("research") or {}
    tool_results = state.get("tool_results") or []
    retry_note = ""
    if attempts > 1:
        retry_note = ("\n# IMPORTANT - your previous answer failed validation:\n"
                      + json.dumps(state.get("validation", {}).get("issues", []))
                      + "\nFix these problems. Only cite numbers from the Sources list.")
    errors = state.get("errors", [])
    if errors:
        retry_note += "\n# Parts that failed (tell the user briefly): " + "; ".join(e["error"] for e in errors)[:600]
    past = "\n".join(f"- {p['question']} -> {p['answer_summary'][:150]}" for p in state.get("past_interactions", []))
    prompt = RESPONSE_PROMPT.format(
        user_name=user.full_name, role=user.role, department=user.department,
        profile=json.dumps(state.get("user_profile", {}).get("frequent_topics", [])),
        past=past or "(none)", history=history,
        research=research.get("summary") or "(not used)",
        tool_results=json.dumps([{k: r.get(k) for k in ("tool", "ok", "result", "error")} for r in tool_results],
                                default=str)[:6000] if tool_results else "(not used)",
        sources=_sources_block(sources), retry_note=retry_note)

    emit("agent_state", agent="response", status="generating", sources=len(sources), attempt=attempts)
    try:
        answer = await _stream_answer([SystemMessage(prompt), HumanMessage(state["question"])], "response")
    except LLMError as e:
        answer = extractive_answer(sources, tool_results, research, "the language model is unavailable")
        emit("token", text=answer)
        return {"answer": answer, "sources": sources, "attempts": attempts,
                "errors": [{"agent": "response", "error": f"LLM unavailable: {e.details.get('errors')}"}]}
    return {"answer": answer, "sources": sources, "attempts": attempts}


@agent_node("validator")
async def validator_node(state: dict) -> dict:
    if state.get("blocked"):
        return {"validation": {"valid": True, "issues": [], "note": "blocked request - fixed refusal"}}
    sources = state.get("sources", [])
    # IDs that came from tools / research are allowed to be mentioned too
    extra_ids = set(DOC_ID.findall(json.dumps(state.get("tool_results", []), default=str)))
    extra_ids |= {f["doc_id"] for f in (state.get("research") or {}).get("findings", [])}
    need_citations = bool(sources)
    result = validate_answer(state.get("answer", ""), sources if need_citations else [], extra_ids)
    result["attempt"] = state.get("attempts", 1)

    update = {"validation": result}
    if not result["valid"] and state.get("attempts", 1) >= 2:
        # second failure -> safe answer instead of a possibly wrong one
        safe = extractive_answer(sources, state.get("tool_results"), state.get("research") or {},
                                 "the generated answer did not pass validation")
        update["answer"] = safe
        result["final"] = "replaced_with_safe_answer"
        emit("answer_reset", reason="validation failed twice - safe answer")
        emit("token", text=safe)
    emit("validation", stage="output", valid=result["valid"], issues=result["issues"], cited=result["cited"],
         final=result.get("final", "accepted" if result["valid"] else "retry"))
    return update


def route_after_validation(state: dict) -> str:
    v = state.get("validation", {})
    if not v.get("valid") and state.get("attempts", 1) < 2 and not v.get("final"):
        return "response"
    return "memory_save"
