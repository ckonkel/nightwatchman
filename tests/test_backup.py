import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import backup_qbittorrent as backup


class PathValidationTests(unittest.TestCase):
    def test_accepts_absolute_safe_paths_with_destination_outside_source(self):
        backup.validate_paths("/mnt/appdata/vpn-qtorrent/config", "/mnt/backups/qbittorrent")

    def test_rejects_relative_traversal_whitespace_control_and_shell_characters(self):
        bad = ["relative/path", "/mnt/../etc", "/mnt/bad path", "/mnt/bad\npath", "/mnt/$bad", "/mnt/a;id"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                backup.validate_path(value)

    def test_rejects_destination_inside_source(self):
        with self.assertRaises(ValueError):
            backup.validate_paths("/mnt/appdata/config", "/mnt/appdata/config/backups")


class HostKeyTests(unittest.TestCase):
    KEY = "192.168.50.101 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests"

    def test_requires_exact_ed25519_fingerprint(self):
        backup.verify_fingerprint(
            "256 SHA256:5c6n415kx1MHa4uN6Ui0fgrG3VxdDiGOP97BR76pX8I host (ED25519)"
        )
        with self.assertRaises(RuntimeError):
            backup.verify_fingerprint("256 SHA256:not-the-key host (ED25519)")

    def test_scan_verifies_key_before_writing_mode_0600_file(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = mock.Mock(side_effect=[
                mock.Mock(stdout=self.KEY + "\n"),
                mock.Mock(stdout="256 " + backup.EXPECTED_FINGERPRINT + " host (ED25519)\n"),
            ])
            target = Path(directory) / "known_hosts"
            backup.create_known_hosts(target, runner=runner)
            self.assertEqual(target.read_text(), self.KEY + "\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertIn("ssh-keyscan", runner.call_args_list[0].args[0][0])
            self.assertEqual(runner.call_args_list[1].kwargs["input"], self.KEY + "\n")

    def test_scan_rejects_unexpected_or_multiple_keys_without_writing(self):
        for output in [
            "host ssh-rsa key\n",
            "other-host ssh-ed25519 key\n",
            self.KEY + "\n" + self.KEY + "2\n",
        ]:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "known_hosts"
                runner = mock.Mock(return_value=mock.Mock(stdout=output))
                with self.assertRaises(RuntimeError):
                    backup.create_known_hosts(target, runner=runner)
                self.assertFalse(target.exists())


class CommandTests(unittest.TestCase):
    def test_ssh_command_pins_known_hosts_and_ed25519(self):
        command = backup.build_ssh_command(Path("/tmp/known_hosts"), "remote command")
        rendered = " ".join(command)
        self.assertIn("StrictHostKeyChecking=yes", rendered)
        self.assertIn("UserKnownHostsFile=/tmp/known_hosts", rendered)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", rendered)
        self.assertIn("root@192.168.50.101", command)
        self.assertNotIn("password", rendered.lower())

    def test_remote_script_archives_config_and_never_downloads(self):
        script = backup.build_remote_script(
            "/mnt/ssd1tb-asus/appdata/vpn-qtorrent/config",
            "/mnt/user/backups/qbittorrent",
            "20260808T120000Z",
        )
        self.assertIn("vpn-qtorrent/config", script)
        self.assertIn("sha256sum", script)
        self.assertIn("qbittorrent-config-20260808T120000Z.tar.gz", script)
        self.assertIn("sha256sum '/mnt/user/backups/qbittorrent'/'qbittorrent-config-20260808T120000Z.tar.gz'", script)
        self.assertNotIn("/mnt/user/media/_inbox", script)
        self.assertNotIn("downloads", script.lower())

    def test_remote_script_quotes_each_validated_path(self):
        script = backup.build_remote_script("/mnt/source.config", "/mnt/backup-dir", "stamp")
        self.assertIn("'/mnt/source.config'", script)
        self.assertIn("'/mnt/backup-dir'", script)


class StoppedConfirmationTests(unittest.TestCase):
    def test_discovery_artifact_accepts_only_stopped_qbittorrent(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "discovery.json"
            artifact.write_text(json.dumps({"containers": {"qbittorrent": {"State": {"Status": "exited"}}}}))
            self.assertTrue(backup.artifact_confirms_stopped(artifact))
            artifact.write_text(json.dumps({"containers": {"qbittorrent": {"State": {"Status": "running"}}}}))
            self.assertFalse(backup.artifact_confirms_stopped(artifact))

    def test_execute_refuses_when_interactive_confirmation_is_not_exact(self):
        with mock.patch("builtins.input", return_value="yes"), self.assertRaises(RuntimeError):
            backup.require_stopped_confirmation(None)

    def test_dry_run_does_not_scan_or_connect(self):
        runner = mock.Mock()
        output = io.StringIO()
        result = backup.main([], runner=runner, stdout=output)
        self.assertEqual(result, 0)
        self.assertIn("DRY RUN", output.getvalue())
        runner.assert_not_called()

    def test_execute_scans_then_invokes_ssh_without_password_material(self):
        runner = mock.Mock(side_effect=[
            mock.Mock(stdout=HostKeyTests.KEY + "\n"),
            mock.Mock(stdout="256 " + backup.EXPECTED_FINGERPRINT + " host (ED25519)\n"),
            mock.Mock(stdout=""),
        ])
        with tempfile.TemporaryDirectory() as directory, mock.patch("builtins.input", return_value="STOPPED"):
            result = backup.main(["--execute", "--known-hosts", str(Path(directory) / "kh")], runner=runner)
        self.assertEqual(result, 0)
        ssh_call = runner.call_args_list[-1]
        flattened = " ".join(ssh_call.args[0])
        self.assertNotIn("password", flattened.lower())
        self.assertNotIn("_inbox", flattened)


if __name__ == "__main__":
    unittest.main()
