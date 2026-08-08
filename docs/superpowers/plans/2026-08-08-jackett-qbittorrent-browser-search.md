# Jackett and qBittorrent Browser Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a VPN-routed Jackett service and the documentation needed to search Jackett indexers directly from qBittorrent's browser Web UI without changing qBittorrent's existing config or download mounts.

**Architecture:** Jackett joins qBittorrent in Gluetun's network namespace and is reached by the qBittorrent Jackett plugin at fixed internal URL `http://127.0.0.1:9117`. Gluetun alone publishes Jackett's configurable LAN host port, while Jackett persists only `/config` and uses the existing identity, timezone, logging, and healthy-Gluetun patterns.

**Tech Stack:** Docker Compose, LinuxServer Jackett, Gluetun, qBittorrent Web UI search plugins, Python `unittest`, Markdown runbook documentation.

---

## File Structure

- Modify `compose.yaml`: add the Jackett service, publish its UI through Gluetun, and add its readiness check.
- Modify `.env.example`: document the required Jackett image, host port, and config path placeholders without secrets or machine-specific values.
- Modify `.env.test`: provide safe values used only for offline Compose rendering.
- Modify `tests/test_compose.py`: enforce Jackett service, network, mount, port, logging, lifecycle, health, and secret/path invariants while retaining all qBittorrent assertions.
- Modify `README.md`: document configuration, preflight, deployment, browser plugin integration, verification, backup, upgrade, and rollback.

### Task 1: Specify the Jackett Compose Contract

**Files:**
- Modify: `tests/test_compose.py`
- Modify: `.env.test`

- [ ] **Step 1: Add safe Jackett test variables**

Append to `.env.test`:

```dotenv
JACKETT_IMAGE=ghcr.io/example/jackett:test
JACKETT_PORT=19117
JACKETT_CONFIG_PATH=/tmp/nightwatchman-test/jackett-config
```

- [ ] **Step 2: Replace the two-service assertion with the required three-service contract**

Update the service/image test to require exactly `gluetun`, `qbittorrent`, and `jackett`, including:

```python
self.assertEqual({"gluetun", "qbittorrent", "jackett"}, set(self.services))
self.assertEqual(self.test_env["JACKETT_IMAGE"], self.services["jackett"]["image"])
```

- [ ] **Step 3: Extend the port contract**

Require no `ports` key on either namespace-sharing service and require Gluetun's port set to include:

```python
("9117", self.test_env["JACKETT_PORT"], "tcp")
```

while preserving the existing qBittorrent Web UI and peer TCP/UDP mappings.

- [ ] **Step 4: Add Jackett network, dependency, environment, mount, and lifecycle tests**

Assert:

```python
jackett["network_mode"] == "service:gluetun"
jackett["depends_on"]["gluetun"]["condition"] == "service_healthy"
jackett["environment"] == {
    "AUTO_UPDATE": "false",
    "PGID": test_env["PGID"],
    "PUID": test_env["PUID"],
    "TZ": test_env["TZ"],
}
jackett mounts exactly JACKETT_CONFIG_PATH -> /config
jackett has no /downloads mount
jackett container_name == "jackett"
jackett restart == "unless-stopped"
```

Also require qBittorrent's exact existing environment-key set, exact `/config` and `/downloads` mount targets, and sole `gluetun` dependency so the Jackett addition cannot silently alter qBittorrent's runtime contract. Update the bounded logging test to cover all services.

- [ ] **Step 5: Add the readiness-check contract**

Require `CMD-SHELL`, a bounded localhost request to `/UI/Dashboard`, accepted 2xx/3xx/401/403 status classes, a five-second request timeout, and finite Compose `interval`, `timeout`, `retries`, and `start_period`. Ensure the command contains no API key, password, or authorization header.

- [ ] **Step 6: Extend required-variable and preservation assertions**

Require `${JACKETT_IMAGE:?required}`, `${JACKETT_PORT:?required}`, and `${JACKETT_CONFIG_PATH:?required}` in Compose. Preserve exact existing qBittorrent environment and mount assertions. Keep `.env.example` free of `/mnt/` and keep test credentials absent from tracked source.

- [ ] **Step 7: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.test_compose -v
```

Expected: FAIL because `compose.yaml` has no `jackett` service or Jackett port mapping.

### Task 2: Implement the Minimal Compose and Variable Changes

**Files:**
- Modify: `compose.yaml`
- Modify: `.env.example`

- [ ] **Step 1: Add Jackett placeholders to `.env.example`**

Add:

```dotenv
JACKETT_IMAGE=lscr.io/linuxserver/jackett:latest
JACKETT_PORT=9117
JACKETT_CONFIG_PATH=/path/to/jackett/config
```

Document that `latest` is an example only and Portainer must receive a concrete reviewed stable immutable tag or digest recorded at the deployment gate.

- [ ] **Step 2: Publish Jackett's fixed internal port through Gluetun**

Add to `gluetun.ports`:

```yaml
- target: 9117
  published: ${JACKETT_PORT:?required}
  protocol: tcp
```

- [ ] **Step 3: Add the minimal Jackett service**

Add:

```yaml
jackett:
  image: ${JACKETT_IMAGE:?required}
  container_name: jackett
  restart: unless-stopped
  network_mode: service:gluetun
  depends_on:
    gluetun:
      condition: service_healthy
  environment:
    PUID: ${PUID:?required}
    PGID: ${PGID:?required}
    TZ: ${TZ:?required}
    AUTO_UPDATE: "false"
  volumes:
    - ${JACKETT_CONFIG_PATH:?required}:/config
  healthcheck:
    test:
      - CMD-SHELL
      - >-
        curl --silent --show-error --output /dev/null --write-out '%{http_code}'
        --max-time 5 http://127.0.0.1:9117/UI/Dashboard |
        grep -Eq '^(2[0-9]{2}|3[0-9]{2}|401|403)$'
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 30s
  logging:
    driver: json-file
    options:
      max-size: 10m
      max-file: "3"
```

Do not add `/downloads`, VPN credentials, an API key, an independent network, or direct Jackett port publication.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_compose -v
docker compose --env-file .env.test -f compose.yaml config --quiet
```

Expected: all Compose contract tests pass and Compose exits 0.

- [ ] **Step 5: Commit the tested Compose increment**

```bash
git add compose.yaml .env.example .env.test tests/test_compose.py
git commit -m "feat: add VPN-routed Jackett service"
```

### Task 3: Document Browser Search and Operations

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update repository and variable descriptions**

Change the overview from two services to three and add `JACKETT_IMAGE`, `JACKETT_PORT`, and `JACKETT_CONFIG_PATH`. Use the intended live path only in the operator runbook block:

```text
JACKETT_PORT=9117
JACKETT_CONFIG_PATH=/mnt/ssd1tb-asus/appdata/jackett/config
```

Explicitly call out that the reported qBittorrent port 8080 must be reconciled with the older 9081 README value before deployment.

- [ ] **Step 2: Add a predeployment gate**

Document read-only checks for current image/application versions, exact Git revision, complete Portainer variable backup, unused selected Jackett host port, qBittorrent Web UI Search tab/plugin manager, Python support, existing Jackett config, and immutable Jackett image selection.

- [ ] **Step 3: Add deployment and first-run Jackett steps**

Document that deployment is not performed by offline tests and requires explicit approval. State that `${JACKETT_PORT}` remains LAN-only with no router forwarding. During an approved maintenance window: stop qBittorrent, make/verify its existing config backup, update Portainer variables and Git revision, deploy the full stack, wait for Gluetun then Jackett/qBittorrent health, and configure a Jackett admin password before adding indexers.

- [ ] **Step 4: Add qBittorrent browser-plugin integration**

Document installing only the maintained plugin URL:

```text
https://raw.githubusercontent.com/qbittorrent/search-plugins/master/nova3/engines/jackett.py
```

Configure its runtime `jackett.json` with `url` set to `http://127.0.0.1:9117` and the API key copied locally from Jackett. Show placeholders only, state that `jackett.json` stays under the protected qBittorrent `/config` bind, and prohibit copying its content into Git, chat, logs, tests, or discovery artifacts.

- [ ] **Step 5: Add verification, backup, upgrade, and rollback procedures**

Verification must cover container health, both Web UIs, persistent Jackett config, a legal indexer/search test, direct add to qBittorrent, PIA egress from Jackett's shared namespace, existing qBittorrent state/mounts, and Gluetun-recreation restart behavior.

Backup/upgrade/rollback must cover stopping Jackett for config backup, disabling in-container auto-update, changing only reviewed image references, preserving bind data, removing/disabling the plugin, and redeploying the exact prior Git revision plus its saved Portainer variables.

- [ ] **Step 6: Run documentation safety checks**

Run:

```bash
rg -n "JACKETT_(IMAGE|PORT|CONFIG_PATH)|127\.0\.0\.1:9117|jackett\.py|Backup|Upgrade|Rollback" README.md
! rg -n "(api_key|password)\"?[[:space:]]*[:=][[:space:]]*\"?[A-Za-z0-9_-]{20,}" README.md .env.example compose.yaml
git diff --check
```

Expected: required operational topics are present, no populated long credential is detected, and diff check exits 0.

- [ ] **Step 7: Commit documentation**

```bash
git add README.md
git commit -m "docs: add Jackett browser search runbook"
```

### Task 4: Complete Focused Verification and Review

**Files:**
- Verify: `compose.yaml`
- Verify: `.env.example`
- Verify: `.env.test`
- Verify: `tests/test_compose.py`
- Verify: `README.md`

- [ ] **Step 1: Run the full existing unit suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass with 0 failures.

- [ ] **Step 2: Run syntax and offline Compose checks**

```bash
python3 -m py_compile nightwatchman_portainer.py backup_qbittorrent.py
docker compose --env-file .env.test -f compose.yaml config --quiet
bin/nightwatchman-portainer --help
bin/backup-qbittorrent --help
bin/backup-qbittorrent
```

Expected: all commands exit 0; backup remains a dry run and opens no connection.

- [ ] **Step 3: Inspect the rendered contract and tracked diff**

```bash
docker compose --env-file .env.test -f compose.yaml config --format json
git diff develop...HEAD --check
git status --short
```

Confirm manually that qBittorrent retains its original `/config` and `/downloads` sources, only Gluetun publishes ports, Jackett has only `/config`, and no secrets/local environment files are newly tracked.

- [ ] **Step 4: Request focused code review**

Review the feature diff against:

```text
docs/superpowers/specs/2026-08-08-jackett-qbittorrent-browser-search-design.md
```

Fix every Critical or Important issue and rerun the relevant tests.

- [ ] **Step 5: Commit any review fixes and run fresh final verification**

Commit review fixes with a focused message, then repeat Steps 1-3. Do not claim completion based on an earlier test run.

- [ ] **Step 6: Integrate locally without pushing**

After the implementation passes review and verification, merge the feature branch into local `develop`, rerun the full verification on `develop`, and retain or remove the worktree according to the finishing workflow. Never push any branch or commit.

- [ ] **Step 7: Stop before live deployment**

Report the local commits and merged `develop` revision, summarize verified behavior, and request explicit approval for the live Portainer deployment. Remind the user that they must push the exact `develop` revision themselves before Portainer can consume it; never request or perform a repository push. Do not call Portainer mutation endpoints or change Unraid before deployment approval and confirmation that the user push is available to Portainer.
