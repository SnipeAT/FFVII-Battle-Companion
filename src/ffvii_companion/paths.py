import os
import sys
from pathlib import Path

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
