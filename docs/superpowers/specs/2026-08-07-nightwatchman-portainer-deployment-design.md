# Nightwatchman Portainer Deployment Design

## Purpose

Nightwatchman will make this repository the source of truth for a Git-backed Portainer deployment of Gluetun and qBittorrent. It will correct the current stack configuration, monitor persistent VPN failures, recover the two-container application safely, and provide a local administrative CLI for inspecting Portainer and entering the application containers during diagnosis.

The initial release will log recovery events but will not send notifications. Discord webhook alerts are explicitly deferred.

## Deployment Context

- Portainer URL: configurable, initially `http://192.168.50.101:9000`
- Portainer environment: configurable, initially environment ID `2`
- Stack name: `vpn-qtorrent`
- Deployment method: Portainer Git-backed stack
- VPN provider: Private Internet Access using OpenVPN
- qBittorrent networking: shares Gluetun's network namespace

Portainer stack environment variables will supply secrets and host-specific paths. Secrets and operational tokens must never be committed.

## Repository Components

### Compose deployment

`compose.yaml` will define four services:

1. `gluetun` establishes the VPN, owns the shared network namespace, publishes the qBittorrent Web UI and peer ports, and exposes its Docker health state.
2. `qbittorrent` shares `gluetun`'s network namespace and therefore has no independent route around the VPN. Its Compose dependency requires Gluetun's health check to pass before qBittorrent starts.
3. `docker-socket-proxy` exposes only the Docker API capabilities Nightwatchman needs instead of mounting the raw Docker socket into Nightwatchman.
4. `nightwatchman` observes Gluetun health and executes the approved recovery state machine.

The deployment will use current Gluetun configuration names, including `VPN_SERVICE_PROVIDER` and `SERVER_REGIONS`, and explicitly map `/dev/net/tun`. The malformed `ports:a` key, blank timezone, legacy `VPNSP` and `REGION` names, and embedded credentials in the existing stack will not be carried forward.

`.env.example` will document all required settings without usable secrets. At minimum, Portainer must supply:

- `OPENVPN_USER`
- `OPENVPN_PASSWORD`
- `TZ`
- `QBITTORRENT_CONFIG_PATH`
- `DOWNLOADS_PATH`

Provider region, Web UI port, peer port, PUID, and PGID will also be configurable where doing so does not weaken validation. Host paths will have no committed machine-specific defaults.

Images will use explicit version tags or immutable digests where practical. Docker log rotation limits will prevent unbounded disk use.

### Nightwatchman recovery service

Nightwatchman will implement a small, testable state machine rather than using a generic autoheal container. Its responsibilities are limited to observing Gluetun and coordinating recovery of this stack.

Configuration defaults:

- Persistent unhealthy threshold: 2 minutes
- Post-remediation cooldown: 10 minutes
- Gluetun recovery timeout: 5 minutes
- Initial notifications: structured container logs only

The service will identify managed containers by Compose labels and expected project/service names rather than fragile container IDs. It will reject ambiguous or missing targets.

Nightwatchman and the local CLI will use Python 3.13 and share a tested Portainer/Docker API client package. GitHub Actions will build and publish the recovery image to `ghcr.io/ckonkel/nightwatchman`, tagged with both a release version and an immutable commit SHA. Production Compose deployments will reference a pinned published tag or digest; Portainer will not build the production image from the Git checkout.

Nightwatchman will poll health every five seconds. It will persist the continuous-failure start time, remediation-attempt time, and cooldown deadline in a small named volume using atomic state-file replacement. On startup it will reconcile persisted timing data with actual container state before acting. Host wall-clock timestamps will be used for persisted deadlines, with invalid or future-skewed state handled conservatively and logged.

### Local Portainer CLI

The repository will provide an executable `nightwatchman` helper for administrative access. It is separate from the deployed recovery service and communicates with Portainer's HTTP API.

Planned commands include:

- `login`
- `logout`
- `status`
- `environments`
- `stacks`
- `stack vpn-qtorrent`
- `containers`
- `inspect gluetun`
- `inspect qbittorrent`
- `health gluetun`
- `logs gluetun`
- `logs qbittorrent`
- `shell gluetun`
- `shell qbittorrent`
- `exec gluetun <command>`
- `exec qbittorrent <command>`

Read-only inspection is the default. Commands that change live state, including restart or redeploy operations, must be explicitly selected and require confirmation. The CLI will never make a live repair implicitly as a side effect of inspection.

Before the first session file exists, `login` will accept `--url` and `--environment` options. They default to `http://192.168.50.101:9000` and environment ID `2`; successful login stores the selected values with the JWT for later commands.

## Authentication and Secret Handling

`nightwatchman login` will silently prompt for a Portainer username and password and send them directly to `POST /api/auth`. The password will never be stored and will be removed from process state as soon as practical.

The returned eight-hour JWT will be stored in the repository-local `.env.portainer.local` with file mode `0600`. This file will contain only the Portainer connection settings, environment ID, and temporary JWT. It will be excluded by `.gitignore`, never printed, and removed by `nightwatchman logout`.

An expired or rejected JWT will produce a clear login-required error. The CLI must redact authorization headers, tokens, VPN credentials, and sensitive environment values from diagnostics.

The current Portainer endpoint uses unencrypted HTTP, so credentials and JWTs are not protected in transit. The README will prominently recommend enabling HTTPS. Until then, only short-lived JWT authentication on the trusted local network is in scope; a persistent administrator API key is not.

## Recovery Data Flow

Nightwatchman polls the Docker API through the socket proxy and evaluates Gluetun's Docker health status.

1. A healthy result resets the continuous-failure timer.
2. An unhealthy result starts or advances the timer.
3. Recovery before two continuous minutes cancels remediation and records no failure action.
4. Two continuous unhealthy minutes trigger remediation unless the ten-minute cooldown is active.
5. Nightwatchman records the trigger and current target states, persists the attempt time, and starts the ten-minute cooldown immediately.
6. Nightwatchman stops qBittorrent.
7. Nightwatchman restarts Gluetun.
8. Nightwatchman waits up to five minutes for Gluetun to become healthy.
9. Once Gluetun is healthy, Nightwatchman starts qBittorrent. The cooldown remains measured from the beginning of the attempt.
10. If Gluetun does not recover, qBittorrent remains stopped and Nightwatchman emits a prominent failure event.

Leaving qBittorrent stopped after a failed VPN recovery is the fail-closed behavior. Although qBittorrent shares Gluetun's namespace, Nightwatchman will not assume that an unhealthy or partially initialized VPN is safe.

Nightwatchman must cope safely with its own restart. It will inspect actual container state before every action, make actions idempotent where possible, and never start qBittorrent unless Gluetun currently reports healthy.

## Diagnostic Container Access

The CLI will use Portainer as a gateway to Docker's exec API to create, start, stream, and clean up exec sessions. Interactive shell access and arbitrary commands are allowlisted to the Gluetun and qBittorrent services by default. qBittorrent console sessions will default to `/bin/bash` as `root`; Gluetun sessions will default to `/bin/sh` as `root` because Bash is not assumed to be installed.

Diagnostic access may inspect:

- Processes and container state
- Routes and network interfaces
- DNS resolution
- Gluetun firewall state
- Health endpoints
- Application configuration and logs

Every interactive `shell` and arbitrary `exec` invocation requires explicit confirmation because the CLI cannot reliably infer whether a command will mutate state. Named read-only operations such as `logs`, `inspect`, and `health` do not require confirmation. Diagnostic output will be redacted for known secrets. The CLI will not silently edit files inside containers. Live container changes are ephemeral; durable remediation belongs in this repository and must be deployed through Portainer.

## Migration from the Existing Manual Stack

Portainer does not expose a supported conversion from a Web Editor stack to a Git-backed stack. Migration will therefore be a controlled replacement that preserves bind-mounted application data and presents a short planned outage.

### Discovery and backup access

Portainer API and exec operations will capture the existing stack definition, environment variables, container inspections, image identifiers, health state, and logs. Container console diagnostics will use the Portainer exec path described above, not SSH into the containers.

SSH to the Unraid host at `192.168.50.101` as `root` will be used only for host filesystem discovery, backup, ownership checks, and restore. The server's verified ED25519 host-key fingerprint is `SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I`. The user will enter the Unraid root password silently for this operation. The password must not be stored, echoed, included on a command line, written to shell history, or placed in `.env.portainer.local`.

The migration tooling will refuse SSH if the advertised host key does not match the verified fingerprint. Host commands will be auditable and separated into read-only discovery, backup, and later approved mutation phases.

### State that must be preserved

During read-only discovery, the process may create a preliminary backup of the entire qBittorrent config bind mount and record ownership, permissions, size, and a manifest. This includes torrent resume state, preferences, cookies, Web UI username, and `WebUI\Password_PBKDF2`. Because qBittorrent can change these files while running, this preliminary copy is not an authoritative rollback artifact. It will verify the downloads bind mount but will not copy, delete, or rewrite downloaded media as part of stack migration.

Immediately after qBittorrent stops cleanly and before the old stack is removed, the process will create and verify a final offline config backup. That final backup and its checksum manifest are the authoritative restore point. Cutover cannot continue if qBittorrent does not stop cleanly, the backup fails, checksums fail, or the source changes during final verification.

The user will provide a dedicated Unraid backup path outside the active qBittorrent config directory. Backup directories will be owned by root with mode `0700`, and files containing configuration, manifests, stack environment, or credentials will use mode `0600`. Sensitive rollback material will never enter Git or command output. Backups will remain until post-migration verification succeeds and the user separately approves cleanup; the tooling will not delete them automatically.

The existing Web UI URL, port, image version, configured username, password-hash presence, and login behavior will be recorded. The migration must not delete or reset the password hash. If Web UI authentication is already broken, password recovery is a separate, explicitly approved repair performed only after backup. On qBittorrent 4.6.1 or later, removing an invalid password hash causes a temporary administrator password to be emitted in container logs; that credential must be treated as a secret and replaced through qBittorrent settings.

### Cutover and rollback

Before cutover, the captured legacy stack will be converted into a rollback bundle that preserves its effective service configuration, current image identifiers, environment, ports, mounts, and network relationship while fixing serialization errors that would prevent redeployment. The bundle will be rendered with its required environment and validated using Docker Compose without starting containers. A rollback dry run must resolve the expected services, mounts, ports, and images. Destructive cutover cannot begin until this deployable rollback bundle and its protected environment file pass validation.

After the new Compose model, image, Portainer variables, preliminary discovery, and rollback inputs have been validated:

1. Stop qBittorrent cleanly and wait for it to exit.
2. Create and verify the authoritative offline qBittorrent config backup and final manifest.
3. Stop the remaining existing manual stack services.
4. Remove only its Portainer stack record, containers, and project network. Do not remove bind-mounted data or backup artifacts.
5. Create a Git-backed stack named `vpn-qtorrent` with the same persistent host paths, ports, and qBittorrent application state.
6. Enforce the Compose health dependency so qBittorrent does not start until Gluetun is healthy.
7. Verify from inside qBittorrent that public egress uses the VPN, and verify DNS, Gluetun firewall behavior, Web UI access, stored authentication, torrent state, and download paths.

If a mandatory check fails, stop and remove only the new stack containers/network, then redeploy the prevalidated rollback bundle with its protected environment and pinned original image references. Restore qBittorrent config from the authoritative offline backup only if verification shows the live config was changed or damaged. Every live stop, removal, creation, restore, restart, or redeployment requires separate user authorization immediately before execution.

## Error Handling

The CLI will distinguish unreachable servers, authentication failures, authorization failures, expired sessions, missing environments, missing or ambiguous containers, API errors, malformed responses, and failed exec sessions. Errors will be actionable and must not include secret material.

The recovery service will distinguish transient health failures, cooldown suppression, target discovery failure, Docker API failure, stop/restart/start failure, and VPN recovery timeout. It will emit structured events suitable for later notification integrations.

When Docker or Portainer state is uncertain, the system will fail closed: it will not start qBittorrent unless it can positively verify that Gluetun is healthy.

## Testing and Validation

Local and CI checks will validate the Compose model and verify that required variables are documented.

Automated recovery-service tests will cover:

- Recovery before the two-minute threshold
- Persistent unhealthiness triggering exactly one remediation
- Continuous-time semantics rather than accumulated intermittent failures
- Ten-minute cooldown suppression
- Stop qBittorrent, restart Gluetun, wait, then start qBittorrent ordering
- Gluetun recovery timeout leaving qBittorrent stopped
- Missing or ambiguous managed containers
- Docker API failures at each action boundary
- Nightwatchman restarting during remediation
- Idempotent handling of already-stopped or already-running targets

CLI tests will use mocked HTTP and exec-session responses and cover:

- Login without password persistence
- Session-file permissions and logout cleanup
- JWT expiry and rejection
- URL and environment selection
- Stack and container discovery
- Logs, inspect, and health requests
- Exec creation, start, streaming, and cleanup
- Confirmation requirements for interactive or mutating actions
- Secret and token redaction
- SSH host-key pinning and silent password handling
- Migration discovery, backup manifests, cutover gates, and rollback command generation without executing against a live host

No automated test will target the live Portainer instance. Live verification will begin with read-only API calls. Any restart, redeployment, or other live mutation requires separate user authorization.

## Operations and Documentation

The README will document:

- Rotating the VPN credential exposed during initial discovery
- Configuring Portainer Git deployment and environment variables
- Enabling GitOps updates intentionally
- Logging in and out of the local CLI
- Read-only inspection and container console workflows
- Recovery behavior, thresholds, and cooldown
- Manual rollback
- Manual-stack-to-Git migration, backup verification, and rollback
- qBittorrent Web UI credential preservation and separately approved recovery
- Diagnosing Gluetun without bypassing the VPN
- Enabling HTTPS for Portainer
- Image update policy

The repository will remain the source of truth. Manual edits inside Portainer's Compose editor or inside running containers are not durable and should be translated into reviewed repository changes.

## Deferred Work

- Add optional Discord webhook alerts for remediation start, success, timeout, and cooldown suppression.
- Consider additional notification providers only after the structured event interface is stable.
- Consider replacing administrator login with a least-privilege Portainer account after initial evaluation confirms the minimum permissions required.

## Acceptance Criteria

The design is implemented successfully when:

1. Portainer can deploy the complete stack from this Git repository using environment-supplied credentials and host paths.
2. qBittorrent has no independent network path outside Gluetun.
3. A Gluetun failure lasting less than two minutes causes no restart.
4. A continuous failure lasting two minutes triggers the defined coordinated recovery once.
5. The ten-minute cooldown prevents restart loops.
6. qBittorrent remains stopped if Gluetun cannot become healthy within five minutes.
7. The local CLI maintains an eight-hour JWT in a gitignored `0600` file without storing the administrator password.
8. The CLI can inspect and open confirmed console sessions in only the two managed containers by default.
9. Automated tests and Compose validation pass without accessing the live home server.
10. Documentation explains deployment, security limitations, operation, and rollback.
11. The manual Portainer stack can be replaced by the Git-backed stack without deleting qBittorrent configuration, torrent state, or downloaded media.
12. Pre- and post-migration checks verify qBittorrent Web UI authentication state and preserve its configured password hash.
