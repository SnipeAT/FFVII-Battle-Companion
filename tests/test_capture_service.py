"""Capture state machine tests driven through CaptureService.process_frame (real code, no live screen)."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2

from ffvii_companion.engine.calibration import calibration_manager
from ffvii_companion.engine.capture_service import CaptureService, MAX_GATED_SKIPS
from ffvii_companion.engine.detection_report import ReportError, parse_expected
from ffvii_companion.compendium import compendium
from tests.helpers import load_fixture_bgra, make_config


class _Recorder:
    def __init__(self, service: CaptureService):
        self.events = []
        service.on_battle_detected = lambda enemies, title: self.events.append(("detected", title))
        service.on_battle_cleared = lambda: self.events.append(("cleared", None))


class TestCaptureStateMachine(unittest.TestCase):
    def setUp(self):
        self.service = CaptureService()
        self.recorder = _Recorder(self.service)
        self.cfg = make_config()

    def tearDown(self):
        if self.service.is_running():
            self.service.stop()

    def step(self, fixture: str, max_empty_frames: int = 3):
        return self.service.process_frame(load_fixture_bgra(fixture), self.cfg, max_empty_frames)

    def test_initial_state(self):
        status = self.service.get_status()
        self.assertEqual(status["combat_phase"], "idle")
        self.assertFalse(status["running"])

    def test_full_battle_sequence(self):
        self.step("field_no_text.png")
        self.assertEqual(self.service.combat_phase, "idle")

        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "hunting", "Battle HUD without enemies must boost to hunting")

        self.step("prowler.png")
        self.step("kalm_fang_b.png")
        counts = {e["name"]: e["count"] for e in self.service.current_enemies}
        self.assertEqual(counts, {"Prowler": 1, "Kalm Fang": 2})
        self.assertEqual(self.service.combat_phase, "hunting")
        self.assertEqual([e[0] for e in self.recorder.events], ["detected", "detected"])

        # Unchanged text: the gate skips OCR (the hunting safety net forces a pass after
        # MAX_GATED_SKIPS["hunting"] skips) and the hunting burst decays into settled
        outs = [self.step("kalm_fang_b.png") for _ in range(MAX_GATED_SKIPS["hunting"] + 1)]
        self.assertEqual([o["ocr_ran"] for o in outs], [False] * MAX_GATED_SKIPS["hunting"] + [True])
        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "settled")
        self.assertEqual(len(self.recorder.events), 2, "No broadcast while the encounter is unchanged")

        # Victory screen and field: no HUD -> battle clears after max_empty_frames
        self.step("victory_screen.png")
        self.step("field_no_text.png")
        self.step("field_no_text.png")
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertEqual(self.service.current_enemies, [])
        self.assertEqual(self.recorder.events[-1], ("cleared", None))

    def test_limit_menu_left_open_keeps_the_battle(self):
        # A limit menu shows no enemy name, only its "Lv 3" header under the HUD's HP / MP headers;
        # it used to read as a field menu and clear the battle after max_empty_frames
        self.step("hud_only.png")
        self.step("prowler.png")
        for _ in range(8):
            self.assertTrue(self.step("limit_menu_lv3.png")["combat_hud"])
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Prowler"])
        self.assertEqual(self.service.combat_phase, "settled")
        self.assertEqual([e[0] for e in self.recorder.events], ["detected"])

        # The battle still clears once it is over
        for _ in range(3):
            self.step("field_no_text.png")
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertEqual(self.recorder.events[-1], ("cleared", None))

    def test_phase_change_notifies_listeners(self):
        phases = []
        self.service.on_phase_changed = lambda: phases.append(
            (self.service.combat_phase, self.service.current_fps))

        # Battle HUD without enemies: idle -> hunting changes no encounter data, so it is notified,
        # and current_fps already matches the new phase (1.5 FPS)
        self.step("hud_only.png")
        self.assertEqual(self.service.current_fps, 1.5)
        self.assertEqual(phases, [("hunting", 1.5)])

        # Encounter broadcasts carry the phase themselves: no extra phase notification
        self.step("prowler.png")
        self.step("kalm_fang_b.png")
        self.assertEqual([e[0] for e in self.recorder.events], ["detected", "detected"])
        self.assertEqual(len(phases), 1)

        # Hunting burst decays into settled without any encounter change: the UI must hear about it
        for _ in range(MAX_GATED_SKIPS["hunting"] + 1):
            self.step("kalm_fang_b.png")
        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "settled")
        self.assertEqual(phases, [("hunting", 1.5), ("settled", 0.35)])

        # Steady settled frames do not notify again
        self.step("hud_only.png")
        self.assertEqual(len(phases), 2)

        # The battle clearing is announced by on_battle_cleared, not by a phase notification
        self.step("victory_screen.png")
        self.step("field_no_text.png")
        self.step("field_no_text.png")
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertEqual(self.service.current_fps, 0.5)
        self.assertEqual(self.recorder.events[-1], ("cleared", None))
        self.assertEqual(len(phases), 2)

    def test_phase_callback_errors_are_contained(self):
        def boom():
            raise RuntimeError("listener failure")
        self.service.on_phase_changed = boom
        self.step("hud_only.png")
        self.step("prowler.png")
        for _ in range(MAX_GATED_SKIPS["hunting"] + 1):
            self.step("prowler.png")
        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "settled")

    def test_instance_letters_accumulate(self):
        self.step("hud_only.png")
        self.step("devil_ride_a.png")
        self.step("devil_ride_b.png")
        self.assertEqual({e["name"]: e["count"] for e in self.service.current_enemies}, {"Devil Ride": 2})

    def test_instance_letter_follows_the_count(self):
        # GEMINI-20261008-03: count 2 must not be reported with the stale letter "A"
        self.step("hud_only.png")
        self.step("devil_ride_a.png")
        self.assertEqual([(e["count"], e["instance_letter"]) for e in self.service.current_enemies], [(1, "A")])
        self.step("devil_ride_b.png")
        self.assertEqual([(e["count"], e["instance_letter"]) for e in self.service.current_enemies], [(2, "B")])
        # A later frame that shows only the lower letter keeps the highest observed one
        self.step("devil_ride_a.png")
        self.assertEqual([(e["count"], e["instance_letter"]) for e in self.service.current_enemies], [(2, "B")])

    def test_instance_letter_after_an_unlettered_read(self):
        # GEMINI-20261009-01: "Devil Ride" read without its letter, then "Devil Ride A": same count,
        # the letter must still be taken; a later unlettered read keeps it
        def read(line):
            enemies = self.service.detector.identify_enemies_from_lines([line])
            self.service._update_battle_state(enemies, True, True, self.cfg, 3)
            return [(e["count"], e["instance_letter"]) for e in self.service.current_enemies]

        self.assertEqual(read("Devil Ride"), [(1, None)])
        self.assertEqual(read("Devil Ride A"), [(1, "A")])
        self.assertEqual(read("Devil Ride"), [(1, "A")])

    def test_game_window_closing_while_hunting_clears_the_phase(self):
        # GEMINI-20261008-01: HUD seen, no enemy registered yet, then the game goes away
        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "hunting")
        self.assertEqual(self.service.current_enemies, [])
        self.assertEqual([self.service._note_window_missing(3) for _ in range(3)], [False, False, True])
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertIsNone(self.service.last_game_window)
        # Nothing to clear while idle: the counter must not run away
        self.assertFalse(self.service._note_window_missing(3))
        self.assertEqual(self.service.empty_frames_count, 0)

    def test_gate_forces_periodic_ocr(self):
        self.step("hud_only.png")
        self.step("devil_ride_b_static_1.png")
        # Let the burst decay into settled, then count OCR passes on an unchanged screen
        for _ in range(6):
            self.step("devil_ride_b_static_1.png")
        self.assertEqual(self.service.combat_phase, "settled")
        ran = [self.step("devil_ride_b_static_1.png")["ocr_ran"] for _ in range(15)]
        limit = MAX_GATED_SKIPS["settled"]
        self.assertGreaterEqual(ran.count(True), 15 // (limit + 1))
        self.assertLessEqual(ran.count(True), 15 // (limit + 1) + 1)
        longest_gap = max(len(run) for run in "".join("x" if r else "." for r in ran).split("x"))
        self.assertLessEqual(longest_gap, limit)

    def test_gate_can_be_disabled(self):
        self.cfg["text_gate"] = False
        results = [self.step("devil_ride_b_static_1.png")["ocr_ran"] for _ in range(3)]
        self.assertEqual(results, [True, True, True])

    def test_battle_square_round_transition(self):
        sea_worm = dict(compendium.get_by_id("sea-worm"))
        dragon = dict(compendium.get_by_id("dragon"))
        self.service._update_battle_state([sea_worm], True, True, self.cfg, 3)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Sea Worm"])
        # Sea Worm and Dragon never share a formation: the next round replaces the encounter
        self.service._update_battle_state([dragon], True, True, self.cfg, 3)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Dragon"])
        self.assertEqual(self.service.observed_counts, {"Dragon": 1})
        self.assertEqual(self.service.combat_phase, "hunting")

    def test_compatible_partner_is_not_a_round_transition(self):
        prowler = dict(compendium.get_by_name("Prowler"))
        fang = dict(compendium.get_by_name("Kalm Fang"))
        self.service._update_battle_state([prowler], True, True, self.cfg, 3)
        self.service._update_battle_state([fang], True, True, self.cfg, 3)
        self.assertEqual(sorted(e["name"] for e in self.service.current_enemies), ["Kalm Fang", "Prowler"])

    def test_auto_add_partners_only_adds_certain_partners(self):
        self.cfg["auto_add_scene_partners"] = True
        devil_ride = dict(compendium.get_by_name("Devil Ride"))
        self.service._update_battle_state([devil_ride], True, True, self.cfg, 3)
        # Devil Ride also appears alone: Kalm Fang must not be added (recorded battle had none)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Devil Ride"])

        self.service._clear_battle_state()
        prowler = dict(compendium.get_by_name("Prowler"))
        self.service._update_battle_state([prowler], True, True, self.cfg, 3)
        # Every Prowler formation has Kalm Fangs
        self.assertEqual(sorted(e["name"] for e in self.service.current_enemies), ["Kalm Fang", "Prowler"])

    def test_clear_battle_state_method(self):
        self.service.current_enemies = [{"id": "mp", "name": "MP", "count": 1}]
        self.service.current_battle_title = "Battle: MP"
        self.service.observed_counts = {"MP": 1}
        self.service.empty_frames_count = 3
        self.assertTrue(self.service._clear_battle_state())
        self.assertEqual(self.service.current_enemies, [])
        self.assertIsNone(self.service.current_battle_title)
        self.assertEqual(self.service.observed_counts, {})
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertFalse(self.service._clear_battle_state())

    def test_clear_battle_forgets_the_collected_enemies(self):
        # Regression: the dashboard's CLEAR reset only the server's battle. The loop kept its
        # collected enemies, so the next new name brought the cleared ones back with it
        self.step("hud_only.png")
        self.step("prowler.png")
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Prowler"])

        self.service.clear_battle()
        self.assertEqual(self.recorder.events[-1], ("cleared", None))
        self.assertEqual(self.service.current_enemies, [])
        self.assertEqual(self.service.observed_counts, {})
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertEqual(self.service.current_fps, 0.5, "current_fps follows the phase for the broadcast")

        # Kalm Fang shares formations with Prowler, so only the clear keeps Prowler out
        self.step("kalm_fang_b.png")
        self.assertEqual({e["name"]: e["count"] for e in self.service.current_enemies}, {"Kalm Fang": 2})
        self.assertEqual(self.recorder.events[-1], ("detected", "Battle: 2x Kalm Fang"))

    def test_clear_battle_waits_for_a_sampling_step_in_progress(self):
        # The loop holds _capture_lock for a whole step, callbacks included: a clear that ran in
        # the middle of a step would be followed by that step's broadcast of the cleared enemies
        done = threading.Event()
        with self.service._capture_lock:
            worker = threading.Thread(target=lambda: (self.service.clear_battle(), done.set()))
            worker.start()
            self.assertFalse(done.wait(0.2), "clear_battle must wait for the step to finish")
            self.assertEqual(self.recorder.events, [])
        worker.join(timeout=5)
        self.assertTrue(done.is_set())
        self.assertEqual(self.recorder.events, [("cleared", None)])

    def test_downstream_memoization(self):
        res1, hud1 = self.service._deduce_with_cache(("MP A", "SOLDIER:1ST B"))
        res2, hud2 = self.service._deduce_with_cache(("MP A", "SOLDIER:1ST B"))
        self.assertIs(res1, res2)
        self.assertEqual(hud1, hud2)
        # The HUD check depends on the battle state, so the state is part of the key
        lines = ("Lv3", "HP", "MP", "3587/3675")
        self.assertFalse(self.service._deduce_with_cache(lines, False)[1])
        self.assertTrue(self.service._deduce_with_cache(lines, True)[1])

    def test_phase_fps(self):
        self.assertEqual(CaptureService._phase_fps(self.cfg, "idle"), 0.5)
        self.assertEqual(CaptureService._phase_fps(self.cfg, "hunting"), 1.5)
        self.assertEqual(CaptureService._phase_fps(self.cfg, "settled"), 0.35)


class TestCaptureBbox(unittest.TestCase):
    def test_negative_monitor_origin_is_kept(self):
        # GEMINI-20261007-04: a monitor left of / above the primary has negative coordinates
        service = CaptureService()
        win = {"left": -1920, "top": -3000, "width": 1920, "height": 1080}
        bbox = service._calculate_capture_bbox(win)
        self.assertLess(bbox["left"], 0)
        self.assertLess(bbox["top"], 0)


class TestDiagnosticSnapshot(unittest.TestCase):
    def test_snapshot_applies_current_detector_settings(self):
        # GEMINI-20261008-04: with the capture loop stopped, nothing else syncs the detector
        service = CaptureService()
        cfg = make_config(det_limit_side_len=960, confidence_threshold=0.35)
        self.assertNotEqual(service.detector.det_limit_side_len, 960)
        service._calculate_capture_bbox = lambda win=None: {"top": 0, "left": 0, "width": 1, "height": 1}
        service._grab = lambda bbox: load_fixture_bgra("prowler.png")
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg):
            snap = service.capture_snapshot()
        self.assertEqual(service.detector.det_limit_side_len, 960)
        self.assertEqual(service.detector.confidence_threshold, 0.35)
        self.assertIn("Prowler", [e["name"] for e in snap["matched_enemies"]])


class TestMisdetectionReport(unittest.TestCase):
    def setUp(self):
        self.service = CaptureService()
        self.cfg = make_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_ocr_frame_is_kept_only_from_ocr_passes(self):
        self.assertIsNone(self.service.last_ocr_frame)
        self.service.process_frame(load_fixture_bgra("prowler.png"), self.cfg, 3)
        first = self.service.last_ocr_frame
        self.assertEqual(first.ndim, 3)
        self.assertEqual(first.shape[2], 3, "BGR, the OCR input")
        gated = self.service.process_frame(load_fixture_bgra("prowler.png"), self.cfg, 3)
        self.assertFalse(gated["ocr_ran"])
        self.assertIs(self.service.last_ocr_frame, first)

    def test_report_has_fixture_format(self):
        self.service.process_frame(load_fixture_bgra("devil_ride_b.png"), self.cfg, 3)
        current = load_fixture_bgra("kalm_fang_b.png")
        self.service._calculate_capture_bbox = lambda win=None: {"top": 0, "left": 0, "width": 1, "height": 1}
        self.service._grab = lambda bbox: current
        result = self.service.save_detection_report(None, "wrong count", base_dir=self.base)

        folder = self.base / result["folder"]
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["current.png", "last_ocr.png", "manifest.json"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        # Without expected enemies the detected ones are recorded and flagged for review
        self.assertTrue(result["needs_review"])
        self.assertEqual(manifest["frames"]["last_ocr.png"], {"enemies": {"Devil Ride": 2}, "hud": True})
        self.assertEqual(manifest["frames"]["current.png"], {"enemies": {"Kalm Fang": 2}, "hud": True})
        report = manifest["report"]
        self.assertEqual(report["note"], "wrong count")
        self.assertEqual(report["battle_on_dashboard"], {"Devil Ride": 2})
        self.assertTrue(report["frames"]["current.png"]["recognized_lines"])
        # The saved frame is the OCR input and reproduces the detection like the real fixtures
        saved = cv2.imread(str(folder / "last_ocr.png"), cv2.IMREAD_COLOR)
        self.assertEqual(saved.shape, self.service.last_ocr_frame.shape)

    def test_report_without_any_frame_fails(self):
        def no_screen(win=None):
            raise RuntimeError("no screen")
        self.service._calculate_capture_bbox = no_screen
        with self.assertRaises(ReportError):
            self.service.save_detection_report(base_dir=self.base)

    def test_parse_expected(self):
        enemies, unresolved = parse_expected("Devil Ride x2, kalm fang; Zzyzx Qwerty\nPROWLER *3")
        self.assertEqual(enemies, {"Devil Ride": 2, "Kalm Fang": 1, "Prowler": 3})
        self.assertEqual(unresolved, ["Zzyzx Qwerty"])
        self.assertEqual(parse_expected(None), ({}, []))


class TestSettledSamplingRate(unittest.TestCase):
    def test_settled_grabs_only_at_settled_fps(self):
        # The screen text changes on every grab (like spell or summon flashes): settled must
        # still sample at settled_fps and never grab between samples.
        service = CaptureService()
        cfg = make_config(mode="auto_window", settled_fps=0.5)
        devil_ride = dict(compendium.get_by_name("Devil Ride"), count=2)
        service.current_enemies = [devil_ride]
        service.observed_counts = {"Devil Ride": 2}
        service.current_battle_title = "Battle: 2x Devil Ride"
        service.combat_phase = "settled"
        frames = [load_fixture_bgra("devil_ride_b.png"), load_fixture_bgra("hud_only.png")]
        grabs = []

        def fake_grab(bbox):
            grabs.append(time.monotonic())
            return frames[len(grabs) % 2]

        service._grab = fake_grab
        service.window_finder.find_ffvii_window = lambda: {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg):
            service.start()
            time.sleep(2.5)
            service.stop()
        self.assertEqual(service.combat_phase, "settled")
        # Samples at t = 0 and t = 2 s (0.5 FPS); the old 0.35 s polling grabbed 7+ times
        self.assertEqual(len(grabs), 2, grabs)
        self.assertGreaterEqual(grabs[1] - grabs[0], 1.9)


class TestCaptureLoopLifecycle(unittest.TestCase):
    def test_stop_event_latency(self):
        service = CaptureService()
        service.start()
        self.assertTrue(service.is_running())
        time.sleep(0.05)
        t0 = time.perf_counter()
        service.stop()
        elapsed = time.perf_counter() - t0
        self.assertFalse(service.is_running())
        self.assertLess(elapsed, 2.5, "Service stop must not hang")

    def test_thread_qos_toggle_is_safe(self):
        service = CaptureService()
        errors = []

        def worker():
            try:
                service._apply_thread_qos(True)
                service._apply_thread_qos(False)
                service._apply_thread_qos(False)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(errors, [])
        self.assertFalse(service._qos_efficient)


if __name__ == "__main__":
    unittest.main()
