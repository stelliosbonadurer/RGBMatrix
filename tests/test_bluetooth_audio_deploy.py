"""Static, hardware-free contract checks for Bluetooth deployment assets."""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy" / "bluetooth-audio"


class BluetoothAudioDeployTests(unittest.TestCase):
    def test_wireplumber_rule_pins_proved_format_and_disables_capture_nodes(self):
        rule = (DEPLOY / "files" / "60-rgb-matrix-bluetooth-audio.lua").read_text()
        for required in (
            '"audio.channels"] = 1',
            '"audio.position"] = "MONO"',
            '"audio.rate"] = 44100',
            '"audio.format"] = "S32LE"',
            '"session.suspend-timeout-seconds"] = 0',
            '"node.disabled"] = true',
            "alsa_output.platform-snd_aloop.*",
            "alsa_input.platform-snd_aloop.*",
        ):
            with self.subTest(required=required):
                self.assertIn(required, rule)

    def test_boot_files_pin_card_and_substream(self):
        self.assertEqual(
            (DEPLOY / "files" / "modules-load.conf").read_text(),
            "snd-aloop\n",
        )
        self.assertEqual(
            (DEPLOY / "files" / "modprobe.conf").read_text(),
            "options snd-aloop index=10 pcm_substreams=1\n",
        )

    def test_installer_verifies_the_live_module_parameters(self):
        install = (DEPLOY / "install.sh").read_text()
        self.assertIn("/sys/module/snd_aloop/parameters/index", install)
        self.assertIn("/sys/module/snd_aloop/parameters/pcm_substreams", install)
        self.assertIn('${loaded_indexes%%,*}', install)
        self.assertIn('${loaded_substreams%%,*}', install)

    def test_scripts_parse_and_uninstall_has_required_revert_order(self):
        for script_name in ("install.sh", "uninstall.sh"):
            result = subprocess.run(
                ["/bin/sh", "-n", str(DEPLOY / script_name)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        uninstall = (DEPLOY / "uninstall.sh").read_text()
        stop = uninstall.index("systemctl --user stop wireplumber")
        unload = uninstall.index("sudo modprobe -r snd-aloop")
        start = uninstall.rindex("    restart_wireplumber")
        self.assertLess(stop, unload)
        self.assertLess(unload, start)

    def test_scripts_manage_only_named_files_and_never_restart_matrix_service(self):
        scripts = "\n".join(
            (DEPLOY / name).read_text() for name in ("install.sh", "uninstall.sh")
        )
        self.assertNotIn("rgb-matrix-web", scripts)
        self.assertNotIn("bluetoothctl scan", scripts)
        self.assertIn("rgb-matrix-bluetooth-audio.conf", scripts)
        self.assertIn("60-rgb-matrix-bluetooth-audio.lua", scripts)


if __name__ == "__main__":
    unittest.main()
