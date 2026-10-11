import sys
import os
import time
import shutil
import subprocess
import tempfile
from pathlib import Path

APP_NAME = "FFVII_Companion"

# PyInstaller writes its work and output folders outside the project. The project lives in
# OneDrive, and letting OneDrive sync (and race against) a 250 MB build that is deleted,
# recreated or renamed can make it delete freshly built files afterwards.
LOCAL_BUILD_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "FFVIIBuild"

# Folders next to the packaged EXE that hold user data (settings, profile, logs, misdetection
# reports); never purged.
PRESERVED_DIRS = {"data", "logs", "reports"}


def safe_rmtree(target: Path, max_retries: int = 5, delay: float = 0.8):
    if not target.exists():
        return
    for attempt in range(max_retries):
        try:
            # Clear read-only flags that Windows/OneDrive may set
            for root, dirs, files in os.walk(target):
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), 0o777)
                    except Exception:
                        pass
            shutil.rmtree(target)
            return
        except Exception:
            time.sleep(delay)
    # Fallback to PowerShell forced removal if handle was lingering
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Remove-Item -Recurse -Force -LiteralPath '{target}' -ErrorAction SilentlyContinue"],
            check=False
        )
    except Exception:
        pass
    time.sleep(1.0)


def install_bundle(src: Path, dst: Path) -> None:
    """
    Updates dst in place from a freshly built bundle: copies every file (overwriting),
    then removes files and folders left over from the previous build. The folder itself is
    never deleted or renamed, and the user's data/, logs/ and reports/ next to the EXE are kept.
    Read-only attributes are cleared first: OneDrive sets them on folders, and Windows refuses
    to delete a read-only file or folder.
    Raises PermissionError when a file is locked (the companion is still running).
    """
    dst.mkdir(parents=True, exist_ok=True)
    new_files = set()
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            new_files.add(rel)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.chmod(target, 0o666)
            shutil.copy2(path, target)

    for path in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(dst)
        if rel.parts[0] in PRESERVED_DIRS:
            continue
        if path.is_file() and rel not in new_files:
            os.chmod(path, 0o666)
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            os.chmod(path, 0o777)
            path.rmdir()


def build():
    root = Path(__file__).resolve().parent.parent  # build_tools/ -> project root
    spec_file = root / "build_tools" / "ffvii_companion.spec"

    if not spec_file.exists():
        print(f"Error: Spec file not found at {spec_file}")
        sys.exit(1)

    print("=" * 65)
    print("  BUILDING NATIVE DESKTOP EXECUTABLE: FFVII COMPANION")
    print("=" * 65)

    # Thoroughly clean previous build artifacts (outside OneDrive)
    if LOCAL_BUILD_ROOT.exists():
        print(f"Cleaning previous build folder {LOCAL_BUILD_ROOT}...")
        safe_rmtree(LOCAL_BUILD_ROOT)

    python_exe = sys.executable
    cmd = [python_exe, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", str(LOCAL_BUILD_ROOT / "dist"), "--workpath", str(LOCAL_BUILD_ROOT / "work"),
           str(spec_file)]

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(root))

    if result.returncode != 0:
        safe_rmtree(LOCAL_BUILD_ROOT)
        print("=" * 65)
        print("  BUILD FAILED! Check compiler output above.")
        print("  The previous dist/ build (if any) was left untouched.")
        print("=" * 65)
        sys.exit(result.returncode)

    # Install the fresh build into dist/ only after PyInstaller succeeded
    dist_app = root / "dist" / APP_NAME
    print(f"Installing build into {dist_app}...")
    try:
        install_bundle(LOCAL_BUILD_ROOT / "dist" / APP_NAME, dist_app)
    except PermissionError as exc:
        print("=" * 65)
        print("  BUILD SUCCEEDED, BUT dist/ COULD NOT BE UPDATED")
        print(f"  {exc.strerror}: {exc.filename}")
        print("  If FFVII_Companion.exe is running, close it, then run this script again.")
        print("=" * 65)
        sys.exit(2)
    safe_rmtree(LOCAL_BUILD_ROOT)

    # Older versions of this script built inside the project (OneDrive); drop that work folder
    legacy_work = root / "build"
    if legacy_work.exists():
        safe_rmtree(legacy_work)

    exe_path = dist_app / f"{APP_NAME}.exe"
    print("=" * 65)
    print("  BUILD SUCCESSFUL!")
    print(f"  Executable location: {exe_path}")
    print("=" * 65)

if __name__ == "__main__":
    build()
