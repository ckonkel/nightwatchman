# Portainer Backup and Git Deployment Implementation Plan

> **For agentic workers:** Use test-driven development for behavior-bearing helpers. Keep the scope to backup, discovery, Compose, and documentation. Do not implement or merge the paused watcher.

**Goal:** Produce a safe, Git-backed Gluetun/qBittorrent Portainer deployment with read-only discovery, qBittorrent config backup, migration, and rollback instructions.

**Architecture:** A two-service Compose file is the deployment source of truth. One small Python helper performs temporary Portainer authentication and read-only discovery; one shell helper performs fingerprint-pinned Unraid backup through the user's SSH password prompt. Live cutover remains manual and separately approved.

**Tech Stack:** Docker Compose, Python standard library, POSIX shell, pytest

---

### Task 1: Create the Compose deployment

**Files:** `compose.yaml`, `.env.example`, `.env.test`, `.gitignore`, `tests/test_compose.py`

- [ ] Write failing contract tests for two services, VPN namespace sharing, health dependency, current variables, required substitutions, port ownership, `/dev/net/tun`, `NET_ADMIN`, and absence of secrets/legacy variables.
- [ ] Implement the minimal Compose and environment examples.
- [ ] Run `docker compose --env-file .env.test config --quiet` and focused tests.
- [ ] Commit `feat: add Portainer VPN stack`.

### Task 2: Add temporary Portainer login and read-only discovery

**Files:** `bin/nightwatchman-portainer`, `nightwatchman_portainer.py`, `tests/test_portainer_helper.py`

- [ ] Write failing tests for silent login, mode `0600` session storage, JWT reuse/logout, read-only endpoint construction, exact stack/container discovery, bounded logs, recursive secret redaction, literal secrets embedded in Compose YAML, deployable `Config.Image` plus repository-digest capture, and in-memory Docker archive inspection that reports password-hash presence without persisting file contents.
- [ ] Implement with Python's standard library; default URL `http://192.168.50.101:9000`, environment `2`, stack `vpn-qtorrent`.
- [ ] Ensure no mutating HTTP method or Docker action exists. Use only Portainer/Docker GETs after authentication; use the container archive GET to read `qBittorrent.conf` in memory and discard its contents.
- [ ] Run focused and full tests.
- [ ] Commit `feat: add read-only Portainer discovery`.

### Task 3: Add fingerprint-pinned Unraid backup

**Files:** `bin/backup-qbittorrent`, `backup_qbittorrent.py`, `tests/test_backup.py`

- [ ] Write failing tests for absolute paths, destination-outside-source validation, rejection of traversal/whitespace/control/shell-metacharacter paths, exact fingerprint verification, exact verified-key known-hosts binding, strict SSH options, no password handling, safely quoted fixed archive/checksum command construction, config inclusion, and downloads exclusion.
- [ ] Implement a dry-run default and explicit `--execute` mode. Let `ssh` prompt for the password directly.
- [ ] Refuse execution unless qBittorrent is confirmed stopped through a supplied/verified discovery artifact or an explicit interactive confirmation.
- [ ] Run focused and full tests without opening SSH.
- [ ] Commit `feat: add qBittorrent configuration backup`.

### Task 4: Document and validate migration/rollback

**Files:** `README.md`, `docs/portainer-deployment.md`, `docs/migration-runbook.md`, `docs/troubleshooting.md`, `tests/test_docs.py`

- [ ] Write a small failing documentation contract test for required variables, credential rotation, backup verification, Web UI password-hash preservation, Git-stack replacement, verification, and rollback.
- [ ] Write concise operator documentation and explicitly defer watcher/Discord work.
- [ ] Run all tests, Compose rendering, Python compile checks, and `git diff --check`.
- [ ] Commit `docs: add Portainer migration runbook`.

### Live Operations Gate

Implementation ends after offline verification. Then request separate approval to:

1. Log in to Portainer for read-only discovery.
2. SSH to Unraid and create the backup.
3. Stop/remove/recreate the stack.

No live mutation is part of these four implementation tasks.
