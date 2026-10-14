"""
Packages a release of the desktop app for GitHub: dist/FFVII_Companion-<version>-windows-x64.zip.

Builds the EXE with build_exe.py (which updates dist/FFVII_Companion/ as usual), then zips that
build without the data folders next to the EXE (PRESERVED_DIRS: the settings, profile, logs and
reports of whoever built it), together with LICENSE, README.md and the docs/ it links to (so the
links work offline), the LAN launcher and THIRD_PARTY_LICENSES.txt (the license texts of Python,
the bundled OCR models and the packages in the bundle).
Refuses to run with uncommitted changes, so every release is built from a commit; the zip's
comment names it (and, with --allow-dirty, says the build had uncommitted changes).

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

ROOT = Path(__file__).resolve().parent.parent  # build_tools/ -> project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # run as a script: build_tools/ is on sys.path, not the root
from build_tools.build_exe import APP_NAME, PRESERVED_DIRS  # noqa: E402

LICENSE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|ThirdPartyNotices)", re.IGNORECASE)
# The WebView2 SDK's license (from its NuGet package): pywebview ships the SDK's DLLs without it
WEBVIEW2_LICENSE = Path(__file__).resolve().parent / "licenses" / "Microsoft.Web.WebView2.txt"
# The OCR models and post-processing bundled in the app's own package (Apache-2.0, from RapidOCR)
PPOCR_LICENSE = ROOT / "src" / "ffvii_companion" / "ppocr" / "LICENSE.txt"


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def bundle_top_level_names(app_dir: Path) -> set:
    """
    Top-level import names in a build: the modules in the EXE's PYZ archive, and the files and
    folders in _internal (extension modules, packages kept as files). Read from the build itself,
    since PyInstaller follows imports, not declared requirements: an installed requirement that
    the app never imports stays out (Pillow, which only tools/ uses), and the spec's excludes
    (setuptools) stay out even where a bundled package imports them.
    """
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(app_dir / f"{APP_NAME}.exe"))
    names = set()
    for name, entry in archive.toc.items():
        if entry[-1] == "z":  # the PYZ archive of the pure-Python modules
            names |= {module.split(".")[0] for module in archive.open_embedded_archive(name).toc}
    names |= {path.name.split(".")[0] for path in (app_dir / "_internal").iterdir()}
    return names


def bundled_distributions(top_level_names) -> list:
    """
    The installed distributions that provide these import names (standard library modules have
    none), without the app itself, plus PyInstaller, whose bootloader starts the EXE.
    """
    owners = md.packages_distributions()
    found = {}
    for name in top_level_names:
        for dist_name in owners.get(name, []):
            if _canonical(dist_name) != "ffvii-companion":
                found[_canonical(dist_name)] = md.distribution(dist_name)
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


def third_party_notices(version: str, distributions) -> str:
    """THIRD_PARTY_LICENSES.txt: Python's license, then each bundled distribution's."""
    out = [
        f"Third-party software in FFVII_Companion {version}",
        "",
        "FFVII_Companion.exe is built with PyInstaller. It contains Python and the packages below,",
        "each under its own license. The license texts follow where a package includes them;",
        "otherwise its license and home page are named. The companion itself is under the MIT",
        "License (LICENSE.txt).",
        "",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    out += [f"==== Python {sys.version.split()[0]} ====", "License: PSF-2.0 (and the licenses it lists)",
            "Home page: https://www.python.org", ""]
    if python_license.exists():
        out += [python_license.read_text(encoding="utf-8", errors="replace").strip(), ""]
    # Part of the app's package, so no distribution of its own lists it
    out += ["==== PP-OCRv4 models and post-processing from RapidOCR ====",
            "Files: _internal/ffvii_companion/ppocr/ (ch_PP-OCRv4_det_infer.onnx, ch_PP-OCRv4_rec_infer.onnx: "
            "PaddleOCR's PP-OCRv4 models in RapidOCR's ONNX conversion) and the module "
            "ffvii_companion.ppocr.postprocess in FFVII_Companion.exe (adapted from rapidocr_onnxruntime 1.4.4)",
            "License: Apache-2.0",
            "Home page: https://github.com/RapidAI/RapidOCR (models: https://github.com/PaddlePaddle/PaddleOCR)", "",
            "--- LICENSE ---", PPOCR_LICENSE.read_text(encoding="utf-8").strip(), ""]
    for dist in distributions:
        out += [f"==== {dist.metadata['Name']} {dist.version} ====", f"License: {_license_summary(dist)}"]
        if _home_page(dist):
            out.append(f"Home page: {_home_page(dist)}")
        out.append("")
        texts = _license_texts(dist)
        if not texts and "\n" in (dist.metadata.get("License") or ""):
            texts = [("License", dist.metadata["License"])]
        for name, text in texts:
            out += [f"--- {name} ---", text.strip(), ""]
        if _canonical(dist.metadata["Name"]) == "pywebview":
            # pywebview's license covers its own code, not the Microsoft binaries it ships
            out += ["==== Microsoft WebView2 SDK (in pywebview) ====",
                    "Files: _internal/webview/lib/Microsoft.Web.WebView2.Core.dll, "
                    "Microsoft.Web.WebView2.WinForms.dll and runtimes/*/native/WebView2Loader.dll",
                    "License: BSD-3-Clause (the WebView2 SDK's license, "
                    "https://www.nuget.org/packages/Microsoft.Web.WebView2)", "",
                    "--- LICENSE ---", WEBVIEW2_LICENSE.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(out) + "\n"


def release_files(app_dir: Path):
    """(path, path relative to app_dir) of every file of the build, without the PRESERVED_DIRS."""
    for path in sorted(app_dir.rglob("*")):
        rel = path.relative_to(app_dir)
        if path.is_file() and rel.parts[0] not in PRESERVED_DIRS:
            yield path, rel


def doc_files() -> list:
    """The committed files of docs/ (paths relative to the root): the pages README.md links to and
    their images. Read from Git, so nothing untracked in the folder ends up in a release."""
    return _git("ls-files", "--", "docs").splitlines() if (ROOT / "docs").is_dir() else []


def project_files() -> list:
    """(source, path in the release folder) of the project's files the zip adds to the build:
    the license, the user guide with the docs/ it links to, and the LAN launcher."""
    files = [(ROOT / "LICENSE", "LICENSE.txt"), (ROOT / "README.md", "README.md")]
    files += [(ROOT / rel, rel) for rel in doc_files()]
    files.append((ROOT / "Launch_LAN_Mode.bat", "Launch_LAN_Mode.bat"))
    return files


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

    dirty = bool(_git("status", "--porcelain"))
    if dirty and not args.allow_dirty:
        sys.exit("Uncommitted changes: commit them first, so the release matches a commit "
                 "(or pass --allow-dirty).")
    commit = _git("rev-parse", "--short", "HEAD") + (" plus uncommitted changes" if dirty else "")
    init = (ROOT / "src" / "ffvii_companion" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)

    build = subprocess.run([sys.executable, str(ROOT / "build_tools" / "build_exe.py")], cwd=ROOT)
    if build.returncode != 0:
        sys.exit(build.returncode)

    app_dir = ROOT / "dist" / APP_NAME
    notices = third_party_notices(version, bundled_distributions(bundle_top_level_names(app_dir)))
    out = ROOT / "dist" / f"{APP_NAME}-{version}-windows-x64.zip"
    partial = out.with_name(out.name + ".partial")
    print(f"Packaging {out}...")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in release_files(app_dir):
            zf.write(path, f"{APP_NAME}/{rel.as_posix()}")
        for path, name in project_files():
            zf.write(path, f"{APP_NAME}/{name}")
        zf.writestr(f"{APP_NAME}/THIRD_PARTY_LICENSES.txt", notices)
        zf.comment = f"{APP_NAME} {version}, built from commit {commit}".encode()
    os.replace(partial, out)
    print(f"Release package: {out} ({out.stat().st_size / 1e6:.0f} MB), version {version}, commit {commit}")


if __name__ == "__main__":
    main()
