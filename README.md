# nightwatchman

Git-managed Portainer deployment for an existing Gluetun, qBittorrent, and Jackett stack on Unraid. The repository currently provides:

- a three-service Compose file with qBittorrent and Jackett sharing Gluetun's network;
- torrent search in the qBittorrent browser Web UI through the maintained Jackett search plugin;
- read-only Portainer discovery with a temporary local API session;
- a fingerprint-pinned SSH backup of the complete qBittorrent config directory; and
- a controlled runbook for updating and rolling back the Git-backed stack.

It does not automatically change, restart, or redeploy the live stack. Health recovery and Discord alerts are deferred in [TODO.md](TODO.md).

## Prerequisites

- Portainer access to the target environment and stack;
- Python 3.12 or newer on the workstation;
- `ssh`, `ssh-keyscan`, and `ssh-keygen` for backup execution;
- root SSH access to Unraid at `192.168.50.101`;
- the repository pushed to a Git URL that Portainer can read; and
- a maintenance window for the final backup and stack replacement.

The backup helper pins this verified Unraid ED25519 fingerprint:

```text
SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I
```

If Unraid's SSH host key intentionally changes, stop and update the pin in code only after independently verifying the new fingerprint at the server console.

## Configure without committing secrets

For local Compose validation, copy the template and restrict its permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Fill in `.env` locally. It is ignored by Git. Never commit VPN credentials, Portainer tokens, qBittorrent password hashes, or a populated environment file. Rotate any credential that has previously appeared in Compose text, chat, terminal history, or Git.

For Portainer's Git deployment, enter the same variables in the stack's **Environment variables** section instead of committing `.env`. Preserve the live values, especially:

```text
CONFIG_PATH=/mnt/ssd1tb-asus/appdata/vpn-qtorrent/config
DOWNLOADS_PATH=/mnt/user/media/_inbox
WEBUI_PORT=8080
TORRENTING_PORT=6881
JACKETT_PORT=9117
JACKETT_BIND_IP=192.168.50.101
JACKETT_CONFIG_PATH=/mnt/ssd1tb-asus/appdata/jackett/config
PUID=1000
PGID=1000
```

The live qBittorrent port must be verified before deployment: the current environment description reports 8080, while an older repository example used 9081. Preserve the live value rather than changing qBittorrent merely to match this document.

Also set `GLUETUN_IMAGE`, `QBITTORRENT_IMAGE`, `JACKETT_IMAGE`, `VPN_SERVICE_PROVIDER`, `OPENVPN_USER`, `OPENVPN_PASSWORD`, `SERVER_REGIONS`, and `TZ`. Preserve the digest-pinned Gluetun and qBittorrent references reported by discovery when available. Jackett is not in the existing deployment: choose a reviewed LinuxServer Jackett release, resolve its platform-specific digest, and set `JACKETT_IMAGE` to an `image@sha256:digest` reference. Record the release and digest at the deployment gate. Tags in `.env.example` are examples and are mutable, including versioned tags. The Compose file uses current Gluetun names such as `VPN_SERVICE_PROVIDER` and `SERVER_REGIONS`.

## Read-only Portainer discovery

The helper defaults to Portainer at `http://192.168.50.101:9000`, environment `2`, and stack `vpn-qtorrent`. Login prompts for the administrator username and password; the password is not stored. Only the URL, environment ID, and temporary JWT are written to the ignored `.env.portainer.local` file with mode `0600`.

```bash
bin/nightwatchman-portainer login
bin/nightwatchman-portainer status
bin/nightwatchman-portainer discover
bin/nightwatchman-portainer logout
```

Use `login --url URL --environment ID`, `discover --stack NAME --output DIRECTORY`, or the global `--session PATH` option when the defaults do not apply. Discovery performs GET requests only and writes `.nightwatchman/portainer-discovery.json` with restricted permissions. It collects bounded logs and sanitized metadata; it does not store the qBittorrent config or password hash.

Before migration, review the artifact's:

- `images.GLUETUN_IMAGE` and `images.QBITTORRENT_IMAGE` values;
- mounts, published ports, state, and health under `containers`;
- sanitized `compose` text; and
- `qbittorrent_webui_password_hash_present` boolean.

If the hash-present value is `true`, the existing qBittorrent Web UI credential is stored in the bind-mounted config and will be preserved when `CONFIG_PATH` stays unchanged. Treat the discovery artifact as sensitive operational metadata even though known secrets are redacted.

Discovery cannot report `JACKETT_IMAGE` before Jackett exists. Resolve the selected LinuxServer image's platform-specific digest independently from official metadata and record the exact `image@sha256:digest` reference with the deployment record.

## Back up qBittorrent configuration

The default command is an offline dry-run and makes no network connection:

```bash
bin/backup-qbittorrent
```

For a consistent final backup:

1. Stop the **qBittorrent container only** in Portainer. Do not remove the stack or its bind-mounted directories.
2. Confirm it is stopped. Optionally run discovery again while it is stopped and retain that artifact.
3. Execute the backup:

```bash
bin/backup-qbittorrent --execute
```

The helper asks you to type `STOPPED` unless a supplied discovery artifact proves qBittorrent is stopped. To use such an artifact:

```bash
bin/backup-qbittorrent --execute \
  --discovery-artifact .nightwatchman/portainer-discovery.json
```

It verifies the pinned host key, then SSH prompts directly for the Unraid root password if needed. The password is never accepted as a command argument or saved by this project. By default, the complete config directory is archived to `/mnt/user/backups/qbittorrent` with a UTC timestamp and SHA-256 checksum. Downloads are not included. Override paths only with normalized absolute Unraid paths:

```bash
bin/backup-qbittorrent \
  --source /mnt/ssd1tb-asus/appdata/vpn-qtorrent/config \
  --destination /mnt/user/backups/qbittorrent
```

Record the reported archive name and verify its adjacent `.sha256` file on Unraid before continuing.

## Add Jackett browser search

Jackett supplies normalized indexer results; it does not replace qBittorrent. The browser flow is:

```text
Browser -> qBittorrent Search tab -> Jackett plugin -> Jackett -> indexers
                                      |
                                      +-> selected result -> qBittorrent download
```

Both applications use `network_mode: service:gluetun`. qBittorrent reaches Jackett at the fixed internal address `http://127.0.0.1:9117`, and Jackett's indexer requests use PIA through Gluetun. Only Gluetun publishes the configurable `JACKETT_PORT`, bound specifically to `JACKETT_BIND_IP`. Set that address to Unraid's trusted-LAN IP (`192.168.50.101` here), not `0.0.0.0`, and do not forward the port through the router.

Jackett mounts only its own `/config`. It does not mount `/downloads`, and the existing qBittorrent config and downloads mounts remain unchanged.

### Predeployment gate

Before requesting or performing a live deployment:

1. Run read-only discovery and record the current Gluetun/qBittorrent images, application versions, exact deployed Git revision, sanitized Compose, and complete Portainer environment-variable set. Keep credentials in approved secret storage outside Git.
2. Confirm the live qBittorrent Web UI port and preserve it. Do not assume either 8080 or the older 9081 value.
3. Confirm the qBittorrent Web UI exposes the Search tab and plugin manager. The LinuxServer qBittorrent image includes Python for search-plugin support, but the deployed application version must support downloading a result through the Web UI.
4. Confirm `JACKETT_BIND_IP` is the Unraid trusted-LAN address, and that the selected `JACKETT_PORT` is unused and not externally forwarded.
5. Check whether `/mnt/ssd1tb-asus/appdata/jackett/config` already exists. If it contains data, stop and preserve it rather than overwriting it.
6. Select a reviewed LinuxServer Jackett release, resolve its platform-specific digest, and record the exact `JACKETT_IMAGE=image@sha256:digest` reference. A tag alone is not an immutable production pin.
7. On a compatible Docker host, smoke-check the selected image reference before deployment: confirm `curl` and `grep` exist, then confirm the dashboard readiness command accepts unauthenticated, redirect, and authentication-required responses without embedding a credential. Do not substitute this for the post-deployment health check.
8. Confirm the exact `develop` commit to deploy is available to Portainer. The repository owner performs the push; this project never pushes branches or commits.
9. Stop qBittorrent during the approved maintenance window and create and verify the complete pre-plugin config backup.

If the current qBittorrent version lacks the required Web UI search behavior, stop. A qBittorrent image upgrade is a separate change requiring its own review and approval.

### Deploy and configure Jackett

Live deployment requires separate explicit approval. During the approved window:

1. Add `JACKETT_IMAGE`, `JACKETT_PORT`, `JACKETT_BIND_IP`, and `JACKETT_CONFIG_PATH` to the existing Portainer Git stack variables without changing the existing qBittorrent variables or mounts.
2. Point the stack at the approved `develop` revision and deploy the complete stack. Gluetun must become healthy before qBittorrent and Jackett start.
3. If Gluetun is recreated independently, wait for the replacement to become healthy and then **force-recreate** both qBittorrent and Jackett. An ordinary container restart is insufficient because it can retain the removed network namespace.
4. Open `http://192.168.50.101:JACKETT_PORT` from the trusted LAN, immediately set an admin password before adding indexers, and never expose Jackett directly to the Internet.
5. Test each configured indexer in Jackett before configuring qBittorrent.

Jackett uses a secret-free readiness check against its local dashboard. HTTP success, redirect, and authentication-required responses count as ready; connection failures and server errors do not. Jackett's in-container updater is disabled so the deployed image reference controls upgrades.

### Configure qBittorrent's maintained Jackett plugin

Install reviewed qBittorrent Jackett plugin version 4.9 from this commit-pinned source:

```text
https://raw.githubusercontent.com/qbittorrent/search-plugins/fa0be6abdc47b8622e8ec71a0d4427d9a7770eab/nova3/engines/jackett.py
```

Before installing, download that exact URL on a trusted workstation and verify SHA-256 `04edbb791fbcf870fe61d9f476adff3115c32900d8e24dcfa66381cc1649ed9d`. Review and checksum a newer upstream commit before changing this pin; never silently switch the URL back to `master`.

The plugin stores settings in `jackett.json` beside qBittorrent's search-plugin files under its existing protected `/config` bind. Configure it conceptually as follows, substituting the API key locally in qBittorrent rather than in Git:

```json
{
  "api_key": "<copy-from-Jackett-UI>",
  "url": "http://127.0.0.1:9117",
  "tracker_first": false,
  "thread_count": 20
}
```

Do not commit, print, copy into chat, or include the populated `jackett.json` in discovery artifacts or logs. Plugin updates are independent from Jackett container updates and must preserve this local configuration.

### Verify browser search

After configuration, verify all of the following before declaring the deployment successful:

- Gluetun is healthy and qBittorrent and Jackett report healthy/running as intended.
- qBittorrent's existing Web UI login, torrent state, categories, `/config`, and `/downloads` still work.
- Jackett's Web UI authentication and configuration persist after a container restart.
- Each intended indexer passes its Jackett test.
- A qBittorrent Web UI search for a legal test term such as a Linux distribution returns Jackett results and can add a selected result directly to qBittorrent.
- Jackett's external IP matches the intended PIA egress rather than the residential WAN address.
- A Jackett failure prevents searches but does not stop existing qBittorrent transfers.
- Recreating Gluetun followed by both dependent services restores Web UI, search, and download behavior.

If the plugin search/add workflow fails, disable or remove the plugin and restore its pre-plugin qBittorrent configuration before considering any qBittorrent upgrade.

### Back up and upgrade Jackett

Back up `/mnt/ssd1tb-asus/appdata/jackett/config` while Jackett is stopped. Store the backup separately from the appdata directory, record a checksum, and never treat the container filesystem as persistent state.

To upgrade, review Jackett and LinuxServer release notes, back up Jackett config, resolve the chosen image's platform-specific digest, update `JACKETT_IMAGE` to the reviewed `image@sha256:digest` reference, validate Compose offline, and redeploy during an approved window. Do not use Jackett's in-container updater. Afterward, repeat health, authentication, indexer, qBittorrent search, PIA-egress, persistence, and force-recreation checks.

### Roll back Jackett search

1. Disable or remove the qBittorrent Jackett plugin. If its installation changed qBittorrent unexpectedly, keep qBittorrent stopped and restore the verified pre-plugin config backup.
2. Redeploy the exact previously recorded Git revision with its saved Portainer environment-variable set and image references. Do not approximate the old stack from memory.
3. Retain the Jackett config directory until rollback is verified; never delete the qBittorrent config or downloads bind.
4. Start Gluetun and qBittorrent and repeat Web UI login, torrent state, mount, peer-port, and VPN-egress checks.
5. Remove or archive Jackett config only after the prior stack is confirmed operational.

## Update the Git-backed stack

Use a controlled update of the existing `vpn-qtorrent` Git stack during an approved maintenance window:

1. Complete the predeployment gate above and identify the exact local `develop` commit.
2. The repository owner pushes that revision. Confirm Portainer can read the exact pushed commit before changing the stack.
3. Run Portainer discovery and save the artifact outside Git. Record the prior Git revision and full environment-variable set for rollback.
4. Stop qBittorrent and create the final config backup as described above. Verify its checksum.
5. Add only `JACKETT_IMAGE`, `JACKETT_PORT`, `JACKETT_BIND_IP`, and `JACKETT_CONFIG_PATH`; preserve all existing variables and bind paths.
6. Update the stack's Git reference to the approved revision and redeploy it. Do not delete `/mnt/ssd1tb-asus/appdata/vpn-qtorrent/config`, `/mnt/user/media/_inbox`, the backup archive, or an existing Jackett config.
7. Verify Gluetun becomes healthy before qBittorrent and Jackett start, then complete every browser-search verification above.
8. Revoke the temporary Portainer session with `bin/nightwatchman-portainer logout`.

The stack update recreates containers and can interrupt service. Exact bind paths preserve application state; removed container filesystems do not.

## Rollback

If the Git-backed deployment fails:

1. Capture sanitized errors and bounded container logs. Do not delete bind-mounted host data.
2. Disable or remove the Jackett plugin, then redeploy the exact previously recorded Git commit with its saved environment-variable set and image references.
3. Use the exact original config and downloads bind paths.
4. If the config itself was changed or damaged, keep qBittorrent stopped, verify the chosen archive checksum, move the current config aside, and restore the full archived config directory on Unraid with its original ownership and permissions.
5. Start Gluetun and qBittorrent and repeat the Web UI, torrent state, path, port, and VPN-egress checks.

Restoring an archive overwrites operational state and is intentionally not automated by this repository. Keep the current config aside until the restored instance is verified.

## qBittorrent Web UI login troubleshooting

- Confirm the browser is using `http://UNRAID-IP:WEBUI_PORT` and that `WEBUI_PORT` is published on Gluetun, not directly on qBittorrent.
- Confirm `CONFIG_PATH` is the original bind path. A new or empty path creates a fresh qBittorrent configuration instead of preserving the prior login.
- Check `qbittorrent_webui_password_hash_present` in discovery. A value of `true` means the PBKDF2 password hash existed in the preserved config; discovery never records its value.
- If the hash was absent when qBittorrent initialized, inspect the qBittorrent container's current startup logs in Portainer for its generated temporary Web UI password. Do not paste that password into issues, chat, Git, or discovery artifacts.
- If authentication still fails, restore the verified config archive before attempting manual config edits. Keep qBittorrent stopped while restoring.
- Browser autofill and cached credentials can mislead testing; try a private browser window before changing server state.

The helpers deliberately avoid displaying or extracting stored Web UI password material.

## Offline verification

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile nightwatchman_portainer.py backup_qbittorrent.py
docker compose --env-file .env.test -f compose.yaml config --quiet
bin/nightwatchman-portainer --help
bin/backup-qbittorrent --help
bin/backup-qbittorrent
```
