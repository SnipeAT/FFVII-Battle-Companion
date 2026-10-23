"""
Records the capture region (ROI) of the running FFVII window to PNG files.

Use it to collect real frames for tuning and regression tests while you play:

    .\\.venv\\Scripts\\python.exe -m tools.record_frames recordings\\session1 --seconds 120

Only the configured ROI (calibration settings) is saved, at ~1 frame per second,
together with a frames.jsonl index. Replay the result through the capture loop
with tools/benchmark_loop.py.
"""
import argparse
import ctypes
import json
import os
import sys
import time

if sys.platform == "win32":
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import cv2  # noqa: E402
import mss  # noqa: E402
import numpy as np  # noqa: E402

from ffvii_companion.engine import win_qos  # noqa: E402
from ffvii_companion.engine.calibration import calibration_manager  # noqa: E402
from ffvii_companion.engine.window_finder import FFVIIWindowFinder  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Record FFVII capture-region frames")
    parser.add_argument("out_dir")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between frames (default 1.0)")
    args = parser.parse_args()

    # A second recording into the same folder would overwrite the first one's frames from roi_0000
    # on and add to its index: the folder would hold a mix of both
    existing = os.listdir(args.out_dir) if os.path.isdir(args.out_dir) else []
    if any(n == "frames.jsonl" or n.startswith("roi_") for n in existing):
        parser.error(f"{args.out_dir} already holds a recording: record into a new folder")
    win_qos.apply_process_efficiency_mode()
    os.makedirs(args.out_dir, exist_ok=True)
    roi = calibration_manager.get_config()["roi"]
    finder = FFVIIWindowFinder()
    sct = mss.MSS()

    count = 0
    deadline = time.time() + args.seconds
    with open(os.path.join(args.out_dir, "frames.jsonl"), "a", encoding="utf-8") as index:
        while time.time() < deadline:
            t0 = time.time()
            win = finder.find_ffvii_window()
            if win:
                bbox = {
                    "top": int(win["top"] + win["height"] * roi["top_pct"]),
                    "left": int(win["left"] + win["width"] * roi["left_pct"]),
                    "width": max(64, int(win["width"] * roi["width_pct"])),
                    "height": max(64, int(win["height"] * roi["height_pct"])),
                }
                shot = sct.grab(bbox)
                frame = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
                name = f"roi_{count:04d}.png"
                # imencode + write instead of imwrite, which fails silently on non-ASCII Windows paths
                ok, png = cv2.imencode(".png", frame[..., :3], [cv2.IMWRITE_PNG_COMPRESSION, 3])
                if not ok:
                    sys.exit(f"Could not encode {name}")
                with open(os.path.join(args.out_dir, name), "wb") as f:
                    f.write(png.tobytes())
                index.write(json.dumps({"file": name, "t": round(t0, 3), "bbox": bbox}) + "\n")
                index.flush()
                count += 1
            time.sleep(max(0.05, args.interval - (time.time() - t0)))
    print(f"Recorded {count} frames to {os.path.abspath(args.out_dir)}")


if __name__ == "__main__":
    main()
