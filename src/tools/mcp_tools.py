"""
MCP client. Opens a streamable-HTTP session to the enterprise MCP server,
calls one tool and returns the JSON result.

Failures are turned into MCPError / ToolTimeoutError so the tool agent can
say "enterprise data is not available right now" instead of crashing.
"""
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from config.settings import settings
from src.utils.errors import MCPError, ToolTimeoutError


async def _call(tool: str, args: dict):
    async with streamablehttp_client(settings.MCP_SERVER_URL, timeout=settings.TOOL_TIMEOUT_SECONDS) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            if res.isError:
                raise MCPError(f"MCP tool error: {res.content[0].text if res.content else 'unknown'}")
            if getattr(res, "structuredContent", None):
                data = res.structuredContent
                return data.get("result", data) if isinstance(data, dict) else data
            texts = [c.text for c in res.content if getattr(c, "text", None)]
            try:
                parsed = [json.loads(t) for t in texts]
                return parsed[0] if len(parsed) == 1 else parsed
            except json.JSONDecodeError:
                return "\n".join(texts)


async def call_mcp_tool(tool: str, args: dict, timeout: float | None = None):
    try:
        return await asyncio.wait_for(_call(tool, args), timeout=timeout or settings.TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"MCP tool {tool} timed out") from e
    except (MCPError, ToolTimeoutError):
        raise
    except BaseException as e:  # anyio ExceptionGroup, connection refused ...
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise MCPError(f"MCP server not reachable: {type(e).__name__}") from e
