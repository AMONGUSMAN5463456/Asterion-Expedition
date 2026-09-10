"""Offline launcher and distribution checks; never install a package in tests."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave


ROOT = Path(__file__).resolve().parents[1]
_BOOTSTRAP_MODULE_NAME = "asterion_bootstrap_lazy"


def load_bootstrap():
    """Import bootstrap.py on demand; cached under a stable module nickname."""
    existing = sys.modules.get(_BOOTSTRAP_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _BOOTSTRAP_MODULE_NAME, ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_BOOTSTRAP_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


bootstrap = None


def completed(code=0, output="", error=""):
    return subprocess.CompletedProcess([], code, stdout=output, stderr=error)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        global bootstrap
        bootstrap = load_bootstrap()
        self.directory = tempfile.TemporaryDirectory(prefix="asterion launcher with spaces ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = self.root / ".venv"
        self.interpreter = self.environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.patcher = patch.multiple(bootstrap, ROOT=self.root, ENV=self.environment,
                                     ENV_PYTHON=self.interpreter)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.output = io.StringIO()
        self.quiet = redirect_stdout(self.output)
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def create_existing_environment(self):
        self.interpreter.parent.mkdir(parents=True)
        self.interpreter.touch()

    def test_help_needs_neither_extracted_game_nor_installer(self):
        with patch.object(bootstrap, "prepare_environment") as prepare:
            self.assertEqual(bootstrap.main(["--bootstrap-help"]), 0)
        prepare.assert_not_called()
        self.assertFalse(self.environment.exists())
        self.assertIn("--setup-only", self.output.getvalue())

    def test_missing_game_fails_before_any_environment_change(self):
        with patch.object(bootstrap, "prepare_environment") as prepare:
            with self.assertRaisesRegex(bootstrap.SetupError, "Extract the entire ZIP"):
                bootstrap.main([])
        prepare.assert_not_called()

    def test_incomplete_environment_is_preserved(self):
        self.environment.mkdir()
        marker = self.environment / "keep-this-file"
        marker.write_text("user content", encoding="utf-8")
        with patch.object(bootstrap, "run") as execute:
            with self.assertRaisesRegex(bootstrap.SetupError, "No files have been deleted"):
                bootstrap.prepare_environment()
        execute.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "user content")

    def test_unsupported_existing_environment_does_not_get_overwritten(self):
        self.create_existing_environment()
        with patch.object(bootstrap, "compatible", return_value=False), \
                patch.object(bootstrap, "run") as execute:
            with self.assertRaisesRegex(bootstrap.SetupError, "unsupported or unavailable"):
                bootstrap.prepare_environment()
        execute.assert_not_called()
        self.assertTrue(self.interpreter.exists())

    def test_valid_environment_launches_without_network_or_pip(self):
        self.create_existing_environment()
        calls = []

        def execute(command, **kwargs):
            calls.append(command)
            return completed(output=bootstrap.PIN + "\n")

        with patch.object(bootstrap, "compatible", return_value=True), \
                patch.object(bootstrap, "run", side_effect=execute):
            bootstrap.prepare_environment()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(command[0] == str(self.interpreter) for command in calls))
        self.assertFalse(any("pip" in command for command in calls))

    def test_first_install_is_pinned_private_binary_only_and_isolated(self):
        self.create_existing_environment()
        calls = []

        def execute(command, **kwargs):
            calls.append(command)
            if "pip" in command:
                return completed()
            if bootstrap.PACKAGE_CHECK in command:
                return completed(code=1)
            return completed(output=bootstrap.PIN + "\n")

        with patch.object(bootstrap, "compatible", return_value=True), \
                patch.object(bootstrap, "run", side_effect=execute):
            bootstrap.prepare_environment()
        install = next(command for command in calls if "pip" in command)
        self.assertEqual(install[:3], [str(self.interpreter), "-m", "pip"])
        self.assertIn("panda3d==1.10.16", install)
        self.assertIn("--only-binary=:all:", install)
        self.assertIn("--no-deps", install)
        self.assertIn("--isolated", install)
        self.assertEqual(install[install.index("--index-url") + 1], "https://pypi.org/simple")

    def test_failed_download_stops_without_trying_an_unpinned_fallback(self):
        self.create_existing_environment()
        calls = []

        def execute(command, **kwargs):
            calls.append(command)
            return completed(code=1, error="offline")

        with patch.object(bootstrap, "compatible", return_value=True), \
                patch.object(bootstrap, "run", side_effect=execute):
            with self.assertRaisesRegex(bootstrap.SetupError, "installation failed"):
                bootstrap.prepare_environment()
        self.assertEqual(sum("pip" in command for command in calls), 1)
        self.assertEqual(len(calls), 2)

    def test_missing_native_library_fails_before_starting_game(self):
        self.create_existing_environment()
        results = [completed(output=bootstrap.PIN), completed(code=1, error="native library missing")]
        with patch.object(bootstrap, "compatible", return_value=True), \
                patch.object(bootstrap, "run", side_effect=results):
            with self.assertRaisesRegex(bootstrap.SetupError, "native library missing"):
                bootstrap.prepare_environment()

    def test_setup_only_does_not_launch_game(self):
        (self.root / "main.py").touch()
        with patch.object(bootstrap, "prepare_environment") as prepare, \
                patch.object(bootstrap, "run") as execute:
            self.assertEqual(bootstrap.main(["--setup-only", "--no-audio"]), 0)
        prepare.assert_called_once_with()
        execute.assert_not_called()

    def test_spaces_and_shell_metacharacters_stay_in_single_arguments(self):
        (self.root / "main.py").touch()
        arguments = ["--save-dir", "a folder with spaces; $(literal)", "--no-audio"]
        with patch.object(bootstrap, "prepare_environment"), \
                patch.object(bootstrap, "run", return_value=completed(7)) as execute:
            self.assertEqual(bootstrap.main(arguments), 7)
        self.assertEqual(execute.call_args.args[0],
                         [str(self.interpreter), str(self.root / "main.py"), *arguments])

    def test_child_signal_becomes_shell_exit_status(self):
        (self.root / "main.py").touch()
        with patch.object(bootstrap, "prepare_environment"), \
                patch.object(bootstrap, "run", return_value=completed(-2)):
            self.assertEqual(bootstrap.main([]), 130)

    def test_python_probe_handles_broken_and_unsupported_interpreters(self):
        for result in (completed(output="3.9"), completed(output="3.15"),
                       completed(output="not a version"), completed(code=1, output="3.12")):
            with self.subTest(result=result), patch.object(bootstrap, "run", return_value=result):
                self.assertFalse(bootstrap.compatible(["python"]))
        for minor in range(10, 15):
            with patch.object(bootstrap, "run", return_value=completed(output=f"3.{minor}")):
                self.assertTrue(bootstrap.compatible(["python"]))


class DistributionTests(unittest.TestCase):
    def test_cli_help_runs_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename, option in (("main.py", "--help"), ("bootstrap.py", "--bootstrap-help")):
                with self.subTest(filename=filename):
                    result = subprocess.run([sys.executable, str(ROOT / filename), option],
                                            cwd=directory, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Asterion", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX launch scripts need a POSIX shell")
    def test_posix_launchers_work_from_other_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename, shell in (("Start-Linux.sh", "sh"), ("Start-Mac.command", "bash")):
                with self.subTest(filename=filename):
                    result = subprocess.run([shell, str(ROOT / filename), "--bootstrap-help"],
                                            cwd=directory, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Asterion Expedition launcher", result.stdout)

    def test_audio_assets_decode_and_have_audible_nonclipping_samples(self):
        effects = {"scan", "mine", "collect", "craft", "launch", "warp", "ui",
                   "land", "alert", "discover", "surface", "space", "engine"}
        for name in sorted(effects):
            with self.subTest(name=name), wave.open(str(ROOT / "assets" / "audio" / f"{name}.wav")) as sound:
                self.assertEqual(sound.getsampwidth(), 2)
                self.assertIn(sound.getnchannels(), (1, 2))
                self.assertGreaterEqual(sound.getframerate(), 22050)
                self.assertGreater(sound.getnframes() / sound.getframerate(), 0.03)
                samples = sound.readframes(sound.getnframes())
                peak = max(abs(value[0]) for value in struct.iter_unpack("<h", samples))
                self.assertGreater(peak, 100)
                self.assertLess(peak, 32767)

    def test_font_assets_have_redistribution_license(self):
        for filename in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            font = ROOT / "assets" / "fonts" / filename
            self.assertGreater(font.stat().st_size, 10000)
            self.assertIn(font.read_bytes()[:4], (b"\x00\x01\x00\x00", b"OTTO"))
        license_text = (ROOT / "assets" / "fonts" / "LICENSE-DejaVu.txt").read_text(encoding="utf-8")
        self.assertIn("Permission", license_text)
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "THIRD_PARTY_NOTICES.md").is_file())

    def test_requirements_pin_matches_bootstrap(self):
        pinned = load_bootstrap().PIN
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        pins = [re.fullmatch(r"panda3d==(\S+)", line.strip()).group(1)
                for line in lines if line.strip().startswith("panda3d==")]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0], pinned)

    def test_manifest_entries_rehash_correctly(self):
        manifest = ROOT / "MANIFEST.sha256"
        self.assertTrue(manifest.is_file())
        entries = 0
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, name = line.partition("  ")
            target = ROOT / name.strip()
            self.assertTrue(target.is_file(), name)
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(actual, digest.strip(), name)
            entries += 1
        self.assertGreater(entries, 0)

    def test_committed_reference_matches_generated_output(self):
        from docs import build_reference
        committed = (ROOT / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
        build_reference.build()
        rebuilt = (ROOT / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
        if rebuilt != committed:
            (ROOT / "docs" / "REFERENCE.md").write_text(committed, encoding="utf-8")
        self.assertEqual(rebuilt, committed)


if __name__ == "__main__":
    unittest.main()
