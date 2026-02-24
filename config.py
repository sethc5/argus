"""Configuration loading for github-research-feed."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_PATH = Path.home() / ".github-research-feed" / "feed.db"
DEFAULT_POLL_INTERVAL_HOURS = 6
DEFAULT_MIN_RELEVANCE = 0.4
DEFAULT_SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class Config:
    github_token: str
    anthropic_api_key: str
    openai_api_key: str
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    poll_interval_hours: int = DEFAULT_POLL_INTERVAL_HOURS
    min_relevance: float = DEFAULT_MIN_RELEVANCE
    summarizer_model: str = DEFAULT_SUMMARIZER_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    def __post_init__(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    github_token = os.environ.get("GITHUB_TOKEN", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")

    db_path_str = os.environ.get("FEED_DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_DB_PATH

    return Config(
        github_token=github_token,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        db_path=db_path,
        poll_interval_hours=int(os.environ.get("FEED_POLL_INTERVAL_HOURS", DEFAULT_POLL_INTERVAL_HOURS)),
        min_relevance=float(os.environ.get("FEED_MIN_RELEVANCE", DEFAULT_MIN_RELEVANCE)),
        summarizer_model=os.environ.get("FEED_SUMMARIZER_MODEL", DEFAULT_SUMMARIZER_MODEL),
        embedding_model=os.environ.get("FEED_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
