"""
Where the app's files are: read-only resources next to the code (PACKAGE_DIR) and the writable
data folder (get_user_data_dir(): settings, player profile, reports, logs), and how its JSON
files are read and written (read_json(), write_json_atomic()). Nothing here creates a folder on
import; each writer creates its own on its first write, so importing the app writes nothing.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any

# The ffvii_companion package folder. Its read-only resources (data/*.json, server/static/, the
# OCR models in ppocr/) sit next to the code: src/ffvii_companion/ from source,
# <_MEIPASS>/ffvii_companion/ in the EXE (PyInstaller sets __file__ of bundled modules to their
# path inside sys._MEIPASS).
PACKAGE_DIR = Path(__file__).resolve().parent

# Optional override for the writable data directory (settings, player profile, reports, logs).
# The test suite points this at a temporary folder so test runs never modify the user's files.
USER_DIR_ENV = "FFVII_COMPANION_HOME"

# Writable data directory of runs from source (desktop app, preview server, tools), in the project
# root and git-ignored. It has the layout of the packaged app's folder: data/, logs/, reports/.
DEV_HOME_DIRNAME = ".dev_home"


def project_root() -> Path:
    """The source checkout (src/ffvii_companion -> project root). Only meaningful from source."""
    return PACKAGE_DIR.parent.parent


def get_user_data_dir() -> Path:
    """
    Returns the directory for writable, persistent files: data/calibration.json (settings),
    data/profile.json (player profile), reports/ (misdetection reports) and logs/.
    Packaged (PyInstaller): the folder of the executable.
    From source: .dev_home/ in the project, so runs from source never write into the source tree.
    The FFVII_COMPANION_HOME environment variable overrides both (used by the tests).
    """
    override = os.environ.get(USER_DIR_ENV)
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return project_root() / DEV_HOME_DIRNAME


def read_json(path: Path) -> Any:
    """Reads a JSON file of the data folder. utf-8-sig: Notepad ("UTF-8 with BOM") and
    PowerShell 5.1 write a byte order mark."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Any) -> None:
    """Writes a JSON file of the data folder atomically (temp file + replace), so a crash never
    leaves a torn file, creating its folder first. Raises OSError when the file cannot be written
    (for example while another program holds it); the old file then stays as it was."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
