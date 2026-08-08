import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "compose.yaml"
EXAMPLE_ENV = ROOT / ".env.example"
TEST_ENV = ROOT / ".env.test"


def read_env(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


class ComposeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command = [
            "docker",
            "compose",
            "--env-file",
            str(TEST_ENV),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if result.returncode != 0:
            raise AssertionError(result.stderr.strip() or result.stdout.strip())
        cls.config = json.loads(result.stdout)
        cls.services = cls.config["services"]
        cls.test_env = read_env(TEST_ENV)
        cls.compose_source = COMPOSE_FILE.read_text()
        cls.example_source = EXAMPLE_ENV.read_text()

    def test_stack_has_exactly_two_expected_services_and_images(self):
        self.assertEqual({"gluetun", "qbittorrent"}, set(self.services))
        self.assertEqual(self.test_env["GLUETUN_IMAGE"], self.services["gluetun"]["image"])
        self.assertEqual(
            self.test_env["QBITTORRENT_IMAGE"], self.services["qbittorrent"]["image"]
        )

    def test_gluetun_has_tunnel_permissions_and_device(self):
        gluetun = self.services["gluetun"]
        self.assertIn("NET_ADMIN", gluetun["cap_add"])
        self.assertTrue(
            any(
                device["source"] == "/dev/net/tun"
                and device["target"] == "/dev/net/tun"
                for device in gluetun["devices"]
            )
        )

    def test_gluetun_uses_current_vpn_environment_names(self):
        environment = self.services["gluetun"]["environment"]
        for name in (
            "VPN_SERVICE_PROVIDER",
            "OPENVPN_USER",
            "OPENVPN_PASSWORD",
            "SERVER_REGIONS",
        ):
            self.assertEqual(self.test_env[name], environment[name])
        self.assertNotIn("VPNSP", environment)
        self.assertNotIn("REGION", environment)

    def test_only_gluetun_publishes_web_and_torrent_ports(self):
        self.assertNotIn("ports", self.services["qbittorrent"])
        published = {
            (str(port["target"]), str(port["published"]), port["protocol"])
            for port in self.services["gluetun"]["ports"]
        }
        self.assertEqual(
            {
                (self.test_env["WEBUI_PORT"], self.test_env["WEBUI_PORT"], "tcp"),
                (
                    self.test_env["TORRENTING_PORT"],
                    self.test_env["TORRENTING_PORT"],
                    "tcp",
                ),
                (
                    self.test_env["TORRENTING_PORT"],
                    self.test_env["TORRENTING_PORT"],
                    "udp",
                ),
            },
            published,
        )

    def test_qbittorrent_shares_gluetun_network_and_waits_for_health(self):
        qbittorrent = self.services["qbittorrent"]
        self.assertEqual("service:gluetun", qbittorrent["network_mode"])
        self.assertEqual(
            "service_healthy", qbittorrent["depends_on"]["gluetun"]["condition"]
        )

    def test_qbittorrent_environment_has_identity_timezone_and_ports(self):
        environment = self.services["qbittorrent"]["environment"]
        for name in ("WEBUI_PORT", "TORRENTING_PORT", "PUID", "PGID", "TZ"):
            self.assertEqual(self.test_env[name], environment[name])

    def test_qbittorrent_mounts_required_host_paths(self):
        mounts = {
            volume["target"]: volume["source"]
            for volume in self.services["qbittorrent"]["volumes"]
        }
        self.assertEqual(self.test_env["CONFIG_PATH"], mounts["/config"])
        self.assertEqual(self.test_env["DOWNLOADS_PATH"], mounts["/downloads"])

    def test_both_services_have_bounded_json_file_logging(self):
        for service in self.services.values():
            logging = service["logging"]
            self.assertEqual("json-file", logging["driver"])
            self.assertRegex(logging["options"]["max-size"], r"^\d+[kKmMgG]$")
            self.assertGreater(int(logging["options"]["max-file"]), 0)

    def test_runtime_lifecycle_settings_are_preserved(self):
        self.assertEqual("gluetun", self.services["gluetun"]["container_name"])
        self.assertEqual("qBittorrent", self.services["qbittorrent"]["container_name"])
        for service in self.services.values():
            self.assertEqual("unless-stopped", service["restart"])
        self.assertEqual("30s", self.services["qbittorrent"]["stop_grace_period"])

    def test_sensitive_and_host_values_are_required_not_literal(self):
        for name in ("OPENVPN_USER", "OPENVPN_PASSWORD", "CONFIG_PATH", "DOWNLOADS_PATH"):
            self.assertIn("${" + name + ":?required}", self.compose_source)
        combined = self.compose_source + self.example_source
        self.assertNotIn("/mnt/", combined)
        self.assertNotIn(self.test_env["OPENVPN_USER"], combined)
        self.assertNotIn(self.test_env["OPENVPN_PASSWORD"], combined)


if __name__ == "__main__":
    unittest.main()
