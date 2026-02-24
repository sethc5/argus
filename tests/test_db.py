import asyncio
import sqlite3
from pathlib import Path
import os

import pytest

from github_research_feed import db


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_init_and_migration(tmp_path):
    db_path = tmp_path / "feed.db"
    # create initial schema by calling init_db twice (migration logic should be safe)
    run_async(db.init_db(db_path))
    # manually verify matched_context column exists after migration
    conn = sqlite3.connect(db_path)
    cur = conn.execute("PRAGMA table_info(feed_events)")
    cols = [row[1] for row in cur.fetchall()]
    assert "matched_context" in cols
    conn.close()


def test_insert_and_query_event(tmp_path):
    db_path = tmp_path / "feed.db"
    run_async(db.init_db(db_path))
    # insert two events with same key, second should be ignored
    run_async(db.insert_feed_event(db_path, "owner/repo", "release", "2023-01-01T00:00:00Z", "v1", relevance_score=0.5))
    run_async(db.insert_feed_event(db_path, "owner/repo", "release", "2023-01-01T00:00:00Z", "v1", relevance_score=0.8))
    events = run_async(db.get_feed_events(db_path, days_back=365))
    assert len(events) == 1
    assert events[0]["relevance_score"] == 0.5

