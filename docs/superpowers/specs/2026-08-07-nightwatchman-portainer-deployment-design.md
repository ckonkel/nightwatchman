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
2. `qbittorrent` shares `gluetun`'s network namespace and therefore has no independent route around the VPN.
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
5. Nightwatchman records the trigger and current target states.
6. Nightwatchman stops qBittorrent.
7. Nightwatchman restarts Gluetun.
8. Nightwatchman waits up to five minutes for Gluetun to become healthy.
9. Once Gluetun is healthy, Nightwatchman starts qBittorrent and begins the ten-minute cooldown.
10. If Gluetun does not recover, qBittorrent remains stopped and Nightwatchman emits a prominent failure event.

Leaving qBittorrent stopped after a failed VPN recovery is the fail-closed behavior. Although qBittorrent shares Gluetun's namespace, Nightwatchman will not assume that an unhealthy or partially initialized VPN is safe.

Nightwatchman must cope safely with its own restart. It will inspect actual container state before every action, make actions idempotent where possible, and never start qBittorrent unless Gluetun currently reports healthy.

## Diagnostic Container Access

The CLI will use Portainer as a gateway to Docker's exec API to create, start, stream, and clean up exec sessions. Interactive shell access and arbitrary commands are allowlisted to the Gluetun and qBittorrent services by default.

Diagnostic access may inspect:

- Processes and container state
- Routes and network interfaces
- DNS resolution
- Gluetun firewall state
- Health endpoints
- Application configuration and logs

Interactive shells require explicit confirmation. Diagnostic output will be redacted for known secrets. The CLI will not silently edit files inside containers. Live container changes are ephemeral; durable remediation belongs in this repository and must be deployed through Portainer.

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
