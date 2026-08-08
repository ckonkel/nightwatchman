# TODO

- Add Discord alerts after the Git-backed deployment and backup workflow are proven stable.
- Evaluate a narrowly scoped recovery daemon for unhealthy services, with rate limits, audit logs, and an explicit opt-in before it can restart containers.
- Add a documented backup retention and restore-drill schedule.
- Review the curated Jackett public-indexer allowlist quarterly against the current pinned image, excluding adult-only and unreliable services and testing candidates with legal search terms before inclusion.
