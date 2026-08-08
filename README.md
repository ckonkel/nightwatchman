# nightwatchman

Git-managed Portainer deployment for an existing Gluetun and qBittorrent stack on Unraid. The repository currently provides:

- a two-service Compose file with qBittorrent sharing Gluetun's network;
- read-only Portainer discovery with a temporary local API session;
- a fingerprint-pinned SSH backup of the complete qBittorrent config directory; and
- a controlled runbook for replacing the manually entered stack with a Git-backed stack.

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
WEBUI_PORT=9081
TORRENTING_PORT=6881
PUID=1000
PGID=1000
```

Also set `GLUETUN_IMAGE`, `QBITTORRENT_IMAGE`, `VPN_SERVICE_PROVIDER`, `OPENVPN_USER`, `OPENVPN_PASSWORD`, `SERVER_REGIONS`, and `TZ`. Prefer the immutable image references reported by discovery. The Compose file uses current Gluetun names such as `VPN_SERVICE_PROVIDER` and `SERVER_REGIONS`.

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

## Convert the manual stack to a Git stack

Portainer cannot change a manually entered Compose stack into a Git-backed stack in place. Use this controlled replacement sequence during a maintenance window:

1. Push this repository and choose the exact commit to deploy. Do not continue with uncommitted local changes.
2. Run Portainer discovery and save the artifact outside Git.
3. Compare the discovered images, mounts, ports, environment ID, and stack name with the planned Portainer variables.
4. Stop qBittorrent and create the final config backup as described above. Verify its checksum.
5. Export or copy the current sanitized Compose definition for rollback. Keep the VPN credentials separately in a password manager.
6. In Portainer, remove the existing `vpn-qtorrent` stack. Do **not** delete `/mnt/ssd1tb-asus/appdata/vpn-qtorrent/config`, `/mnt/user/media/_inbox`, or the backup archive. These bind-mounted host directories carry the settings and downloads across the replacement.
7. Immediately create a new stack named exactly `vpn-qtorrent`, select the **Repository/Git** build method, provide the repository URL and selected reference, and set the Compose path to `compose.yaml`.
8. Add all variables from `.env.example` in Portainer's Environment variables UI, using the preserved live bind paths, ports, IDs, timezone, VPN values, and reviewed image references.
9. Deploy the stack. Verify Gluetun becomes healthy before qBittorrent starts, then confirm the qBittorrent Web UI, saved torrents, download paths, VPN egress, and TCP/UDP port mappings.
10. Revoke the temporary Portainer session with `bin/nightwatchman-portainer logout`.

The service interruption between steps 6 and 9 is intentional. Reusing the same stack name and exact bind paths preserves qBittorrent's config; it does not depend on the removed container filesystem.

## Rollback

If the Git-backed deployment fails:

1. Capture its sanitized error and container logs, then remove only the failed stack. Do not delete bind-mounted host data.
2. Recreate `vpn-qtorrent` from the previously saved Compose definition and its recorded environment values, or redeploy the last known-good Git commit.
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
