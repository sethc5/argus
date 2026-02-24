"""SQLite schema, migrations, and CRUD helpers."""

import json
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS watched_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    added_at TEXT NOT NULL,
    last_checked TEXT,
    last_summary TEXT,
    embedding TEXT
);

CREATE TABLE IF NOT EXISTS feed_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    relevance_score REAL DEFAULT 0.0,
    matched_context TEXT,
    raw_data TEXT,
    UNIQUE(repo_full_name, event_type, event_at)
);

CREATE TABLE IF NOT EXISTS project_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT UNIQUE NOT NULL,
    discovered_at TEXT NOT NULL,
    similarity_score REAL NOT NULL,
    matched_context TEXT,
    description TEXT,
    stars INTEGER DEFAULT 0,
    language TEXT,
    dismissed INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_feed_events_repo ON feed_events(repo_full_name);
CREATE INDEX IF NOT EXISTS idx_feed_events_at ON feed_events(event_at);
CREATE INDEX IF NOT EXISTS idx_feed_events_relevance ON feed_events(relevance_score);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON discovery_candidates(similarity_score);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        # perform simple migrations for existing databases
        cursor = await db.execute("PRAGMA table_info(feed_events)")
        cols = await cursor.fetchall()
        existing = {c[1] for c in cols}
        if "matched_context" not in existing:
            await db.execute("ALTER TABLE feed_events ADD COLUMN matched_context TEXT")
        await db.commit()


# --- Watched Repos ---

async def upsert_watched_repo(db_path: Path, full_name: str, source: str = "manual") -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO watched_repos (full_name, source, added_at)
               VALUES (?, ?, ?)
               ON CONFLICT(full_name) DO NOTHING""",
            (full_name, source, _now())
        )
        await db.commit()


async def get_watched_repos(db_path: Path) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM watched_repos ORDER BY added_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def remove_watched_repo(db_path: Path, full_name: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM watched_repos WHERE full_name = ?", (full_name,))
        await db.commit()
        return cursor.rowcount > 0


async def update_repo_checked(db_path: Path, full_name: str, summary: Optional[str] = None) -> None:
    async with aiosqlite.connect(db_path) as db:
        if summary is None:
            await db.execute(
                "UPDATE watched_repos SET last_checked = ? WHERE full_name = ?",
                (_now(), full_name)
            )
        else:
            await db.execute(
                "UPDATE watched_repos SET last_checked = ?, last_summary = ? WHERE full_name = ?",
                (_now(), summary, full_name)
            )
        await db.commit()


async def update_repo_embedding(db_path: Path, full_name: str, embedding: List[float]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE watched_repos SET embedding = ? WHERE full_name = ?",
            (json.dumps(embedding), full_name)
        )
        await db.commit()


# --- Feed Events ---

async def insert_feed_event(
    db_path: Path,
    repo_full_name: str,
    event_type: str,
    event_at: str,
    title: str,
    summary: Optional[str] = None,
    relevance_score: float = 0.0,
    matched_context: Optional[str] = None,
    raw_data: Optional[Dict] = None,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """INSERT INTO feed_events
               (repo_full_name, event_type, event_at, title, summary, relevance_score, matched_context, raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo_full_name, event_type, event_at) DO NOTHING""",
            (repo_full_name, event_type, event_at, title, summary, relevance_score,
             matched_context, json.dumps(raw_data) if raw_data else None)
        )
        await db.commit()
        return cursor.lastrowid


async def get_feed_events(
    db_path: Path,
    days_back: int = 7,
    min_relevance: float = 0.0,
    repo_filter: Optional[str] = None,
    context_filter: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    query = """SELECT * FROM feed_events
               WHERE event_at >= ? AND relevance_score >= ?"""
    params: List[Any] = [cutoff, min_relevance]

    if repo_filter:
        query += " AND repo_full_name = ?"
        params.append(repo_filter)

    if context_filter:
        query += " AND matched_context = ?"
        params.append(context_filter)

    query += " ORDER BY relevance_score DESC, event_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# --- Project Contexts ---

async def upsert_project_context(
    db_path: Path,
    name: str,
    description: str,
    embedding: Optional[List[float]] = None,
) -> None:
    now = _now()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO project_contexts (name, description, embedding, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   description = excluded.description,
                   embedding = excluded.embedding,
                   updated_at = excluded.updated_at""",
            (name, description, json.dumps(embedding) if embedding else None, now, now)
        )
        await db.commit()


async def get_project_contexts(db_path: Path) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM project_contexts ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_project_context(db_path: Path, name: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM project_contexts WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# --- Discovery Candidates ---

async def upsert_discovery_candidate(
    db_path: Path,
    full_name: str,
    similarity_score: float,
    matched_context: Optional[str],
    description: Optional[str],
    stars: int = 0,
    language: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO discovery_candidates
               (full_name, discovered_at, similarity_score, matched_context, description, stars, language)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(full_name) DO UPDATE SET
                   similarity_score = MAX(excluded.similarity_score, similarity_score),
                   matched_context = excluded.matched_context,
                   description = excluded.description,
                   stars = excluded.stars""",
            (full_name, _now(), similarity_score, matched_context, description, stars, language)
        )
        await db.commit()


async def get_discovery_candidates(
    db_path: Path,
    min_score: float = 0.5,
    project_filter: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    query = """SELECT * FROM discovery_candidates
               WHERE dismissed = 0 AND similarity_score >= ?"""
    params: List[Any] = [min_score]

    if project_filter:
        query += " AND matched_context = ?"
        params.append(project_filter)

    query += " ORDER BY similarity_score DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def dismiss_candidate(db_path: Path, full_name: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE discovery_candidates SET dismissed = 1 WHERE full_name = ?",
            (full_name,)
        )
        await db.commit()
        return cursor.rowcount > 0
