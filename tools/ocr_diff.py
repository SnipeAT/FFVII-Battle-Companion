"""
OCR output of recorded frames, and the differences between two runs: the check for a change
to the OCR pipeline beyond the test fixtures.

`record` runs every frame of one or more frame folders (recordings made with
tools/record_frames.py, or tests/fixtures/real) through the capture loop's OCR path: the
1280x720 downscale of CaptureService._prepare_ocr_frame(), then the detector with the default
settings and every box recognized (no recognition cache, so each frame stands alone). Per frame
it writes the raw OCR lines (text, score, box), the lines the detector keeps, and what they
mean: the enemies matched (name, count, instance letter) and whether the battle HUD shows, out
of battle and in battle.

`compare` lists the frames whose detection outcome changed (enemies or HUD), then those whose
kept lines, raw text, or only scores and boxes changed. It exits with 1 when an outcome
changed, the two files hold different frames or no frame was compared, else 0 (2 for a usage
error, such as a file that is not a record of this format).

    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff record before.json recordings\\session1 recordings\\session2
    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff record after.json recordings\\session1 recordings\\session2 --tree <source tree>
    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff compare before.json after.json

--tree points at another copy of the project (for example a commit exported with git archive),
as in tools/benchmark_loop.py, but only one with src/ffvii_companion/ of version 2.0.0 or later
(record() calls the capture loop's internals). That copy runs with this environment's packages:
a commit that needs a dependency this one dropped needs it installed first (Shapely, for
commits from before ppocr/postprocess.py stopped importing it).
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Sequence

from tools.source_tree import PROJECT_ROOT, use_tree

FORMAT = 1
FRAME_FIELDS = {"lines", "kept", "enemies", "hud"}  # what compare() reads of every frame


def frame_files(folder: str) -> List[str]:
    """The frames of a folder in order: roi_*.png of a recording, else every PNG (fixtures)."""
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".png"))
    recorded = [n for n in names if n.startswith("roi_")]
    return [os.path.join(folder, n) for n in (recorded or names)]


def folder_key(folder: str) -> str:
    """The name a folder's frames are keyed under: its own name, so records of the same
    recordings made from different places compare."""
    return os.path.basename(os.path.normpath(folder))


def folder_frames(folders: Sequence[str]) -> Dict[str, List[str]]:
    """The frames of each folder, keyed by folder_key(). Refuses folders that would make a record
    incomplete: a missing one, two with the same name (their frames would share keys) or one
    without frames (a record of nothing compares as unchanged)."""
    frames: Dict[str, List[str]] = {}
    seen: Dict[str, str] = {}
    for folder in folders:
        if not os.path.isdir(folder):
            raise ValueError(f"No folder {folder}")
        key = folder_key(folder)
        if key in seen:
            raise ValueError(f"Two folders are named {key!r} ({seen[key]}, {folder}): their frames would share keys")
        seen[key] = folder
        frames[key] = frame_files(folder)
        if not frames[key]:
            raise ValueError(f"No PNG frames in {folder}")
    return frames


def record(frames_by_folder: Dict[str, List[str]], progress=None) -> Dict[str, Any]:
    """OCR output and detection outcome of every frame of folder_frames(), keyed
    "<folder name>/<file>"."""
    import cv2
    from ffvii_companion.engine.calibration import calibration_manager
    from ffvii_companion.engine.capture_service import CaptureService

    # Built from the current settings, as the capture loop's detector is
    svc = CaptureService()
    detector = svc.detector

    # The raw lines of the pass detect_text_lines() runs, before it filters them
    last = {}
    engine_run = detector._ocr.run

    def recording_run(img, use_cache=True):
        last["result"] = engine_run(img, use_cache=use_cache)
        return last["result"]

    detector._ocr.run = recording_run

    total = sum(len(paths) for paths in frames_by_folder.values())
    frames: Dict[str, Any] = {}
    for key, paths in frames_by_folder.items():
        for path in paths:
            bgr = cv2.imread(path, cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError(f"Cannot read {path}")
            frame = svc._prepare_ocr_frame(cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA))
            kept = detector.detect_text_lines(frame, use_cache=False)
            enemies = detector.identify_enemies_from_lines(kept)
            frames[f"{key}/{os.path.basename(path)}"] = {
                "lines": [[l.text, round(l.score, 4), list(l.box)] for l in last["result"].lines],
                "kept": kept,
                "enemies": [[e["name"], e["count"], e["instance_letter"]] for e in enemies],
                "hud": [detector.is_combat_hud_active(kept, False), detector.is_combat_hud_active(kept, True)],
            }
            if progress:
                progress(len(frames), total)
    cfg = calibration_manager.get_config()
    return {
        "format": FORMAT,
        "settings": {k: cfg[k] for k in ("det_limit_side_len", "confidence_threshold")},
        "frames": frames,
    }


def load(path: str) -> Dict[str, Any]:
    """A record written by `record`. Refuses a file of another format, or with a frame that lacks
    a field: compare() would misread it or stop with a KeyError."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    frames = data.get("frames") if isinstance(data, dict) else None
    if (not isinstance(frames, dict) or data.get("format") != FORMAT
            or not all(isinstance(frame, dict) and FRAME_FIELDS <= frame.keys() for frame in frames.values())):
        raise ValueError(f"{path} is not an ocr_diff record of format {FORMAT}")
    return data


def compare(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Frame-by-frame differences between two record() results."""
    a, b = before["frames"], after["frames"]
    common = [k for k in a if k in b]
    result: Dict[str, Any] = {
        "frames": len(common),
        "only_before": [k for k in a if k not in b],
        "only_after": [k for k in b if k not in a],
        "settings_differ": before.get("settings") != after.get("settings"),
        "outcome": [], "kept": [], "text": [], "scores_or_boxes": [],
    }
    for k in common:
        fa, fb = a[k], b[k]
        if fa["enemies"] != fb["enemies"] or fa["hud"] != fb["hud"]:
            result["outcome"].append({"frame": k,
                                      "before": {"enemies": fa["enemies"], "hud": fa["hud"]},
                                      "after": {"enemies": fb["enemies"], "hud": fb["hud"]}})
        if fa["kept"] != fb["kept"]:
            result["kept"].append({"frame": k, "before": fa["kept"], "after": fb["kept"]})
        texts_a, texts_b = [l[0] for l in fa["lines"]], [l[0] for l in fb["lines"]]
        if texts_a != texts_b:
            result["text"].append({"frame": k, "before": texts_a, "after": texts_b})
        elif fa["lines"] != fb["lines"]:
            result["scores_or_boxes"].append(k)
    return result


def failed(diff: Dict[str, Any]) -> bool:
    """Whether a comparison shows a regression or proves nothing: an outcome changed, the records
    hold different frames, or no frame was compared."""
    return bool(diff["outcome"] or diff["only_before"] or diff["only_after"] or not diff["frames"])


def report(diff: Dict[str, Any], show: int) -> str:
    """The comparison as text: counts, then up to `show` frames of each kind."""
    out = [f"Frames compared: {diff['frames']}"]
    if diff["only_before"] or diff["only_after"]:
        out.append(f"Frames in only one file: {len(diff['only_before'])} before, {len(diff['only_after'])} after")
    if diff["settings_differ"]:
        out.append("Warning: the two runs used different OCR settings")
    out.append(f"Detection outcome changed: {len(diff['outcome'])}")
    out.append(f"Kept lines changed: {len(diff['kept'])}")
    out.append(f"Raw OCR text changed: {len(diff['text'])}")
    out.append(f"Only scores or boxes changed: {len(diff['scores_or_boxes'])}")
    for title, rows in (("Detection outcome", diff["outcome"]), ("Kept lines", diff["kept"]),
                        ("Raw OCR text", diff["text"])):
        if rows and show:
            out.append(f"\n{title} (first {min(show, len(rows))} of {len(rows)}):")
            for row in rows[:show]:
                out.append(f"  {row['frame']}")
                out.append(f"    before: {json.dumps(row['before'], ensure_ascii=False)}")
                out.append(f"    after:  {json.dumps(row['after'], ensure_ascii=False)}")
    return "\n".join(out)


def frame_count(value: str) -> int:
    """argparse type of --show: a whole number, 0 or more."""
    count = int(value)
    if count < 0:
        raise argparse.ArgumentTypeError(f"{value} is below 0")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Record OCR output of frame folders, or compare two records")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="OCR every frame and write the result")
    rec.add_argument("out")
    rec.add_argument("folders", nargs="+")
    rec.add_argument("--tree", default=PROJECT_ROOT)
    cmp_ = sub.add_parser("compare", help="Differences between two records")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    cmp_.add_argument("--show", type=frame_count, default=20, help="Frames listed per kind of change (default 20)")
    args = parser.parse_args()

    if args.command == "compare":
        try:
            before, after = load(args.before), load(args.after)
        except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError
            parser.error(str(e))
        diff = compare(before, after)
        print(report(diff, args.show))
        sys.exit(1 if failed(diff) else 0)

    # Paths given relative to the working directory, resolved before the tree goes on sys.path
    out = os.path.abspath(args.out)
    tree = os.path.abspath(args.tree)
    # Checked before the OCR starts; errors raised during it are the tree's and keep their traceback
    try:
        frames_by_folder = folder_frames([os.path.abspath(f) for f in args.folders])
        use_tree(tree, "ffvii_ocr_diff_")
    except ValueError as e:
        parser.error(str(e))

    t0 = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"{done}/{total} frames, {time.monotonic() - t0:.0f} s", file=sys.stderr)

    result = record(frames_by_folder, progress)
    result["tree"] = tree
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(result['frames'])} frames to {out}")


if __name__ == "__main__":
    main()
