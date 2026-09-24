import asyncio

import pytest

from src.rag.hybrid_search import build_filter, hybrid_search
from src.rag.vector_store import match_filter
from src.security.auth import get_user
from src.tools.python_analysis import run_analysis, validate_code
from src.tools.registry import execute_tool
from src.utils.errors import InvalidRequestError


def test_access_filter_always_present():
    f = build_filter(["public"], {"document_type": "incident", "access_level": "restricted", "evil": 1})
    assert f["$and"][0] == {"access_level": {"$in": ["public"]}}
    assert all("evil" not in p and "restricted" not in str(p) for p in f["$and"])


def test_local_filter_matches_pinecone_syntax():
    meta = {"access_level": "internal", "created_ts": 100, "tags": ["a", "b"]}
    assert match_filter(meta, {"$and": [{"access_level": {"$in": ["internal"]}}, {"created_ts": {"$gte": 50}}]})
    assert not match_filter(meta, {"created_ts": {"$gte": 150}})
    assert match_filter(meta, {"tags": {"$in": ["b"]}})


def test_viewer_cannot_retrieve_confidential():
    res = asyncio.run(hybrid_search("duplicate debits mobile wallet idempotency", ["public", "internal"]))
    assert all(h["metadata"]["access_level"] in ("public", "internal") for h in res["hits"])
    assert "INC-2026-0303" not in {h["metadata"]["doc_id"] for h in res["hits"]}


@pytest.mark.parametrize("code", ["import os", "result = open('x').read()", "result = ().__class__",
                                  "while True: pass", "result = __import__('os')"])
def test_sandbox_blocks_bad_code(code):
    with pytest.raises(InvalidRequestError):
        validate_code(code)


def test_sandbox_runs_analysis():
    data = [{"c": "a"}, {"c": "a"}, {"c": "b"}]
    out = asyncio.run(run_analysis("result = dict(Counter([d['c'] for d in data]))", {"data": data}))
    assert out == {"a": 2, "b": 1}


def test_execute_tool_rbac_and_validation():
    viewer, analyst = get_user("alice"), get_user("bob")
    denied = asyncio.run(execute_tool(viewer, "incident_records", {}))
    assert not denied["ok"] and denied["code"] == "forbidden"
    bad = asyncio.run(execute_tool(analyst, "incident_records", {"since": "last year"}))
    assert not bad["ok"] and bad["code"] == "invalid_request"
    unknown = asyncio.run(execute_tool(analyst, "delete_everything", {}))
    assert not unknown["ok"]


def test_mcp_down_is_graceful(monkeypatch):
    monkeypatch.setattr("config.settings.settings.MCP_SERVER_URL", "http://127.0.0.1:9/mcp")
    res = asyncio.run(execute_tool(get_user("bob"), "employee_directory", {"query": "bob"}))
    assert not res["ok"] and res["code"] in ("mcp_unavailable", "tool_timeout")
