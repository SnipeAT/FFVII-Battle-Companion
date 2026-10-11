import json
import tempfile
import unittest
from pathlib import Path

from ffvii_companion.engine.calibration import (
    CalibrationManager, ConfigValidationError, DEFAULT_CONFIG, PRESETS, validate_settings,
)


class TestCalibrationManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "calibration.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_file(self):
        mgr = CalibrationManager(self.path)
        if not mgr.file_path.exists():
            for key in ("idle_fps", "active_fps", "settled_fps", "ocr_threads", "text_gate"):
                self.assertIn(key, mgr.get_config())

    def test_update_persists_atomically(self):
        mgr = CalibrationManager(self.path)
        mgr.update_config({"settled_fps": 0.3})
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["settled_fps"], 0.3)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())
        self.assertEqual(CalibrationManager(self.path).get_config()["settled_fps"], 0.3)

    def test_invalid_updates_raise_and_do_not_apply(self):
        mgr = CalibrationManager(self.path)
        before = dict(mgr.get_config())
        for bad in ({"idle_fps": -1}, {"det_limit_side_len": "big"}, {"ocr_threads": 64},
                    {"roi": {"top_pct": 0.5}}, {"eco_mode": 1}):
            with self.assertRaises(ConfigValidationError, msg=bad):
                mgr.update_config(bad)
        self.assertEqual(mgr.get_config(), before)

    def test_hand_edited_file_is_clamped_on_load(self):
        self.path.parent.mkdir(parents=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"active_fps": 30, "idle_fps": "x", "target_menu_preset": "x" * 100,
                       "unknown": True}, f)
        cfg = CalibrationManager(self.path).get_config()
        self.assertEqual(cfg["active_fps"], 5.0)
        self.assertEqual(cfg["idle_fps"], DEFAULT_CONFIG["idle_fps"])
        self.assertEqual(cfg["target_menu_preset"], DEFAULT_CONFIG["target_menu_preset"])
        self.assertNotIn("unknown", cfg)

    def test_presets(self):
        mgr = CalibrationManager(self.path)
        for key, preset in PRESETS.items():
            cfg = mgr.apply_preset(key)
            self.assertEqual(cfg["target_menu_preset"], key)
            self.assertEqual(cfg["roi"]["top_pct"], preset["top_pct"])
        self.assertIsNone(mgr.apply_preset("missing"))

    def test_validate_settings_drops_unknown_keys(self):
        # capture_fps is a retired setting that older calibration files still contain
        self.assertEqual(validate_settings({"idle_fps": 2.0, "capture_fps": 1.5, "malicious_key": 1}),
                         {"idle_fps": 2.0})


if __name__ == "__main__":
    unittest.main()
