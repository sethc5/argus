"""Anthropic-based summarization for repos and feed events."""


from typing import Optional
# anthropic lib is optional in test environment
try:
    import anthropic
except ImportError:  # pragma: no cover - optional
    anthropic = None

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300


class Summarizer:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if anthropic is None:
            # dummy client for tests; returns empty summaries
            class DummyClient:
                class _Messages:
                    async def create(self, *args, **kwargs):
                        return type("Resp", (), {"content": [type("T", (), {"text": ""})]})
                def __init__(self):
                    self.messages = DummyClient._Messages()
            self._client = DummyClient()
        else:
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def summarize_repo(
        self,
        repo_name: str,
        description: Optional[str],
        readme_excerpt: Optional[str],
        project_context: Optional[str] = None,
    ) -> str:
        """
        Generate a 2-3 sentence summary of a repo.
        If project_context provided, tailor the summary to relevance.
        """
        parts = [f"Repository: {repo_name}"]
        if description:
            parts.append(f"Description: {description}")
        if readme_excerpt:
            parts.append(f"README excerpt:\n{readme_excerpt[:3000]}")

        context_hint = ""
        if project_context:
            context_hint = f"\nFrame the summary in terms of relevance to: {project_context}"

        prompt = f"""Summarize this GitHub repository in 2-3 sentences. Be specific about what it does and who would use it.{context_hint}

{chr(10).join(parts)}

Summary:"""

        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()

    async def summarize_release(
        self,
        repo_name: str,
        version: str,
        release_notes: Optional[str],
        project_context: Optional[str] = None,
    ) -> str:
        """Summarize a release in 1-2 sentences."""
        notes_excerpt = (release_notes or "No release notes provided.")[:2000]

        context_hint = ""
        if project_context:
            context_hint = f" Focus on what matters for: {project_context}."

        prompt = f"""Summarize this GitHub release in 1-2 sentences. What changed and why does it matter?{context_hint}

Repository: {repo_name}
Version: {version}
Release notes:
{notes_excerpt}

Summary:"""

        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()

    async def summarize_commit_burst(
        self,
        repo_name: str,
        commit_messages: list[str],
        project_context: Optional[str] = None,
    ) -> str:
        """Summarize a batch of recent commits in 1-2 sentences."""
        messages_text = "\n".join(f"- {m}" for m in commit_messages[:20])

        context_hint = ""
        if project_context:
            context_hint = f" Frame relevance to: {project_context}."

        prompt = f"""Summarize these recent commits to {repo_name} in 1-2 sentences. What's the overall direction of recent work?{context_hint}

Recent commits:
{messages_text}

Summary:"""

        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()

    async def summarize_digest(
        self,
        events: list[dict],
        project_context: Optional[str] = None,
    ) -> str:
        """Generate a high-level digest summary from a list of feed events."""
        if not events:
            return "No new activity in the feed for this period."

        event_lines = []
        for e in events[:30]:
            score_indicator = "🔥" if e.get("relevance_score", 0) >= 0.7 else "→"
            event_lines.append(
                f"{score_indicator} [{e['repo_full_name']}] {e['title']}: {e.get('summary', '')}"
            )

        context_hint = f" for {project_context} research" if project_context else ""
        prompt = f"""Here are recent GitHub feed events{context_hint}. Write a 3-5 sentence digest highlighting the most significant developments and any patterns worth noting.

Events:
{chr(10).join(event_lines)}

Digest:"""

        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
