"""Static, hardware-free contract checks for the onboard-audio blacklist assets."""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy" / "onboard-audio-off"


class OnboardAudioOffDeployTests(unittest.TestCase):
    def test_modprobe_file_blacklists_only_the_onboard_driver(self):
        self.assertEqual(
            (DEPLOY / "files" / "modprobe.conf").read_text(),
            "blacklist snd_bcm2835\n",
        )

    def test_scripts_parse_and_resolve_modprobe_through_privileged_path(self):
        for script_name in ("install.sh", "uninstall.sh"):
            path = DEPLOY / script_name
            result = subprocess.run(
                ["/bin/sh", "-n", str(path)], check=False, capture_output=True, text=True
            )
            with self.subTest(script=script_name):
                self.assertEqual(result.returncode, 0, result.stderr)
        install = (DEPLOY / "install.sh").read_text()
        self.assertIn("MODPROBE=$(sudo sh -c 'command -v modprobe')", install)

    def test_scripts_manage_only_their_named_file_and_never_touch_services(self):
        scripts = "\n".join(
            (DEPLOY / name).read_text() for name in ("install.sh", "uninstall.sh")
        )
        self.assertIn("/etc/modprobe.d/rgb-matrix-onboard-audio-off.conf", scripts)
        self.assertNotIn("systemctl", scripts)
        for command in ("systemctl reboot", "shutdown", "sudo reboot", "\nreboot"):
            with self.subTest(command=command):
                self.assertNotIn(command, scripts)
        # The boot config is only read; editing it stays a deliberate manual step.
        self.assertNotIn("sed -i", scripts)
        self.assertNotIn("> /boot", scripts)

    def test_installer_refuses_to_replace_a_modified_file(self):
        install = (DEPLOY / "install.sh").read_text()
        self.assertIn("refusing to replace", install)
        uninstall = (DEPLOY / "uninstall.sh").read_text()
        self.assertIn("refusing to remove modified file", uninstall)


if __name__ == "__main__":
    unittest.main()
