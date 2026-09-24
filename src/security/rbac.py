"""
Role Based Access Control for tools.

Two checks (defence in depth):
1. Before the LLM sees tools -> we only bind the tools the role is allowed to use.
2. At execution time        -> execute_tool() checks again with can_use_tool().
So even if the LLM is tricked into asking for a tool, it still can't run it.
"""
from config.settings import load_yaml
from src.security.auth import User


def _cfg() -> dict:
    return load_yaml("roles.yaml")


def can_use_tool(user: User, tool_name: str) -> bool:
    cfg = _cfg()
    if tool_name in cfg.get("admin_only_tools", []) and user.role != "admin":
        return False
    return "*" in user.tools or tool_name in user.tools


def allowed_tools(user: User, all_tools: list[str]) -> list[str]:
    return [t for t in all_tools if can_use_tool(user, t)]


def needs_approval(tool_name: str) -> bool:
    return tool_name in _cfg().get("approval_required_tools", [])


def rate_limit_for(role: str) -> dict:
    return _cfg()["roles"][role].get("rate_limit", {})
