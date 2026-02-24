import asyncio
import json
import os, sys
# import from local package directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
# skip entire file when sqlite client not available
pytest.importorskip("aiosqlite")

from github_research_feed import server
from github_research_feed.server import DigestInput


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_digest_empty(tmp_path, monkeypatch):
    # use a fresh database path
    monkeypatch.setenv("FEED_DB_PATH", str(tmp_path / "feed.db"))
    # reload config and engine to pick up new path
    server._config = server.load_config()
    server._engine = server.FeedEngine(
        server._config.db_path,
        server._github,
        server._embeddings,
        server._summarizer,
        server._config.min_relevance,
    )

    inp = DigestInput(days_back=1, min_relevance=0.0, project_filter=None, summarize=False)
    result = run_async(server.feed_get_digest(inp))
    data = json.loads(result)
    assert data["event_count"] == 0
    assert data["digest_summary"] is None

    # now add a context and an event, then verify filtering works
    # use DB helpers directly
    from github_research_feed import db
    run_async(db.upsert_project_context(server._config.db_path, "testctx", "dummy", None))
    # insert a feed event with matched_context
    run_async(db.insert_feed_event(
        server._config.db_path,
        repo_full_name="owner/repo",
        event_type="release",
        event_at="2025-01-01T00:00:00Z",
        title="v1",
        summary="summary",
        relevance_score=0.5,
        matched_context="testctx",
    ))

    inp2 = DigestInput(days_back=365, min_relevance=0.0, project_filter="testctx", summarize=False)
    result2 = run_async(server.feed_get_digest(inp2))
    data2 = json.loads(result2)
    assert data2["event_count"] == 1
    assert data2["events"][0]["repo"] == "owner/repo"

