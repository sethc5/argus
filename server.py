"""
github-research-feed MCP Server

Personalized GitHub research intelligence feed.
Exposes tools for feed digests, repo discovery, watch management,
and project context registration — all queryable by Claude mid-session.
"""

import json
from typing import Optional
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from .config import load_config
from .db import (
    init_db, upsert_watched_repo, get_watched_repos, remove_watched_repo,
    upsert_project_context, get_project_contexts, get_project_context,
    get_feed_events, get_discovery_candidates, dismiss_candidate,
)
from .github import GitHubClient
from .embeddings import EmbeddingClient, build_repo_text, score_against_contexts
from .summarizer import Summarizer
from .feed_engine import FeedEngine

# --- Server Init ---

mcp = FastMCP("github_research_feed_mcp")
_config = load_config()
_github = GitHubClient(_config.github_token)
_embeddings = EmbeddingClient(_config.openai_api_key, _config.embedding_model)
_summarizer = Summarizer(_config.anthropic_api_key, _config.summarizer_model)
_engine = FeedEngine(_config.db_path, _github, _embeddings, _summarizer, _config.min_relevance)


async def _ensure_db():
    await init_db(_config.db_path)


# ============================================================
# FEED TOOLS
# ============================================================

class DigestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days_back: int = Field(default=7, ge=1, le=90, description="How many days back to look")
    min_relevance: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum relevance score (0.0-1.0)")
    project_filter: Optional[str] = Field(default=None, description="Filter events relevant to this project context name")
    summarize: bool = Field(default=True, description="Include an AI-generated digest summary")


@mcp.tool(
    name="feed_get_digest",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def feed_get_digest(params: DigestInput) -> str:
    """Get a digest of recent activity across all watched repos.

    Returns feed events sorted by relevance score, with an optional AI summary.
    Use project_filter to focus on events relevant to a specific project.

    Args:
        params: DigestInput with days_back, min_relevance, project_filter, summarize

    Returns:
        str: JSON with events list and optional digest summary
    """
    await _ensure_db()
    events = await get_feed_events(
        _config.db_path,
        days_back=params.days_back,
        min_relevance=params.min_relevance,
        limit=50,
    )

    # Filter by project context if requested
    if params.project_filter:
        # Re-fetch with higher relevance threshold for the filtered view
        events = [e for e in events if e.get("relevance_score", 0) >= 0.4]

    digest_summary = None
    if params.summarize and events:
        digest_summary = await _summarizer.summarize_digest(events, params.project_filter)

    result = {
        "period_days": params.days_back,
        "event_count": len(events),
        "digest_summary": digest_summary,
        "events": [
            {
                "repo": e["repo_full_name"],
                "type": e["event_type"],
                "title": e["title"],
                "summary": e["summary"],
                "relevance_score": round(e.get("relevance_score", 0), 3),
                "event_at": e["event_at"],
            }
            for e in events
        ]
    }
    return json.dumps(result, indent=2)


class RepoSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(..., description="Repository full name, e.g. 'Future-House/paper-qa'")
    project_context: Optional[str] = Field(default=None, description="Score and frame summary relative to this project")


@mcp.tool(
    name="feed_get_repo_summary",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False}
)
async def feed_get_repo_summary(params: RepoSummaryInput) -> str:
    """Get a detailed AI summary of a specific GitHub repo.

    Fetches live repo metadata, README, and recent activity, then generates
    a tailored summary. If project_context provided, scores relevance to your work.

    Args:
        params: RepoSummaryInput with repo name and optional project_context

    Returns:
        str: JSON with repo metadata, summary, relevance score, and recent activity
    """
    await _ensure_db()

    repo_data = await _github.get_repo(params.repo)
    readme = await _github.get_readme(params.repo)
    releases = await _github.get_releases(params.repo, limit=3)
    topics = await _github.get_topics(params.repo)

    repo_data["topics"] = topics
    text = build_repo_text(repo_data, readme)
    embedding = await _embeddings.embed(text)

    relevance_score = 0.0
    matched_context = None
    if params.project_context:
        ctx = await get_project_context(_config.db_path, params.project_context)
        if ctx and ctx.get("embedding"):
            import json as _json
            ctx_emb = _json.loads(ctx["embedding"])
            from .embeddings import cosine_similarity
            relevance_score = cosine_similarity(embedding, ctx_emb)
            matched_context = params.project_context
    else:
        contexts = await get_project_contexts(_config.db_path)
        relevance_score, matched_context = score_against_contexts(embedding, contexts)

    summary = await _summarizer.summarize_repo(
        params.repo,
        repo_data.get("description"),
        readme,
        matched_context,
    )

    result = {
        "full_name": params.repo,
        "description": repo_data.get("description"),
        "stars": repo_data.get("stargazers_count"),
        "language": repo_data.get("language"),
        "topics": topics,
        "homepage": repo_data.get("homepage"),
        "last_updated": repo_data.get("updated_at"),
        "summary": summary,
        "relevance_score": round(relevance_score, 3),
        "matched_context": matched_context,
        "recent_releases": [
            {"tag": r.get("tag_name"), "date": r.get("published_at"), "url": r.get("html_url")}
            for r in releases
        ],
    }
    return json.dumps(result, indent=2)


# ============================================================
# DISCOVERY TOOLS
# ============================================================

class DiscoverInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., description="Natural language query or project description to match against", min_length=3)
    language: Optional[str] = Field(default=None, description="Filter by programming language, e.g. 'python'")
    min_stars: int = Field(default=50, ge=0, description="Minimum star count")
    limit: int = Field(default=10, ge=1, le=30, description="Number of results to return")


@mcp.tool(
    name="feed_discover_repos",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def feed_discover_repos(params: DiscoverInput) -> str:
    """Discover new GitHub repos semantically similar to a query or project description.

    Searches GitHub, embeds results, and scores against your registered project contexts.
    Promising candidates are stored locally for future reference.

    Args:
        params: DiscoverInput with query, language, min_stars, limit

    Returns:
        str: JSON list of repos with similarity scores and descriptions
    """
    await _ensure_db()
    candidates = await _engine.discover_repos(
        params.query,
        language=params.language,
        min_stars=params.min_stars,
        limit=params.limit,
    )
    return json.dumps({"query": params.query, "results": candidates}, indent=2)


class CandidatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score: float = Field(default=0.5, ge=0.0, le=1.0, description="Minimum similarity score")
    project_filter: Optional[str] = Field(default=None, description="Filter by matched project context")
    limit: int = Field(default=20, ge=1, le=50)


@mcp.tool(
    name="feed_get_candidates",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def feed_get_candidates(params: CandidatesInput) -> str:
    """List pending discovery candidates above a similarity threshold.

    These are repos found during discovery that haven't been watched or dismissed yet.

    Args:
        params: CandidatesInput with min_score, project_filter, limit

    Returns:
        str: JSON list of candidate repos with scores
    """
    await _ensure_db()
    candidates = await get_discovery_candidates(
        _config.db_path,
        min_score=params.min_score,
        project_filter=params.project_filter,
        limit=params.limit,
    )
    return json.dumps({"candidates": candidates}, indent=2)


class DismissInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(..., description="Repository full name to dismiss, e.g. 'owner/repo'")


@mcp.tool(
    name="feed_dismiss_candidate",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
)
async def feed_dismiss_candidate(params: DismissInput) -> str:
    """Mark a discovery candidate as not relevant so it stops appearing.

    Args:
        params: DismissInput with repo full name

    Returns:
        str: Confirmation message
    """
    await _ensure_db()
    success = await dismiss_candidate(_config.db_path, params.repo)
    return json.dumps({"dismissed": success, "repo": params.repo})


# ============================================================
# WATCH MANAGEMENT TOOLS
# ============================================================

class WatchRepoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(..., description="Repository full name, e.g. 'Future-House/paper-qa'")


@mcp.tool(
    name="feed_watch_repo",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
)
async def feed_watch_repo(params: WatchRepoInput) -> str:
    """Add a repository to the watch list.

    Args:
        params: WatchRepoInput with repo full name

    Returns:
        str: Confirmation with repo details
    """
    await _ensure_db()
    await upsert_watched_repo(_config.db_path, params.repo, source="manual")
    return json.dumps({"watching": params.repo, "status": "added"})


class WatchOrgInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org: str = Field(..., description="GitHub organization name, e.g. 'Future-House'")
    language: Optional[str] = Field(default=None, description="Filter by language")
    min_stars: int = Field(default=10, ge=0, description="Minimum star count")


@mcp.tool(
    name="feed_watch_org",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def feed_watch_org(params: WatchOrgInput) -> str:
    """Add all repos from a GitHub org to the watch list (filtered by language/stars).

    Args:
        params: WatchOrgInput with org name, optional language filter and min_stars

    Returns:
        str: JSON with count of repos added
    """
    await _ensure_db()
    repos = await _github.get_org_repos(params.org, params.language, params.min_stars)
    added = []
    for repo in repos:
        full_name = repo.get("full_name")
        if full_name:
            await upsert_watched_repo(_config.db_path, full_name, source="org_watch")
            added.append(full_name)
    return json.dumps({"org": params.org, "added_count": len(added), "repos": added})


@mcp.tool(
    name="feed_list_watched",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def feed_list_watched() -> str:
    """List all watched repositories with their last activity summary.

    Returns:
        str: JSON list of watched repos
    """
    await _ensure_db()
    repos = await get_watched_repos(_config.db_path)
    return json.dumps({
        "count": len(repos),
        "repos": [
            {
                "full_name": r["full_name"],
                "source": r["source"],
                "added_at": r["added_at"],
                "last_checked": r["last_checked"],
                "last_summary": r["last_summary"],
            }
            for r in repos
        ]
    }, indent=2)


@mcp.tool(
    name="feed_unwatch_repo",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}
)
async def feed_unwatch_repo(params: WatchRepoInput) -> str:
    """Remove a repository from the watch list.

    Args:
        params: WatchRepoInput with repo full name

    Returns:
        str: Confirmation
    """
    await _ensure_db()
    removed = await remove_watched_repo(_config.db_path, params.repo)
    return json.dumps({"removed": removed, "repo": params.repo})


class SyncStarredInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., description="GitHub username to sync starred repos from")
    limit: int = Field(default=200, ge=1, le=500, description="Max number of starred repos to sync")


@mcp.tool(
    name="feed_sync_starred",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def feed_sync_starred(params: SyncStarredInput) -> str:
    """Pull your GitHub starred repos into the watch list.

    Args:
        params: SyncStarredInput with username and limit

    Returns:
        str: JSON with count of repos synced
    """
    await _ensure_db()
    starred = await _github.get_starred_repos(params.username, params.limit)
    added = []
    for repo in starred:
        full_name = repo.get("full_name")
        if full_name:
            await upsert_watched_repo(_config.db_path, full_name, source="starred")
            added.append(full_name)
    return json.dumps({"synced_count": len(added), "repos": added[:50], "truncated": len(added) > 50})


# ============================================================
# PROJECT CONTEXT TOOLS
# ============================================================

class AddContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="Short project name, e.g. 'athanor' or 'cytools'", min_length=1, max_length=50)
    description: str = Field(..., description="What this project does and what topics/domains matter to it", min_length=10)


@mcp.tool(
    name="feed_add_context",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def feed_add_context(params: AddContextInput) -> str:
    """Register a project context for relevance scoring.

    The description is embedded and used to score all repos and events.
    More specific descriptions produce better relevance scores.

    Args:
        params: AddContextInput with name and description

    Returns:
        str: Confirmation
    """
    await _ensure_db()
    embedding = await _embeddings.embed(params.description)
    await upsert_project_context(_config.db_path, params.name, params.description, embedding)
    return json.dumps({"added_context": params.name, "description": params.description})


@mcp.tool(
    name="feed_list_contexts",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def feed_list_contexts() -> str:
    """List all registered project contexts used for relevance scoring.

    Returns:
        str: JSON list of contexts
    """
    await _ensure_db()
    contexts = await get_project_contexts(_config.db_path)
    return json.dumps({
        "count": len(contexts),
        "contexts": [
            {"name": c["name"], "description": c["description"], "updated_at": c["updated_at"]}
            for c in contexts
        ]
    }, indent=2)


class UpdateContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="Project context name to update")
    description: str = Field(..., description="New description — will be re-embedded", min_length=10)


@mcp.tool(
    name="feed_update_context",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
)
async def feed_update_context(params: UpdateContextInput) -> str:
    """Update a project context description and re-embed it.

    Args:
        params: UpdateContextInput with name and new description

    Returns:
        str: Confirmation
    """
    await _ensure_db()
    embedding = await _embeddings.embed(params.description)
    await upsert_project_context(_config.db_path, params.name, params.description, embedding)
    return json.dumps({"updated_context": params.name})


# ============================================================
# POLL TOOL
# ============================================================

@mcp.tool(
    name="feed_poll_now",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def feed_poll_now() -> str:
    """Manually trigger a poll of all watched repos for new events.

    Checks for new releases and commit activity, scores against project contexts,
    and stores events in the local feed. Can take a few minutes for large watch lists.

    Returns:
        str: JSON with counts of new events detected
    """
    await _ensure_db()
    counts = await _engine.poll_all()
    return json.dumps({"poll_complete": True, "new_events": counts})


# ============================================================
# Entry point
# ============================================================

def main():
    mcp.run()


if __name__ == "__main__":
    main()
