#!/usr/bin/env python3
"""Create a fingerprint-pinned backup of qBittorrent's configuration on Unraid."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence, TextIO


DEFAULT_HOST = "192.168.50.101"
DEFAULT_SOURCE = "/mnt/ssd1tb-asus/appdata/vpn-qtorrent/config"
DEFAULT_DESTINATION = "/mnt/user/backups/qbittorrent"
EXPECTED_FINGERPRINT = "SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I"
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def validate_path(value: str) -> PurePosixPath:
    if not SAFE_PATH.fullmatch(value) or any(part == ".." for part in PurePosixPath(value).parts):
        raise ValueError(f"unsafe path: {value!r}")
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value.rstrip("/") or value == "/":
        raise ValueError(f"path must be a normalized absolute path: {value!r}")
    return path


def validate_paths(source: str, destination: str) -> tuple[PurePosixPath, PurePosixPath]:
    source_path = validate_path(source)
    destination_path = validate_path(destination)
    if destination_path == source_path or source_path in destination_path.parents:
        raise ValueError("backup destination must be outside the source directory")
    return source_path, destination_path


def verify_fingerprint(output: str) -> None:
    fields = output.strip().split()
    if EXPECTED_FINGERPRINT not in fields or "(ED25519)" not in fields:
        raise RuntimeError("Unraid host key fingerprint did not match the pinned ED25519 key")


def create_known_hosts(
    path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    scan = runner(
        ["ssh-keyscan", "-t", "ed25519", DEFAULT_HOST],
        check=True,
        capture_output=True,
        text=True,
    )
    keys = [line for line in scan.stdout.splitlines() if line and not line.startswith("#")]
    if (
        len(keys) != 1
        or len(keys[0].split()) != 3
        or keys[0].split()[0] != DEFAULT_HOST
        or keys[0].split()[1] != "ssh-ed25519"
    ):
        raise RuntimeError("ssh-keyscan did not return exactly one ED25519 host key")
    key = keys[0] + "\n"
    fingerprint = runner(
        ["ssh-keygen", "-lf", "-", "-E", "sha256"],
        input=key,
        check=True,
        capture_output=True,
        text=True,
    )
    verify_fingerprint(fingerprint.stdout)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key)
    os.chmod(path, 0o600)


def build_remote_script(source: str, destination: str, stamp: str) -> str:
    source_path, destination_path = validate_paths(source, destination)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stamp):
        raise ValueError("unsafe archive timestamp")
    archive = f"qbittorrent-config-{stamp}.tar.gz"
    # Validation excludes single quotes; explicit quoting makes the fixed remote
    # shell script straightforward to audit even though every accepted value is
    # already restricted to a safe alphabet.
    quote = lambda value: "'" + value + "'"
    src = quote(str(source_path))
    dest = quote(str(destination_path))
    parent = quote(str(source_path.parent))
    leaf = quote(source_path.name)
    archive_name = quote(archive)
    return (
        "set -eu; "
        f"test -d {src}; mkdir -p {dest}; "
        f"tar -C {parent} -czf {dest}/{archive_name}.partial {leaf}; "
        f"mv {dest}/{archive_name}.partial {dest}/{archive_name}; "
        f"sha256sum {dest}/{archive_name} > {dest}/{archive_name}.sha256.partial; "
        f"mv {dest}/{archive_name}.sha256.partial {dest}/{archive_name}.sha256"
    )


def build_ssh_command(known_hosts: Path, remote_script: str) -> list[str]:
    return [
        "ssh",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "HostKeyAlgorithms=ssh-ed25519",
        "-o", "PubkeyAcceptedAlgorithms=ssh-ed25519",
        f"root@{DEFAULT_HOST}",
        remote_script,
    ]


def artifact_confirms_stopped(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    containers = data.get("containers", {}) if isinstance(data, dict) else {}
    entries = containers.items() if isinstance(containers, dict) else (("", item) for item in containers)
    for service, container in entries:
        if not isinstance(container, dict):
            continue
        identity = " ".join([str(service)] + [str(container.get(key, "")) for key in ("service", "name", "Names")])
        if "qbittorrent" not in identity.lower():
            continue
        state = container.get("state", container.get("State", ""))
        if isinstance(state, dict):
            state = state.get("Status", state.get("status", ""))
        return str(state).lower() in {"exited", "stopped", "created"}
    return False


def require_stopped_confirmation(artifact: Path | None) -> None:
    if artifact is not None and artifact_confirms_stopped(artifact):
        return
    response = input("Confirm qBittorrent is stopped by typing STOPPED: ")
    if response != "STOPPED":
        raise RuntimeError("backup cancelled: qBittorrent stop was not confirmed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Back up qBittorrent configuration from Unraid")
    result.add_argument("--execute", action="store_true", help="perform the backup (default is dry-run)")
    result.add_argument("--source", default=DEFAULT_SOURCE, help="remote qBittorrent config directory")
    result.add_argument("--destination", default=DEFAULT_DESTINATION, help="remote backup directory")
    result.add_argument("--known-hosts", type=Path, default=Path(".nightwatchman/unraid_known_hosts"))
    result.add_argument("--discovery-artifact", type=Path, help="JSON discovery result confirming qBittorrent is stopped")
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    stdout: TextIO = sys.stdout,
) -> int:
    args = parser().parse_args(argv)
    validate_paths(args.source, args.destination)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    remote_script = build_remote_script(args.source, args.destination, stamp)
    if not args.execute:
        print("DRY RUN: no network connection or backup was made.", file=stdout)
        print(f"Source: {args.source}", file=stdout)
        print(f"Destination: {args.destination}", file=stdout)
        print("Run again with --execute after stopping qBittorrent.", file=stdout)
        return 0
    require_stopped_confirmation(args.discovery_artifact)
    create_known_hosts(args.known_hosts, runner=runner)
    runner(build_ssh_command(args.known_hosts, remote_script), check=True)
    print(f"Backup completed in {args.destination}", file=stdout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
