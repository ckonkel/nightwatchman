import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

import nightwatchman_portainer as portainer


class Response:
    def __init__(self, payload=b""):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, request):
        body = request.data.decode() if request.data else None
        self.requests.append((request.get_method(), request.full_url, body, dict(request.header_items())))
        key = (request.get_method(), request.full_url)
        value = self.routes[key]
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode())


def archive_with_config(contents):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        data = contents.encode()
        info = tarfile.TarInfo("qBittorrent.conf")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


class PortainerHelperTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.session = self.root / ".env.portainer.local"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_login_prompts_and_stores_only_session_values_mode_0600(self):
        opener = FakeOpener({("POST", "http://host:9000/api/auth"): {"jwt": "secret-jwt"}})
        portainer.login("http://host:9000", "2", self.session, opener, lambda _: "admin", lambda _: "password")
        text = self.session.read_text()
        self.assertIn("PORTAINER_JWT=secret-jwt", text)
        self.assertNotIn("password", text)
        self.assertNotIn("admin", text)
        self.assertEqual(stat.S_IMODE(self.session.stat().st_mode), 0o600)
        self.assertEqual(json.loads(opener.requests[0][2]), {"username": "admin", "password": "password"})

    def test_load_session_reuses_jwt_and_logout_removes_it(self):
        portainer.save_session(self.session, "http://host:9000", "2", "jwt")
        self.assertEqual(portainer.load_session(self.session)["PORTAINER_JWT"], "jwt")
        portainer.logout(self.session)
        self.assertFalse(self.session.exists())

    def test_client_get_is_authenticated_and_has_no_mutating_operation(self):
        opener = FakeOpener({("GET", "http://host/api/status"): {"Version": "2.39.5"}})
        client = portainer.PortainerClient("http://host", "jwt", opener)
        self.assertEqual(client.get_json("/api/status")["Version"], "2.39.5")
        self.assertEqual(opener.requests[0][0], "GET")
        self.assertEqual(opener.requests[0][3]["Authorization"], "Bearer jwt")
        for name in ("post", "put", "patch", "delete", "restart", "stop", "update"):
            self.assertFalse(hasattr(client, name))

    def test_recursive_redaction_covers_credentials_and_auth_headers(self):
        value = {"password": "one", "nested": [{"OPENVPN_USER": "two"}, "OPENVPN_PASSWORD=three"], "Authorization": "four", "safe": 4}
        self.assertEqual(portainer.redact(value), {"password": "[REDACTED]", "nested": [{"OPENVPN_USER": "[REDACTED]"}, "OPENVPN_PASSWORD=[REDACTED]"], "Authorization": "[REDACTED]", "safe": 4})

    def test_compose_text_redacts_list_and_mapping_style_secrets(self):
        source = "- OPENVPN_PASSWORD=fake-pass\nOPENVPN_USER: fake-user\nAuthorization: bearer\nSAFE: yes\n"
        clean = portainer.sanitize_compose(source)
        self.assertNotIn("fake-pass", clean)
        self.assertNotIn("fake-user", clean)
        self.assertNotIn("bearer", clean)
        self.assertIn("SAFE: yes", clean)

    def test_log_sanitization_omits_temporary_password_line_but_keeps_safe_lines(self):
        log = (
            "qBittorrent started\n"
            "The WebUI administrator password was not set. A temporary password is provided for this session: Secret123\n"
            "WebUI listening on port 9081\n"
        )
        clean = portainer.sanitize_log(log)
        self.assertNotIn("Secret123", clean)
        self.assertNotIn("temporary password", clean)
        self.assertIn("qBittorrent started", clean)
        self.assertIn("WebUI listening on port 9081", clean)

    def test_password_hash_detection_reads_tar_only_in_memory(self):
        present = archive_with_config("[Preferences]\nWebUI\\Password_PBKDF2=@ByteArray(hash)\n")
        absent = archive_with_config("[Preferences]\nWebUI\\Port=9081\n")
        self.assertTrue(portainer.password_hash_present(present))
        self.assertFalse(portainer.password_hash_present(absent))

    def test_image_reference_prefers_matching_repository_digest(self):
        inspect = {"Config": {"Image": "qmcgaw/gluetun:latest"}}
        image = {"RepoDigests": ["qmcgaw/gluetun@sha256:abc", "mirror/gluetun@sha256:def"]}
        self.assertEqual(portainer.deployable_image(inspect, image), "qmcgaw/gluetun@sha256:abc")

    def test_image_reference_falls_back_to_config_image(self):
        inspect = {"Config": {"Image": "local/image:test"}}
        self.assertEqual(portainer.deployable_image(inspect, {"RepoDigests": []}), "local/image:test")

    def test_discovery_uses_exact_compose_labels_bounded_logs_and_get_only(self):
        base = "http://host"
        routes = {
            ("GET", base + "/api/status"): {"Version": "2.39.5"},
            ("GET", base + "/api/endpoints/2"): {"Id": 2, "Name": "unraid"},
            ("GET", base + "/api/stacks"): [{"Id": 7, "Name": "vpn-qtorrent", "EndpointId": 2}],
            ("GET", base + "/api/stacks/7/file"): {"StackFileContent": "OPENVPN_PASSWORD: literal-secret\n"},
        }
        filters = portainer.container_filters("vpn-qtorrent")
        container_url = base + "/api/endpoints/2/docker/containers/json?all=1&filters=" + portainer.urlquote(filters)
        routes[("GET", container_url)] = [
            {"Id": "g", "Labels": {"com.docker.compose.project": "vpn-qtorrent", "com.docker.compose.service": "gluetun"}},
            {"Id": "q", "Labels": {"com.docker.compose.project": "vpn-qtorrent", "com.docker.compose.service": "qbittorrent"}},
        ]
        for cid, image_id, ref, digest in (("g", "img-g", "qmcgaw/gluetun:latest", "qmcgaw/gluetun@sha256:aaa"), ("q", "img-q", "ghcr.io/linuxserver/qbittorrent:latest", "ghcr.io/linuxserver/qbittorrent@sha256:bbb")):
            routes[("GET", base + f"/api/endpoints/2/docker/containers/{cid}/json")] = {"Id": cid, "Image": image_id, "Config": {"Image": ref}, "Mounts": [], "NetworkSettings": {"Ports": {}}, "State": {"Status": "running"}}
            routes[("GET", base + f"/api/endpoints/2/docker/images/{image_id}/json")] = {"RepoDigests": [digest]}
            routes[("GET", base + f"/api/endpoints/2/docker/containers/{cid}/logs?stdout=1&stderr=1&tail=200")] = b"bounded log"
        routes[("GET", base + "/api/endpoints/2/docker/containers/q/logs?stdout=1&stderr=1&tail=200")] = (
            b"qBittorrent started\nA temporary password is provided for this session: Secret123\nWebUI ready\n"
        )
        routes[("GET", base + "/api/endpoints/2/docker/containers/q/archive?path=%2Fconfig%2FqBittorrent%2FqBittorrent.conf")] = archive_with_config("WebUI\\Password_PBKDF2=hash-that-must-not-persist\n")
        opener = FakeOpener(routes)
        result = portainer.discover(portainer.PortainerClient(base, "jwt", opener), "2", "vpn-qtorrent")
        artifact = portainer.write_discovery(self.root / ".nightwatchman", result)
        self.assertTrue(result["qbittorrent_webui_password_hash_present"])
        self.assertEqual(result["images"]["GLUETUN_IMAGE"], "qmcgaw/gluetun@sha256:aaa")
        self.assertNotIn("literal-secret", json.dumps(result))
        self.assertNotIn("hash-that-must-not-persist", json.dumps(result))
        self.assertNotIn("literal-secret", artifact.read_text())
        self.assertNotIn("hash-that-must-not-persist", artifact.read_text())
        self.assertNotIn("Secret123", artifact.read_text())
        self.assertIn("qBittorrent started", result["logs"]["qbittorrent"])
        self.assertIn("WebUI ready", result["logs"]["qbittorrent"])
        self.assertTrue(all(method == "GET" for method, *_ in opener.requests))
        self.assertTrue(any("tail=200" in url for _, url, *_ in opener.requests))

    def test_write_discovery_uses_private_directory_and_file_modes(self):
        destination = self.root / ".nightwatchman"
        path = portainer.write_discovery(destination, {"safe": True})
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
