"""
Packages a release of the desktop app for GitHub: dist/FFVII_Companion-<version>-windows-x64.zip.

Builds the EXE with build_exe.py (which updates dist/FFVII_Companion/ as usual), then zips that
build without the data folders next to the EXE (PRESERVED_DIRS: the settings, profile, logs and
reports of whoever built it), together with LICENSE, README.md, the LAN launcher and
THIRD_PARTY_LICENSES.txt (the license texts of Python and of the packages in the bundle).
Refuses to run with uncommitted changes, so every release is built from a commit; the zip's
comment names it.

    .\\.venv\\Scripts\\python.exe build_tools\\package_release.py
"""
import argparse
import importlib.metadata as md
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent  # build_tools/ -> project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # run as a script: build_tools/ is on sys.path, not the root
from build_tools.build_exe import APP_NAME, PRESERVED_DIRS  # noqa: E402

LICENSE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|ThirdPartyNotices)", re.IGNORECASE)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def bundled_distributions() -> list:
    """
    The installed distributions the EXE contains: the app's runtime dependencies, recursively
    (requested extras such as uvicorn[standard] included, the dev extra not), and PyInstaller,
    whose bootloader starts the EXE. A few may be left out of the bundle by the spec's excludes.
    """
    found = {}
    done = set()  # (canonical name, extra) pairs whose requirements were followed
    todo = [("ffvii-companion", "")]
    while todo:
        name, extra = todo.pop()
        key = _canonical(name)
        if (key, extra) in done:
            continue
        done.add((key, extra))
        try:
            dist = found.get(key) or md.distribution(name)
        except md.PackageNotFoundError:
            continue  # a dependency for another platform or Python version
        found[key] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": extra}):
                todo.append((req.name, ""))
                todo.extend((req.name, e) for e in req.extras)
    found.pop("ffvii-companion", None)
    found["pyinstaller"] = md.distribution("pyinstaller")
    return [found[k] for k in sorted(found)]


def _license_summary(dist) -> str:
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    short = (meta.get("License") or "").strip()
    if short and len(short) <= 100 and "\n" not in short:
        classifiers.insert(0, short)
    return ", ".join(dict.fromkeys(classifiers)) or "see the license text below"


def _home_page(dist) -> str:
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"]
    urls = [u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return urls[0] if urls else ""


def _license_texts(dist) -> list:
    """
    (file name, text) of the license files in the distribution's .dist-info folder, or else at the
    top of its package folders (onnxruntime keeps its LICENSE and ThirdPartyNotices there).
    """
    files = [f for f in dist.files or [] if LICENSE_FILE.match(f.name)]
    in_info = [f for f in files if any(p.endswith(".dist-info") for p in f.parts[:-1])]
    texts = []
    for f in in_info or [f for f in files if len(f.parts) == 2]:
        try:
            texts.append((f.name, Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return texts


def third_party_notices(version: str) -> str:
    """THIRD_PARTY_LICENSES.txt: Python's license, then each bundled distribution's."""
    out = [
        f"Third-party software in FFVII_Companion {version}",
        "",
        "FFVII_Companion.exe is built with PyInstaller. It contains Python and the packages below,",
        "each under its own license; their license texts follow. The companion itself is under the",
        "MIT License (LICENSE.txt).",
        "",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    out += [f"==== Python {sys.version.split()[0]} ====", "License: PSF-2.0 (and the licenses it lists)",
            "Home page: https://www.python.org", ""]
    if python_license.exists():
        out += [python_license.read_text(encoding="utf-8", errors="replace").strip(), ""]
    for dist in bundled_distributions():
        out += [f"==== {dist.metadata['Name']} {dist.version} ====", f"License: {_license_summary(dist)}"]
        if _home_page(dist):
            out.append(f"Home page: {_home_page(dist)}")
        out.append("")
        texts = _license_texts(dist)
        if not texts and "\n" in (dist.metadata.get("License") or ""):
            texts = [("License", dist.metadata["License"])]
        for name, text in texts:
            out += [f"--- {name} ---", text.strip(), ""]
    return "\n".join(out) + "\n"


def release_files(app_dir: Path):
    """(path, path relative to app_dir) of every file of the build, without the PRESERVED_DIRS."""
    for path in sorted(app_dir.rglob("*")):
        rel = path.relative_to(app_dir)
        if path.is_file() and rel.parts[0] not in PRESERVED_DIRS:
            yield path, rel


def _git(*args) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the EXE and zip it for a GitHub release")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="Package even with uncommitted changes (the zip then matches no commit)")
    args = parser.parse_args()

    if not args.allow_dirty and _git("status", "--porcelain"):
        sys.exit("Uncommitted changes: commit them first, so the release matches a commit "
                 "(or pass --allow-dirty).")
    commit = _git("rev-parse", "--short", "HEAD")
    init = (ROOT / "src" / "ffvii_companion" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)

    build = subprocess.run([sys.executable, str(ROOT / "build_tools" / "build_exe.py")], cwd=ROOT)
    if build.returncode != 0:
        sys.exit(build.returncode)

    app_dir = ROOT / "dist" / APP_NAME
    out = ROOT / "dist" / f"{APP_NAME}-{version}-windows-x64.zip"
    partial = out.with_name(out.name + ".partial")
    print(f"Packaging {out}...")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in release_files(app_dir):
            zf.write(path, f"{APP_NAME}/{rel.as_posix()}")
        zf.write(ROOT / "LICENSE", f"{APP_NAME}/LICENSE.txt")
        zf.write(ROOT / "README.md", f"{APP_NAME}/README.md")
        zf.write(ROOT / "Launch_LAN_iPad.bat", f"{APP_NAME}/Launch_LAN_iPad.bat")
        zf.writestr(f"{APP_NAME}/THIRD_PARTY_LICENSES.txt", third_party_notices(version))
        zf.comment = f"{APP_NAME} {version}, built from commit {commit}".encode()
    os.replace(partial, out)
    print(f"Release package: {out} ({out.stat().st_size / 1e6:.0f} MB), version {version}, commit {commit}")


if __name__ == "__main__":
    main()
