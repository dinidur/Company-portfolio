"""
Main coordinator - builds the LangGraph.

 START
   |
 input_guard --(blocked)--------------------------------------+
   |                                                          |
 memory_load                                                  |
   |                                                          |
 supervisor --route_next--> retrieval --+                     |
      ^                  |-> research --+--> (next step)      |
      |                  |-> tool_planner -> [human_approval] -> tool_executor
      |                  |                                    |
      +--- route_next <--+                                    v
                                     response <--(retry)-- validator
                                        |                      |
                                        +--------> validator --+--> memory_save --> END

The supervisor plan can have up to 2 steps (e.g. research + tools). After each agent,
route_next() sends the graph to the next planned step, then to the response agent.
The checkpointer keeps the state per session (thread_id), that is our short-term memory.
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.guard_agent import input_guard_node
from src.agents.memory_agent import memory_load_node, memory_save_node
from src.agents.research_agent import research_node
from src.agents.response_agent import response_node, route_after_validation, validator_node
from src.agents.retrieval_agent import retrieval_node
from src.agents.state import AgentState
from src.agents.supervisor_agent import route_next, supervisor_node
from src.agents.tool_agent import (human_approval_node, route_after_planner, tool_executor_node,
                                   tool_planner_node)

STEP_TO_NODE = {"retrieval": "retrieval", "research": "research", "tools": "tool_planner", "response": "response"}


def _after_guard(state: dict) -> str:
    return "response" if state.get("blocked") else "memory_load"


def _next_step(state: dict) -> str:
    return STEP_TO_NODE[route_next(state)]


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("input_guard", input_guard_node)
    g.add_node("memory_load", memory_load_node)
    g.add_node("supervisor", supervisor_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("research", research_node)
    g.add_node("tool_planner", tool_planner_node)
    g.add_node("human_approval", human_approval_node)
    g.add_node("tool_executor", tool_executor_node)
    g.add_node("response", response_node)
    g.add_node("validator", validator_node)
    g.add_node("memory_save", memory_save_node)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", _after_guard, ["memory_load", "response"])
    g.add_edge("memory_load", "supervisor")
    routes = ["retrieval", "research", "tool_planner", "response"]
    g.add_conditional_edges("supervisor", _next_step, routes)
    g.add_conditional_edges("retrieval", _next_step, routes)
    g.add_conditional_edges("research", _next_step, routes)
    g.add_conditional_edges("tool_planner", route_after_planner, ["human_approval", "tool_executor"])
    g.add_edge("human_approval", "tool_executor")
    g.add_conditional_edges("tool_executor", _next_step, routes)
    g.add_edge("response", "validator")
    g.add_conditional_edges("validator", route_after_validation, ["response", "memory_save"])
    g.add_edge("memory_save", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


graph = build_graph()
