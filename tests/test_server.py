import asyncio
import json
import os, sys
from datetime import datetime, timezone, timedelta
# import from local package directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
# skip entire file when sqlite client not available
pytest.importorskip("aiosqlite")

from github_research_feed import server
from github_research_feed.server import DigestInput


def run_async(coro):
    return asyncio.run(coro)


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
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    run_async(db.insert_feed_event(
        server._config.db_path,
        repo_full_name="owner/repo",
        event_type="release",
        event_at=recent,
        title="v1",
        summary="summary",
        relevance_score=0.5,
        matched_context="testctx",
    ))

    inp2 = DigestInput(days_back=90, min_relevance=0.0, project_filter="testctx", summarize=False)
    result2 = run_async(server.feed_get_digest(inp2))
    data2 = json.loads(result2)
    assert data2["event_count"] == 1
    assert data2["events"][0]["repo"] == "owner/repo"
    assert data2["events"][0]["matched_context"] == "testctx"

    # exercise watch management tools
    add_res = run_async(server.feed_watch_repo(server.WatchRepoInput(repo="owner/repo1")))
    assert "watching" in add_res
    list_res = run_async(server.feed_list_watched())
    list_data = json.loads(list_res)
    assert any(r["full_name"] == "owner/repo1" for r in list_data["repos"])

    # remove it
    rm_res = run_async(server.feed_unwatch_repo(server.WatchRepoInput(repo="owner/repo1")))
    rm_data = json.loads(rm_res)
    assert rm_data["removed"] is True

    # ensure context listing and deletion work
    contexts_before = json.loads(run_async(server.feed_list_contexts()))
    assert any(c["name"] == "testctx" for c in contexts_before["contexts"])
    delete_res = run_async(server.feed_delete_context(server.DeleteContextInput(name="testctx")))
    delete_data = json.loads(delete_res)
    assert delete_data["deleted"] is True
    contexts_after = json.loads(run_async(server.feed_list_contexts()))
    assert all(c["name"] != "testctx" for c in contexts_after["contexts"])

    # summary tool with monkeypatched github/embeddings
    class GHShim:
        async def get_repo(self, full_name):
            return {"full_name": full_name, "description": "desc", "stargazers_count": 5, "language": "python", "updated_at": "2026-01-01T00:00:00Z"}
        async def get_readme(self, full_name):
            return "README"
        async def get_releases(self, full_name, limit=3):
            return []
        async def get_topics(self, full_name):
            return ["topic1"]
    class EmbShim:
        async def embed(self, text):
            return [1.0]
    # override summarizer with simple stub to avoid network call
    class SummStub:
        async def summarize_repo(self, *args, **kwargs):
            return "stub"
    server._github = GHShim()
    server._embeddings = EmbShim()
    server._summarizer = SummStub()
    server._engine = server.FeedEngine(server._config.db_path, server._github, server._embeddings, server._summarizer, server._config.min_relevance)
    summary_res = run_async(server.feed_get_repo_summary(server.RepoSummaryInput(repo="owner/repo")))
    summary_data = json.loads(summary_res)
    assert summary_data["full_name"] == "owner/repo"

