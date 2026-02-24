# github-research-feed

Personalized GitHub research intelligence feed as an MCP server.

Turns GitHub into a queryable, summarized, discovery-capable research tool that Claude can call mid-session inside VSCode (via Cline, Continue, or Claude Code).

## What it does

- **Digest**: Summarizes recent activity across your watched repos, scored by relevance to your projects
- **Discovery**: Finds new repos semantically similar to your work — not just global trending
- **Context-aware**: Register your project descriptions; everything gets scored against them
- **Claude-native**: Exposes MCP tools so Claude can query your feed without leaving the editor

## Quick start

```bash
pip install github-research-feed

# Configure
export GITHUB_TOKEN=ghp_...
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...   # for embeddings
```

Add to your MCP config (Cline / Claude Desktop):

```json
{
  "mcpServers": {
    "github-research-feed": {
      "command": "python",
      "args": ["-m", "github_research_feed.server"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

## First run

```
# Add your project contexts
feed_add_context(name="athanor", description="Automated scientific discovery pipeline. Ingests literature, identifies research gaps, generates hypotheses. Drug discovery, longevity research, literature mining, PubMed, arXiv, hypothesis generation.")

feed_add_context(name="cytools", description="String theory research pipeline. Calabi-Yau manifold scanning, Standard Model candidates, algebraic geometry, line bundles, h11 values.")

feed_add_context(name="modframe", description="Structural map of federal power and cross-domain problem-solving patterns. Agent-based modeling, network analysis, political structure, systemic exploitation patterns.")

# Sync your starred repos
feed_sync_starred(username="your-github-username")

# Poll for events
feed_poll_now()

# Get your digest
feed_get_digest(days_back=7, project_filter="athanor")
```

## Available tools

| Tool | Description |
|------|-------------|
| `feed_get_digest` | Summarized activity digest across watched repos |
| `feed_get_repo_summary` | Deep summary of a specific repo |
| `feed_discover_repos` | Find new repos semantically similar to a query |
| `feed_get_candidates` | List pending discovery candidates |
| `feed_dismiss_candidate` | Mark a candidate as not relevant |
| `feed_watch_repo` | Add a repo to watch list |
| `feed_watch_org` | Watch all repos in a GitHub org |
| `feed_list_watched` | List watched repos |
| `feed_unwatch_repo` | Remove from watch list |
| `feed_sync_starred` | Sync your GitHub starred repos |
| `feed_add_context` | Register a project context |
| `feed_list_contexts` | List project contexts |
| `feed_update_context` | Update a project context |
| `feed_poll_now` | Manually trigger a feed poll |

## Architecture

See [SPEC.md](SPEC.md) for full architecture, data model, and build notes.
