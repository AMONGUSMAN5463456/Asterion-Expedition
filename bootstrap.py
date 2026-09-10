#!/usr/bin/env python3
"""Prepare a private Python environment once, then launch the native game.

No administrator access, global package changes, telemetry, or update check.
The only downloaded package is the pinned Panda3D wheel from official PyPI.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv"
PIN = "1.10.16"
ENV_PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
VERSION_CHECK = "import sys; print('%d.%d' % sys.version_info[:2])"
PACKAGE_CHECK = (
    "import importlib.metadata as m; "
    "print(m.version('panda3d'))"
)


class SetupError(Exception):
    pass


def say(message):
    print(message, flush=True)


def run(command, **kwargs):
    try:
        return subprocess.run(command, cwd=str(ROOT), **kwargs)
    except OSError as error:
        raise SetupError("Could not run %s: %s" % (command[0], error)) from error


def compatible(command):
    try:
        result = run(command + ["-c", VERSION_CHECK], capture_output=True, text=True)
        version = tuple(int(part) for part in result.stdout.strip().split("."))
        return result.returncode == 0 and (3, 10) <= version <= (3, 14)
    except (SetupError, ValueError):
        return False


def find_python():
    if compatible([sys.executable]):
        return [sys.executable]
    candidates = []
    for minor in (14, 13, 12, 11, 10):
        name = shutil.which("python3.%d" % minor)
        if name:
            candidates.append([name])
    if os.name == "nt" and shutil.which("py"):
        candidates.extend([["py", "-3.%d" % n] for n in (14, 13, 12, 11, 10)])
    for path in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if Path(path).is_file():
            candidates.append([path])
    for command in candidates:
        if compatible(command):
            return command
    raise SetupError(
        "Python 3.10 through 3.14 is required. Install a supported 64-bit Python "
        "from https://www.python.org/downloads/ and launch again. On macOS, "
        "Apple's older system Python may not be sufficient."
    )


def prepare_environment():
    if ENV_PYTHON.is_file():
        if not compatible([str(ENV_PYTHON)]):
            raise SetupError(
                "The existing .venv uses an unsupported or unavailable Python. "
                "Close the game, rename .venv to .venv-old, then launch again "
                "with Python 3.10–3.14. Your saves are kept separately."
            )
    else:
        if ENV.exists():
            raise SetupError(
                "A .venv folder exists but its Python executable is missing. "
                "Rename .venv to .venv-old and launch again to create a fresh "
                "environment. No files have been deleted."
            )
        interpreter = find_python()
        say("Creating the game's private Python environment (.venv)…")
        created = run(interpreter + ["-m", "venv", str(ENV)])
        if created.returncode:
            raise SetupError(
                "Python could not create a virtual environment. On Linux, "
                "install the python3-venv package for your Python version. "
                "Also check that the extracted game folder is writable. "
                "If a partial .venv now exists, rename it before trying again."
            )
    package = run([str(ENV_PYTHON), "-c", PACKAGE_CHECK], capture_output=True, text=True)
    if package.returncode or package.stdout.strip() != PIN:
        say("First-run setup: downloading Panda3D %s from pypi.org." % PIN)
        say("An internet connection is needed for this step only.")
        install = run([
            str(ENV_PYTHON), "-m", "pip", "--isolated", "install",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-deps",
            "--disable-pip-version-check", "--no-input", "panda3d==" + PIN,
        ])
        if install.returncode:
            raise SetupError(
                "Panda3D installation failed. Check your internet connection "
                "and the pip message above. Use 64-bit Python 3.10–3.14 on "
                "a supported desktop platform, then run this launcher again. "
                "The launcher never installs an unpinned fallback version."
            )
    check = run([str(ENV_PYTHON), "-c", "from panda3d.core import PandaSystem; print(PandaSystem.getVersionString())"],
                capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != PIN:
        detail = check.stderr.strip() or check.stdout.strip()
        raise SetupError(
            "Panda3D is installed but could not load correctly. This may mean "
            "the Python/OS architecture is unsupported.\n" + detail
        )


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--bootstrap-help" in arguments:
        say("Asterion Expedition launcher\n"
            "  python3 bootstrap.py                Set up once, then play\n"
            "  python3 bootstrap.py --setup-only   Install dependencies only\n"
            "  python3 bootstrap.py --no-audio     Play without sound\n"
            "All other options are forwarded to main.py.\n"
            "Requires Python 3.10–3.14. The local .venv is reusable offline.")
        return 0
    setup_only = "--setup-only" in arguments
    arguments = [argument for argument in arguments if argument != "--setup-only"]
    if not (ROOT / "main.py").is_file():
        raise SetupError("main.py is missing. Extract the entire ZIP before launching.")
    prepare_environment()
    if setup_only:
        say("Setup complete. This copy is ready to launch offline.")
        return 0
    say("Starting Asterion Expedition…")
    result = run([str(ENV_PYTHON), str(ROOT / "main.py"), *arguments])
    # Normal application error codes pass straight through. POSIX signals map
    # to the standard shell convention (for example SIGINT becomes 130).
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as error:
        print("\nAsterion setup: " + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nLaunch cancelled.", file=sys.stderr)
        raise SystemExit(130)
