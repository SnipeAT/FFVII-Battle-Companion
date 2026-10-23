import codecs
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

    def test_unhashable_mode_is_dropped_alone(self):
        # A list or object as mode raised TypeError: the file's other values were all discarded
        # (and the next save lost them), and the API answered 500 instead of 422
        self.path.parent.mkdir(parents=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"mode": ["auto_window"], "idle_fps": 1.0}, f)
        mgr = CalibrationManager(self.path)
        self.assertEqual(mgr.get_config()["idle_fps"], 1.0)
        self.assertEqual(mgr.get_config()["mode"], DEFAULT_CONFIG["mode"])
        for bad in ([], {}):
            with self.assertRaises(ConfigValidationError, msg=bad):
                mgr.update_config({"mode": bad})

    def test_presets(self):
        mgr = CalibrationManager(self.path)
        for key, preset in PRESETS.items():
            cfg = mgr.apply_preset(key)
            self.assertEqual(cfg["target_menu_preset"], key)
            self.assertEqual(cfg["roi"]["top_pct"], preset["top_pct"])
        self.assertIsNone(mgr.apply_preset("missing"))

    def test_file_with_byte_order_mark_is_read(self):
        # CLAUDE-20261012-A03: a hand edit saved as "UTF-8 with BOM" was ignored, and the next
        # save wrote the defaults over it
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(codecs.BOM_UTF8 + json.dumps({"idle_fps": 2.0}).encode("utf-8"))
        mgr = CalibrationManager(self.path)
        self.assertEqual(mgr.get_config()["idle_fps"], 2.0)
        mgr.update_config({"eco_mode": False})
        self.assertEqual(CalibrationManager(self.path).get_config()["idle_fps"], 2.0)

    def test_file_holds_only_saved_values(self):
        # CLAUDE-20261012-A05: the first save wrote every default into the file, so a default
        # changed by a later version never reached the user
        mgr = CalibrationManager(self.path)
        mgr.update_config({"auto_add_scene_partners": True})
        mgr.apply_preset("bottom_half_battle_ui")
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), {"auto_add_scene_partners", "target_menu_preset", "roi"})
        self.assertEqual(set(mgr.get_config()), set(DEFAULT_CONFIG))

    def test_failed_save_changes_nothing(self):
        # CLAUDE-20261012-A04: the value was applied in memory although the file kept the old one
        from unittest import mock
        mgr = CalibrationManager(self.path)
        mgr.update_config({"idle_fps": 1.0})
        before = json.loads(json.dumps(mgr.get_config()))
        with mock.patch("ffvii_companion.engine.calibration.os.replace",
                        side_effect=PermissionError("held by another program")):
            with self.assertRaises(OSError):
                mgr.update_config({"idle_fps": 2.0})
            with self.assertRaises(OSError):
                mgr.apply_preset("full_game_window")
        self.assertEqual(mgr.get_config(), before)
        self.assertEqual(CalibrationManager(self.path).get_config()["idle_fps"], 1.0)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_allowed_hosts_is_read_as_dashboard_host_names(self):
        # GEMINI-20261014-04: allowed_hosts (2.1.0 to 2.2.1) read like a list of devices; files
        # that set it keep their names under the new key, and the next save writes only that
        self.path.parent.mkdir(parents=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"allowed_hosts": ["Gaming-PC.lan"], "idle_fps": 2.0}, f)
        mgr = CalibrationManager(self.path)
        self.assertEqual(mgr.get_config()["dashboard_host_names"], ["gaming-pc.lan"])
        self.assertNotIn("allowed_hosts", mgr.get_config())
        mgr.update_config({"eco_mode": False})
        with open(self.path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["dashboard_host_names"], ["gaming-pc.lan"])
        self.assertNotIn("allowed_hosts", saved)
        # a file that holds both keeps the new one
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"allowed_hosts": ["*"], "dashboard_host_names": ["a.lan"]}, f)
        self.assertEqual(CalibrationManager(self.path).get_config()["dashboard_host_names"], ["a.lan"])

    def test_validate_settings_drops_unknown_keys(self):
        # capture_fps is a retired setting that older calibration files still contain
        self.assertEqual(validate_settings({"idle_fps": 2.0, "capture_fps": 1.5, "malicious_key": 1}),
                         {"idle_fps": 2.0})


if __name__ == "__main__":
    unittest.main()
