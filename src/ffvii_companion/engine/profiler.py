import time
import math
import logging
import threading
from collections import deque
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ffvii-profiler")

from ffvii_companion.paths import get_user_data_dir

LOGS_DIR = get_user_data_dir() / "logs"
PERF_LOG_FILE = LOGS_DIR / "performance.log"

# Per-frame timing fields that are only meaningful on frames where OCR actually ran
OCR_ONLY_KEYS = ("cvt_color_ms", "ocr_det_ms", "ocr_rec_ms", "ocr_total_ms")
TIMING_KEYS = ("window_find_ms", "grab_ms", "gate_ms", "cvt_color_ms", "ocr_det_ms", "ocr_rec_ms",
               "ocr_total_ms", "match_ms", "total_frame_ms", "cpu_ms")

class PerformanceTracker:
    """
    Thread-safe performance monitoring and profiling system for screen capture,
    OpenCV processing, OCR inference, and compendium enemy matching.
    Tracks the real sampling rate, how many frames needed OCR (versus being skipped
    by the text-change gate), and the capture thread's CPU usage.
    Includes automatic log file rotation and strictly bounded memory consumption.
    The log file (and its folder) is created with the first summary line, never on import:
    importing the engine or the server (tools, tests, the preview server) writes nothing.
    """
    def __init__(self, history_size: int = 100, log_interval_frames: int = 45, max_log_bytes: int = 2 * 1024 * 1024, max_backup_files: int = 2):
        self.history_size = history_size
        self.log_interval_frames = log_interval_frames
        self.max_log_bytes = max_log_bytes
        self.max_backup_files = max_backup_files
        self.history: deque = deque(maxlen=history_size)
        self._lock = threading.Lock()

        self.total_frames = 0
        self.total_combat_frames = 0
        self.total_idle_frames = 0
        self.total_ocr_frames = 0
        self.session_start_time = time.time()
        self.last_periodic_log_time = time.time()
        self._frames_since_log = 0

        self.log_file_path = PERF_LOG_FILE

    def _ensure_log_dir(self):
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_file_path.exists():
                with open(self.log_file_path, "w", encoding="utf-8") as f:
                    f.write("# FFVII Tactical Battle Companion - Performance Profiling Log\n")
                    f.write("# Format: Timestamp | Summary | Stage Breakdown | Resolution\n\n")
        except Exception as e:
            logger.warning(f"Failed to initialize performance log directory: {e}")

    def _rotate_log_if_needed(self):
        """Rotates the log file if it exceeds max_log_bytes, keeping up to max_backup_files."""
        try:
            if not self.log_file_path.exists():
                return
            if self.log_file_path.stat().st_size < self.max_log_bytes:
                return

            for i in range(self.max_backup_files - 1, 0, -1):
                sfn = self.log_file_path.with_name(f"{self.log_file_path.name}.{i}")
                dfn = self.log_file_path.with_name(f"{self.log_file_path.name}.{i + 1}")
                if sfn.exists():
                    if dfn.exists():
                        dfn.unlink()
                    sfn.rename(dfn)

            dfn = self.log_file_path.with_name(f"{self.log_file_path.name}.1")
            if dfn.exists():
                dfn.unlink()
            self.log_file_path.rename(dfn)

            with open(self.log_file_path, "w", encoding="utf-8") as f:
                f.write("# FFVII Tactical Battle Companion - Performance Profiling Log (Rotated)\n")
                f.write("# Format: Timestamp | Summary | Stage Breakdown | Resolution\n\n")
        except Exception as e:
            logger.warning(f"Error rotating performance log: {e}")

    def record_frame(
        self,
        window_find_ms: float,
        grab_ms: float,
        cvt_color_ms: float,
        ocr_det_ms: float,
        ocr_rec_ms: float,
        ocr_total_ms: float,
        match_ms: float,
        total_frame_ms: float,
        width: int,
        height: int,
        lines_count: int,
        enemies_count: int,
        in_combat: bool,
        preset_name: str = "custom",
        ocr_ran: bool = True,
        gate_ms: float = 0.0,
        cpu_ms: Optional[float] = None,
        interval_ms: Optional[float] = None,
        phase: str = "",
    ) -> Dict[str, Any]:
        """
        Records metrics for a single capture loop frame.
        ocr_ran: False when the text-change gate skipped OCR for this frame.
        cpu_ms / interval_ms: capture-thread CPU time and wall time since the previous
        frame (including sleeps), used to report CPU usage as a percent of one core.
        """
        now = time.time()
        metric = {
            "timestamp": now,
            "window_find_ms": round(window_find_ms, 2),
            "grab_ms": round(grab_ms, 2),
            "gate_ms": round(gate_ms, 3),
            "cvt_color_ms": round(cvt_color_ms, 2),
            "ocr_det_ms": round(ocr_det_ms, 2),
            "ocr_rec_ms": round(ocr_rec_ms, 2),
            "ocr_total_ms": round(ocr_total_ms, 2),
            "match_ms": round(match_ms, 2),
            "total_frame_ms": round(total_frame_ms, 2),
            "effective_fps": round(1000.0 / total_frame_ms, 2) if total_frame_ms > 0 else 0.0,
            "width": width,
            "height": height,
            "lines_count": lines_count,
            "enemies_count": enemies_count,
            "in_combat": in_combat,
            "preset": preset_name,
            "ocr_ran": bool(ocr_ran),
            "phase": phase,
        }
        if cpu_ms is not None:
            metric["cpu_ms"] = round(cpu_ms, 2)
        if interval_ms is not None:
            metric["interval_ms"] = round(interval_ms, 1)

        with self._lock:
            self.history.append(metric)
            self.total_frames += 1
            self._frames_since_log += 1
            if ocr_ran:
                self.total_ocr_frames += 1

            if in_combat:
                self.total_combat_frames += 1
            else:
                self.total_idle_frames += 1

            # Periodic logging to file (every log_interval_frames or at least every 60s)
            time_since_log = now - self.last_periodic_log_time
            if self._frames_since_log >= self.log_interval_frames or (time_since_log >= 60.0 and self._frames_since_log > 0):
                self._write_summary_log()
                self._frames_since_log = 0
                self.last_periodic_log_time = now

        return metric

    def _write_summary_log(self):
        """Writes an aggregated summary line to logs/performance.log."""
        if not self.history:
            return

        recent = list(self.history)[-self._frames_since_log:] if self._frames_since_log > 0 else list(self.history)
        if not recent:
            return

        ocr_frames = [m for m in recent if m.get("ocr_ran", True)]

        def _avg(rows: List[Dict[str, Any]], key: str) -> float:
            vals = [m[key] for m in rows if key in m]
            return sum(vals) / len(vals) if vals else 0.0

        avg_total = _avg(recent, "total_frame_ms")
        avg_grab = _avg(recent, "grab_ms")
        avg_det = _avg(ocr_frames, "ocr_det_ms")
        avg_rec = _avg(ocr_frames, "ocr_rec_ms")
        avg_match = _avg(recent, "match_ms")
        cpu_pct = self._cpu_percent(recent)
        sample_fps = self._sample_fps(recent)

        latest = recent[-1]
        res_str = f"{latest.get('width', 0)}x{latest.get('height', 0)}"
        preset = latest.get("preset", "default")
        combat_count = sum(1 for m in recent if m.get("in_combat"))

        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        cpu_str = f"{cpu_pct:5.1f}%" if cpu_pct is not None else "  n/a"
        log_line = (
            f"[{timestamp_str}] [PERF] Frames: {len(recent):2d} (Combat: {combat_count:2d}, OCR: {len(ocr_frames):2d}) | "
            f"Sample FPS: {sample_fps:4.2f} | Capture CPU: {cpu_str} | Avg Frame: {avg_total:6.1f}ms | "
            f"Grab: {avg_grab:4.1f}ms | OCR Det: {avg_det:5.1f}ms | OCR Rec: {avg_rec:5.1f}ms | "
            f"Match: {avg_match:4.2f}ms | Res: {res_str} ({preset})\n"
        )

        try:
            self._ensure_log_dir()
            self._rotate_log_if_needed()
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.warning(f"Error appending to performance log: {e}")

    @staticmethod
    def _cpu_percent(rows: List[Dict[str, Any]]) -> Optional[float]:
        cpu = sum(m["cpu_ms"] for m in rows if "cpu_ms" in m and "interval_ms" in m)
        wall = sum(m["interval_ms"] for m in rows if "cpu_ms" in m and "interval_ms" in m)
        if wall <= 0:
            return None
        return round(100.0 * cpu / wall, 2)

    @staticmethod
    def _sample_fps(rows: List[Dict[str, Any]]) -> float:
        if len(rows) < 2:
            return 0.0
        span = rows[-1]["timestamp"] - rows[0]["timestamp"]
        return round((len(rows) - 1) / span, 2) if span > 0 else 0.0

    def get_summary(self) -> Dict[str, Any]:
        """Computes comprehensive performance breakdown for API and UI consumption."""
        with self._lock:
            recent = list(self.history)
            total_frames = self.total_frames
            total_combat = self.total_combat_frames
            total_idle = self.total_idle_frames
            total_ocr = self.total_ocr_frames
            uptime = round(time.time() - self.session_start_time, 1)

        n = len(recent)

        if n == 0:
            return {
                "has_data": False,
                "total_frames": total_frames,
                "uptime_seconds": uptime,
                "latest": None,
                "averages": {},
                "p95": {},
                "log_file": str(self.log_file_path)
            }

        ocr_frames = [m for m in recent if m.get("ocr_ran", True)]

        def _calc_stats(rows: List[Dict[str, Any]], key: str) -> Dict[str, float]:
            vals = sorted([m[key] for m in rows if key in m])
            if not vals:
                return {"avg": 0.0, "min": 0.0, "max": 0.0, "p95": 0.0}
            count = len(vals)
            avg_val = sum(vals) / count
            min_val = vals[0]
            max_val = vals[-1]
            p95_idx = min(count - 1, math.floor(count * 0.95))
            p95_val = vals[p95_idx]
            return {
                "avg": round(avg_val, 2),
                "min": round(min_val, 2),
                "max": round(max_val, 2),
                "p95": round(p95_val, 2)
            }

        stats = {key: _calc_stats(ocr_frames if key in OCR_ONLY_KEYS else recent, key) for key in TIMING_KEYS}

        sample_fps = self._sample_fps(recent)
        cpu_pct = self._cpu_percent(recent)

        return {
            "has_data": True,
            "sample_count": n,
            "total_frames_lifetime": total_frames,
            "combat_frames_lifetime": total_combat,
            "idle_frames_lifetime": total_idle,
            "ocr_frames_lifetime": total_ocr,
            "ocr_frames_recent": len(ocr_frames),
            "ocr_ratio_recent": round(len(ocr_frames) / n, 3),
            "uptime_seconds": uptime,
            # Measured sampling rate of the capture loop over the recent window
            "sample_fps": sample_fps,
            "effective_fps": sample_fps,
            # Capture-thread CPU time as a percent of one core (None until measured)
            "capture_cpu_percent": cpu_pct,
            "latest": recent[-1],
            "stats": stats,
            "log_file": str(self.log_file_path)
        }

    def get_recent_logs(self, max_lines: int = 50) -> List[str]:
        """Returns the most recent log lines from performance.log without unbounded memory usage."""
        if not self.log_file_path.exists():
            return []
        safe_max = min(max(1, max_lines), 200)
        try:
            buffer: deque = deque(maxlen=safe_max)
            with open(self.log_file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        buffer.append(stripped)
            return list(buffer)
        except Exception as e:
            logger.error(f"Error reading performance log: {e}")
            return [f"Error reading log: {e}"]

perf_tracker = PerformanceTracker()
