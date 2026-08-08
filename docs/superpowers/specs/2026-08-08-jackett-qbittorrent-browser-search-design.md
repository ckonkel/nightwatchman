# Jackett and qBittorrent Browser Search Design

## Goal

Add torrent discovery to the existing qBittorrent Web UI so a user can search configured indexers in a browser and send a selected result directly to the existing qBittorrent downloader without bypassing Gluetun or changing qBittorrent's data paths.

## Selected Approach

Add Jackett to the existing Git-backed `vpn-qtorrent` stack and have it share Gluetun's network namespace alongside qBittorrent. Install qBittorrent's maintained Jackett search plugin only after the live stack is approved and healthy.

This modifies the existing stack instead of creating a separate Jackett stack for three concrete reasons:

- qBittorrent can reach Jackett at `http://127.0.0.1:9117`, avoiding LAN firewall exceptions and cross-stack Docker networking.
- Jackett's indexer requests use the same PIA tunnel and Gluetun kill switch as qBittorrent.
- Port 9117 must be published by Gluetun when Jackett shares its network namespace, so a separate stack would still depend on changes to `vpn-qtorrent`.

Jackett remains operationally independent: a Jackett failure makes searches fail but does not stop an already-running qBittorrent container or alter downloads.

## Browser Search Flow

1. The user opens the qBittorrent Web UI in a browser.
2. The qBittorrent Search tab invokes the maintained Jackett Python search plugin.
3. The plugin calls Jackett at `http://127.0.0.1:9117` with the locally stored Jackett API key.
4. Jackett queries the configured indexers through Gluetun and returns normalized results.
5. Selecting a result in qBittorrent sends its magnet link or torrent payload to the existing qBittorrent session.
6. qBittorrent downloads and seeds through Gluetun using the existing `/config` and `/downloads` mounts.

Jackett supplies search results. It does not replace qBittorrent and does not require access to the downloads directory.

## Compose Changes

`compose.yaml` gains a `jackett` service with:

- Image reference from required `JACKETT_IMAGE`.
- `container_name: jackett`.
- `restart: unless-stopped`, matching Gluetun and qBittorrent.
- `network_mode: service:gluetun`.
- A healthy-Gluetun dependency.
- Existing `PUID`, `PGID`, and `TZ` variables.
- `AUTO_UPDATE=false`, keeping upgrades controlled by the container image reference.
- A single persistent `${JACKETT_CONFIG_PATH}:/config` bind.
- The same bounded `json-file` logging policy as the existing services.
- A bounded, secret-free HTTP readiness check against Jackett on localhost. It requests `/UI/Dashboard` without credentials and treats an HTTP 2xx/3xx/401/403 response as ready; connection failures, timeouts, status 000, and 5xx responses fail. This remains valid before and after an admin password is configured.

Gluetun maps Jackett's fixed container port 9117 to the required host-published `${JACKETT_PORT}` TCP port. `JACKETT_PORT` controls only the LAN-facing host port; the qBittorrent plugin always uses `http://127.0.0.1:9117` inside the shared namespace. Jackett declares no ports of its own because Compose forbids port publishing with service network mode.

The default live values will be documented as:

```text
JACKETT_PORT=9117
JACKETT_CONFIG_PATH=/mnt/ssd1tb-asus/appdata/jackett/config
```

The existing stack cannot supply a Jackett image through discovery because Jackett is not deployed yet. The deployable LinuxServer tag or digest will instead be selected from the official image registry/release metadata, reviewed during implementation, and recorded before deployment. No floating or invented version will be silently substituted for the approved value.

## qBittorrent Compatibility and Plugin State

The LinuxServer qBittorrent image includes Python for search-plugin support, but the live qBittorrent version and Web UI behavior must be verified before deployment. The mandatory predeployment checks are:

- Record the current qBittorrent application and image versions.
- Confirm the installed version is documented to support Web UI search, the Web UI exposes the Search tab, and its plugin manager is available. If a safe existing search plugin is already configured, a result-download test may also be performed; otherwise that functional test occurs after the Jackett plugin is installed.
- Confirm port 9117 is unused.
- Resolve the discrepancy between the user-reported qBittorrent port 8080 and the older repository example using 9081.

The Jackett plugin and its configuration are runtime application state, not Git-managed Compose data. The plugin uses a `jackett.json` file under qBittorrent's persistent `/config` tree containing the Jackett URL and API key. That file must never be copied into the repository, discovery artifact, test fixture, logs, or documentation.

After Jackett and its plugin are configured, the mandatory functional test searches a legal test term such as a Linux distribution and adds a selected result through the Web UI. If the current qBittorrent Web UI cannot perform the browser workflow, deployment stops and rolls back the plugin/configuration changes. A qBittorrent upgrade is a separate decision requiring an image/version review and explicit approval; it is not bundled into this change.

## Security and Privacy

- Jackett's UI is published only on the Unraid host's trusted-LAN port 9117 and must not be router-forwarded.
- Jackett external access requires an admin password before indexers or the API key are configured.
- The Jackett API key, indexer credentials, cookies, passkeys, qBittorrent credentials, and password hashes remain outside Git.
- The qBittorrent Jackett plugin stores its API key only in the existing protected qBittorrent config bind.
- Jackett indexer traffic shares Gluetun's namespace and therefore exits through PIA. Gluetun's firewall blocks external egress during tunnel loss.
- If Gluetun stops or is recreated, qBittorrent and Jackett may require restart because their shared network namespace depends on Gluetun. The stack dependency and deployment checks account for this.
- qBittorrent does not depend on Jackett health; a Jackett failure must not prevent qBittorrent from starting or continuing existing transfers.
- Neither Jackett nor its health check receives the VPN credentials.

## Backup and Restore

Before the first live change:

1. Stop qBittorrent cleanly during the approved maintenance window.
2. Create and verify the existing complete qBittorrent config backup. This captures the pre-plugin state.
3. Back up an existing Jackett config directory if one is unexpectedly present; never overwrite it blindly.
4. Record the current Gluetun and qBittorrent image references, exact deployed Git revision, rendered sanitized stack definition, and complete Portainer environment-variable set in approved secret storage outside Git.

After configuration, back up `${JACKETT_CONFIG_PATH}` while Jackett is stopped. Jackett's config backup is independent from qBittorrent's established backup and does not include downloads.

Restore uses the same image reference and config bind. If Jackett state is damaged, stop Jackett, preserve the damaged directory for diagnosis, and restore the last verified Jackett config copy.

## Upgrade Process

Jackett's in-container updater stays disabled. An upgrade consists of:

1. Back up Jackett config.
2. Review upstream and LinuxServer release notes.
3. Update `JACKETT_IMAGE` to a reviewed immutable reference.
4. Render and validate Compose offline.
5. Redeploy the stack during an approved window.
6. Verify health, UI authentication, indexer tests, qBittorrent search, VPN egress, and restart behavior.

The qBittorrent Jackett plugin is updated independently through qBittorrent's plugin manager after its source is reviewed. Plugin updates must preserve the local `jackett.json` configuration.

## Failure Behavior and Rollback

- Jackett failure: qBittorrent search returns no Jackett results; existing torrents and downloads continue.
- Indexer failure: only that indexer's test/search fails; qBittorrent remains operational.
- VPN tunnel failure: Gluetun blocks Jackett and qBittorrent external traffic. LAN UI behavior may remain available while Gluetun is running, but no successful external search or download is expected.
- Gluetun container loss: both namespace-sharing services lose their network dependency and are restarted after Gluetun is restored healthy.

Whenever Gluetun is recreated, both namespace-sharing services are recreated or restarted after the replacement Gluetun reports healthy. The full-stack Compose deployment is the normal mechanism; qBittorrent and Jackett must not be left attached to the removed namespace.

Rollback removes or disables the qBittorrent Jackett plugin, then redeploys the exact previously recorded Git revision with its prior Portainer environment-variable set and image references. That revision removes the Jackett service and Gluetun's 9117 publication. The Jackett config directory is retained until rollback is verified. The existing qBittorrent config/download binds are never deleted or replaced. If plugin installation altered qBittorrent state unexpectedly, restore the verified predeployment qBittorrent config backup while qBittorrent is stopped.

## Documentation

The repository runbook will document:

- Required Jackett Compose variables and Portainer values.
- The browser-to-qBittorrent-to-Jackett data flow.
- Predeployment compatibility checks.
- Jackett first-run authentication and indexer setup.
- Installation of the maintained qBittorrent Jackett plugin without exposing its API key.
- Search, VPN-egress, persistence, restart, backup, upgrade, and rollback checks.

Instructions will use placeholders for every credential. They will not suggest placing a populated environment file or `jackett.json` in Git.

## Focused Validation

- Extend the existing Compose contract tests for the third service, required variables, namespace sharing, Gluetun-only port publication, config isolation, logging, and health check.
- Render Compose using safe non-secret `.env.test` values.
- Scan tracked changes for populated credentials, API keys, password hashes, and local environment files.
- Run the existing Python unit and syntax checks to prove unrelated discovery/backup behavior remains intact.
- Do not make automated requests to Portainer, Unraid, indexers, or qBittorrent during offline validation.

## Acceptance Criteria

1. Compose defines Gluetun, qBittorrent, and Jackett while preserving every existing qBittorrent environment variable and bind mount.
2. qBittorrent and Jackett share only Gluetun's network namespace; only Gluetun publishes Web UI, Jackett, and torrent peer ports.
3. Jackett persists only `/config`, uses bounded logging, has a focused readiness check, and has no downloads mount.
4. All images, ports, IDs, timezone, paths, and credentials remain variables; no populated secrets are committed.
5. Documentation supports browser searching directly in qBittorrent and clearly gates plugin/API-key configuration as a live step.
6. Offline focused tests pass.
7. No live Portainer or Unraid mutation occurs until the user explicitly approves deployment of the integrated `develop` revision.
