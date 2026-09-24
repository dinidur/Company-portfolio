"""
Run the agent graph from the terminal (no API / UI). Good for quick testing.

    python scripts/run_agents.py --user bob "Summarize all outage reports related to payment failures during the last year"
"""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from src.agents.orchestrator import graph  # noqa: E402
from src.security.auth import get_user  # noqa: E402


async def run(username: str, question: str, session: str, approve: bool):
    user = get_user(username)
    config = {"configurable": {"thread_id": session}, "run_name": "chat_turn",
              "metadata": {"user": username, "role": user.role, "session_id": session}}
    inputs = {"messages": [HumanMessage(question)], "question": question, "user": user.public(),
              "session_id": session}
    while True:
        interrupted = None
        async for mode, chunk in graph.astream(inputs, config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                if chunk["type"] == "token":
                    continue
                print("  >>", json.dumps({k: v for k, v in chunk.items() if k != "ts"}, default=str)[:300])
            elif "__interrupt__" in chunk:
                interrupted = chunk["__interrupt__"][0].value
        if not interrupted:
            break
        print("\n** APPROVAL REQUIRED:", interrupted, "->", "approve" if approve else "reject")
        inputs = Command(resume={"approved": approve})
    state = graph.get_state(config).values
    print("\nPATH :", " -> ".join(state.get("path", [])[-12:]))
    print("ERRORS:", state.get("errors"))
    print("\nANSWER:\n" + state.get("answer", ""))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--user", default="bob")
    p.add_argument("--session", default=str(uuid.uuid4()))
    p.add_argument("--approve", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.user, a.question, a.session, a.approve))
