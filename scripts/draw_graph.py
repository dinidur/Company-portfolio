"""Print the LangGraph as mermaid (used for docs/architecture.md)."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.agents.orchestrator import graph  # noqa: E402

print(graph.get_graph().draw_mermaid())
