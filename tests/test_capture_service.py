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
        # One second between steps: a battle clears on the third step without enemies or HUD
        self.cfg = make_config(idle_timeout_seconds=3.0)
        self.now = time.monotonic()

    def tearDown(self):
        if self.service.is_running():
            self.service.stop()

    def step(self, fixture: str):
        self.now += 1.0
        return self.service.process_frame(load_fixture_bgra(fixture), self.cfg, self.now)

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

        # Victory screen and field: no HUD -> battle clears idle_timeout_seconds after the last sighting
        self.step("victory_screen.png")
        self.step("field_no_text.png")
        self.step("field_no_text.png")
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertEqual(self.service.current_enemies, [])
        self.assertEqual(self.recorder.events[-1], ("cleared", None))

    def test_limit_menu_left_open_keeps_the_battle(self):
        # A limit menu shows no enemy name, only its "Lv 3" header under the HUD's HP / MP headers;
        # it used to read as a field menu and clear the battle after idle_timeout_seconds
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
            self.service._update_battle_state(enemies, True, True, self.cfg)
            return [(e["count"], e["instance_letter"]) for e in self.service.current_enemies]

        self.assertEqual(read("Devil Ride"), [(1, None)])
        self.assertEqual(read("Devil Ride A"), [(1, "A")])
        self.assertEqual(read("Devil Ride"), [(1, "A")])

    def test_game_window_closing_while_hunting_clears_the_phase(self):
        # GEMINI-20261008-01: HUD seen, no enemy registered yet, then the game goes away
        self.step("hud_only.png")
        self.assertEqual(self.service.combat_phase, "hunting")
        self.assertEqual(self.service.current_enemies, [])
        missing = []
        for _ in range(3):
            self.now += 1.0
            missing.append(self.service._note_window_missing(self.cfg, self.now))
        self.assertEqual(missing, [False, False, True])
        self.assertEqual(self.service.combat_phase, "idle")
        self.assertIsNone(self.service.last_game_window)
        # Nothing to clear while idle: the counter must not run away
        self.assertFalse(self.service._note_window_missing(self.cfg, self.now + 10.0))
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
        self.service._update_battle_state([sea_worm], True, True, self.cfg)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Sea Worm"])
        # Sea Worm and Dragon never share a formation: the next round replaces the encounter
        self.service._update_battle_state([dragon], True, True, self.cfg)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Dragon"])
        self.assertEqual(self.service.observed_counts, {"Dragon": 1})
        self.assertEqual(self.service.combat_phase, "hunting")

    def test_multi_part_boss_read_with_ocr_punctuation(self):
        # OCR lines -> enemies -> encounter, as in process_frame: one Grosspanzer·Big read as
        # "Grosspanzer-Big" became 7x, and the Grosspanzer·Mobile read next then looked like a new
        # Battle Square round (no formation has seven Bigs) and reset the encounter
        for line in ("Grosspanzer-Big", "Grosspanzer.Mobile"):
            enemies = self.service.detector.identify_enemies_from_lines([line, "Attack", "HP", "MP"])
            self.service._update_battle_state(enemies, True, True, self.cfg)
        counts = {e["name"]: e["count"] for e in self.service.current_enemies}
        self.assertEqual(counts, {"Grosspanzerbig": 1, "Grosspanzermobile": 1})

    def test_item_named_like_an_enemy_is_not_a_new_round(self):
        # CLAUDE-20261012-B01: the battle Item menu is inside the capture region; a highlighted
        # Grenade replaced the encounter, and the next target read lost the instance count
        frames = (["Devil Ride B", "Attack", "HP", "MP"], ["otion", "Grenade", "HP", "MP"],
                  ["Right arm", "X-Potion", "HP", "MP"], ["Devil Ride", "ttack", "Mime", "HP", "MP"])
        titles = []
        for lines in frames:
            enemies = self.service.detector.identify_enemies_from_lines(lines)
            _, title, _ = self.service._update_battle_state(enemies, True, True, self.cfg)
            titles.append(title)
        self.assertEqual({e["name"]: e["count"] for e in self.service.current_enemies}, {"Devil Ride": 2})
        self.assertEqual([t for t in titles if t], ["Battle: 2x Devil Ride"])
        # A battle against Grenades still registers them
        self.service._clear_battle_state()
        enemies = self.service.detector.identify_enemies_from_lines(["Grenade", "Attack", "HP", "MP"])
        self.service._update_battle_state(enemies, True, True, self.cfg)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Grenade"])

    def test_compatible_partner_is_not_a_round_transition(self):
        prowler = dict(compendium.get_by_name("Prowler"))
        fang = dict(compendium.get_by_name("Kalm Fang"))
        self.service._update_battle_state([prowler], True, True, self.cfg)
        self.service._update_battle_state([fang], True, True, self.cfg)
        self.assertEqual(sorted(e["name"] for e in self.service.current_enemies), ["Kalm Fang", "Prowler"])

    def test_auto_add_partners_only_adds_certain_partners(self):
        self.cfg["auto_add_scene_partners"] = True
        devil_ride = dict(compendium.get_by_name("Devil Ride"))
        self.service._update_battle_state([devil_ride], True, True, self.cfg)
        # Devil Ride also appears alone: Kalm Fang must not be added (recorded battle had none)
        self.assertEqual([e["name"] for e in self.service.current_enemies], ["Devil Ride"])

        self.service._clear_battle_state()
        prowler = dict(compendium.get_by_name("Prowler"))
        self.service._update_battle_state([prowler], True, True, self.cfg)
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
    def test_roi_bbox(self):
        # One formula for the game window, the monitor and tools/record_frames.py
        from ffvii_companion.engine.calibration import roi_bbox
        roi = {"top_pct": 0.5, "left_pct": 0.25, "width_pct": 0.5, "height_pct": 0.01}
        self.assertEqual(roi_bbox({"left": 100, "top": -1080, "width": 1920, "height": 1080}, roi),
                         {"top": -540, "left": 580, "width": 960, "height": 64})

    def test_negative_monitor_origin_is_kept(self):
        # GEMINI-20261007-04: a monitor left of / above the primary has negative coordinates
        service = CaptureService()
        win = {"left": -1920, "top": -3000, "width": 1920, "height": 1080}
        bbox = service._calculate_capture_bbox(win)
        self.assertLess(bbox["left"], 0)
        self.assertLess(bbox["top"], 0)

    def test_monitor_change_applies_without_restart(self):
        # CLAUDE-20261012-A07: a changed monitor_index was saved and reported but the capture
        # engine kept the monitor it started with
        service = CaptureService()
        engine = service.capture_engine
        engine.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        roi = {"top_pct": 0.0, "left_pct": 0.0, "width_pct": 1.0, "height_pct": 1.0}
        for monitor, left in ((1, 0), (2, 1920), (1, 0)):
            cfg = make_config(mode="custom_roi", monitor_index=monitor, roi=roi)
            with mock.patch.object(calibration_manager, "get_config", return_value=cfg):
                self.assertEqual(service._calculate_capture_bbox()["left"], left, monitor)
            self.assertEqual(engine.monitor_index, monitor)


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

    def test_snapshot_stays_out_of_the_loop_statistics(self):
        # Snapshots went into the capture loop's history, raising its measured sampling rate and
        # OCR share in the performance panel (code review 2026-10-30); the dialog shows their timings
        from ffvii_companion.engine.profiler import perf_tracker
        service = CaptureService()
        service._calculate_capture_bbox = lambda win=None: {"top": 0, "left": 0, "width": 1, "height": 1}
        service._grab = lambda bbox: load_fixture_bgra("prowler.png")
        with mock.patch.object(perf_tracker, "record_frame", side_effect=AssertionError("recorded")):
            snap = service.capture_snapshot()
        self.assertGreater(snap["performance"]["ocr_total_ms"], 0)

    def test_capture_region_is_found_under_the_capture_lock(self):
        # The snapshot, the report and the loop's own window search used the capture loop's
        # window finder and capture engine (monitor selection) without _capture_lock, so a request
        # thread could run them alongside the loop (code review 2026-10-30)
        service = CaptureService()
        held = []

        def bbox(win=None):
            held.append(service._capture_lock.locked())
            return {"top": 0, "left": 0, "width": 1, "height": 1}

        service._calculate_capture_bbox = bbox
        service._grab = lambda bbox: load_fixture_bgra("prowler.png")
        service.capture_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            service.save_detection_report(base_dir=Path(tmp))
        self.assertEqual(held, [True, True])

        class OneStep(threading.Event):
            def wait(self, timeout=None):
                self.set()
                return True

        def find():
            held.append(service._capture_lock.locked())
            return {"left": 0, "top": 0, "width": 640, "height": 480}

        from ffvii_companion.engine import capture_service as module
        service.window_finder.find_ffvii_window = find
        with mock.patch.object(calibration_manager, "get_config", return_value=make_config(mode="auto_window")), \
                mock.patch.object(module.win_qos, "set_current_thread_qos"):
            service._loop(OneStep())
        # snapshot, report, then the loop's window search and its capture region
        self.assertEqual(held, [True, True, True, True])


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
        self.service.process_frame(load_fixture_bgra("prowler.png"), self.cfg)
        first = self.service.last_ocr_frame
        self.assertEqual(first.ndim, 3)
        self.assertEqual(first.shape[2], 3, "BGR, the OCR input")
        gated = self.service.process_frame(load_fixture_bgra("prowler.png"), self.cfg)
        self.assertFalse(gated["ocr_ran"])
        self.assertIs(self.service.last_ocr_frame, first)

    def test_report_has_fixture_format(self):
        self.service.process_frame(load_fixture_bgra("devil_ride_b.png"), self.cfg)
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

    def test_expected_enemies_with_the_count_first(self):
        # "2x Devil Ride", the form of the dashboard's title and battle summary, was unresolved,
        # "2 Devil Ride" became one Devil Ride, and "x0" recorded a count of 0 (code review 2026-10-30)
        self.assertEqual(parse_expected("2x Devil Ride, 2 x Kalm Fang, 3 Prowler"),
                         ({"Devil Ride": 2, "Kalm Fang": 2, "Prowler": 3}, []))
        # A name with a number of its own
        self.assertEqual(parse_expected("8 Eye; 2x 8 Eye"), ({"8 Eye": 2}, []))
        self.assertEqual(parse_expected("8 Eye x3, 1st Ray"), ({"8 Eye": 3, "1st Ray": 1}, []))
        self.assertEqual(parse_expected("Devil Ride x0"), ({}, ["Devil Ride x0"]))
        # The space after the count is required: "2 Xcannon" is two Xcannons, not a "cannon"
        self.assertEqual(parse_expected("2 Xcannon"), ({"Xcannon": 2}, []))
        self.assertEqual(parse_expected("3x Xcannon"), ({"Xcannon": 3}, []))

    def test_count_prefix_pattern_runs_in_linear_time(self):
        # CodeQL py/polynomial-redos on the first version of the pattern: "\s*[x]?\s+" backtracked
        # quadratically on "0" followed by many spaces (20000 took seconds)
        from ffvii_companion.engine.detection_report import RE_EXPECTED_PREFIX
        t0 = time.perf_counter()
        self.assertIsNone(RE_EXPECTED_PREFIX.match("0" + " " * 20000))
        self.assertLess(time.perf_counter() - t0, 0.5)


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

        states = []

        def worker():
            try:
                service._apply_thread_qos(True)
                service._apply_thread_qos(False)
                service._apply_thread_qos(False)
                states.append(service._thread_qos.efficient)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(errors, [])
        self.assertEqual(states, [False])

    def _blocking_service(self):
        """A service whose loop steps block in the window lookup until `release` is set, with
        every thread that looked up the window recorded in `calls` (no screen is captured)."""
        service = CaptureService()
        service.release = threading.Event()
        service.calls = []

        def find_window():
            service.calls.append(threading.current_thread())
            service.release.wait(10)
            return None

        service.window_finder.find_ffvii_window = find_window
        service.window_finder._scan_interval = 0.01
        return service

    def _wait_for(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "timed out")
            time.sleep(0.01)

    def test_start_after_a_timed_out_stop_runs_one_loop(self):
        # CLAUDE-20261012-B04: stop() gave up after 2 s while a step was still running, and the
        # next start() cleared the shared stop event, so the old thread kept looping next to the
        # new one
        service = self._blocking_service()
        cfg = make_config(mode="auto_window")
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg):
            service.start()
            self._wait_for(lambda: service.calls)
            old = service.calls[0]
            service.stop()  # times out: the step is blocked
            self.assertTrue(old.is_alive())
            service.start()
            time.sleep(0.2)
            self.assertEqual(service.calls, [old], "the new loop waits for the old one's step")
            service.release.set()
            self._wait_for(lambda: not old.is_alive())
            self._wait_for(lambda: any(t is not old for t in service.calls))
            time.sleep(0.1)
            service.stop()
        self.assertEqual(sum(1 for t in service.calls if t is old), 1)
        self.assertEqual([t for t in threading.enumerate() if t.name == "FFVIICaptureLoop" and t.is_alive()], [])

    def test_restarted_capture_thread_sets_its_qos(self):
        # CLAUDE-20261012-B06: the QoS state was kept on the service, so a restarted loop (a new
        # thread, normal priority) skipped setting its priority and EcoQoS
        from ffvii_companion.engine import capture_service as module
        service = self._blocking_service()
        service.release.set()
        applied = []
        cfg = make_config(mode="auto_window")
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg),                 mock.patch.object(module.win_qos, "set_current_thread_qos",
                                  side_effect=lambda **kw: applied.append(threading.current_thread())):
            for _ in range(2):
                service.start()
                self._wait_for(lambda: service.calls and service.calls[-1].is_alive())
                service.stop()
                service.calls.clear()
        self.assertEqual(len(applied), 2)
        self.assertIsNot(applied[0], applied[1])

    def test_game_window_closing_clears_under_the_capture_lock(self):
        # The loop cleared the battle of a game window that went away without _capture_lock,
        # unlike every other clear, so it could interleave with a CLEAR from the dashboard
        service = CaptureService()
        service.window_finder.find_ffvii_window = lambda: None
        service.window_finder._scan_interval = 0.01
        service.combat_phase = "hunting"
        held = []
        service.on_battle_cleared = lambda: held.append(service._capture_lock.locked())
        cfg = make_config(mode="auto_window", idle_timeout_seconds=1.0)
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg):
            service.start()
            self._wait_for(lambda: held)
            service.stop()
        self.assertEqual(held, [True])

    def test_battle_without_game_window_clears_after_idle_timeout(self):
        # The loop waited for the finder's scan interval (1.0 s, backing off to 2.5 s) between
        # samples that counted toward the timeout at the phase's rate: a settled battle cleared
        # about 1.3 s after an alt-tab minimized the game, a hunting one after 17 s (code review
        # 2026-10-30). The loop's clock is faked: each wait advances it by its timeout.
        from ffvii_companion.engine import capture_service as module

        for phase, fps in (("settled", 0.35), ("hunting", 1.5)):
            with self.subTest(phase=phase):
                clock = [100.0]
                service = CaptureService()
                service.window_finder.find_ffvii_window = lambda: None
                service.current_enemies = [dict(compendium.get_by_name("Prowler"))]
                service.combat_phase = phase
                service.battle_seen_at = clock[0]
                cleared = []
                service.on_battle_cleared = lambda: cleared.append(clock[0])

                class Waits(threading.Event):
                    timeouts = []

                    def wait(self, timeout=None):
                        if cleared or len(self.timeouts) > 50:
                            self.set()
                            return True
                        self.timeouts.append(timeout)
                        clock[0] += timeout
                        return False

                stop = Waits()
                stop.timeouts = []
                cfg = make_config(mode="auto_window", idle_timeout_seconds=6.0)
                with mock.patch.object(calibration_manager, "get_config", return_value=cfg), \
                        mock.patch.object(module.win_qos, "set_current_thread_qos"), \
                        mock.patch.object(module.time, "monotonic", lambda: clock[0]):
                    service._loop(stop)
                self.assertEqual(len(cleared), 1)
                for timeout in stop.timeouts:
                    self.assertAlmostEqual(timeout, 1.0 / fps, places=3)
                # Cleared by the first sample at least idle_timeout_seconds after the last sighting
                self.assertGreaterEqual(cleared[0] - 100.0, 6.0)
                self.assertLess(cleared[0] - 100.0, 6.0 + 1.0 / fps)


class TestDocumentedLoopBehavior(unittest.TestCase):
    """Behavior that CLAUDE.md and README promise and that mutations of the code passed every
    test (CLAUDE-20261012-B08)."""

    def setUp(self):
        self.service = CaptureService()
        self.cfg = make_config()

    def test_documented_constants(self):
        from ffvii_companion.engine.capture_service import HUNTING_BURST_FRAMES
        self.assertEqual(MAX_GATED_SKIPS, {"hunting": 2, "settled": 4, "idle": 4})
        self.assertEqual(HUNTING_BURST_FRAMES, 3)

    def test_battle_clears_after_idle_timeout(self):
        # The timeout was a number of samples at the phase's rate, int(fps * idle_timeout_seconds):
        # a settled battle cleared 5.7 s after the last sighting (2 samples at 0.35 FPS), and steps
        # slower than the phase's interval stretched it, 9 hunting samples 1 s apart taking 9 s
        # (code review 2026-10-30). It is the time since the last sighting now.
        def seconds_to_clear(timeout, spacing):
            service = CaptureService()
            cfg = make_config(idle_timeout_seconds=timeout)
            service._update_battle_state([dict(compendium.get_by_name("Prowler"))], True, True, cfg, 0.0)
            for step in range(1, 30):
                if service.process_frame(load_fixture_bgra("field_no_text.png"), cfg, step * spacing)["cleared"]:
                    return step * spacing
            return None

        self.assertAlmostEqual(seconds_to_clear(6.0, 1 / 0.35), 3 / 0.35)  # settled rate
        self.assertAlmostEqual(seconds_to_clear(6.0, 1 / 1.5), 6.0)        # hunting rate
        self.assertAlmostEqual(seconds_to_clear(6.0, 1.0), 6.0)            # slow hunting steps
        # A single sample without enemies never ends a battle, however long it took
        self.assertAlmostEqual(seconds_to_clear(1.0, 1 / 0.35), 2 / 0.35)
        # process_frame without a time uses the current one
        service = CaptureService()
        service._update_battle_state([dict(compendium.get_by_name("Prowler"))], True, True, self.cfg,
                                     time.monotonic() - self.cfg["idle_timeout_seconds"])
        outcomes = [service.process_frame(load_fixture_bgra("field_no_text.png"), self.cfg)["cleared"]
                    for _ in range(2)]
        self.assertEqual(outcomes, [False, True])

    def test_overlapping_names_are_not_a_new_round(self):
        devil_rides = {**compendium.get_by_name("Devil Ride"), "count": 2}
        devil_ride = {**compendium.get_by_name("Devil Ride"), "count": 1}
        dragon = dict(compendium.get_by_id("dragon"))
        self.service._update_battle_state([devil_rides], True, True, self.cfg)
        # Dragon shares no formation with Devil Ride, but a Devil Ride is still on screen: no new
        # round, so the two Devil Rides seen before are kept
        self.service._update_battle_state([devil_ride, dragon], True, True, self.cfg)
        self.assertEqual(self.service.observed_counts.get("Devil Ride"), 2)

    def test_instance_letter_follows_the_count(self):
        def read(line):
            enemies = self.service.detector.identify_enemies_from_lines([line, "Attack", "HP", "MP"])
            self.service._update_battle_state(enemies, True, True, self.cfg)
            return [(e["count"], e["instance_letter"]) for e in self.service.current_enemies]

        # a misread letter ("H": no formation has 8 Devil Rides) raises the count to 2 without
        # a letter; "A" belongs to count 1 and must not become the letter of 2
        self.assertEqual(read("Devil Ride H"), [(2, None)])
        self.assertEqual(read("Devil Ride A"), [(2, None)])
        self.assertEqual(read("Devil Ride B"), [(2, "B")])
        self.assertEqual(read("Devil Ride A"), [(2, "B")])

    def test_certain_partners_get_the_count_of_their_formations(self):
        # Auto-added partners had no count: Grangalan's only formation holds three Grangalan
        # Jr.Jr., and the title and card showed one (code review 2026-10-30)
        cfg = make_config(auto_add_scene_partners=True)
        grangalan = dict(compendium.get_by_name("Grangalan"), count=1)
        self.service._update_battle_state([grangalan], True, True, cfg)
        counts = {e["name"]: e.get("count", 1) for e in self.service.current_enemies}
        self.assertEqual(counts, {"Grangalan": 1, "Grangalan Jr.": 1, "Grangalan Jr.Jr.": 3})
        self.assertEqual(self.service.current_battle_title, "Battle: Grangalan, Grangalan Jr., 3x Grangalan Jr.Jr.")
        self.assertIsNone(compendium.get_by_name("Grangalan Jr.Jr.").get("count"), "compendium entries stay untouched")
        # Reading one of them ("Grangalan Jr.Jr. A") keeps the three (Gemini review of the fix)
        enemies = self.service.detector.identify_enemies_from_lines(["Grangalan Jr.Jr. A", "Attack", "HP", "MP"])
        self.service._update_battle_state(enemies, True, True, cfg)
        jr_jr = next(e for e in self.service.current_enemies if e["name"] == "Grangalan Jr.Jr.")
        self.assertEqual((jr_jr["count"], jr_jr["instance_letter"]), (3, "A"))
        self.assertEqual(self.service.observed_counts["Grangalan Jr.Jr."], 1, "the deduction still uses what was read")
        self.assertEqual(self.service.current_battle_title, "Battle: Grangalan, Grangalan Jr., 3x Grangalan Jr.Jr.")

    def test_added_partner_stays_in_the_encounter(self):
        # A partner added from the dashboard lived only in the server's battle: the next encounter
        # update of the capture loop replaced it without the partner (code review 2026-10-30)
        detected = []
        self.service.on_battle_detected = lambda enemies, title: detected.append((enemies, title))
        self.assertFalse(self.service.add_enemy(compendium.get_by_name("Kalm Fang")), "no encounter yet")
        prowler = dict(compendium.get_by_name("Prowler"), count=1)
        self.service._update_battle_state([prowler], True, True, self.cfg)
        self.assertTrue(self.service.add_enemy(compendium.get_by_name("Kalm Fang")))
        self.assertEqual(detected[-1][1], "Battle: Prowler, Kalm Fang")
        self.assertEqual([e["name"] for e in detected[-1][0]], ["Prowler", "Kalm Fang"])
        # "Prowler B" read next: the update keeps the partner
        snapshot, title, _ = self.service._update_battle_state(
            [dict(compendium.get_by_name("Prowler"), count=2, instance_letter="B")], True, True, self.cfg)
        self.assertEqual(title, "Battle: 2x Prowler, Kalm Fang")
        self.assertEqual([e["name"] for e in snapshot], ["Prowler", "Kalm Fang"])
        # A partner added from the bar gets the formation's count once it is certain
        service = CaptureService()
        service._update_battle_state([dict(compendium.get_by_name("Grangalan"), count=1)], True, True, self.cfg)
        service.add_enemy(compendium.get_by_name("Grangalan Jr.Jr."))
        self.assertEqual(service.current_enemies[-1]["count"], 3)

    def test_new_enemy_restarts_the_hunting_burst(self):
        from ffvii_companion.engine.capture_service import HUNTING_BURST_FRAMES
        prowler = dict(compendium.get_by_name("Prowler"))
        fang = dict(compendium.get_by_name("Kalm Fang"))
        self.service._update_battle_state([prowler], True, True, self.cfg)
        self.service._update_battle_state([prowler], True, True, self.cfg)
        self.assertEqual(self.service.hunting_frames_remaining, HUNTING_BURST_FRAMES - 1)
        self.service._update_battle_state([prowler, fang], True, True, self.cfg)
        self.assertEqual(self.service.hunting_frames_remaining, HUNTING_BURST_FRAMES)

    def test_clear_runs_ocr_on_the_next_sample(self):
        frame = load_fixture_bgra("prowler.png")
        self.assertTrue(self.service.process_frame(frame, self.cfg)["ocr_ran"])
        self.assertFalse(self.service.process_frame(frame, self.cfg)["ocr_ran"])  # gated
        self.service.clear_battle()
        # the cleared enemy is read again at once, not after the gate's safety net
        self.assertTrue(self.service.process_frame(frame, self.cfg)["ocr_ran"])

    def _run_loop_once(self, phase_after_step):
        """Runs one step of _loop (custom_roi, frame and step faked) and returns the QoS it set
        and the sleep it chose."""
        from ffvii_companion.engine import capture_service as module

        class OneStep(threading.Event):
            timeouts = []

            def wait(self, timeout=None):
                self.timeouts.append(timeout)
                self.set()
                return True

        def step(frame, cfg, now):
            with self.service._state_lock:
                self.service.combat_phase = phase_after_step
            return {"ocr_timing": {}, "cvt_color_ms": 0.0, "ocr_total_ms": 0.0, "match_ms": 0.0,
                    "lines": [], "ocr_ran": True, "gate_ms": 0.0}

        qos = []
        cfg = make_config(mode="custom_roi")
        self.service._calculate_capture_bbox = lambda win=None: {"top": 0, "left": 0, "width": 8, "height": 8}
        self.service._grab = lambda bbox: load_fixture_bgra("prowler.png")
        self.service.process_frame = step
        stop = OneStep()
        with mock.patch.object(calibration_manager, "get_config", return_value=cfg),                 mock.patch.object(module.win_qos, "set_current_thread_qos",
                                  side_effect=lambda **kw: qos.append(kw["efficient"])):
            self.service._loop(stop)
        return qos, stop.timeouts[0]

    def test_loop_leaves_ecoqos_while_hunting(self):
        self.service.combat_phase = "hunting"
        qos, _ = self._run_loop_once("hunting")
        self.assertEqual(qos, [False])
        self.service.combat_phase = "idle"
        qos, _ = self._run_loop_once("idle")
        self.assertEqual(qos, [True])

    def test_loop_sleeps_at_the_rate_of_the_phase_the_step_ended_in(self):
        # idle (0.5 FPS) -> a step that starts hunting (1.5 FPS): the next sample comes after
        # about 0.67 s, not 2 s
        self.service.combat_phase = "idle"
        _, sleep = self._run_loop_once("hunting")
        self.assertLess(sleep, 1.0 / 1.5 + 0.01)
        self.assertGreater(sleep, 0.3)

if __name__ == "__main__":
    unittest.main()
