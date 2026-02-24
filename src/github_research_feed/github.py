"""GitHub API client - async, rate-limit-aware."""

import httpx
from typing import Optional, List, Dict, Any

GITHUB_API = "https://api.github.com"
GITHUB_TRENDING_SCRAPE = "https://github.com/trending"


class GitHubClient:
    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            resp = await client.get(f"{GITHUB_API}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

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
        """Get repo metadata."""
        return await self._get(f"/repos/{full_name}")

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
        repos = await self._get(f"/orgs/{org}/repos", {"per_page": 100, "sort": "updated"})
        filtered = [
            r for r in repos
            if r.get("stargazers_count", 0) >= min_stars
            and (language is None or (r.get("language") or "").lower() == language.lower())
        ]
        return filtered[:limit]

    async def get_topics(self, full_name: str) -> List[str]:
        """Get repo topics."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{full_name}/topics",
                headers={**self._headers, "Accept": "application/vnd.github.mercy-preview+json"}
            )
            if resp.status_code == 200:
                return resp.json().get("names", [])
        return []

    async def get_trending(
        self,
        language: Optional[str] = None,
        since: str = "weekly",
    ) -> List[Dict]:
        """
        Scrape GitHub trending page (no official API).
        Returns list of {full_name, description, stars, language}.
        Falls back to search if scraping fails.
        """
        try:
            url = "https://github.com/trending"
            if language:
                url += f"/{language}"
            url += f"?since={since}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={"User-Agent": "github-research-feed/0.1"})
                resp.raise_for_status()

            # Parse trending repos from HTML (simple extraction)
            import re
            html = resp.text
            repos = []
            # Match repo links like /owner/repo
            matches = re.findall(r'href="/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)"', html)
            seen = set()
            for m in matches:
                if "/" in m and m not in seen and not m.startswith("topics/"):
                    seen.add(m)
                    repos.append({"full_name": m, "description": "", "stars": 0})
                if len(repos) >= 25:
                    break
            return repos

        except Exception:
            # Fallback: search for trending by language
            query = f"language:{language}" if language else "stars:>100"
            return await self.search_repos(query, limit=20, sort="updated")
