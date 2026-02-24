import asyncio
import os, sys
# ensure package import works
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest
# note: require aiosqlite to be installed in virtualenv for these tests

from github_research_feed import feed_engine, db


def run_async(coro):
    return asyncio.run(coro)


class DummyGitHub:
    def __init__(self, repos):
        # repos is dict full_name -> repo_data to return
        self._repos = repos
        self.calls = []

    async def get_repo(self, full_name):
        self.calls.append(('get_repo', full_name))
        return self._repos.get(full_name, {})

    async def get_readme(self, full_name):
        self.calls.append(('get_readme', full_name))
        return None

    async def get_releases(self, full_name, limit=5):
        self.calls.append(('get_releases', full_name, limit))
        return []

    async def get_commits(self, full_name, since=None, limit=20):
        self.calls.append(('get_commits', full_name, since, limit))
        return []

    async def search_repos(self, query, language=None, min_stars=50, limit=20):
        # just return one fake repo matching query
        return [{"full_name": "owner/repo", "description": query, "stargazers_count": 100, "language": language}]


class DummyEmbed:
    def __init__(self):
        self.calls = []

    async def embed(self, text):
        self.calls.append(text)
        # return simple vector based on length
        return [float(len(text))]

    async def embed_batch(self, texts):
        return [[float(len(text))] for text in texts]


class DummySumm:
    async def summarize_release(self, *args, **kwargs):
        return "summary"
    async def summarize_commit_burst(self, *args, **kwargs):
        return "commit summary"


def test_discover_no_contexts(tmp_path):
    # use fresh db path so no contexts exist
    db_path = tmp_path / "feed.db"
    run_async(db.init_db(db_path))
    gh = DummyGitHub({})
    emb = DummyEmbed()
    summ = DummySumm()
    engine = feed_engine.FeedEngine(db_path, gh, emb, summ)

    results = run_async(engine.discover_repos("myquery", language="python", min_stars=10, limit=5))
    assert isinstance(results, list)
    assert results and results[0]["full_name"] == "owner/repo"


def test_poll_all(tmp_path, monkeypatch):
    db_path = tmp_path / "feed.db"
    run_async(db.init_db(db_path))
    # add context so embeddings/scoring has something
    run_async(db.upsert_project_context(db_path, "ctx", "desc", [1.0]))
    # add watched repo record
    run_async(db.upsert_watched_repo(db_path, "owner/repo", source="manual"))

    class PollGitHub(DummyGitHub):
        async def get_releases(self, full_name, limit=5):
            # one new release
            return [{"published_at": "2026-01-01T00:00:00Z", "tag_name": "v1", "body": "notes", "html_url": "http://"}]

        async def get_commits(self, full_name, since=None, limit=20):
            return []

    gh = PollGitHub({"owner/repo": {"full_name": "owner/repo"}})
    emb = DummyEmbed()
    summ = DummySumm()
    engine = feed_engine.FeedEngine(db_path, gh, emb, summ)

    counts = run_async(engine.poll_all())
    assert counts["releases"] == 1
    assert counts["repos_checked"] == 1
    # verify feed_events written
    evs = run_async(db.get_feed_events(db_path, days_back=365))
    assert len(evs) == 1
    assert evs[0]["repo_full_name"] == "owner/repo"


def test_discover_with_contexts(tmp_path, monkeypatch):
    db_path = tmp_path / "feed.db"
    run_async(db.init_db(db_path))
    # add a project context with embedding [10.0]
    run_async(db.upsert_project_context(db_path, "ctx", "desc", [10.0]))

    gh = DummyGitHub({})
    emb = DummyEmbed()
    summ = DummySumm()
    engine = feed_engine.FeedEngine(db_path, gh, emb, summ)

    # patch upsert_discovery_candidate to record calls
    recorded = []
    async def fake_upsert(*args, **kwargs):
        recorded.append((args, kwargs))
    monkeypatch.setattr(feed_engine, "upsert_discovery_candidate", fake_upsert)

    results = run_async(engine.discover_repos("myquery", language="python", min_stars=10, limit=1))
    assert results and results[0]["similarity_score"] >= 0.0
    # candidate should have been stored because score >= 0.35 by our vector logic
    assert recorded
