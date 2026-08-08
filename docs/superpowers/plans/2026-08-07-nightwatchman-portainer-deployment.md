# Nightwatchman Portainer Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tested Git-backed Portainer stack, fail-closed Gluetun recovery service, local Portainer administration CLI, and safe manual-stack migration tooling without mutating the live server during implementation.

**Architecture:** A Python 3.13 package provides shared HTTP clients, recovery state-machine logic, a daemon entry point, and a local CLI. The deployed daemon talks to Docker only through a restricted socket proxy; the local CLI talks to Portainer and invokes a tightly scoped SSH helper only for Unraid discovery and backups. Compose and GitHub Actions package the daemon as a pinned GHCR image, while all live cutover actions remain separately approved operations.

**Tech Stack:** Python 3.13, standard-library HTTP/JSON/argparse, pytest, Ruff, Docker Compose, Tecnativa Docker Socket Proxy, GitHub Actions, GHCR

---

## File Map

- `pyproject.toml` — package metadata, console scripts, test and lint configuration.
- `src/nightwatchman/config.py` — validated daemon configuration from environment variables.
- `src/nightwatchman/http.py` — small injectable HTTP transport and typed API errors.
- `src/nightwatchman/docker_api.py` — Docker Engine operations used by the recovery daemon.
- `src/nightwatchman/portainer_api.py` — Portainer authentication, discovery, logs, and exec operations.
- `src/nightwatchman/state.py` — atomic persisted recovery timing state.
- `src/nightwatchman/recovery.py` — pure recovery decision state machine and coordinated actions.
- `src/nightwatchman/daemon.py` — polling loop and structured logging.
- `src/nightwatchman/session.py` — secure `.env.portainer.local` parsing/writing/removal.
- `src/nightwatchman/redaction.py` — secret-key and token redaction.
- `src/nightwatchman/cli.py` — local command parser and safe command dispatch.
- `src/nightwatchman/ssh.py` — pinned-host SSH invocation without password persistence.
- `src/nightwatchman/migration.py` — discovery artifacts, manifests, rollback rendering, and cutover gates.
- `nightwatchman` — repository-local launcher for the installed module.
- `compose.yaml` — production Portainer stack.
- `.env.example` — non-secret Portainer variable contract.
- `.env.test` — safe values used only for Compose validation.
- `Dockerfile` — non-root daemon image.
- `.dockerignore` — minimal image context.
- `.gitignore` — local sessions, migration artifacts, caches, and build output.
- `.github/workflows/ci.yml` — lint, unit tests, and Compose validation.
- `.github/workflows/publish.yml` — release/SHA image publication to GHCR.
- `tests/` — focused unit and contract tests mirroring package modules.
- `README.md` — deployment, authentication, diagnosis, migration, rollback, and security runbook.

## Phase 1: Shared Python Foundation

### Task 1: Create the package and quality gates

**Files:**
- Create: `pyproject.toml`
- Create: `src/nightwatchman/__init__.py`
- Create: `tests/test_package.py`
- Create: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Write the failing package smoke test**

```python
# tests/test_package.py
import nightwatchman


def test_package_exposes_version() -> None:
    assert nightwatchman.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the smoke test and verify import failure**

Run: `python -m pytest tests/test_package.py -v`

Expected: FAIL because the package is not installed or `__version__` is missing.

- [ ] **Step 3: Add the minimal package configuration**

Create `pyproject.toml` with Python `>=3.13`, a `nightwatchman = "nightwatchman.cli:main"` console script, pytest `testpaths = ["tests"]`, Ruff line length 100, and a `dev` dependency group containing pytest, pytest-cov, and Ruff. Add `__version__ = "0.1.0"` to `src/nightwatchman/__init__.py`.

Add these ignore rules:

```gitignore
.env.portainer.local
.nightwatchman/
.pytest_cache/
.ruff_cache/
.venv/
__pycache__/
*.egg-info/
build/
dist/
```

- [ ] **Step 4: Install the development environment and run checks**

Run: `python -m pip install -e '.[dev]'`

Run: `python -m pytest tests/test_package.py -v`

Run: `ruff check .`

Expected: all commands succeed; one test passes.

- [ ] **Step 5: Commit the scaffold**

```bash
git add pyproject.toml src/nightwatchman/__init__.py tests/test_package.py .gitignore README.md
git commit -m "chore: scaffold Nightwatchman Python package"
```

### Task 2: Implement injectable HTTP transport and redaction

**Files:**
- Create: `src/nightwatchman/http.py`
- Create: `src/nightwatchman/redaction.py`
- Create: `tests/test_http.py`
- Create: `tests/test_redaction.py`

- [ ] **Step 1: Write failing transport and redaction tests**

Test that `HttpClient.request_json()` serializes JSON, sets headers, decodes JSON, and raises `ApiError(status, message)` for non-2xx responses without including an authorization token. Test recursive redaction for keys matching `password`, `token`, `authorization`, `OPENVPN_USER`, and `OPENVPN_PASSWORD` case-insensitively.

```python
def test_redact_nested_secrets() -> None:
    value = {"OPENVPN_PASSWORD": "secret", "nested": {"token": "jwt", "ok": 1}}
    assert redact(value) == {
        "OPENVPN_PASSWORD": "<redacted>",
        "nested": {"token": "<redacted>", "ok": 1},
    }
```

- [ ] **Step 2: Run tests and verify missing-module failures**

Run: `python -m pytest tests/test_http.py tests/test_redaction.py -v`

Expected: FAIL because both modules are missing.

- [ ] **Step 3: Implement the minimal interfaces**

Implement:

```python
class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str): ...


class HttpClient:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: object | None = None,
        timeout: float = 10.0,
    ) -> object: ...
```

Use `urllib.request`, inject an opener callable for tests, cap response bodies included in errors, and pass every error message through redaction. Implement `redact(value)` recursively without mutating its argument.

- [ ] **Step 4: Run focused and full checks**

Run: `python -m pytest tests/test_http.py tests/test_redaction.py -v`

Run: `ruff check src/nightwatchman tests`

Expected: all tests and lint pass.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/http.py src/nightwatchman/redaction.py tests/test_http.py tests/test_redaction.py
git commit -m "feat: add secure HTTP transport"
```

## Phase 2: Recovery Service

### Task 3: Implement Docker API discovery and actions

**Files:**
- Create: `src/nightwatchman/docker_api.py`
- Create: `tests/test_docker_api.py`

- [ ] **Step 1: Write failing Docker API contract tests**

Cover exact-one service discovery using labels `com.docker.compose.project` and `com.docker.compose.service`, health extraction from `State.Health.Status`, and `stop`, `restart`, and `start` calls. Assert zero or multiple matches raise `TargetDiscoveryError` and action failures include the operation but no environment secrets.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_docker_api.py -v`

Expected: FAIL because `DockerApi` does not exist.

- [ ] **Step 3: Implement the Docker client**

Implement these public methods against a configurable base URL:

```python
class DockerApi:
    def find_service(self, project: str, service: str) -> Container: ...
    def inspect(self, container_id: str) -> dict[str, object]: ...
    def health(self, container_id: str) -> str: ...
    def stop(self, container_id: str, timeout_seconds: int = 30) -> None: ...
    def restart(self, container_id: str, timeout_seconds: int = 30) -> None: ...
    def start(self, container_id: str) -> None: ...
```

Use Docker API paths `/containers/json`, `/containers/{id}/json`, `/stop`, `/restart`, and `/start`. Treat absent health data as `unknown`.

- [ ] **Step 4: Run focused tests and lint**

Run: `python -m pytest tests/test_docker_api.py -v`

Run: `ruff check src/nightwatchman/docker_api.py tests/test_docker_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/docker_api.py tests/test_docker_api.py
git commit -m "feat: add restricted Docker API client"
```

### Task 4: Persist recovery timing state atomically

**Files:**
- Create: `src/nightwatchman/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing state-store tests**

Cover missing files, round trips, mode `0600`, atomic replacement, corrupt JSON, schema version rejection, future clock skew, and preserving `unhealthy_since`, `attempt_started_at`, `cooldown_until`, `remediation_phase`, and `qbittorrent_was_running` across a reconstructed store.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_state.py -v`

Expected: FAIL because `StateStore` is missing.

- [ ] **Step 3: Implement the state model and store**

```python
@dataclass(frozen=True)
class RecoveryState:
    unhealthy_since: datetime | None = None
    attempt_started_at: datetime | None = None
    cooldown_until: datetime | None = None
    remediation_phase: str = "idle"
    qbittorrent_was_running: bool = False


class StateStore:
    def load(self, now: datetime) -> RecoveryState: ...
    def save(self, state: RecoveryState) -> None: ...
```

Write a temporary file beside the target, `flush`, `fsync`, `chmod(0o600)`, and replace with `os.replace`. On invalid data, preserve the bad file for diagnosis, return a conservative state that prevents immediate remediation for one cooldown period, and log a structured warning.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_state.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/state.py tests/test_state.py
git commit -m "feat: persist recovery timing state"
```

### Task 5: Build the pure recovery decision state machine

**Files:**
- Create: `src/nightwatchman/recovery.py`
- Create: `tests/test_recovery_decisions.py`

- [ ] **Step 1: Write parameterized failing decision tests**

Test `healthy` resets the failure timer, intermittent failure does not accumulate, `unhealthy` below 120 seconds waits, 120 seconds triggers, active cooldown suppresses, and `unknown` never starts qBittorrent or triggers an optimistic action.

```python
def test_two_continuous_minutes_trigger(clock, initial_state):
    decision, state = decide(initial_state, "unhealthy", clock.now())
    clock.advance(seconds=120)
    decision, state = decide(state, "unhealthy", clock.now())
    assert decision is Decision.REMEDIATE
    assert state.cooldown_until == clock.now() + timedelta(minutes=10)
```

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_recovery_decisions.py -v`

Expected: FAIL because decision types are missing.

- [ ] **Step 3: Implement pure decision logic**

Implement `Decision` as `WAIT`, `REMEDIATE`, and `COOLDOWN`; implement `decide(state, health, now, unhealthy_threshold, cooldown)` without I/O. Begin cooldown in the same returned state as `REMEDIATE`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_recovery_decisions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/recovery.py tests/test_recovery_decisions.py
git commit -m "feat: add recovery decision state machine"
```

### Task 6: Coordinate fail-closed remediation

**Files:**
- Modify: `src/nightwatchman/recovery.py`
- Create: `tests/test_remediation.py`

- [ ] **Step 1: Write failing ordered-action tests**

Use a fake Docker API and fake clock to assert: stop qBittorrent, restart Gluetun, poll health, start qBittorrent only after healthy. Cover already-stopped qBittorrent, already-running qBittorrent, Gluetun timeout, target ambiguity, and failures at each API boundary. Assert every failure leaves qBittorrent stopped when its state can be determined.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_remediation.py -v`

Expected: FAIL because `Remediator` is missing.

- [ ] **Step 3: Implement minimal coordinator**

```python
class Remediator:
    def run(self, project: str, recovery_timeout: timedelta) -> RemediationResult:
        qb = self.docker.find_service(project, "qbittorrent")
        vpn = self.docker.find_service(project, "gluetun")
        self.docker.stop(qb.id)
        self.docker.restart(vpn.id)
        if not self.wait_until_healthy(vpn.id, recovery_timeout):
            return RemediationResult.RECOVERY_TIMEOUT
        self.docker.start(qb.id)
        return RemediationResult.RECOVERED
```

Re-inspect before each action, make stop/start idempotent, and do not catch-and-ignore Docker errors.

- [ ] **Step 4: Run remediation and decision tests**

Run: `python -m pytest tests/test_remediation.py tests/test_recovery_decisions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/recovery.py tests/test_remediation.py
git commit -m "feat: coordinate fail-closed VPN recovery"
```

### Task 7: Add validated daemon configuration and polling loop

**Files:**
- Create: `src/nightwatchman/config.py`
- Create: `src/nightwatchman/daemon.py`
- Create: `tests/test_config.py`
- Create: `tests/test_daemon.py`

- [ ] **Step 1: Write failing configuration and daemon tests**

Test defaults of 5-second polling, 120-second unhealthy threshold, 600-second cooldown, and 300-second recovery timeout. Reject non-positive values, malformed socket-proxy URLs, and empty project names. Test one structured JSON event per state transition. Test restart reconciliation for each persisted remediation phase: an `idle` phase follows normal decisions; `stopping_qbittorrent` inspects qBittorrent, retries the idempotent stop if still running, then advances to `waiting_for_vpn`; `waiting_for_vpn` plus healthy Gluetun starts qBittorrent only if it was running before remediation, then clears the phase; `waiting_for_vpn` plus unhealthy Gluetun before the original timeout resumes waiting despite cooldown; and `waiting_for_vpn` after the original timeout leaves qBittorrent stopped, emits timeout, and clears the active phase without bypassing cooldown.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_config.py tests/test_daemon.py -v`

Expected: FAIL because configuration and daemon modules are missing.

- [ ] **Step 3: Implement config and daemon**

Read `DOCKER_HOST`, `COMPOSE_PROJECT_NAME`, `POLL_INTERVAL_SECONDS`, `UNHEALTHY_THRESHOLD_SECONDS`, `COOLDOWN_SECONDS`, `RECOVERY_TIMEOUT_SECONDS`, and `STATE_PATH`. The daemon must load state and reconcile `remediation_phase` before normal cooldown decisions. Before the external stop call, atomically persist `qbittorrent_was_running` and phase `stopping_qbittorrent`; after confirming qBittorrent stopped, persist `waiting_for_vpn` before restarting Gluetun. A restarted daemon in `stopping_qbittorrent` inspects the target, repeats the idempotent stop when necessary, and advances safely. A restarted daemon in `waiting_for_vpn` resumes the original attempt using `attempt_started_at + recovery_timeout`, starts qBittorrent only after verified healthy and only when `qbittorrent_was_running` is true, then clears the phase. It does not blindly issue another Gluetun restart because it cannot know whether the pre-crash request reached Docker; if Gluetun stays unhealthy, the original deadline expires fail-closed with qBittorrent stopped. If the deadline passed or state is uncertain, leave qBittorrent stopped. Persist each intended phase before the corresponding non-idempotent decision and persist confirmed completion before advancing; handle SIGTERM cleanly.

- [ ] **Step 4: Run all recovery tests**

Run: `python -m pytest tests/test_config.py tests/test_daemon.py tests/test_state.py tests/test_recovery_decisions.py tests/test_remediation.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/config.py src/nightwatchman/daemon.py tests/test_config.py tests/test_daemon.py
git commit -m "feat: add Nightwatchman recovery daemon"
```

## Phase 3: Local Portainer CLI

### Task 8: Implement secure Portainer login sessions

**Files:**
- Create: `src/nightwatchman/portainer_api.py`
- Create: `src/nightwatchman/session.py`
- Create: `src/nightwatchman/cli.py`
- Create: `nightwatchman`
- Create: `tests/test_portainer_auth.py`
- Create: `tests/test_session.py`
- Create: `tests/test_cli_login.py`

- [ ] **Step 1: Write failing authentication tests**

Test `POST /api/auth`, silent password callback usage, rejection without credential echo, default URL/environment, overrides, a quoted dotenv-safe session file, mode `0600`, refusal of group/world-readable files, expiration detection from JWT `exp`, and logout removal.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_portainer_auth.py tests/test_session.py tests/test_cli_login.py -v`

Expected: FAIL because authentication/session classes are missing.

- [ ] **Step 3: Implement authentication and session storage**

Implement `PortainerApi.login(username, password) -> jwt`, `Session.load/save/remove`, and CLI subcommands `login`, `logout`, and `status`. Use `getpass.getpass`, never pass passwords as command arguments, decode JWT payload only to check expiration, and authenticate later requests with `Authorization: Bearer <jwt>`.

The launcher must contain only:

```sh
#!/bin/sh
exec python -m nightwatchman.cli "$@"
```

- [ ] **Step 4: Run tests and verify CLI help**

Run: `python -m pytest tests/test_portainer_auth.py tests/test_session.py tests/test_cli_login.py -v`

Run: `./nightwatchman --help`

Expected: tests pass and help lists `login`, `logout`, and `status` without contacting Portainer.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/portainer_api.py src/nightwatchman/session.py src/nightwatchman/cli.py nightwatchman tests/test_portainer_auth.py tests/test_session.py tests/test_cli_login.py
git commit -m "feat: add temporary Portainer login sessions"
```

### Task 9: Add read-only Portainer inspection commands

**Files:**
- Modify: `src/nightwatchman/portainer_api.py`
- Modify: `src/nightwatchman/cli.py`
- Create: `tests/test_portainer_read.py`
- Create: `tests/test_cli_read.py`

- [ ] **Step 1: Write failing API and CLI tests**

Cover environment, stack, and container listing; stack lookup by name; exact-one container lookup by Compose labels; inspect; health; and logs. Assert output redacts credentials and reports expired tokens distinctly.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_portainer_read.py tests/test_cli_read.py -v`

Expected: FAIL because read methods/subcommands are absent.

- [ ] **Step 3: Implement read-only methods and commands**

Use `/api/endpoints`, `/api/stacks`, and `/api/endpoints/{id}/docker/...`. Add `environments`, `stacks`, `stack`, `containers`, `inspect`, `health`, and `logs`. Default `stack` to `vpn-qtorrent`; allow only `gluetun` and `qbittorrent` service aliases for container commands.

- [ ] **Step 4: Run CLI test suite**

Run: `python -m pytest tests/test_portainer_read.py tests/test_cli_read.py tests/test_cli_login.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/portainer_api.py src/nightwatchman/cli.py tests/test_portainer_read.py tests/test_cli_read.py
git commit -m "feat: add Portainer inspection commands"
```

### Task 10: Add confirmed Portainer exec and shell access

**Files:**
- Modify: `src/nightwatchman/portainer_api.py`
- Modify: `src/nightwatchman/cli.py`
- Create: `tests/test_portainer_exec.py`
- Create: `tests/test_cli_exec.py`

- [ ] **Step 1: Write failing exec tests**

Cover Docker exec creation, start, stream decoding, cleanup-by-completion inspection, qBittorrent `/bin/bash`, Gluetun `/bin/sh`, root user, TTY selection, service allowlist, confirmation rejection, EOF, and `--yes` behavior for explicit automation.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_portainer_exec.py tests/test_cli_exec.py -v`

Expected: FAIL because exec support is absent.

- [ ] **Step 3: Implement exec transport and commands**

Add `create_exec`, `start_exec`, and `inspect_exec` using Portainer's Docker proxy. Add `shell SERVICE` and `exec SERVICE -- COMMAND...`. Require the literal confirmation `execute SERVICE` unless `--yes` is passed. Never infer a command is read-only.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_portainer_exec.py tests/test_cli_exec.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/portainer_api.py src/nightwatchman/cli.py tests/test_portainer_exec.py tests/test_cli_exec.py
git commit -m "feat: add confirmed container console access"
```

## Phase 4: Migration Safety Tooling

### Task 11: Add pinned-host SSH discovery and backup commands

**Files:**
- Create: `src/nightwatchman/ssh.py`
- Create: `src/nightwatchman/migration.py`
- Modify: `src/nightwatchman/cli.py`
- Create: `tests/test_ssh.py`
- Create: `tests/test_migration_backup.py`

- [ ] **Step 1: Write failing SSH and backup tests**

Test the exact host `192.168.50.101`, user `root`, fingerprint `SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I`, strict host checking, password via controlling terminal only, and no password in argv/environment/files. Test rejection of a mismatched fingerprint. Test backup-path rejection when inside the active config path, mode requirements, preliminary versus authoritative manifests, destination checksum verification, and rejection when pre-copy and post-copy source manifests differ.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_ssh.py tests/test_migration_backup.py -v`

Expected: FAIL because SSH and migration modules are missing.

- [ ] **Step 3: Implement safe discovery/backup command generation**

Implement host-key acquisition and fingerprint comparison before connection. Invoke the system `ssh` client with `StrictHostKeyChecking=yes` and a project-local generated known-hosts file under `.nightwatchman/` mode `0600`. Allow SSH itself to prompt for the password on its controlling terminal; do not collect or relay the password in Python.

Add `migration discover --output-dir DIR [--backup-path PATH]`. It must use the existing Portainer session and API to capture the stack file, stack environment, stack ID/name/method, endpoint, inspected Gluetun/qBittorrent containers, effective image IDs, ports, mounts, network relationship, health, and bounded logs. SSH adds host path ownership, modes, sizes, and an optional preliminary config copy. Persist a versioned discovery bundle with this fixed schema:

```text
DIR/
  metadata.json                 # schema_version=1, IDs, timestamps, health, topology
  compose.manual.yaml           # exact Portainer stack file
  stack.env                     # exact rollback values, mode 0600, never printed
  containers/gluetun.json       # redacted inspect data
  containers/qbittorrent.json  # redacted inspect data and password-hash presence only
  logs/gluetun.log              # redacted, bounded
  logs/qbittorrent.log          # redacted, bounded
  host-paths.json               # SSH ownership/mode/size results
  manifest.sha256               # hashes of every artifact except itself
```

Create `DIR` with mode `0700` and every artifact with mode `0600`. Store secret values only in `stack.env`; redact them from all other artifacts. Validate the complete manifest before any consumer reads the bundle.

Add `migration backup-final --discovery-dir DIR --backup-path PATH` as an explicitly confirmed command that first verifies qBittorrent is stopped. Generate a source manifest, copy the config, generate a second source manifest, require the two source manifests to match, generate a destination manifest, and require it to match the stable source manifest. Each manifest includes relative paths, SHA-256, sizes, modes, ownership, and timestamps. Never copy the downloads directory.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_ssh.py tests/test_migration_backup.py -v`

Expected: PASS without opening a network connection.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/ssh.py src/nightwatchman/migration.py src/nightwatchman/cli.py tests/test_ssh.py tests/test_migration_backup.py
git commit -m "feat: add safe Unraid discovery and backups"
```

### Task 12: Generate and validate rollback bundles and cutover plans

**Files:**
- Modify: `src/nightwatchman/migration.py`
- Modify: `src/nightwatchman/cli.py`
- Create: `tests/test_migration_rollback.py`
- Create: `tests/test_migration_cutover.py`

- [ ] **Step 1: Write failing rollback and gate tests**

Feed a schema-version-1 discovery bundle fixture representing the current manual stack and container inspections. Assert the generated rollback Compose uses current image IDs, valid `ports`, the existing bind paths, shared Gluetun networking, and a protected environment file. Test rejection of unsupported schema versions, invalid bundle manifests, or missing required artifacts. Test that cutover planning refuses missing/invalid rollback validation, missing final backup, failed source-stability or destination-manifest verification, running qBittorrent, or absent explicit approval.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_migration_rollback.py tests/test_migration_cutover.py -v`

Expected: FAIL because rollback generation and gates are missing.

- [ ] **Step 3: Implement offline rollback rendering and cutover plan output**

Add `migration rollback-bundle --discovery-dir DIR --output-dir OUTPUT_DIR` and `migration plan-cutover --discovery-dir DIR --rollback-dir OUTPUT_DIR`. Both commands first verify the discovery schema and SHA-256 manifest. The rollback bundle must contain `compose.rollback.yaml`, `.env.rollback` mode `0600`, `manifest.json`, and captured redacted diagnostics derived from the discovery bundle. Validate with `docker compose --env-file .env.rollback -f compose.rollback.yaml config --quiet` through an injectable command runner.

`plan-cutover` must print numbered commands and checks but execute no stop, remove, create, restore, restart, or redeploy operation. Do not implement a one-command automatic cutover in this release.

- [ ] **Step 4: Run migration tests**

Run: `python -m pytest tests/test_migration_rollback.py tests/test_migration_cutover.py tests/test_migration_backup.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nightwatchman/migration.py src/nightwatchman/cli.py tests/test_migration_rollback.py tests/test_migration_cutover.py
git commit -m "feat: generate validated migration rollback plans"
```

## Phase 5: Production Deployment

### Task 13: Define the fail-closed Portainer Compose stack

**Files:**
- Create: `compose.yaml`
- Create: `.env.example`
- Create: `.env.test`
- Create: `tests/test_compose_contract.py`

- [ ] **Step 1: Write failing Compose contract tests**

Parse the rendered Compose JSON and assert four services, `/dev/net/tun`, `NET_ADMIN`, current Gluetun variables, no legacy `VPNSP`/`REGION`, no embedded credentials, qBittorrent `network_mode: service:gluetun`, health-gated dependency, ports published only by Gluetun, socket mounted only into the proxy, no raw socket in Nightwatchman, daemon state volume, log rotation, and pinned image references.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_compose_contract.py -v`

Expected: FAIL because `compose.yaml` is missing.

- [ ] **Step 3: Implement Compose and variable contracts**

Define `gluetun`, `qbittorrent`, `docker-socket-proxy`, and `nightwatchman`. Set socket-proxy permissions only for the read/write container endpoints required by discovery, inspect, start, stop, and restart. Put both app containers in the same Compose project and configure Nightwatchman with `DOCKER_HOST=tcp://docker-socket-proxy:2375`.

Use required-variable expansion (`${OPENVPN_USER:?required}`) for secrets and host paths. Include `TORRENTING_PORT` as well as `WEBUI_PORT`. Add JSON-file logging with bounded size/count.

- [ ] **Step 4: Validate Compose**

Run: `docker compose --env-file .env.test -f compose.yaml config --quiet`

Run: `python -m pytest tests/test_compose_contract.py -v`

Expected: both pass without pulling or starting images.

- [ ] **Step 5: Commit**

```bash
git add compose.yaml .env.example .env.test tests/test_compose_contract.py
git commit -m "feat: define Portainer VPN stack"
```

### Task 14: Package a minimal non-root image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `tests/test_image_contract.py`

- [ ] **Step 1: Write failing image contract test**

Assert the Dockerfile uses a pinned Python 3.13 base digest, installs only the package, creates an unprivileged user, declares `/state`, and launches `python -m nightwatchman.daemon` without a shell.

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_image_contract.py -v`

Expected: FAIL because `Dockerfile` is missing.

- [ ] **Step 3: Implement the image**

Use a multi-stage build only if it reduces the final image; otherwise use one pinned slim base. Copy `pyproject.toml` and `src/`, install with `pip --no-cache-dir`, create UID/GID 10001, own `/state`, set `USER 10001:10001`, and use exec-form `CMD`.

- [ ] **Step 4: Build and inspect locally**

Run: `docker build --tag nightwatchman:test .`

Run: `docker image inspect nightwatchman:test --format '{{.Config.User}} {{json .Config.Cmd}}'`

Expected: build succeeds; output starts with `10001:10001` and shows the daemon command.

Run: `python -m pytest tests/test_image_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore tests/test_image_contract.py
git commit -m "build: package Nightwatchman daemon image"
```

### Task 15: Add CI and immutable GHCR publication

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/publish.yml`
- Create: `tests/test_workflows.py`

- [ ] **Step 1: Write failing workflow contract tests**

Assert CI runs on pull requests and pushes, uses Python 3.13, runs Ruff, pytest with coverage, and Compose validation. Assert publication uses `GITHUB_TOKEN`, grants only `contents: read` and `packages: write`, builds from the committed Dockerfile, never publishes `latest`, publishes only the SHA tag for manual dispatch, and publishes both SHA and semantic release tags for a `v*` tag trigger.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_workflows.py -v`

Expected: FAIL because workflows are missing.

- [ ] **Step 3: Implement workflows**

Pin third-party actions by commit SHA. CI must not access Portainer, Unraid, or VPN credentials. The publish workflow runs on `v*` tag pushes and explicit workflow dispatch. Every run publishes `ghcr.io/ckonkel/nightwatchman:sha-<commit>`; only a validated `vMAJOR.MINOR.PATCH` Git tag additionally publishes the matching semantic version tag. A manual dispatch accepts no release-version input and therefore publishes no release tag.

- [ ] **Step 4: Run local workflow contract tests and full suite**

Run: `python -m pytest tests/test_workflows.py -v`

Run: `ruff check .`

Run: `python -m pytest --cov=nightwatchman --cov-report=term-missing`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/publish.yml tests/test_workflows.py
git commit -m "ci: validate and publish Nightwatchman"
```

## Phase 6: Documentation and Safe Verification

### Task 16: Write the operator runbook

**Files:**
- Modify: `README.md`
- Create: `docs/operations/portainer-deployment.md`
- Create: `docs/operations/migration-runbook.md`
- Create: `docs/operations/troubleshooting.md`
- Create: `docs/operations/discord-alerts-todo.md`
- Create: `tests/test_docs_contract.py`

- [ ] **Step 1: Write failing documentation contract tests**

Assert documentation names every required Portainer variable; warns to rotate the exposed PIA credential; explains HTTP token risk and HTTPS; documents login/logout, inspection, console confirmation, backup location rules, qBittorrent password-hash preservation, temporary-password behavior, health verification, rollback, image pinning, GitOps configuration, and deferred Discord events.

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_docs_contract.py -v`

Expected: FAIL because required documentation is missing.

- [ ] **Step 3: Write concise operational documentation**

Make README the entry point. Put exact Portainer Git-stack steps in `portainer-deployment.md`, the separately approved cutover checklist in `migration-runbook.md`, and Gluetun/qBittorrent diagnostics in `troubleshooting.md`. Never include actual usernames, passwords, JWTs, temporary qBittorrent passwords, or backup contents.

- [ ] **Step 4: Run docs and full verification**

Run: `python -m pytest tests/test_docs_contract.py -v`

Run: `ruff check .`

Run: `python -m pytest --cov=nightwatchman --cov-report=term-missing`

Run: `docker compose --env-file .env.test -f compose.yaml config --quiet`

Run: `git diff --check`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/operations tests/test_docs_contract.py
git commit -m "docs: add deployment and migration runbooks"
```

### Task 17: Perform read-only integration verification

**Files:**
- Create: `docs/operations/read-only-assessment-template.md`
- Modify: `README.md`

- [ ] **Step 1: Create the assessment template before connecting**

Include fields for Portainer version/environment, stack metadata, redacted effective configuration, image IDs, container health, Gluetun root-cause logs, qBittorrent config ownership, password-hash presence only, Web UI result, bind-path sizes, and recommended remediation. Explicitly prohibit secret values and live mutations.

- [ ] **Step 2: Run all offline gates before live reads**

Run: `ruff check .`

Run: `python -m pytest --cov=nightwatchman --cov-report=term-missing`

Run: `docker compose --env-file .env.test -f compose.yaml config --quiet`

Expected: all pass.

- [ ] **Step 3: Ask the user to run local interactive authentication**

Run by the user in their terminal:

```bash
./nightwatchman login --url http://192.168.50.101:9000 --environment 2
```

Expected: silent password prompt followed by confirmation that `.env.portainer.local` was saved with mode `0600` and an expiration time; no credential is printed.

- [ ] **Step 4: Run only approved read-only Portainer discovery**

Run: `./nightwatchman status`

Run: `./nightwatchman stack vpn-qtorrent`

Run: `./nightwatchman inspect gluetun`

Run: `./nightwatchman health gluetun`

Run: `./nightwatchman logs gluetun --tail 500`

Run: `./nightwatchman inspect qbittorrent`

Run: `./nightwatchman logs qbittorrent --tail 200`

Expected: redacted diagnostic output only; no container state changes.

- [ ] **Step 5: Request separate approval before SSH discovery**

After approval, run `./nightwatchman migration discover --output-dir .nightwatchman/discovery/current --backup-path <user-approved-absolute-path>`. The user enters the Unraid root password directly into SSH's prompt. Expected: pinned fingerprint verified, protected schema-version-1 discovery bundle and read-only ownership/path manifest created, downloads untouched.

- [ ] **Step 6: Record findings without secrets and commit only the template/runbook improvements**

Do not commit `.env.portainer.local`, `.nightwatchman/`, rollback bundles, backup manifests containing sensitive paths, or raw live logs. If findings require design changes, stop and amend the spec/plan before implementation continues.

```bash
git add docs/operations/read-only-assessment-template.md README.md
git commit -m "docs: add read-only assessment workflow"
```

## Final Verification Gate

- [ ] Run `ruff check .` and confirm exit code 0.
- [ ] Run `python -m pytest --cov=nightwatchman --cov-report=term-missing` and confirm all tests pass.
- [ ] Run `docker compose --env-file .env.test -f compose.yaml config --quiet` and confirm exit code 0.
- [ ] Run `docker build --tag nightwatchman:test .` and confirm a successful non-root image build.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run `git status --short` and confirm only intentional changes remain.
- [ ] Use `superpowers:requesting-code-review` for an implementation review.
- [ ] Use `superpowers:verification-before-completion` before claiming the implementation is ready.
- [ ] Do not perform the manual-to-Git cutover in this implementation plan. Prepare the validated artifacts, present the read-only assessment, and request explicit live-migration authorization as a separate operation.
