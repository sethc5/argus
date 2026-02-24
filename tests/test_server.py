import asyncio
import json
import os, sys
from datetime import datetime, timezone, timedelta
# import from local package directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
# skip entire file when sqlite client not available
pytest.importorskip("aiosqlite")

from github_research_feed import server, db
from github_research_feed.server import DigestInput


def run_async(coro):
    return asyncio.run(coro)


# ---- helpers / stubs ----

class GHShim:
    async def get_repo(self, full_name):
        return {"full_name": full_name, "description": "desc", "stargazers_count": 5,
                "language": "python", "updated_at": "2026-01-01T00:00:00Z"}
    async def get_readme(self, full_name):
        return "README"
    async def get_releases(self, full_name, limit=3):
        return []


class EmbShim:
    async def embed(self, text):
        return [1.0]


class SummStub:
    async def summarize_repo(self, *args, **kwargs):
        return "stub"


@pytest.fixture(autouse=True)
def _init_test_env(tmp_path, monkeypatch):
    """Set up a fresh temp DB and reload server singletons for every test."""
    monkeypatch.setenv("FEED_DB_PATH", str(tmp_path / "feed.db"))
    server._config = server.load_config()
    server._db_initialized_path = None
    server._github = GHShim()
    server._embeddings = EmbShim()
    server._summarizer = SummStub()
    server._engine = server.FeedEngine(
        server._config.db_path,
        server._github,
        server._embeddings,
        server._summarizer,
        server._config.min_relevance,
    )
    # Ensure schema is created so direct db calls work
    run_async(db.init_db(server._config.db_path))


# ---- tests ----

def test_digest_empty():
    inp = DigestInput(days_back=1, min_relevance=0.0, project_filter=None, summarize=False)
    result = run_async(server.feed_get_digest(inp))
    data = json.loads(result)
    assert data["event_count"] == 0
    assert data["digest_summary"] is None


def test_digest_with_event():
    run_async(db.upsert_project_context(server._config.db_path, "testctx", "dummy", None))
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

    inp = DigestInput(days_back=90, min_relevance=0.0, project_filter="testctx", summarize=False)
    result = run_async(server.feed_get_digest(inp))
    data = json.loads(result)
    assert data["event_count"] == 1
    assert data["events"][0]["repo"] == "owner/repo"
    assert data["events"][0]["matched_context"] == "testctx"


def test_watch_unwatch_cycle():
    add_res = run_async(server.feed_watch_repo(server.WatchRepoInput(repo="owner/repo1")))
    assert "watching" in add_res

    list_res = run_async(server.feed_list_watched())
    list_data = json.loads(list_res)
    assert any(r["full_name"] == "owner/repo1" for r in list_data["repos"])

    rm_res = run_async(server.feed_unwatch_repo(server.WatchRepoInput(repo="owner/repo1")))
    rm_data = json.loads(rm_res)
    assert rm_data["removed"] is True


def test_context_crud():
    # add
    run_async(db.upsert_project_context(server._config.db_path, "ctx1", "description", None))

    # list
    ctx_list = json.loads(run_async(server.feed_list_contexts()))
    assert any(c["name"] == "ctx1" for c in ctx_list["contexts"])

    # delete
    del_res = json.loads(run_async(server.feed_delete_context(server.DeleteContextInput(name="ctx1"))))
    assert del_res["deleted"] is True

    # verify gone
    ctx_list2 = json.loads(run_async(server.feed_list_contexts()))
    assert all(c["name"] != "ctx1" for c in ctx_list2["contexts"])


def test_repo_summary():
    result = run_async(server.feed_get_repo_summary(server.RepoSummaryInput(repo="owner/repo")))
    data = json.loads(result)
    assert data["full_name"] == "owner/repo"

