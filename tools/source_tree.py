"""
The --tree option of tools/benchmark_loop.py and tools/ocr_diff.py: run another copy of the
project (for example a commit exported with git archive) in place of this one.
"""
import atexit
import os
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives
        return False


def use_tree(tree: str, home_prefix: str, flat_layout: bool = False) -> bool:
    """Makes the imports that follow load the tree's code and points the data folder at a new
    temporary folder (default settings, the user's files untouched), removed at exit. Returns
    whether the tree has the src layout (src/ffvii_companion/); with flat_layout, a tree with
    engine/ at the top (before 2026-10-08) is accepted too.

    Raises ValueError for a tree without the package: on sys.path it would be skipped, and the
    project's editable install would answer the imports, so a run would measure this copy's code
    under the tree's name. Also raises when the package is already imported from elsewhere."""
    src = os.path.join(tree, "src")
    if os.path.isdir(os.path.join(src, "ffvii_companion")):
        path, package = src, "ffvii_companion"
    elif flat_layout and os.path.isdir(os.path.join(tree, "engine")):
        path, package = tree, "engine"
    else:
        expected = "src/ffvii_companion/ or engine/" if flat_layout else "src/ffvii_companion/"
        raise ValueError(f"No {expected} in {tree}: --tree takes a copy of the project")
    loaded = getattr(sys.modules.get(package), "__file__", None)
    if loaded and not _inside(loaded, path):
        raise ValueError(f"{package} is already imported from {os.path.dirname(loaded)}, not from {tree}")
    # First on sys.path, so the tree's code wins over the project's editable install
    sys.path.insert(0, path)
    home = tempfile.mkdtemp(prefix=home_prefix)
    atexit.register(shutil.rmtree, home, True)
    os.environ["FFVII_COMPANION_HOME"] = home
    return package == "ffvii_companion"
