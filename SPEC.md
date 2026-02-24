# github-research-feed

## What This Is

An MCP server that turns GitHub into a personalized, queryable research intelligence feed.
Runs locally, exposes tools that any Claude-based coding assistant (Cline, Continue, Claude Code)
can call mid-session. The key differentiator: relevance scoring against YOUR project context,
not just global trending.

---

## Problem It Solves

GitHub's native feed is noise. Existing tools either:
- Show global trending (no personalization)
- Wrap the raw GitHub API (no summarization)
- Monitor repos you already know (no discovery)

This fills the gap: **personalized + summarized + discoverable**.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  MCP CLIENT                      │
│         (Cline / Continue / Claude Code)         │
└─────────────────────┬───────────────────────────┘
                      │ MCP Protocol (stdio)
┌─────────────────────▼───────────────────────────┐
│             github-research-feed MCP             │
│                                                  │
│  ┌──────────────┐  ┌──────────────┐             │
│  │  Feed Engine │  │  Indexer     │             │
│  │  (polling)   │  │  (SQLite)    │             │
│  └──────┬───────┘  └──────┬───────┘             │
│         │                 │                      │
│  ┌──────▼─────────────────▼───────┐             │
│  │         GitHub API             │             │
│  │  (starred repos, trending,     │             │
│  │   search, releases, commits)   │             │
│  └────────────────────────────────┘             │
│                                                  │
│  ┌──────────────────────────────────┐           │
│  │     Relevance Engine             │           │
│  │  (cosine sim against project     │           │
│  │   context embeddings)            │           │
│  └──────────────────────────────────┘           │
│                                                  │
│  ┌──────────────────────────────────┐           │
│  │     Summarizer                   │           │
│  │  (Anthropic API, README diffs,   │           │
│  │   changelog parsing)             │           │
│  └──────────────────────────────────┘           │
└─────────────────────────────────────────────────┘
```

---

## Data Model

### Local SQLite Schema

```sql
-- Repos being watched
CREATE TABLE watched_repos (
    id INTEGER PRIMARY KEY,
    full_name TEXT UNIQUE,          -- "owner/repo"
    source TEXT,                    -- 'starred' | 'manual' | 'discovered'
    added_at TEXT,
    last_checked TEXT,
    last_summary TEXT,
    embedding TEXT                  -- JSON float array
);

-- Feed events (new releases, notable commits, README changes)
CREATE TABLE feed_events (
    id INTEGER PRIMARY KEY,
    repo_full_name TEXT,
    event_type TEXT,               -- 'release' | 'commit_burst' | 'readme_change' | 'new_repo'
    event_at TEXT,
    title TEXT,
    summary TEXT,
    relevance_score REAL,          -- 0.0 - 1.0 against project context
    raw_data TEXT                  -- JSON
);

-- Project contexts (your projects, used for relevance scoring)
CREATE TABLE project_contexts (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,              -- 'athanor' | 'cytools' | 'modframe'
    description TEXT,
    embedding TEXT,                -- JSON float array
    created_at TEXT
);

-- Discovery candidates (repos found but not yet confirmed)
CREATE TABLE discovery_candidates (
    id INTEGER PRIMARY KEY,
    full_name TEXT UNIQUE,
    discovered_at TEXT,
    similarity_score REAL,
    matched_context TEXT,
    dismissed INTEGER DEFAULT 0
);
```

---

## MCP Tools

### Feed & Monitoring

**`feed_get_digest`**
Returns summarized activity across all watched repos since last check (or N days).
- `days_back: int = 7`
- `min_relevance: float = 0.0` — filter by relevance score
- `project_filter: str = None` — filter to events relevant to a specific project

**`feed_get_repo_summary`**
Deep summary of a specific repo: what it does, recent activity, why it might matter to your stack.
- `repo: str` — "owner/repo"
- `project_context: str = None` — score against this project

**`feed_get_changelog`**
Summarized recent commits and releases for a repo.
- `repo: str`
- `days_back: int = 30`

### Discovery

**`feed_discover_repos`**
Find new repos semantically similar to a project or description. Searches GitHub and scores candidates.
- `query: str` — natural language description or project name
- `language: str = None`
- `min_stars: int = 50`
- `limit: int = 10`

**`feed_get_candidates`**
List pending discovery candidates above a similarity threshold.
- `min_score: float = 0.5`
- `project_filter: str = None`

**`feed_dismiss_candidate`**
Mark a discovery candidate as not relevant (so it stops appearing).
- `repo: str`

### Watch Management

**`feed_watch_repo`**
Add a repo to the watch list.
- `repo: str`
- `source: str = 'manual'`

**`feed_watch_org`**
Watch all repos in a GitHub org (or filter by topic/language).
- `org: str`
- `language: str = None`
- `min_stars: int = 10`

**`feed_list_watched`**
List all watched repos with last activity summary.

**`feed_unwatch_repo`**
Remove a repo from the watch list.
- `repo: str`

**`feed_sync_starred`**
Pull your GitHub starred repos into the watch list.
- `username: str`

### Project Context

**`feed_add_context`**
Register a project context for relevance scoring.
- `name: str` — project name
- `description: str` — what the project does, what topics matter

**`feed_list_contexts`**
List registered project contexts.

**`feed_update_context`**
Update a project context description (re-embeds automatically).
- `name: str`
- `description: str`

---

## Relevance Scoring

Simple cosine similarity between:
- Repo embedding: `embed(repo.description + repo.topics + readme_excerpt)`
- Project embedding: `embed(project.description)`

Uses `text-embedding-3-small` (cheap, fast, good enough for this).

Score thresholds:
- `>= 0.7` — High relevance, surface prominently
- `0.4 - 0.7` — Medium, include in digest
- `< 0.4` — Low, omit from digest unless asked

---

## Summarization

For each repo event, summarizer generates a 2-3 sentence summary:
- What changed
- Why it might matter to your project context
- Any action worth taking (e.g. "new MCP integration added")

Uses `claude-haiku-4-5` for cost efficiency on bulk summarization.
Falls back to README excerpt if API unavailable.

---

## Config

`~/.github-research-feed/config.json`

```json
{
  "github_token": "ghp_...",
  "anthropic_api_key": "sk-ant-...",
  "poll_interval_hours": 6,
  "db_path": "~/.github-research-feed/feed.db",
  "default_min_relevance": 0.4,
  "embedding_model": "text-embedding-3-small",
  "summarizer_model": "claude-haiku-4-5-20251001"
}
```

---

## File Structure

```
github-research-feed/
├── SPEC.md                  ← this file
├── README.md
├── pyproject.toml
├── server.py                ← MCP server entry point
├── src/
│   ├── __init__.py
│   ├── config.py            ← config loading
│   ├── db.py                ← SQLite schema + queries
│   ├── github.py            ← GitHub API client
│   ├── embeddings.py        ← embedding + cosine sim
│   ├── summarizer.py        ← Anthropic summarization
│   ├── feed_engine.py       ← polling + event detection
│   └── tools/
│       ├── __init__.py
│       ├── feed.py          ← feed_get_* tools
│       ├── discovery.py     ← feed_discover_* tools
│       ├── watch.py         ← feed_watch_* tools
│       └── context.py       ← feed_*_context tools
├── tests/
│   └── test_tools.py
└── .env.example
```

---

## Installation / Usage

```bash
# Install
pip install github-research-feed

# Or dev install
git clone ...
cd github-research-feed
pip install -e .

# Configure
cp .env.example .env
# Add GITHUB_TOKEN and ANTHROPIC_API_KEY

# Add to Claude Desktop / Cline config:
{
  "mcpServers": {
    "github-research-feed": {
      "command": "python",
      "args": ["-m", "github_research_feed"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}

# First run: sync your starred repos and add project contexts
# Then Claude can call tools like:
# "What's new in my research feed relevant to Athanor?"
# "Discover repos similar to my drug discovery pipeline"
# "Show me the changelog for Future-House/paper-qa"
```

---

## Build Order

1. `db.py` — schema, migrations, CRUD helpers
2. `config.py` — env + JSON config loading
3. `github.py` — API client (starred, search, releases, commits, README)
4. `embeddings.py` — embed text, cosine sim, cache embeddings in DB
5. `summarizer.py` — Anthropic calls for repo/event summaries
6. `feed_engine.py` — polling loop, event detection, relevance scoring
7. `tools/` — MCP tool definitions wrapping the above
8. `server.py` — FastMCP server wiring everything together

---

## Phase 2 (Later)

- VSCode extension sidebar wrapping this MCP server
- Scheduled background polling daemon
- Weekly email/markdown digest export
- arXiv feed integration (same architecture, different source)
- Semantic Scholar feed integration
