#!/usr/bin/env python3
"""Temporary authentication and read-only discovery for a Portainer VPN stack."""

import argparse
import getpass
import io
import json
import os
import re
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_URL = "http://192.168.50.101:9000"
DEFAULT_ENVIRONMENT = "2"
DEFAULT_STACK = "vpn-qtorrent"
SESSION_PATH = Path(".env.portainer.local")
DISCOVERY_DIR = Path(".nightwatchman")
REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {"password", "token", "authorization", "openvpn_user", "openvpn_password", "jwt"}


def _is_sensitive(key):
    lowered = str(key).lower()
    return lowered in SENSITIVE_KEYS or lowered.endswith("_password") or lowered.endswith("_token")


def redact(value):
    if isinstance(value, dict):
        return {key: REDACTED if _is_sensitive(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        sensitive = r"(?:password|token|authorization|openvpn_user|openvpn_password|jwt)"
        return re.sub(rf"(?i)(\b{sensitive}\s*[=:]\s*)[^\s,;]+", r"\1" + REDACTED, value)
    return value


def sanitize_compose(text):
    sensitive = r"(?:password|token|authorization|openvpn_user|openvpn_password|jwt)"
    list_style = re.compile(rf"(?im)^(\s*-\s*{sensitive}\s*=).*$")
    mapping_style = re.compile(rf"(?im)^(\s*{sensitive}\s*:).*$")
    return mapping_style.sub(r"\1 " + REDACTED, list_style.sub(r"\1" + REDACTED, text))


def sanitize_log(text):
    """Drop credential-bearing log lines instead of trying to identify the value."""
    sensitive_line = re.compile(r"(?i)password|passphrase|authorization|openvpn_(?:user|password)|\btoken\b")
    return "".join(line for line in text.splitlines(keepends=True) if not sensitive_line.search(line))


def save_session(path, url, environment, jwt):
    path = Path(path)
    contents = f"PORTAINER_URL={url}\nPORTAINER_ENVIRONMENT={environment}\nPORTAINER_JWT={jwt}\n".encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as session_file:
            session_file.write(contents)
    finally:
        os.close(descriptor)


def load_session(path=SESSION_PATH):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def logout(path=SESSION_PATH):
    Path(path).unlink(missing_ok=True)


def login(url, environment, path=SESSION_PATH, opener=None, input_fn=input, getpass_fn=getpass.getpass):
    opener = opener or urllib.request.build_opener()
    username = input_fn("Portainer username: ")
    password = getpass_fn("Portainer password: ")
    payload = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/api/auth", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(request) as response:
        jwt = json.loads(response.read())["jwt"]
    save_session(path, url.rstrip("/"), str(environment), jwt)


class PortainerClient:
    """A deliberately GET-only Portainer API client."""

    def __init__(self, base_url, jwt, opener=None):
        self.base_url = base_url.rstrip("/")
        self.jwt = jwt
        self.opener = opener or urllib.request.build_opener()

    def get_bytes(self, path):
        request = urllib.request.Request(self.base_url + path, headers={"Authorization": "Bearer " + self.jwt}, method="GET")
        with self.opener.open(request) as response:
            return response.read()

    def get_json(self, path):
        return json.loads(self.get_bytes(path))


def urlquote(value):
    return urllib.parse.quote(value, safe="")


def container_filters(stack):
    return json.dumps({"label": [f"com.docker.compose.project={stack}"]}, separators=(",", ":"))


def deployable_image(container_inspect, image_inspect):
    configured = container_inspect.get("Config", {}).get("Image", "")
    repository = configured.split("@", 1)[0].rsplit(":", 1)[0]
    for digest in image_inspect.get("RepoDigests") or []:
        if digest.split("@", 1)[0] == repository:
            return digest
    return configured


def password_hash_present(archive_bytes):
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.rsplit("/", 1)[-1] == "qBittorrent.conf":
                fileobj = archive.extractfile(member)
                if fileobj:
                    return b"WebUI\\Password_PBKDF2=" in fileobj.read()
    return False


def discover(client, environment, stack_name):
    environment = str(environment)
    status = client.get_json("/api/status")
    endpoint = client.get_json(f"/api/endpoints/{environment}")
    stacks = client.get_json("/api/stacks")
    matches = [stack for stack in stacks if stack.get("Name") == stack_name and str(stack.get("EndpointId")) == environment]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one stack named {stack_name!r} in environment {environment}")
    stack = matches[0]
    stack_file = client.get_json(f"/api/stacks/{stack['Id']}/file")
    compose = sanitize_compose(stack_file.get("StackFileContent", ""))

    prefix = f"/api/endpoints/{environment}/docker"
    filters = urlquote(container_filters(stack_name))
    containers = client.get_json(f"{prefix}/containers/json?all=1&filters={filters}")
    by_service = {}
    for container in containers:
        labels = container.get("Labels") or {}
        if labels.get("com.docker.compose.project") != stack_name:
            continue
        service = labels.get("com.docker.compose.service")
        if service not in {"gluetun", "qbittorrent"} or service in by_service:
            raise RuntimeError("unexpected or duplicate Compose service in Portainer discovery")
        by_service[service] = container
    if set(by_service) != {"gluetun", "qbittorrent"}:
        raise RuntimeError("Gluetun and qBittorrent containers were not both found")

    inspected = {}
    logs = {}
    images = {}
    variable_names = {"gluetun": "GLUETUN_IMAGE", "qbittorrent": "QBITTORRENT_IMAGE"}
    for service, summary in by_service.items():
        container_id = summary["Id"]
        detail = client.get_json(f"{prefix}/containers/{container_id}/json")
        image = client.get_json(f"{prefix}/images/{detail['Image']}/json")
        inspected[service] = detail
        images[variable_names[service]] = deployable_image(detail, image)
        raw_log = client.get_bytes(f"{prefix}/containers/{container_id}/logs?stdout=1&stderr=1&tail=200").decode("utf-8", "replace")
        logs[service] = sanitize_log(raw_log)

    qbit_id = by_service["qbittorrent"]["Id"]
    config_path = urlquote("/config/qBittorrent/qBittorrent.conf")
    config_archive = client.get_bytes(f"{prefix}/containers/{qbit_id}/archive?path={config_path}")
    return redact({
        "status": status,
        "environment": endpoint,
        "stack": stack,
        "compose": compose,
        "containers": inspected,
        "images": images,
        "logs": logs,
        "qbittorrent_webui_password_hash_present": password_hash_present(config_archive),
    })


def write_discovery(directory, result):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "portainer-discovery.json"
    path.write_text(json.dumps(redact(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _session_client(path):
    session = load_session(path)
    return session, PortainerClient(session["PORTAINER_URL"], session["PORTAINER_JWT"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=SESSION_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    login_parser = commands.add_parser("login")
    login_parser.add_argument("--url", default=DEFAULT_URL)
    login_parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    commands.add_parser("logout")
    commands.add_parser("status")
    discover_parser = commands.add_parser("discover")
    discover_parser.add_argument("--stack", default=DEFAULT_STACK)
    discover_parser.add_argument("--output", type=Path, default=DISCOVERY_DIR)
    args = parser.parse_args(argv)
    if args.command == "login":
        login(args.url, args.environment, args.session)
        print("Temporary Portainer session saved.")
    elif args.command == "logout":
        logout(args.session)
        print("Temporary Portainer session removed.")
    elif args.command == "status":
        session, client = _session_client(args.session)
        result = client.get_json("/api/status")
        print(json.dumps({"url": session["PORTAINER_URL"], "environment": session["PORTAINER_ENVIRONMENT"], "status": redact(result)}, indent=2))
    else:
        session, client = _session_client(args.session)
        result = discover(client, session["PORTAINER_ENVIRONMENT"], args.stack)
        print(write_discovery(args.output, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
