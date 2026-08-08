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

    def test_stack_has_exactly_three_expected_services_and_images(self):
        self.assertEqual({"gluetun", "qbittorrent", "jackett"}, set(self.services))
        self.assertEqual(self.test_env["GLUETUN_IMAGE"], self.services["gluetun"]["image"])
        self.assertEqual(
            self.test_env["QBITTORRENT_IMAGE"], self.services["qbittorrent"]["image"]
        )
        self.assertEqual(self.test_env["JACKETT_IMAGE"], self.services["jackett"]["image"])

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

    def test_only_gluetun_publishes_application_and_torrent_ports(self):
        self.assertNotIn("ports", self.services["qbittorrent"])
        self.assertNotIn("ports", self.services["jackett"])
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
                ("9117", self.test_env["JACKETT_PORT"], "tcp"),
            },
            published,
        )
        jackett_port = next(
            port
            for port in self.services["gluetun"]["ports"]
            if str(port["target"]) == "9117"
        )
        self.assertEqual(self.test_env["JACKETT_BIND_IP"], jackett_port["host_ip"])

    def test_qbittorrent_shares_gluetun_network_and_waits_for_health(self):
        qbittorrent = self.services["qbittorrent"]
        self.assertEqual("service:gluetun", qbittorrent["network_mode"])
        self.assertEqual(
            {"gluetun": {"condition": "service_healthy", "required": True}},
            qbittorrent["depends_on"],
        )

    def test_jackett_shares_gluetun_network_and_waits_for_health(self):
        jackett = self.services["jackett"]
        self.assertEqual("service:gluetun", jackett["network_mode"])
        self.assertEqual(
            {"gluetun": {"condition": "service_healthy", "required": True}},
            jackett["depends_on"],
        )

    def test_qbittorrent_environment_has_identity_timezone_and_ports(self):
        environment = self.services["qbittorrent"]["environment"]
        self.assertEqual(
            {"WEBUI_PORT", "TORRENTING_PORT", "PUID", "PGID", "TZ"},
            set(environment),
        )
        for name in ("WEBUI_PORT", "TORRENTING_PORT", "PUID", "PGID", "TZ"):
            self.assertEqual(self.test_env[name], environment[name])

    def test_jackett_environment_has_identity_timezone_and_no_in_container_updates(self):
        environment = self.services["jackett"]["environment"]
        self.assertEqual({"AUTO_UPDATE", "PUID", "PGID", "TZ"}, set(environment))
        for name in ("PUID", "PGID", "TZ"):
            self.assertEqual(self.test_env[name], environment[name])
        self.assertEqual("false", environment["AUTO_UPDATE"])

    def test_qbittorrent_mounts_required_host_paths(self):
        mounts = {
            volume["target"]: volume["source"]
            for volume in self.services["qbittorrent"]["volumes"]
        }
        self.assertEqual(
            {
                "/config": self.test_env["CONFIG_PATH"],
                "/downloads": self.test_env["DOWNLOADS_PATH"],
            },
            mounts,
        )

    def test_jackett_mounts_only_its_config_path(self):
        mounts = {
            volume["target"]: volume["source"]
            for volume in self.services["jackett"]["volumes"]
        }
        self.assertEqual({"/config": self.test_env["JACKETT_CONFIG_PATH"]}, mounts)

    def test_all_services_have_bounded_json_file_logging(self):
        for service in self.services.values():
            logging = service["logging"]
            self.assertEqual("json-file", logging["driver"])
            self.assertRegex(logging["options"]["max-size"], r"^\d+[kKmMgG]$")
            self.assertGreater(int(logging["options"]["max-file"]), 0)

    def test_jackett_has_bounded_authentication_safe_readiness_check(self):
        healthcheck = self.services["jackett"]["healthcheck"]
        self.assertEqual("CMD-SHELL", healthcheck["test"][0])
        command = healthcheck["test"][1]
        for required in (
            "--max-time 5",
            "http://127.0.0.1:9117/UI/Dashboard",
            "2[0-9]{2}",
            "3[0-9]{2}",
            "401",
            "403",
        ):
            self.assertIn(required, command)
        for forbidden in ("api_key", "apikey", "password", "authorization"):
            self.assertNotIn(forbidden, command.lower())
        self.assertEqual("30s", healthcheck["interval"])
        self.assertEqual("10s", healthcheck["timeout"])
        self.assertEqual(3, healthcheck["retries"])
        self.assertEqual("30s", healthcheck["start_period"])

    def test_runtime_lifecycle_settings_are_preserved(self):
        self.assertEqual("gluetun", self.services["gluetun"]["container_name"])
        self.assertEqual("qBittorrent", self.services["qbittorrent"]["container_name"])
        self.assertEqual("jackett", self.services["jackett"]["container_name"])
        for service in self.services.values():
            self.assertEqual("unless-stopped", service["restart"])
        self.assertEqual("30s", self.services["qbittorrent"]["stop_grace_period"])

    def test_sensitive_and_host_values_are_required_not_literal(self):
        for name in (
            "OPENVPN_USER",
            "OPENVPN_PASSWORD",
            "CONFIG_PATH",
            "DOWNLOADS_PATH",
            "JACKETT_IMAGE",
            "JACKETT_PORT",
            "JACKETT_BIND_IP",
            "JACKETT_CONFIG_PATH",
        ):
            self.assertIn("${" + name + ":?required}", self.compose_source)
        combined = self.compose_source + self.example_source
        self.assertNotIn("/mnt/", combined)
        self.assertNotIn(self.test_env["OPENVPN_USER"], combined)
        self.assertNotIn(self.test_env["OPENVPN_PASSWORD"], combined)


if __name__ == "__main__":
    unittest.main()
