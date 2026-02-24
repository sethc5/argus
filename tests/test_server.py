import asyncio
import json

import pytest

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

