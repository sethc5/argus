# Changelog

## [0.2.0] - 2026-02-24
### Added
- `feed_delete_context` tool so clients can remove stale project contexts without touching the database directly.
- `matched_context` metadata in digest responses and discovery results so callers know why events/repositories scored highly.
- Batch embedding support in `feed_engine.discover_repos` to reduce OpenAI/Anthropic calls, plus a helper in the tests and dummy embedding client to cover the new contract.

### Changed
- Pagination now drives `GitHubClient.get_org_repos`, either stopping early if the org only returns a single page or continuing until the requested limit is satisfied, while still applying language and star filters.
- Rate-limit handling now sleeps, retries, and logs (instead of debating immutable 429 responses), and `_ensure_db()` tracks the actual database path so tests/sessions that swap `FEED_DB_PATH` recreate the schema automatically.
- Package version bumped to `0.2.0` and optional imports stubs keep the project importable even when AI/HTTP dependencies are absent.

### Fixed
- Database feed event tests now anchor on recent timestamps and avoid Pytest warnings by using `asyncio.run()` rather than `get_event_loop()`.
- Server tests switched to timezone-aware datetimes and validate the new context cleanup/listing workflow.
- `update_repo_checked` no longer overwrites `last_summary` when called without a summary, and feed events now deduplicate via the new schema constraint/migration.
