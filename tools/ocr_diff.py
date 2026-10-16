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
changed or the two files hold different frames, else 0.

    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff record before.json recordings\\session1 recordings\\session2
    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff record after.json recordings\\session1 recordings\\session2 --tree <source tree>
    .\\.venv\\Scripts\\python.exe -m tools.ocr_diff compare before.json after.json

--tree points at another copy of the project (for example a commit exported with git archive),
as in tools/benchmark_loop.py.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Sequence

FORMAT = 1


def frame_files(folder: str) -> List[str]:
    """The frames of a folder in order: roi_*.png of a recording, else every PNG (fixtures)."""
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".png"))
    recorded = [n for n in names if n.startswith("roi_")]
    return [os.path.join(folder, n) for n in (recorded or names)]


def record(folders: Sequence[str], progress=None) -> Dict[str, Any]:
    """OCR output and detection outcome of every frame in folders, keyed "<folder name>/<file>"."""
    import cv2
    from ffvii_companion.engine.calibration import calibration_manager
    from ffvii_companion.engine.capture_service import CaptureService

    svc = CaptureService()
    cfg = calibration_manager.get_config()
    svc._sync_detector_settings(cfg)
    detector = svc.detector

    # The raw lines of the pass detect_text_lines() runs, before it filters them
    last = {}
    engine_run = detector._ocr.run

    def recording_run(img, use_cache=True):
        last["result"] = engine_run(img, use_cache=use_cache)
        return last["result"]

    detector._ocr.run = recording_run

    paths = [p for folder in folders for p in frame_files(folder)]
    frames: Dict[str, Any] = {}
    for i, path in enumerate(paths):
        bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f"Cannot read {path}")
        frame = svc._prepare_ocr_frame(cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA))
        kept = detector.detect_text_lines(frame, use_cache=False)
        enemies = detector.identify_enemies_from_lines(kept)
        key = f"{os.path.basename(os.path.normpath(os.path.dirname(path)))}/{os.path.basename(path)}"
        frames[key] = {
            "lines": [[l.text, round(l.score, 4), list(l.box)] for l in last["result"].lines],
            "kept": kept,
            "enemies": [[e["name"], e.get("count", 1), e.get("instance_letter")] for e in enemies],
            "hud": [detector.is_combat_hud_active(kept, False), detector.is_combat_hud_active(kept, True)],
        }
        if progress:
            progress(i + 1, len(paths))
    return {
        "format": FORMAT,
        "settings": {k: cfg[k] for k in ("det_limit_side_len", "confidence_threshold")},
        "frames": frames,
    }


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Record OCR output of frame folders, or compare two records")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="OCR every frame and write the result")
    rec.add_argument("out")
    rec.add_argument("folders", nargs="+")
    rec.add_argument("--tree", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cmp_ = sub.add_parser("compare", help="Differences between two records")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    cmp_.add_argument("--show", type=int, default=20, help="Frames listed per kind of change (default 20)")
    args = parser.parse_args()

    if args.command == "compare":
        with open(args.before, encoding="utf-8") as f:
            before = json.load(f)
        with open(args.after, encoding="utf-8") as f:
            after = json.load(f)
        diff = compare(before, after)
        print(report(diff, args.show))
        sys.exit(1 if diff["outcome"] or diff["only_before"] or diff["only_after"] else 0)

    # Paths given relative to the working directory, resolved before the tree goes on sys.path
    out = os.path.abspath(args.out)
    folders = [os.path.abspath(f) for f in args.folders]
    tree = os.path.abspath(args.tree)
    # First on sys.path, so the tree's code wins over the project's editable install
    sys.path.insert(0, os.path.join(tree, "src"))
    os.environ["FFVII_COMPANION_HOME"] = tempfile.mkdtemp(prefix="ffvii_ocr_diff_")

    t0 = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"{done}/{total} frames, {time.monotonic() - t0:.0f} s", file=sys.stderr)

    result = record(folders, progress)
    result["tree"] = tree
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(result['frames'])} frames to {out}")


if __name__ == "__main__":
    main()
