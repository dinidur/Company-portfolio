"""
Python Analysis Tool - structured analysis on retrieved data.

The LLM (or the RLM planner) writes a small python snippet. We do NOT exec it
blindly. The code is parsed to an AST and checked against an allow-list:
  - no import, no while loops, no def/class, no dunder / private attributes
  - only safe builtins (len, sum, sorted, Counter ...)
  - must put the answer in a variable called `result`
Then it runs in a worker thread with a timeout.

Trade-off: this is a POC sandbox. For production run it in a separate container
(gVisor / Firecracker) with no network - noted in docs/assumptions.md.
"""
import ast
import asyncio
import statistics
from collections import Counter, defaultdict

from config.settings import settings
from src.utils.errors import InvalidRequestError, ToolTimeoutError

SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max, "sorted": sorted, "round": round, "abs": abs,
    "range": range, "enumerate": enumerate, "zip": zip, "list": list, "dict": dict, "set": set,
    "tuple": tuple, "str": str, "int": int, "float": float, "bool": bool, "any": any, "all": all,
    "Counter": Counter, "defaultdict": defaultdict, "mean": statistics.mean, "median": statistics.median,
}
BANNED_NODES = (ast.Import, ast.ImportFrom, ast.While, ast.FunctionDef, ast.AsyncFunctionDef,
                ast.ClassDef, ast.Global, ast.Nonlocal, ast.With, ast.AsyncWith, ast.Try, ast.Raise,
                ast.Await, ast.Yield, ast.YieldFrom, ast.Delete)
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "input", "breakpoint", "exit", "quit", "help", "type", "object"}
MAX_CODE_CHARS = 3000


def validate_code(code: str, extra_names: set[str] | None = None) -> ast.Module:
    if len(code) > MAX_CODE_CHARS:
        raise InvalidRequestError("analysis code too long")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise InvalidRequestError(f"analysis code syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, BANNED_NODES):
            raise InvalidRequestError(f"not allowed in analysis code: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise InvalidRequestError("private attributes are not allowed")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            raise InvalidRequestError(f"name not allowed: {node.id}")
    return tree


def _run(code: str, variables: dict) -> object:
    tree = validate_code(code)
    scope = {"__builtins__": SAFE_BUILTINS, **variables}
    exec(compile(tree, "<analysis>", "exec"), scope)  # noqa: S102 - validated above
    if "result" not in scope:
        raise InvalidRequestError("analysis code must set a variable named `result`")
    return scope["result"]


async def run_analysis(code: str, variables: dict, timeout: float | None = None) -> object:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run, code, variables),
                                      timeout=timeout or settings.TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError("python analysis timed out") from e
