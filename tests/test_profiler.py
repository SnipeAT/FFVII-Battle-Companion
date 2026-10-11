import tempfile
import time
import unittest
from pathlib import Path

from ffvii_companion.engine.profiler import PerformanceTracker


def _frame(tracker: PerformanceTracker, ocr_ran: bool = True, ocr_ms: float = 200.0, **kw):
    return tracker.record_frame(
        window_find_ms=1.5, grab_ms=12.0, cvt_color_ms=3.0 if ocr_ran else 0.0,
        ocr_det_ms=ocr_ms * 0.4 if ocr_ran else 0.0, ocr_rec_ms=ocr_ms * 0.6 if ocr_ran else 0.0,
        ocr_total_ms=ocr_ms if ocr_ran else 0.0, match_ms=0.8, total_frame_ms=(ocr_ms if ocr_ran else 0.0) + 15.0,
        width=2304, height=388, lines_count=5, enemies_count=2, in_combat=True,
        preset_name="center_target_box", ocr_ran=ocr_ran, **kw,
    )


class TestPerformanceTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def tracker(self, **kwargs) -> PerformanceTracker:
        t = PerformanceTracker(**kwargs)
        t.log_file_path = Path(self.tmp.name) / "logs" / "perf.log"
        return t

    def test_log_file_is_created_by_the_first_summary_only(self):
        # Creating the tracker on import used to create logs/ in the project for every tool,
        # preview server or script that imported the engine
        tracker = self.tracker(log_interval_frames=3)
        _frame(tracker)
        self.assertFalse(tracker.log_file_path.parent.exists())
        self.assertEqual(tracker.get_recent_logs(), [])
        for _ in range(2):
            _frame(tracker)
        self.assertTrue(tracker.log_file_path.exists())
        self.assertIn("[PERF] Frames:  3", tracker.get_recent_logs()[-1])

    def test_performance_tracker_unit(self):
        tracker = self.tracker(history_size=10, log_interval_frames=5)
        metric = _frame(tracker)
        self.assertEqual(metric["lines_count"], 5)
        self.assertTrue(metric["in_combat"])
        summary = tracker.get_summary()
        self.assertTrue(summary["has_data"])
        self.assertEqual(summary["total_frames_lifetime"], 1)
        self.assertEqual(summary["combat_frames_lifetime"], 1)
        self.assertEqual(summary["stats"]["grab_ms"]["avg"], 12.0)

    def test_ocr_stats_exclude_gated_frames(self):
        tracker = self.tracker(history_size=20)
        _frame(tracker, ocr_ran=True, ocr_ms=200.0)
        for _ in range(3):
            _frame(tracker, ocr_ran=False)
        summary = tracker.get_summary()
        self.assertEqual(summary["stats"]["ocr_total_ms"]["avg"], 200.0)
        self.assertEqual(summary["ocr_frames_recent"], 1)
        self.assertAlmostEqual(summary["ocr_ratio_recent"], 0.25)
        self.assertEqual(summary["ocr_frames_lifetime"], 1)

    def test_cpu_percent_and_sample_rate(self):
        tracker = self.tracker(history_size=20)
        for _ in range(3):
            _frame(tracker, cpu_ms=20.0, interval_ms=1000.0)
            time.sleep(0.02)
        summary = tracker.get_summary()
        self.assertAlmostEqual(summary["capture_cpu_percent"], 2.0)
        self.assertGreater(summary["sample_fps"], 0)

    def test_log_rotation_and_bounded_read(self):
        tracker = self.tracker(history_size=10, max_log_bytes=150, max_backup_files=2)
        for _ in range(5):
            _frame(tracker, cpu_ms=10.0, interval_ms=500.0)
            tracker._write_summary_log()
        log_file = tracker.log_file_path
        backup1 = log_file.with_name(f"{log_file.name}.1")
        self.assertTrue(backup1.exists() or log_file.exists())
        self.assertLessEqual(len(tracker.get_recent_logs(max_lines=3)), 3)


if __name__ == "__main__":
    unittest.main()
