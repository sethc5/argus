"""Feed engine - polling, event detection, relevance scoring."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from .db import (
    get_watched_repos, update_repo_checked, update_repo_embedding,
    insert_feed_event, get_project_contexts, upsert_discovery_candidate,
)
from .github import GitHubClient
from .embeddings import EmbeddingClient, build_repo_text, score_against_contexts
from .summarizer import Summarizer


class FeedEngine:
    def __init__(
        self,
        db_path: Path,
        github: GitHubClient,
        embeddings: EmbeddingClient,
        summarizer: Summarizer,
        min_relevance: float = 0.4,
    ):
        self.db_path = db_path
        self.github = github
        self.embeddings = embeddings
        self.summarizer = summarizer
        self.min_relevance = min_relevance

    async def poll_all(self) -> Dict[str, int]:
        """
        Poll all watched repos for new events.
        Returns counts: {releases, commits, repos_checked}.
        """
        repos = await get_watched_repos(self.db_path)
        contexts = await get_project_contexts(self.db_path)

        counts = {"releases": 0, "commits": 0, "repos_checked": 0}

        for repo in repos:
            try:
                new_events = await self._poll_repo(repo, contexts)
                counts["releases"] += new_events.get("releases", 0)
                counts["commits"] += new_events.get("commits", 0)
                counts["repos_checked"] += 1
            except Exception as e:
                # Don't let one bad repo kill the whole poll
                print(f"Error polling {repo['full_name']}: {e}")

        return counts

    async def _poll_repo(self, repo: dict, contexts: List[dict]) -> Dict[str, int]:
        """Poll a single repo for new events."""
        full_name = repo["full_name"]
        last_checked = repo.get("last_checked")
        counts = {"releases": 0, "commits": 0}

        # Fetch repo metadata and embed if not yet embedded
        repo_data = await self.github.get_repo(full_name)
        if not repo.get("embedding"):
            readme = await self.github.get_readme(full_name)
            text = build_repo_text(repo_data, readme)
            embedding = await self.embeddings.embed(text)
            await update_repo_embedding(self.db_path, full_name, embedding)
            repo["embedding"] = json.dumps(embedding)

        # Score against project contexts
        embedding = json.loads(repo["embedding"])
        best_score, best_context = score_against_contexts(embedding, contexts)

        # Check releases
        releases = await self.github.get_releases(full_name, limit=5)
        for release in releases:
            published = release.get("published_at", "")
            if last_checked and published <= last_checked:
                continue
            if not published:
                continue

            summary = await self.summarizer.summarize_release(
                full_name,
                release.get("tag_name", "?"),
                release.get("body"),
                best_context,
            )
            await insert_feed_event(
                self.db_path,
                repo_full_name=full_name,
                event_type="release",
                event_at=published,
                title=f"Release {release.get('tag_name', '?')}",
                summary=summary,
                relevance_score=best_score,
                raw_data={"tag": release.get("tag_name"), "url": release.get("html_url")},
            )
            counts["releases"] += 1

        # Check recent commits (only if active last week)
        since = last_checked or (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        commits = await self.github.get_commits(full_name, since=since, limit=20)
        if commits:
            messages = [c.get("commit", {}).get("message", "").split("\n")[0] for c in commits]
            summary = await self.summarizer.summarize_commit_burst(full_name, messages, best_context)
            latest_commit_at = commits[0].get("commit", {}).get("author", {}).get("date", since)
            await insert_feed_event(
                self.db_path,
                repo_full_name=full_name,
                event_type="commit_burst",
                event_at=latest_commit_at,
                title=f"{len(commits)} new commits",
                summary=summary,
                relevance_score=best_score,
                raw_data={"count": len(commits), "sample_messages": messages[:5]},
            )
            counts["commits"] += len(commits)

        # Update last checked timestamp
        await update_repo_checked(self.db_path, full_name)
        return counts

    async def discover_repos(
        self,
        query: str,
        language: Optional[str] = None,
        min_stars: int = 50,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search GitHub and score candidates against all project contexts.
        Stores promising candidates in DB.
        """
        contexts = await get_project_contexts(self.db_path)
        if not contexts:
            # No contexts to score against — just return raw search results
            results = await self.github.search_repos(query, language, min_stars, limit)
            return [{"full_name": r["full_name"], "description": r.get("description"), "similarity_score": 0.0} for r in results]

        # Get search results
        raw_results = await self.github.search_repos(query, language, min_stars, limit * 2)

        # Embed and score each candidate
        candidates = []
        for repo in raw_results[:limit * 2]:
            full_name = repo.get("full_name") or f"{repo.get('owner', {}).get('login', '')}/{repo.get('name', '')}"
            text = build_repo_text(repo)
            embedding = await self.embeddings.embed(text)
            score, matched_ctx = score_against_contexts(embedding, contexts)

            candidate = {
                "full_name": full_name,
                "description": repo.get("description"),
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language"),
                "similarity_score": round(score, 3),
                "matched_context": matched_ctx,
            }
            candidates.append(candidate)

            # Store in DB if score is interesting
            if score >= 0.35:
                await upsert_discovery_candidate(
                    self.db_path,
                    full_name=full_name,
                    similarity_score=score,
                    matched_context=matched_ctx,
                    description=repo.get("description"),
                    stars=repo.get("stargazers_count", 0),
                    language=repo.get("language"),
                )

        # Sort by score, return top N
        candidates.sort(key=lambda x: x["similarity_score"], reverse=True)
        return candidates[:limit]
