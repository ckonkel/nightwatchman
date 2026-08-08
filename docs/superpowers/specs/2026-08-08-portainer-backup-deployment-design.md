# Portainer Backup and Git Deployment Design

## Goal

Make this repository deploy the existing Gluetun and qBittorrent application from Git in Portainer while preserving qBittorrent settings, torrent state, Web UI authentication, and downloads.

## Active Scope

The first release contains:

- A two-service `compose.yaml` for Gluetun and qBittorrent.
- Portainer environment-variable documentation with no committed secrets or server-specific paths.
- A read-only Portainer discovery helper using an eight-hour JWT stored in gitignored `.env.portainer.local` mode `0600`.
- An Unraid backup helper that connects to `root@192.168.50.101`, verifies the approved SSH fingerprint, prompts for the password through SSH, and creates a checksummed qBittorrent config archive at a user-selected host path.
- A manual-stack-to-Git migration and rollback runbook.
- A few focused tests for Compose invariants, secret handling, and non-destructive backup command construction.

## Deferred Scope

- Automatic unhealthy-container remediation.
- Docker socket proxy and custom watcher container.
- Discord or other alerts.
- Automatic live cutover or rollback.
- Publishing a custom image to GHCR.

The paused `feature/nightwatchman-implementation` branch is not merged into this work.

## Compose Deployment

`compose.yaml` defines only `gluetun` and `qbittorrent`.

- qBittorrent uses `network_mode: service:gluetun` and has no independent network path.
- Gluetun maps `/dev/net/tun`, has `NET_ADMIN`, and publishes the Web UI and peer TCP/UDP ports.
- qBittorrent waits for Gluetun health before starting.
- Current Gluetun variable names are used: `VPN_SERVICE_PROVIDER`, `OPENVPN_USER`, `OPENVPN_PASSWORD`, and `SERVER_REGIONS`.
- qBittorrent uses `WEBUI_PORT` and `TORRENTING_PORT` consistently with the published ports.
- Required Portainer variables include image references, credentials, timezone, UID/GID, ports, region, config path, and downloads path.
- Image references are variables so discovery can preserve the currently installed versions/digests before an intentional upgrade.
- Log rotation is bounded.

## Discovery and Authentication

The local helper prompts silently for Portainer username/password and calls `/api/auth`. It stores only URL, environment ID, and the temporary JWT in `.env.portainer.local` mode `0600`.

Read-only discovery captures:

- Portainer version and selected environment.
- Existing `vpn-qtorrent` stack metadata and Compose content.
- Redacted stack variables.
- Gluetun and qBittorrent `Config.Image` deployment references, local image IDs, available repository digests, mounts, ports, state, health, and bounded logs. Discovery maps the deployable references to `GLUETUN_IMAGE` and `QBITTORRENT_IMAGE`; immutable repository digests are preferred when the current local image exposes them.
- Presence—but never the value—of qBittorrent's Web UI password hash.

Discovery artifacts live under gitignored `.nightwatchman/` mode `0700`/`0600`. Secrets are redacted. The helper does not restart, stop, remove, create, update, or redeploy anything.

Stack Compose content is sanitized as text before persistence, including list-style and mapping-style assignments for password, token, authorization, `OPENVPN_USER`, and `OPENVPN_PASSWORD`. Tests use literal embedded credentials and prove they never reach disk.

Password-hash presence is checked with the read-only Docker archive GET endpoint through Portainer: `/api/endpoints/{id}/docker/containers/{container}/archive?path=/config/qBittorrent/qBittorrent.conf`. The helper reads the returned tar stream in memory, reports only whether `WebUI\Password_PBKDF2` exists, and never persists the configuration contents or hash.

## Unraid Backup

The backup helper verifies the server's ED25519 fingerprint:

`SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I`

It writes the exact scanned ED25519 public key to a dedicated mode-`0600` known-hosts file only after its fingerprint matches, then connects with `StrictHostKeyChecking=yes`, that explicit `UserKnownHostsFile`, and ED25519 host-key restriction. This binds the backup connection to the verified key rather than performing an independent scan-and-connect.

It then lets the system SSH client prompt for the Unraid root password. The password is never accepted as a command argument, environment variable, or file.

The helper validates absolute source and destination paths, rejects a destination inside the source tree, and rejects whitespace, control characters, shell metacharacters, traversal components, and characters outside a documented safe path alphabet. Remote arguments are still encoded with `shlex.quote` and passed to a fixed script. It creates a timestamped archive plus SHA-256 checksum on Unraid. It backs up the entire qBittorrent config path, including resume state and `WebUI\Password_PBKDF2`. It never copies, deletes, or rewrites downloaded media.

Before migration, qBittorrent must be stopped cleanly and the final offline archive verified. Running the backup helper is a separately approved live write.

## Migration

Portainer cannot convert the Web Editor stack to Git in place. The runbook therefore uses a controlled replacement:

1. Run read-only discovery and record current images/settings.
2. Rotate the VPN password exposed during initial discussion.
3. Validate the new Compose model with non-secret test values.
4. Stop qBittorrent cleanly.
5. Create and verify the final offline config backup.
6. Capture and validate a rollback Compose file before removing anything.
7. Remove only the old stack record/containers/network, never bind-mounted data.
8. Create the Git-backed stack with the same name, paths, ports, and preserved image references.
9. Verify Gluetun health, VPN egress from qBittorrent, DNS, Web UI authentication, torrent state, and downloads.
10. Roll back using the captured stack and backup if any mandatory check fails.

Every live mutation requires explicit approval at execution time.

## Validation

- Render `compose.yaml` with safe test values and verify its security invariants.
- Unit-test redaction/session file permissions and Portainer request construction without network access.
- Unit-test backup validation and SSH command construction without opening SSH.
- Run shell/Python lint or syntax checks.
- Never run automated tests against Portainer or Unraid.

## Acceptance Criteria

1. The repository contains a valid two-service Portainer Compose deployment.
2. No usable secret or machine-specific path is committed.
3. Read-only discovery can preserve the existing deployment facts and redact secrets.
4. The backup helper creates a verifiable qBittorrent config archive without touching downloads.
5. Documentation gives a gated migration and rollback procedure.
6. Automatic remediation and alerts remain explicitly deferred.
