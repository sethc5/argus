"""GitHub API client - async, rate-limit-aware."""


from typing import Optional, List, Dict, Any
import time
import logging
import asyncio

# httpx is an external dependency; allow module to import without it
try:
    import httpx
except ImportError:  # pragma: no cover - optional
    httpx = None

GITHUB_API = "https://api.github.com"
GITHUB_TRENDING_SCRAPE = "https://github.com/trending"

_logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str, params: Optional[Dict] = None, extra_headers: Optional[Dict[str, str]] = None) -> Any:
        headers = self._headers.copy()
        if extra_headers:
            headers.update(extra_headers)

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            retries = 0
            while True:
                resp = await client.get(f"{GITHUB_API}{path}", params=params)
                if resp.headers:
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    if remaining is not None:
                        try:
                            rem = int(remaining)
                            if rem <= 0 and reset:
                                wait = max(int(reset) - int(time.time()), 0)
                                if wait > 0:
                                    _logger.info("GitHub rate limit reached, sleeping %ds", wait)
                                    await asyncio.sleep(wait)
                                retries += 1
                                if retries >= 3:
                                    break
                                continue
                        except ValueError:
                            pass
                resp.raise_for_status()
                return resp.json()
            resp.raise_for_status()

    async def get_starred_repos(self, username: str, limit: int = 200) -> List[Dict]:
        """Fetch starred repos for a user (paginates automatically up to limit)."""
        results = []
        page = 1
        while len(results) < limit:
            per_page = min(100, limit - len(results))
            data = await self._get(f"/users/{username}/starred", {"per_page": per_page, "page": page})
            if not data:
                break
            results.extend(data)
            if len(data) < per_page:
                break
            page += 1
        return results[:limit]

    async def get_repo(self, full_name: str) -> Dict:
        """Get repo metadata (including topics)."""
        # topics are only returned when the mercy-preview accept header is used
        return await self._get(
            f"/repos/{full_name}",
            extra_headers={"Accept": "application/vnd.github.mercy-preview+json"}
        )

    async def get_readme(self, full_name: str) -> Optional[str]:
        """Get README content (decoded from base64)."""
        import base64
        try:
            data = await self._get(f"/repos/{full_name}/readme")
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return data.get("content")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_releases(self, full_name: str, limit: int = 5) -> List[Dict]:
        """Get recent releases."""
        try:
            return await self._get(f"/repos/{full_name}/releases", {"per_page": limit})
        except httpx.HTTPStatusError:
            return []

    async def get_commits(self, full_name: str, since: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Get recent commits."""
        params: Dict[str, Any] = {"per_page": limit}
        if since:
            params["since"] = since
        try:
            return await self._get(f"/repos/{full_name}/commits", params)
        except httpx.HTTPStatusError:
            return []

    async def search_repos(
        self,
        query: str,
        language: Optional[str] = None,
        min_stars: int = 50,
        limit: int = 20,
        sort: str = "stars",
    ) -> List[Dict]:
        """Search GitHub repos by query string."""
        q = f"{query} stars:>={min_stars}"
        if language:
            q += f" language:{language}"
        data = await self._get("/search/repositories", {
            "q": q,
            "sort": sort,
            "order": "desc",
            "per_page": min(limit, 30),
        })
        return data.get("items", [])

    async def get_org_repos(
        self,
        org: str,
        language: Optional[str] = None,
        min_stars: int = 10,
        limit: int = 50,
    ) -> List[Dict]:
        """Get repos from an org, filtered by language and stars."""
        repos = []
        page = 1
        per_page = 100
        while len(repos) < limit:
            batch = await self._get(
                f"/orgs/{org}/repos",
                {"per_page": per_page, "page": page, "sort": "updated"},
            )
            if not batch:
                break
            filtered = [
                r for r in batch
                if r.get("stargazers_count", 0) >= min_stars
                and (language is None or (r.get("language") or "").lower() == language.lower())
            ]
            repos.extend(filtered)
            if len(batch) < per_page:
                break
            page += 1
        return repos[:limit]


    async def get_trending(
        self,
        language: Optional[str] = None,
        since: str = "weekly",
    ) -> List[Dict]:
        """
        Return trending repos for a language.  The classic HTML scrape
        was brittle and no longer needed; we just perform a lightweight
        search as a proxy.  The parameters are kept for compatibility.
        """
        # GitHub has never offered a stable trending API; the previous
        # scraping logic often failed.  Simplest approach is to perform a
        # search ordered by stars or recent updates.
        query = f"language:{language}" if language else "stars:>100"
        return await self.search_repos(query, limit=25, sort="updated")
