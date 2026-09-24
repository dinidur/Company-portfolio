"""
End-to-end graph tests with a FAKE LLM (no API keys needed).
The fake returns a different answer depending on which agent is calling (purpose tag).
"""
import asyncio
import json
import re
import uuid

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from src.agents.orchestrator import build_graph
from src.security.auth import get_user


class FakeLLM:
    def __init__(self, bad_first_answer=False):
        self.bad_first_answer = bad_first_answer
        self.response_calls = 0

    def _reply(self, purpose: str, text: str = "") -> str:
        if purpose == "supervisor":
            return json.dumps({"intent": "research_incidents", "standalone_question": "payment outages last year",
                               "plan": ["research"], "sub_tasks": ["find incidents", "group root causes"],
                               "filters": {"document_type": "incident", "created_after": "2025-09-24"},
                               "reason": "many documents"})
        if purpose == "rlm_planner":
            return ("docs = [d for d in catalog if d['document_type'] == 'incident' "
                    "and d['created_date'] >= '2025-09-24' and d['department'] == 'payments']\n"
                    "result = [d['doc_id'] for d in docs]")
        if purpose.startswith("rlm_sub_agent"):
            ids = sorted(set(re.findall(r'<document id="([^"]+)"', text)))
            # no root_cause on purpose -> the agent must fill it from the structured text
            return json.dumps([{"doc_id": d, "title": d, "finding": "payments failed", "root_cause": None}
                               for d in ids] + [{"doc_id": "INC-9999-0000", "finding": "made up"}])
        if purpose == "rlm_aggregate":
            return "Connection pool exhaustion is the top recurring cause."
        if purpose == "response":
            self.response_calls += 1
            if self.bad_first_answer and self.response_calls == 1:
                return "Pool exhaustion happened 3 times [7]."  # invalid citation -> retry
            return "Connection pool exhaustion happened 3 times [1][2]."
        return "Hello!"

    async def ainvoke(self, messages, config=None):
        return AIMessage(self._reply(config["tags"][0], str(messages[-1].content)))

    async def astream(self, messages, config=None):
        for word in self._reply(config["tags"][0]).split(" "):
            yield AIMessageChunk(content=word + " ")


def _run(fake, question, user="bob"):
    import src.llm.client as client
    client._models.clear()
    client.get_model = lambda provider: fake  # noqa
    graph = build_graph()
    u = get_user(user)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    events = []

    async def go():
        async for mode, chunk in graph.astream({"messages": [HumanMessage(question)], "question": question,
                                                "user": u.public(), "session_id": "t1"}, config,
                                               stream_mode=["custom", "updates"]):
            if mode == "custom":
                events.append(chunk)
        return (await graph.aget_state(config)).values
    return asyncio.run(go()), events


def test_rlm_flow_with_llm():
    state, events = _run(FakeLLM(), "Summarize all payment outages last year and recurring root causes")
    assert state["path"][:4] == ["input_guard", "memory_load", "supervisor", "research"]
    steps = [e["step"] for e in events if e["type"] == "rlm"]
    for s in ("explore", "search", "plan", "decompose", "sub_agent", "aggregate", "done"):
        assert s in steps
    assert state["research"]["plan_source"] == "llm"
    assert state["research"]["counts"]["root_cause_counts"]["database connection pool exhaustion"] >= 2
    assert "INC-9999-0000" not in {f["doc_id"] for f in state["research"]["findings"]}  # hallucinated id dropped
    assert state["validation"]["valid"]
    assert any(e["type"] == "token" for e in events)


def test_validator_retry_on_bad_citation():
    state, events = _run(FakeLLM(bad_first_answer=True), "Summarize all payment outages last year")
    assert state["attempts"] == 2 and state["validation"]["valid"]
    assert any(e["type"] == "answer_reset" for e in events)
