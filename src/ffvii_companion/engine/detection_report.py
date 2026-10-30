"""
Misdetection reports: the dashboard's "wrong detection" button saves the capture-region frames
with what the OCR read and matched, in the format of tests/fixtures/real (PNG frames at the OCR
input size plus a manifest.json whose "frames" entries hold the expected result), so a reported
misdetection can be turned into a regression test by copying the frames and manifest entries.
"""
import datetime
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ffvii_companion.compendium import compendium
from ffvii_companion.paths import get_user_data_dir

REPORTS_DIRNAME = "reports"
MAX_EXPECTED_CHARS = 500
MAX_NOTE_CHARS = 1000
RE_EXPECTED_ITEM = re.compile(r"[x×*] ?(\d+)$", re.IGNORECASE)              # "Devil Ride x2"
# "2x Devil Ride" (the dashboard's form), "2 x Devil Ride", "2 Devil Ride", on items whose spaces are
# already single. One literal space per place: runs of \s on both sides of the optional "x" backtracked
# against each other (polynomial time on "0" and many spaces).
RE_EXPECTED_PREFIX = re.compile(r"^(\d+)(?: ?[x×*])? (\S.*)$", re.IGNORECASE)


class ReportError(ValueError):
    pass


def reports_dir() -> Path:
    return get_user_data_dir() / REPORTS_DIRNAME


def parse_expected(text: Optional[str]) -> Tuple[Dict[str, int], List[str]]:
    """
    Parses what the user says is on screen, e.g. "Devil Ride x2, Kalm Fang" or "2x Devil Ride",
    into canonical enemy names with counts. Returns (enemies, unresolved items); an item with a
    count of 0 is unresolved.
    """
    enemies: Dict[str, int] = {}
    unresolved: List[str] = []
    for raw_item in re.split(r"[,;\n]+", (text or "")[:MAX_EXPECTED_CHARS]):
        item = " ".join(raw_item.split())
        if not item:
            continue
        suffix = RE_EXPECTED_ITEM.search(item)
        prefix = RE_EXPECTED_PREFIX.match(item)
        if compendium.get_by_normalized_name(item):
            name, count = item, 1  # a name with a number of its own ("8 Eye" is not eight Eyes)
        elif suffix:
            name, count = item[:suffix.start()].strip(), int(suffix.group(1))
        elif prefix:
            name, count = prefix.group(2), int(prefix.group(1))
        else:
            name, count = item, 1
        enemy = compendium.match_enemy(name) if name and count > 0 else None
        if enemy:
            enemies[enemy["name"]] = max(enemies.get(enemy["name"], 0), min(count, 8))
        else:
            unresolved.append(item)
    return enemies, unresolved


def _write_png(path: Path, frame_bgr: np.ndarray):
    # imencode + write_bytes instead of imwrite: works with non-ASCII Windows paths
    ok, buf = cv2.imencode(".png", frame_bgr)
    if not ok:
        raise ReportError(f"could not encode {path.name}")
    path.write_bytes(buf.tobytes())


def _new_report_dir(base: Path, now: datetime.datetime) -> Path:
    stem = now.strftime("%Y%m%d-%H%M%S")
    for i in range(100):
        candidate = base / (stem if i == 0 else f"{stem}-{i}")
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            continue
    raise ReportError("could not create a report folder")


def write_report(frames: Dict[str, Dict[str, Any]], expected_text: Optional[str], note: Optional[str],
                 context: Dict[str, Any], base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Writes a report folder. frames maps a file name to {"image": BGR array, "lines": [...],
    "detected": {name: count}, "hud": bool}. Each manifest frame entry uses the expected enemies
    when the user gave them, otherwise what was detected (marked needs_review).
    Returns {"folder", "path", "files", "expected", "unresolved", "needs_review"}. A folder that
    cannot be written completely (a full disk, a file that cannot be encoded) is removed again.
    """
    if not frames:
        raise ReportError("no capture frame available: start capture or open the game first")
    expected, unresolved = parse_expected(expected_text)
    needs_review = not expected
    now = datetime.datetime.now()

    manifest_frames = {}
    report_frames = {}
    for file_name, info in frames.items():
        manifest_frames[file_name] = {"enemies": expected if expected else dict(info["detected"]),
                                      "hud": bool(info["hud"])}
        report_frames[file_name] = {"recognized_lines": list(info["lines"]),
                                    "detected": dict(info["detected"]), "hud": bool(info["hud"])}

    manifest = {
        "source": ("Misdetection report saved from the dashboard: capture-region frames downscaled to the "
                   "OCR input size, the format of tests/fixtures/real. Copy the frames and their 'frames' "
                   "entries into tests/fixtures/real to add them to the regression tests."),
        "frames": manifest_frames,
        "report": {
            "created": now.isoformat(timespec="seconds"),
            "note": (note or "")[:MAX_NOTE_CHARS],
            "expected_text": (expected_text or "")[:MAX_EXPECTED_CHARS],
            "unresolved_expected": unresolved,
            "needs_review": needs_review,
            "frames": report_frames,
            **context,
        },
    }
    folder = _new_report_dir(base_dir or reports_dir(), now)
    try:
        for file_name, info in frames.items():
            _write_png(folder / file_name, info["image"])
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"folder": folder.name, "path": str(folder), "files": sorted(frames) + ["manifest.json"],
            "expected": expected, "unresolved": unresolved, "needs_review": needs_review}
