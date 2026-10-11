"""
End-to-end capture loop benchmark on recorded frames.

Replays a folder of recorded ROI frames (tools/record_frames.py) through the real
CaptureService loop in real time. Only the screen grab and the game-window lookup
are replaced; OCR, the text-change gate, phase transitions, sleeps and settled-phase
sleeps all run exactly as in the app. Reports process CPU time (all threads,
including ONNX Runtime workers), OCR passes, the phase share, when enemies were
first detected, and a timeline of OCR passes, grabs, CPU and phase per --window
seconds of the recording.

    .\\.venv\\Scripts\\python.exe -m tools.benchmark_loop <frames_dir> [--tree <source tree>] [--json out.json]

--tree points at another copy of the project (e.g. an older commit exported with
git archive) to benchmark that version against the same recording. Both layouts
work: src/ffvii_companion/ (current) and engine/ at the top (before 2026-10-08).
"""
import argparse
import glob
import json
import os
import sys
import tempfile
import threading
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay recorded frames through the capture loop")
    parser.add_argument("frames_dir")
    parser.add_argument("--tree", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--fps-recorded", type=float, default=1.0, help="Rate the frames were recorded at")
    parser.add_argument("--json", default=None, help="Write the summary to this JSON file")
    parser.add_argument("--window", type=int, default=10, help="Seconds per timeline entry (default 10)")
    args = parser.parse_args()

    tree = os.path.abspath(args.tree)
    # Paths given relative to the working directory, resolved before the chdir into the tree
    frames_dir = os.path.abspath(args.frames_dir)
    json_path = os.path.abspath(args.json) if args.json else None
    src_layout = os.path.isdir(os.path.join(tree, "src", "ffvii_companion"))
    # First on sys.path, so the tree's code wins over the project's editable install
    sys.path.insert(0, os.path.join(tree, "src") if src_layout else tree)
    os.chdir(tree)
    os.environ["FFVII_COMPANION_HOME"] = tempfile.mkdtemp(prefix="ffvii_bench_")

    import cv2
    import numpy as np

    files = sorted(glob.glob(os.path.join(frames_dir, "roi_*.png")))
    if not files:
        sys.exit(f"No roi_*.png frames in {args.frames_dir}")
    frames = []
    for path in files:
        bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        frames.append(np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)))
    height, width = frames[0].shape[:2]
    duration = len(frames) / args.fps_recorded

    if src_layout:
        from ffvii_companion.engine import capture_service as cs_module
    else:
        from engine import capture_service as cs_module

    svc = cs_module.capture_service
    start_wall = [0.0]

    class _Shot:
        def __init__(self, arr):
            self.raw = arr.tobytes()
            self.height, self.width = arr.shape[:2]

    grabs = [0]
    grab_times = []

    def fake_grab(_bbox):
        grabs[0] += 1
        grab_times.append(time.monotonic() - start_wall[0])
        idx = min(len(frames) - 1, int((time.monotonic() - start_wall[0]) * args.fps_recorded))
        return _Shot(frames[idx])

    window = {"hwnd": 0, "title": "FINAL FANTASY VII", "pid": 0, "left": 0, "top": 0,
              "width": int(round(width / 0.60)), "height": int(round(height / 0.18)), "source": "benchmark"}
    svc.capture_engine.sct.grab = fake_grab
    svc.window_finder.find_ffvii_window = lambda: dict(window)

    ocr_calls = [0]
    ocr_times = []
    original_detect = svc.detector.detect_text_lines

    def counting_detect(*a, **kw):
        ocr_calls[0] += 1
        ocr_times.append(time.monotonic() - start_wall[0])
        return original_detect(*a, **kw)

    svc.detector.detect_text_lines = counting_detect

    events = []
    svc.on_battle_detected = lambda enemies, title: events.append(
        {"t": round(time.monotonic() - start_wall[0], 2), "event": "detected", "title": title})
    svc.on_battle_cleared = lambda: events.append(
        {"t": round(time.monotonic() - start_wall[0], 2), "event": "cleared"})

    # Warm the OCR models outside the measured window (both versions load lazily)
    svc.detector.detect_text_lines(cv2.cvtColor(frames[0], cv2.COLOR_BGRA2BGR))
    ocr_calls[0] = 0
    ocr_times.clear()

    phases = []
    samples = []  # (seconds since start, phase, process CPU seconds)
    stop = threading.Event()

    def sample_phase():
        while not stop.wait(0.25):
            phases.append(svc.combat_phase)
            samples.append((time.monotonic() - start_wall[0], svc.combat_phase, time.process_time()))

    sampler = threading.Thread(target=sample_phase, daemon=True)
    cpu0 = time.process_time()
    start_wall[0] = time.monotonic()
    sampler.start()
    svc.start()
    time.sleep(duration)
    svc.stop()
    stop.set()
    cpu = time.process_time() - cpu0
    wall = time.monotonic() - start_wall[0]

    # Per-window breakdown (recording time = wall time), e.g. to isolate a summon animation
    timeline = []
    for t0 in range(0, int(wall), args.window):
        t1 = t0 + args.window
        rows = [s for s in samples if t0 <= s[0] < t1]
        window_phases = [s[1] for s in rows]
        cpu_pct = (round(100.0 * (rows[-1][2] - rows[0][2]) / (rows[-1][0] - rows[0][0]), 1)
                   if len(rows) > 1 and rows[-1][0] > rows[0][0] else None)
        timeline.append({
            "t": f"{t0}-{t1}s",
            "ocr": sum(1 for t in ocr_times if t0 <= t < t1),
            "grabs": sum(1 for t in grab_times if t0 <= t < t1),
            "cpu_percent": cpu_pct,
            "phase": max(set(window_phases), key=window_phases.count) if window_phases else None,
        })

    summary = {
        "tree": tree,
        "frames": len(frames),
        "wall_seconds": round(wall, 1),
        "process_cpu_seconds": round(cpu, 2),
        "avg_cpu_percent_of_one_core": round(100.0 * cpu / wall, 2),
        "ocr_passes": ocr_calls[0],
        "screen_grabs": grabs[0],
        "phase_share": {p: round(phases.count(p) / max(1, len(phases)), 3) for p in ("idle", "hunting", "settled")},
        "events": events,
        "timeline": timeline,
    }
    print(json.dumps(summary, indent=2))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
