import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "compose.yaml"
EXAMPLE_ENV = ROOT / ".env.example"
TEST_ENV = ROOT / ".env.test"
TODO_FILE = ROOT / "TODO.md"


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
        cls.todo_source = TODO_FILE.read_text()

    def test_stack_has_expected_services_and_images(self):
        self.assertEqual(
            {
                "gluetun",
                "jackett-indexer-init",
                "qbittorrent-search-init",
                "qbittorrent",
                "jackett",
            },
            set(self.services),
        )
        self.assertEqual(self.test_env["GLUETUN_IMAGE"], self.services["gluetun"]["image"])
        self.assertEqual(
            self.test_env["QBITTORRENT_IMAGE"], self.services["qbittorrent"]["image"]
        )
        self.assertEqual(
            self.test_env["QBITTORRENT_IMAGE"],
            self.services["qbittorrent-search-init"]["image"],
        )
        self.assertEqual(self.test_env["JACKETT_IMAGE"], self.services["jackett"]["image"])
        self.assertEqual(
            self.test_env["JACKETT_IMAGE"],
            self.services["jackett-indexer-init"]["image"],
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

    def test_only_gluetun_publishes_application_and_torrent_ports(self):
        self.assertNotIn("ports", self.services["qbittorrent"])
        self.assertNotIn("ports", self.services["qbittorrent-search-init"])
        self.assertNotIn("ports", self.services["jackett-indexer-init"])
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
            {
                "gluetun": {"condition": "service_healthy", "required": True},
                "qbittorrent-search-init": {
                    "condition": "service_completed_successfully",
                    "required": True,
                },
            },
            qbittorrent["depends_on"],
        )

    def test_search_init_shares_gluetun_and_waits_for_health(self):
        init = self.services["qbittorrent-search-init"]
        self.assertEqual("service:gluetun", init["network_mode"])
        self.assertEqual(
            {
                "gluetun": {"condition": "service_healthy", "required": True},
                "jackett": {"condition": "service_healthy", "required": True},
            },
            init["depends_on"],
        )

    def test_jackett_shares_gluetun_network_and_waits_for_health(self):
        jackett = self.services["jackett"]
        self.assertEqual("service:gluetun", jackett["network_mode"])
        self.assertEqual(
            {
                "gluetun": {"condition": "service_healthy", "required": True},
                "jackett-indexer-init": {
                    "condition": "service_completed_successfully",
                    "required": True,
                },
            },
            jackett["depends_on"],
        )

    def test_indexer_init_is_network_isolated_and_uses_curated_allowlist(self):
        init = self.services["jackett-indexer-init"]
        self.assertEqual("none", init["network_mode"])
        self.assertNotIn("depends_on", init)
        self.assertEqual(["/bin/sh", "-euc"], init["entrypoint"])
        self.assertEqual(
            {
                "JACKETT_CONFIG_ROOT": "/config",
                "JACKETT_DEFINITIONS_ROOT": "/app/Jackett/Definitions",
                "JACKETT_BUILTIN_PUBLIC_INDEXERS": "knaben",
                "JACKETT_PUBLIC_INDEXERS": (
                    "1337x eztv knaben limetorrents thepiratebay torrentdownloads yts"
                ),
            },
            init["environment"],
        )
        mounts = {volume["target"]: volume["source"] for volume in init["volumes"]}
        self.assertEqual({"/config": self.test_env["JACKETT_CONFIG_PATH"]}, mounts)

    def test_indexer_init_seeds_missing_configs_and_preserves_existing_configs(self):
        init = self.services["jackett-indexer-init"]
        script = init["command"][0].replace("$$", "$")
        indexer_ids = init["environment"]["JACKETT_PUBLIC_INDEXERS"].split()
        builtin_ids = init["environment"]["JACKETT_BUILTIN_PUBLIC_INDEXERS"].split()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            definitions = root / "definitions"
            configs = root / "config" / "Jackett" / "Indexers"
            definitions.mkdir()
            configs.mkdir(parents=True)
            for indexer_id in indexer_ids:
                if indexer_id in builtin_ids:
                    continue
                (definitions / f"{indexer_id}.yml").write_text(
                    f"---\nid: {indexer_id}\nlanguage: en-US\ntype: public\n",
                    encoding="utf-8",
                )

            existing = configs / "1337x.json"
            existing_contents = '[{"id":"custom","value":"preserve"}]\n'
            existing.write_text(existing_contents, encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "JACKETT_CONFIG_ROOT": str(root / "config"),
                    "JACKETT_DEFINITIONS_ROOT": str(definitions),
                    "JACKETT_BUILTIN_PUBLIC_INDEXERS": " ".join(builtin_ids),
                    "JACKETT_PUBLIC_INDEXERS": " ".join(indexer_ids),
                }
            )

            for _ in range(2):
                result = subprocess.run(
                    ["/bin/sh", "-euc", script],
                    text=True,
                    capture_output=True,
                    env=environment,
                )
                self.assertEqual(0, result.returncode, result.stderr)

            self.assertEqual(existing_contents, existing.read_text(encoding="utf-8"))
            for indexer_id in indexer_ids[1:]:
                self.assertEqual(
                    "[]\n",
                    (configs / f"{indexer_id}.json").read_text(encoding="utf-8"),
                )

    def test_indexer_init_rejects_non_public_or_non_english_definitions(self):
        init = self.services["jackett-indexer-init"]
        script = init["command"][0].replace("$$", "$")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            definitions = root / "definitions"
            definitions.mkdir()
            (definitions / "example.yml").write_text(
                "---\nid: example\nlanguage: en-US\ntype: private\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "JACKETT_CONFIG_ROOT": str(root / "config"),
                    "JACKETT_DEFINITIONS_ROOT": str(definitions),
                    "JACKETT_BUILTIN_PUBLIC_INDEXERS": "",
                    "JACKETT_PUBLIC_INDEXERS": "example",
                }
            )
            result = subprocess.run(
                ["/bin/sh", "-euc", script],
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("not a public en-US definition", result.stderr)

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

    def test_search_init_mounts_both_configs_and_uses_pinned_plugin(self):
        init = self.services["qbittorrent-search-init"]
        mounts = {volume["target"]: volume["source"] for volume in init["volumes"]}
        self.assertEqual(
            {
                "/config": self.test_env["CONFIG_PATH"],
                "/jackett-config": self.test_env["JACKETT_CONFIG_PATH"],
            },
            mounts,
        )
        jackett_mount = next(
            volume for volume in init["volumes"] if volume["target"] == "/jackett-config"
        )
        self.assertTrue(jackett_mount["read_only"])
        environment = init["environment"]
        self.assertNotIn("JACKETT_API_KEY", environment)
        self.assertEqual(
            "https://raw.githubusercontent.com/qbittorrent/search-plugins/"
            "fa0be6abdc47b8622e8ec71a0d4427d9a7770eab/nova3/engines/jackett.py",
            environment["JACKETT_PLUGIN_URL"],
        )
        self.assertEqual(
            "04edbb791fbcf870fe61d9f476adff3115c32900d8e24dcfa66381cc1649ed9d",
            environment["JACKETT_PLUGIN_SHA256"],
        )
        self.assertEqual("/config", environment["QBITTORRENT_CONFIG_ROOT"])
        self.assertEqual("/jackett-config", environment["JACKETT_CONFIG_ROOT"])
        self.assertEqual(["python3", "-c"], init["entrypoint"])

    def test_search_init_writes_verified_plugin_and_preserves_preferences(self):
        self.assertIn("qbittorrent-search-init", self.services)
        init = self.services["qbittorrent-search-init"]
        script = init["command"][0]
        plugin = b"# test Jackett plugin\n"
        api_key = "local-test-api-key-1234"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            jackett_root = root / "jackett-config"
            jackett_data = jackett_root / "Jackett"
            jackett_data.mkdir(parents=True)
            (jackett_data / "ServerConfig.json").write_text(
                json.dumps({"APIKey": api_key}), encoding="utf-8"
            )
            source = root / "source.py"
            source.write_bytes(plugin)
            engines = root / "qBittorrent" / "nova3" / "engines"
            engines.mkdir(parents=True)
            config = engines / "jackett.json"
            config.write_text(
                json.dumps({"tracker_first": True, "thread_count": 7}),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "JACKETT_PLUGIN_URL": source.as_uri(),
                    "JACKETT_PLUGIN_SHA256": hashlib.sha256(plugin).hexdigest(),
                    "QBITTORRENT_CONFIG_ROOT": directory,
                    "JACKETT_CONFIG_ROOT": str(jackett_root),
                }
            )
            result = subprocess.run(
                ["python3", "-c", script],
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(plugin, (engines / "jackett.py").read_bytes())
            settings = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(api_key, settings["api_key"])
            self.assertEqual("http://127.0.0.1:9117", settings["url"])
            self.assertIs(True, settings["tracker_first"])
            self.assertEqual(7, settings["thread_count"])
            self.assertNotIn(api_key, result.stdout + result.stderr)

            source.unlink()
            cached_result = subprocess.run(
                ["python3", "-c", script],
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertEqual(0, cached_result.returncode, cached_result.stderr)
            self.assertEqual(plugin, (engines / "jackett.py").read_bytes())

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
        self.assertEqual(
            "qbittorrent-search-init",
            self.services["qbittorrent-search-init"]["container_name"],
        )
        self.assertEqual(
            "jackett-indexer-init",
            self.services["jackett-indexer-init"]["container_name"],
        )
        self.assertEqual("qBittorrent", self.services["qbittorrent"]["container_name"])
        self.assertEqual("jackett", self.services["jackett"]["container_name"])
        for name, service in self.services.items():
            if name in {"qbittorrent-search-init", "jackett-indexer-init"}:
                self.assertEqual("no", service["restart"])
                continue
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

    def test_example_env_ends_with_one_contiguous_jackett_block(self):
        lines = self.example_source.rstrip().splitlines()
        expected = [
            "# Jackett",
            "JACKETT_IMAGE=lscr.io/linuxserver/jackett:latest",
            "JACKETT_PORT=9117",
            "JACKETT_BIND_IP=192.168.50.101",
            "JACKETT_CONFIG_PATH=/path/to/jackett/config",
        ]
        self.assertEqual(expected, lines[-len(expected) :])
        for name in (
            "JACKETT_IMAGE",
            "JACKETT_PORT",
            "JACKETT_BIND_IP",
            "JACKETT_CONFIG_PATH",
        ):
            self.assertEqual(1, sum(line.startswith(name + "=") for line in lines))

    def test_todo_tracks_curated_indexer_review(self):
        self.assertIn(
            "Review the curated Jackett public-indexer allowlist quarterly",
            self.todo_source,
        )


if __name__ == "__main__":
    unittest.main()
