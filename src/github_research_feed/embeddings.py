"""Embedding generation and cosine similarity scoring."""

import json
import math
from typing import List, Optional

# openai may not be available during testing
try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - optional
    AsyncOpenAI = None

DEFAULT_MODEL = "text-embedding-3-small"
MAX_TEXT_CHARS = 8000  # Keep well under token limits


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two embedding vectors without numpy."""
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_against_contexts(
    repo_embedding: List[float],
    contexts: List[dict],
) -> tuple[float, Optional[str]]:
    """
    Score a repo embedding against all project contexts.
    Returns (best_score, best_context_name).
    """
    best_score = 0.0
    best_name = None

    for ctx in contexts:
        if not ctx.get("embedding"):
            continue
        ctx_embedding = json.loads(ctx["embedding"])
        score = cosine_similarity(repo_embedding, ctx_embedding)
        if score > best_score:
            best_score = score
            best_name = ctx["name"]

    return best_score, best_name


def build_repo_text(repo_data: dict, readme_excerpt: Optional[str] = None) -> str:
    """Build a text representation of a repo for embedding."""
    parts = []

    name = repo_data.get("full_name") or repo_data.get("name", "")
    if name:
        parts.append(f"Repository: {name}")

    description = repo_data.get("description") or ""
    if description:
        parts.append(f"Description: {description}")

    topics = repo_data.get("topics") or []
    if topics:
        parts.append(f"Topics: {', '.join(topics)}")

    language = repo_data.get("language") or ""
    if language:
        parts.append(f"Language: {language}")

    if readme_excerpt:
        # Take first 2000 chars of README
        parts.append(f"README: {readme_excerpt[:2000]}")

    text = "\n".join(parts)
    return text[:MAX_TEXT_CHARS]


class EmbeddingClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if AsyncOpenAI is None:
            # create dummy client that will raise if used
            class _Dummy:
                async def embeddings(self, *args, **kwargs):
                    raise RuntimeError("openai package required for embedding")
            self._client = _Dummy()
        else:
            self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, text: str) -> List[float]:
        """Embed a single text string."""
        text = text[:MAX_TEXT_CHARS]
        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts in one API call."""
        texts = [t[:MAX_TEXT_CHARS] for t in texts]
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        # Return in same order
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
