"""
Simple MCP server exposing dummy enterprise data (employee directory, service
catalog, incident records). Runs as its own process, like a real internal service:

    python -m src.mcp_server.enterprise_server        # http://localhost:8001/mcp

The agent calls it through src/tools/mcp_tools.py (MCP client over streamable HTTP).
"""
import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA = json.loads((Path(__file__).resolve().parents[2] / "data" / "mcp" / "enterprise_data.json")
                  .read_text(encoding="utf-8"))

mcp = FastMCP("nova-enterprise-data", host=os.getenv("MCP_HOST", "0.0.0.0"),
              port=int(os.getenv("MCP_PORT", "8001")), stateless_http=True, json_response=True)


@mcp.tool()
def search_employees(query: str = "", department: str = "", on_call_only: bool = False) -> list[dict]:
    """Search the employee directory by name, title or department."""
    q = query.lower().strip()
    rows = []
    for e in DATA["employees"]:
        if department and e["department"] != department.lower():
            continue
        if on_call_only and not e["on_call"]:
            continue
        if q and q not in f"{e['name']} {e['title']} {e['department']}".lower():
            continue
        rows.append(e)
    return rows[:20]


@mcp.tool()
def get_service(name_or_id: str) -> dict:
    """Get one service from the service catalog (owner, tier, SLO, status, dependencies)."""
    key = name_or_id.lower().strip()
    for s in DATA["services"]:
        if key in (s["id"].lower(), s["name"].lower()):
            return s
    return {"error": f"service '{name_or_id}' not found"}


@mcp.tool()
def list_services(status: str = "") -> list[dict]:
    """List services in the catalog, optionally by status (operational / degraded / slow)."""
    return [s for s in DATA["services"] if not status or s["status"] == status]


@mcp.tool()
async def get_service_health(name: str) -> dict:
    """Live health check of a service. legacy-core-reports is very slow (used to demo tool timeouts)."""
    if name == "legacy-core-reports":
        await asyncio.sleep(30)
    svc = get_service(name)
    return {"service": name, "status": svc.get("status", "unknown")}


@mcp.tool()
def get_incident_records(service: str = "", severity: str = "", since: str = "") -> list[dict]:
    """Structured incident records from the incident management system."""
    rows = []
    for i in DATA["incidents"]:
        if service and i["service"] != service:
            continue
        if severity and i["severity"] != severity.upper():
            continue
        if since and i["date"] < since:
            continue
        rows.append(i)
    return rows


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
