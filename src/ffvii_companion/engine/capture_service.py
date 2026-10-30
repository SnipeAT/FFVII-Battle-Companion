"""
The capture loop (`capture_service` singleton): samples the capture region of the game window,
reads it, and keeps the battle state the dashboards are told about.

One sampling step is process_frame(), which the tests drive directly: the text-change gate, OCR
when the text may have changed, enemy matching, then _update_battle_state() (accumulated enemies
and counts, formation deduction, Battle Square round changes, battle-menu items named like
enemies) and the phase change. _loop() runs the steps on the FFVIICaptureLoop thread: find the
window, grab the region, process_frame(), record the timings (perf_tracker), then sleep at the
rate of the phase the step ended in. Nothing is grabbed between samples.

Phases: idle (idle_fps); hunting (active_fps) while the battle HUD shows without a registered
enemy, and for HUNTING_BURST_FRAMES after a new enemy; settled (settled_fps) once enemies are
registered. The battle clears once idle_timeout_seconds have passed since a sample last showed
enemies or HUD (at least MIN_EMPTY_SAMPLES samples later), also while the game window is gone
(closed or minimized: the loop then waits at the phase's rate too). MAX_GATED_SKIPS
forces OCR after that many skipped frames. The thread runs at below-normal priority, with EcoQoS
(eco_mode) except while hunting.

Callbacks, set by server/main.py: on_battle_detected(enemies, title) when the encounter changed
(also when add_enemy() adds a partner from the dashboard), on_battle_cleared() when it ended or
was cleared, on_phase_changed() for a phase change without an encounter change. They fire outside
_state_lock but inside _capture_lock, which a sampling step, clear_battle() and add_enemy() hold,
so a CLEAR or an added partner never interleaves with a step's broadcast.

Locks: _capture_lock serializes the window finder, the monitor selection, the grabber, the OCR
session and the text gate, which are not thread-safe (a sampling step from the window search on,
capture_snapshot(), save_detection_report(), clear_battle(), add_enemy());
_state_lock guards the battle state fields the server reads; _lock guards start() and stop().

After changing the loop, compare CPU and detection times with tools/benchmark_loop.py on a real
recording (docs/DEVELOPMENT.md, "Tools").
"""
import time
import threading
import logging
from typing import Optional, Dict, Any, List, Callable, Tuple
import numpy as np
import cv2

from ffvii_companion.engine.capture import ScreenCaptureEngine
from ffvii_companion.engine.detector import BattleDetector, ITEM_NAMED_ENEMIES
from ffvii_companion.engine.window_finder import FFVIIWindowFinder
from ffvii_companion.engine.calibration import calibration_manager, roi_bbox
from ffvii_companion.engine.profiler import perf_tracker
from ffvii_companion.engine.text_gate import TextChangeGate
from ffvii_companion.engine import win_qos
from ffvii_companion.engine import detection_report
from ffvii_companion.compendium import compendium

logger = logging.getLogger("ffvii-capture-service")

# Safety net for the text-change gate: OCR is forced after this many consecutive
# gated (skipped) frames even if the gate saw no text change.
MAX_GATED_SKIPS = {"hunting": 2, "settled": 4, "idle": 4}
# Frames to stay in the hunting phase after a new enemy appears. While the battle HUD shows
# without a registered enemy, hunting lasts as long as the HUD does.
HUNTING_BURST_FRAMES = 3
# Samples without enemies or HUD needed to clear a battle, however long they took: a single
# misread sample never ends one.
MIN_EMPTY_SAMPLES = 2
# Minimum seconds between repeated identical error log lines from the capture loop.
ERROR_LOG_INTERVAL = 30.0


class CaptureService:
    def __init__(self, on_battle_detected: Optional[Callable] = None, on_battle_cleared: Optional[Callable] = None,
                 on_phase_changed: Optional[Callable] = None):
        self.on_battle_detected = on_battle_detected
        self.on_battle_cleared = on_battle_cleared
        # Fired (no arguments) when the sampling phase changes without an encounter broadcast,
        # e.g. hunting -> settled. Encounter broadcasts already carry the current phase.
        self.on_phase_changed = on_phase_changed

        # Settings are read without fallbacks: get_config() always holds every DEFAULT_CONFIG key,
        # and the defaults live only there (engine/calibration.py)
        cfg = calibration_manager.get_config()
        self.capture_engine = ScreenCaptureEngine(monitor_index=cfg["monitor_index"])
        self.detector = BattleDetector(
            num_threads=cfg["ocr_threads"],
            det_limit_side_len=cfg["det_limit_side_len"],
            confidence_threshold=cfg["confidence_threshold"],
        )
        self.window_finder = FFVIIWindowFinder()
        self.text_gate = TextChangeGate()

        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._capture_lock = threading.Lock()

        # Cumulative Battle State tracking
        self.current_enemies: List[Dict[str, Any]] = []
        self.current_battle_title: Optional[str] = None
        self.observed_counts: Dict[str, int] = {}
        self.empty_frames_count = 0
        # time.monotonic() of the last sample that showed enemies or the battle HUD
        self.battle_seen_at = 0.0
        self.last_detection_time = 0.0
        self.last_game_window: Optional[Dict[str, Any]] = None
        self.last_recognized_lines: List[str] = []
        # Frame of the last OCR pass (BGR, OCR input size), kept for misdetection reports
        self.last_ocr_frame: Optional[np.ndarray] = None

        # Text-change gate state: signature of the region at the last OCR pass
        self._last_ocr_signature: Optional[np.ndarray] = None
        self._frames_since_ocr: int = 0

        # Downstream memoization cache on unchanged OCR text lines
        self._last_downstream_lines_key: Optional[tuple] = None
        self._last_downstream_enemies: List[Dict[str, Any]] = []
        self._last_downstream_hud: bool = False

        # Adaptive sampling rate and combat phase state
        self.combat_phase: str = "idle"  # "idle", "hunting", "settled"
        self.hunting_frames_remaining: int = 0
        self.current_fps: float = 0.5

        # Loop bookkeeping
        self._thread_qos = threading.local()  # .efficient: the QoS this loop thread last set
        self._last_error: Tuple[str, float] = ("", 0.0)
        self.ocr_frames = 0
        self.gated_frames = 0

    def is_running(self) -> bool:
        return self._running

    def start(self):
        with self._lock:
            if self._running:
                return
            # Every loop thread gets its own stop event: a stop() that timed out while a step was
            # still running (REPORT holding _capture_lock, slow OCR) leaves that thread finishing
            # its step, and a shared event cleared here would keep it looping next to the new one.
            # The new thread first waits for the old one to end.
            self._stop_event = threading.Event()
            self._running = True
            self._thread = threading.Thread(target=self._loop, args=(self._stop_event, self._thread),
                                            daemon=True, name="FFVIICaptureLoop")
            self._thread.start()
            logger.info("FFVII Screen Capture service started.")

    def stop(self):
        with self._lock:
            self._running = False
            self._stop_event.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        logger.info("FFVII Screen Capture service stopped.")

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            enemies_copy = [dict(e) for e in self.current_enemies]
            lines_copy = list(self.last_recognized_lines)
            win_copy = dict(self.last_game_window) if self.last_game_window else None
            last_det = self.last_detection_time
            running = self._running
            combat_phase = self.combat_phase
            current_fps = self.current_fps
            ocr_frames = self.ocr_frames
            gated_frames = self.gated_frames

        return {
            "running": running,
            "monitor_index": self.capture_engine.monitor_index,
            "game_window_found": win_copy is not None,
            "game_window": win_copy,
            "active_enemy_names": [e["name"] for e in enemies_copy],
            "last_recognized_lines": lines_copy,
            "last_detection_time": last_det,
            "combat_phase": combat_phase,
            "current_fps": round(current_fps, 2),
            "ocr_frames": ocr_frames,
            "gated_frames": gated_frames,
            "ocr_engine_loaded": self.detector.ocr_loaded,
            "performance": perf_tracker.get_summary()
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _phase_fps(cfg: Dict[str, Any], phase: str) -> float:
        """Target sampling rate for a combat phase (idle / hunting / settled)."""
        if phase == "hunting":
            return max(0.5, cfg["active_fps"])
        if phase == "settled":
            return max(0.2, cfg["settled_fps"])
        return max(0.2, cfg["idle_fps"])

    def _count_empty_sample_locked(self, cfg: Dict[str, Any], now: float) -> bool:
        """Counts a sample without enemies or HUD during a battle; True when the battle is over:
        idle_timeout_seconds since a sample last showed enemies or HUD, and at least
        MIN_EMPTY_SAMPLES such samples. Elapsed time, not a number of samples at the phase's rate,
        so slow steps or a changed rate do not stretch or shorten it (caller holds _state_lock)."""
        self.empty_frames_count += 1
        return (self.empty_frames_count >= MIN_EMPTY_SAMPLES
                and now - self.battle_seen_at >= cfg["idle_timeout_seconds"])

    def _sync_detector_settings(self, cfg: Dict[str, Any]):
        det_limit = cfg["det_limit_side_len"]
        if self.detector.det_limit_side_len != det_limit:
            self.detector.set_det_limit_side_len(det_limit)
        threads = cfg["ocr_threads"]
        if self.detector.num_threads != threads:
            self.detector.set_num_threads(threads)
        self.detector.confidence_threshold = cfg["confidence_threshold"]

    def _apply_thread_qos(self, efficient: bool):
        """Switches the capture thread between EcoQoS (efficiency cores) and full speed. Priority
        and QoS belong to the thread, so the state is kept per thread (a restarted loop is a new
        thread that starts at normal priority)."""
        if getattr(self._thread_qos, "efficient", None) is efficient:
            return
        win_qos.set_current_thread_qos(efficient=efficient, below_normal=True)
        self._thread_qos.efficient = efficient

    def _log_error(self, message: str):
        """Logs loop errors without flooding the log when the same error repeats every frame."""
        now = time.monotonic()
        last_msg, last_time = self._last_error
        if message != last_msg or now - last_time >= ERROR_LOG_INTERVAL:
            logger.error(message)
            self._last_error = (message, now)

    def _prepare_ocr_frame(self, frame_bgra: np.ndarray) -> np.ndarray:
        """Centralized 1280x720 scaling and BGRA-to-BGR color conversion."""
        h, w = frame_bgra.shape[:2]
        max_w, max_h = 1280, 720
        if w > max_w or h > max_h:
            scale = min(max_w / w, max_h / h)
            target_w = int(w * scale)
            target_h = int(h * scale)
            small_bgra = cv2.resize(frame_bgra, (target_w, target_h), interpolation=cv2.INTER_AREA)
            return cv2.cvtColor(small_bgra, cv2.COLOR_BGRA2BGR)
        return cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)

    def _calculate_capture_bbox(self, win: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """The capture region in the passed window, the FFVII window (auto_window mode) or else
        the monitor the settings name (caller holds _capture_lock)."""
        cfg = calibration_manager.get_config()

        # Use passed window or try to locate FFVII window
        target_win = win
        if target_win is None and cfg["mode"] == "auto_window":
            target_win = self.window_finder.find_ffvii_window()

        if target_win:
            with self._state_lock:
                self.last_game_window = target_win
            return roi_bbox(target_win, cfg["roi"])

        # Fallback to monitor coordinates, of the monitor the settings name now (the setting can
        # change while the companion runs)
        with self._state_lock:
            self.last_game_window = None
        if self.capture_engine.requested_monitor != cfg["monitor_index"]:
            self.capture_engine.select_monitor(cfg["monitor_index"])
        return roi_bbox(self.capture_engine.monitors[self.capture_engine.monitor_index], cfg["roi"])

    def _grab(self, bbox: Dict[str, int]) -> np.ndarray:
        """Grabs a screen region as a BGRA array (caller must hold _capture_lock)."""
        return self.capture_engine.grab(bbox)

    def _clear_battle_state(self) -> bool:
        """Resets active battle tracking state. Returns True if a battle was previously active."""
        with self._state_lock:
            was_in_battle = bool(self.current_enemies) or self.combat_phase != "idle"
            self.current_enemies = []
            self.current_battle_title = None
            self.observed_counts = {}
            self.empty_frames_count = 0
            self._frames_since_ocr = 0
            self._last_ocr_signature = None
            self.combat_phase = "idle"
            self.hunting_frames_remaining = 0
        return was_in_battle

    def clear_battle(self) -> None:
        """
        Forgets the encounter collected so far (the dashboard's CLEAR) and fires on_battle_cleared:
        the next sample starts a new encounter, so a cleared enemy comes back only when it is read
        again, not along with the next new name. Waits for a sampling step in progress
        (_capture_lock), so that step cannot broadcast the cleared enemies afterwards.
        """
        with self._capture_lock:
            self._clear_battle_state()
            self._sync_current_fps(calibration_manager.get_config())
            self._fire_cleared()

    def _sync_current_fps(self, cfg: Dict[str, Any]) -> str:
        """Brings current_fps in line with the current phase and returns that phase."""
        with self._state_lock:
            phase = self.combat_phase
            self.current_fps = self._phase_fps(cfg, phase)
        return phase

    def _note_window_missing(self, cfg: Dict[str, Any], now: float) -> bool:
        """FFVII is not running: counts toward clearing any battle state, including a hunting
        phase that never registered an enemy. Returns True when the state was cleared."""
        cleared = False
        with self._state_lock:
            self.last_game_window = None
            if self.current_enemies or self.combat_phase != "idle":
                cleared = self._count_empty_sample_locked(cfg, now)
        if cleared:
            self._clear_battle_state()
        return cleared

    def add_enemy(self, enemy: Dict[str, Any]) -> bool:
        """
        Adds an enemy (a compendium entry, copied) to the encounter collected so far, for the
        dashboard's partners bar, and fires on_battle_detected: later updates of the encounter keep
        it, with the count every matching formation holds once it is a certain partner. Returns
        False when there is no encounter. Waits for a sampling step in progress (_capture_lock),
        like clear_battle().
        """
        with self._capture_lock:
            with self._state_lock:
                if not self.current_enemies:
                    return False
                if all(e["name"] != enemy["name"] for e in self.current_enemies):
                    added = dict(enemy)
                    count = compendium.deduce_formation(self.observed_counts)["partner_counts"].get(enemy["name"])
                    if count is not None:
                        added["count"] = count
                    self.current_enemies.append(added)
                    self.current_battle_title = self._battle_title_locked()
                    logger.info(f"Combat state updated (partner added): {self.current_battle_title}")
                snapshot = [dict(e) for e in self.current_enemies]
                title = self.current_battle_title
            self._fire_detected(snapshot, title)
        return True

    def _fire_detected(self, enemies: List[Dict[str, Any]], title: Optional[str]):
        if self.on_battle_detected:
            try:
                self.on_battle_detected(enemies, title)
            except Exception as ex:
                logger.error(f"Error in on_battle_detected callback: {ex}")

    def _fire_cleared(self):
        if self.on_battle_cleared:
            try:
                self.on_battle_cleared()
            except Exception as ex:
                logger.error(f"Error in on_battle_cleared callback: {ex}")

    def _fire_phase_changed(self):
        if self.on_phase_changed:
            try:
                self.on_phase_changed()
            except Exception as ex:
                logger.error(f"Error in on_phase_changed callback: {ex}")

    def capture_snapshot(self) -> Dict[str, Any]:
        """Captures a single frame, runs OCR, and returns diagnostic results with performance metrics."""
        t0 = time.perf_counter()

        with self._capture_lock:
            t_bbox = time.perf_counter()
            bbox = self._calculate_capture_bbox()
            window_find_ms = (time.perf_counter() - t_bbox) * 1000.0
            # The capture loop is the usual place that syncs these; it may be stopped
            self._sync_detector_settings(calibration_manager.get_config())
            t_grab = time.perf_counter()
            frame_bgra = self._grab(bbox)
            grab_ms = (time.perf_counter() - t_grab) * 1000.0

            t_cvt = time.perf_counter()
            frame = self._prepare_ocr_frame(frame_bgra)
            cvt_color_ms = (time.perf_counter() - t_cvt) * 1000.0

            t_ocr = time.perf_counter()
            # Diagnostics always recognize every box (no reuse of cached text)
            lines = self.detector.detect_text_lines(frame, use_cache=False)
            ocr_timing = dict(self.detector.last_ocr_timing)
            ocr_total_ms = (time.perf_counter() - t_ocr) * 1000.0

        t_match = time.perf_counter()
        enemies = self.detector.identify_enemies_from_lines(lines)
        match_ms = (time.perf_counter() - t_match) * 1000.0

        total_frame_ms = (time.perf_counter() - t0) * 1000.0

        perf_data = {
            "window_find_ms": round(window_find_ms, 2),
            "grab_ms": round(grab_ms, 2),
            "cvt_color_ms": round(cvt_color_ms, 2),
            "ocr_det_ms": ocr_timing.get("det_ms", 0.0),
            "ocr_rec_ms": ocr_timing.get("rec_ms", 0.0),
            "ocr_total_ms": round(ocr_total_ms, 2),
            "ocr_boxes": ocr_timing.get("boxes", 0),
            "match_ms": round(match_ms, 2),
            "total_frame_ms": round(total_frame_ms, 2),
            "effective_fps": round(1000.0 / total_frame_ms, 2) if total_frame_ms > 0 else 0.0
        }
        # Not recorded in perf_tracker: its statistics describe the capture loop's samples (a
        # snapshot raised their sampling rate and OCR share); the dialog shows these timings

        return {
            "bbox": bbox,
            "dimensions": {"width": frame.shape[1], "height": frame.shape[0]},
            "recognized_lines": lines,
            "matched_enemies": enemies,
            "game_window": self.last_game_window,
            "performance": perf_data
        }

    def _analyze_report_frame(self, frame_bgr: np.ndarray, in_battle: bool) -> Dict[str, Any]:
        """Full OCR pass (no cached boxes) and matching on one frame (caller holds _capture_lock)."""
        lines = self.detector.detect_text_lines(frame_bgr, use_cache=False)
        enemies = self.detector.identify_enemies_from_lines(lines)
        return {"image": frame_bgr, "lines": lines,
                "detected": {e["name"]: e.get("count", 1) for e in enemies},
                "hud": self.detector.is_combat_hud_active(lines, in_battle)}

    def save_detection_report(self, expected_text: Optional[str] = None, note: Optional[str] = None,
                              base_dir=None) -> Dict[str, Any]:
        """
        Saves a misdetection report (engine/detection_report.py): the frame of the last OCR pass
        (last_ocr.png) and a fresh grab of the capture region (current.png), each with its OCR
        lines and matches, plus the battle shown on the dashboard. A frame that is not available
        (no OCR pass yet, game window gone) is left out; with neither, ReportError is raised.
        """
        with self._state_lock:
            last_frame = self.last_ocr_frame
            battle = {e["name"]: e.get("count", 1) for e in self.current_enemies}
            in_battle = bool(self.current_enemies) or self.combat_phase != "idle"
            game_window = dict(self.last_game_window) if self.last_game_window else None
        cfg = calibration_manager.get_config()

        current_frame = None
        try:
            with self._capture_lock:
                current_frame = self._prepare_ocr_frame(self._grab(self._calculate_capture_bbox()))
        except Exception as ex:
            logger.warning(f"Misdetection report: could not grab the capture region: {ex}")

        frames: Dict[str, Dict[str, Any]] = {}
        with self._capture_lock:
            if last_frame is not None:
                frames["last_ocr.png"] = self._analyze_report_frame(last_frame, in_battle)
            if current_frame is not None:
                frames["current.png"] = self._analyze_report_frame(current_frame, in_battle)

        context = {
            "battle_on_dashboard": battle,
            "roi": cfg["roi"],
            "target_menu_preset": cfg["target_menu_preset"],
            "game_window": game_window,
        }
        result = detection_report.write_report(frames, expected_text, note, context, base_dir=base_dir)
        logger.info(f"Misdetection report saved to {result['path']}")
        return result

    def _deduce_with_cache(self, lines, in_battle: bool = False):
        """Downstream memoization: cached parse of enemies and combat HUD on identical line tuples
        (and battle state, which the HUD check depends on)."""
        lines_key = (tuple(lines), in_battle)
        if lines_key == self._last_downstream_lines_key:
            return self._last_downstream_enemies, self._last_downstream_hud
        detected_enemies = self.detector.identify_enemies_from_lines(lines)
        is_combat_hud = self.detector.is_combat_hud_active(lines, in_battle)
        self._last_downstream_lines_key = lines_key
        self._last_downstream_enemies = detected_enemies
        self._last_downstream_hud = is_combat_hud
        return detected_enemies, is_combat_hud

    # --------------------------------------------------------- state machine
    def _battle_title_locked(self) -> str:
        """The encounter's title with counts, names as the game writes them (caller holds _state_lock)."""
        parts = []
        for e in self.current_enemies:
            c = e.get("count", 1)
            label = e.get("display_name") or e["name"]
            parts.append(f"{c}x {label}" if c > 1 else label)
        return f"Battle: {', '.join(parts)}"

    def _decay_hunting_locked(self, settled_fps: float):
        """Counts down the hunting burst; settles once enemies are registered (caller holds _state_lock)."""
        if self.combat_phase != "hunting":
            return
        self.hunting_frames_remaining = max(0, self.hunting_frames_remaining - 1)
        if self.hunting_frames_remaining == 0 and self.current_enemies:
            logger.info(f"Enemies registered ({[e['name'] for e in self.current_enemies]}). "
                        f"Reducing sample rate to settled mode ({settled_fps} FPS).")
            self.combat_phase = "settled"

    def _update_battle_state(self, detected_enemies: List[Dict[str, Any]], is_combat_hud: bool,
                             ocr_ran: bool, cfg: Dict[str, Any], now: Optional[float] = None
                             ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], bool]:
        """
        Applies one frame's detection result to the accumulated battle state and the
        adaptive sampling phase. now is the sample's time.monotonic() (default: the current
        time). Returns (enemies_snapshot, battle_title, cleared): a snapshot/title when the
        encounter changed and should be broadcast, and cleared=True when the battle just ended.
        """
        now = time.monotonic() if now is None else now
        auto_add_partners = cfg["auto_add_scene_partners"]
        active_fps = self._phase_fps(cfg, "hunting")
        settled_fps = self._phase_fps(cfg, "settled")
        curr_enemies_snapshot = None
        battle_title_to_broadcast = None

        if detected_enemies:
            with self._state_lock:
                self.empty_frames_count = 0
                self.battle_seen_at = now
                self.last_detection_time = time.time()

                if not ocr_ran and self.current_enemies and self.current_battle_title:
                    # Steady state fast path: screen text is unchanged and battle is already registered.
                    # Eliminates redundant formation deductions, allocations, and title formatting.
                    self._decay_hunting_locked(settled_fps)
                else:
                    current_names = {e["name"] for e in self.current_enemies}
                    detected_names = {e["name"] for e in detected_enemies}
                    new_names = detected_names - current_names

                    # Items named like enemies: the battle Item menu lists them inside the capture
                    # region. One that cannot join the current enemies is that menu, not a new
                    # round (a highlighted Grenade used to replace the battle and its counts).
                    if current_names and new_names & ITEM_NAMED_ENEMIES:
                        menu_items = {n for n in new_names & ITEM_NAMED_ENEMIES
                                      if not compendium.find_matching_formations({**self.observed_counts, n: 1})}
                        if menu_items:
                            detected_enemies = [e for e in detected_enemies if e["name"] not in menu_items]
                            detected_names -= menu_items
                            new_names -= menu_items

                    # Battle Square / Arena round transition detection:
                    # If we have existing enemies and detected names contain a new enemy,
                    # verify if the new enemy can legally co-exist in ANY formation with current enemies.
                    if current_names and new_names:
                        combined_counts = {**self.observed_counts}
                        for e in detected_enemies:
                            combined_counts[e["name"]] = max(e.get("count", 1), combined_counts.get(e["name"], 0))
                        is_compatible = len(compendium.find_matching_formations(combined_counts)) > 0
                        if not is_compatible and not (detected_names & current_names):
                            logger.info(f"Battle Square round transition detected: {current_names} -> {detected_names}. Resetting encounter state.")
                            self.current_enemies = []
                            self.observed_counts = {}
                            self.current_battle_title = None
                            current_names = set()

                    # Update observed enemy instance counts
                    previous_counts = dict(self.observed_counts)
                    for e in detected_enemies:
                        name = e["name"]
                        detected_count = e.get("count", 1)
                        self.observed_counts[name] = max(detected_count, self.observed_counts.get(name, 0))

                    # Perform formation deduction against game encounter tables. The enemies
                    # collected so far need no pruning: within an encounter the observation only
                    # grows, which only narrows the matching formations, so every enemy seen and
                    # every partner that all earlier matches contained stays allowed (an
                    # incompatible new enemy starts a new encounter above). test_compendium checks
                    # this on every formation (GEMINI-20261013-03).
                    deduction = compendium.deduce_formation(self.observed_counts)
                    certain_partners = deduction["certain_partners"]
                    partner_counts = deduction["partner_counts"]

                    existing_names = {e["name"] for e in self.current_enemies}
                    added = False

                    for e in detected_enemies:
                        if e["name"] not in existing_names:
                            self.current_enemies.append(dict(e))
                            existing_names.add(e["name"])
                            added = True

                    # Auto-add only partners that every matching formation contains: an enemy that is
                    # merely possible (e.g. Kalm Fang next to a Devil Ride that can also appear alone)
                    # is offered on the dashboard's partners bar instead
                    if auto_add_partners and certain_partners:
                        for p in certain_partners:
                            if p["name"] not in existing_names:
                                self.current_enemies.append(dict(p))
                                existing_names.add(p["name"])
                                added = True

                    # Attach latest counts to current_enemies; the instance letter follows the
                    # count (a frame showing "B" raises the count to 2 and the letter to "B"), and
                    # an enemy first read without its letter takes the letter of the same count
                    detected_by_name = {d["name"]: d for d in detected_enemies}
                    for e in self.current_enemies:
                        count = self.observed_counts.get(e["name"])
                        if count is None:
                            # A partner not read yet (added automatically or from the partners
                            # bar): as many as every matching formation holds, once it is certain
                            if e["name"] in partner_counts:
                                e["count"] = partner_counts[e["name"]]
                            continue
                        seen = detected_by_name.get(e["name"])
                        if (seen is not None and seen.get("count", 1) == count
                                and (count != previous_counts.get(e["name"]) or e.get("instance_letter") is None)):
                            e["instance_letter"] = seen.get("instance_letter")
                        # A partner shown with its formations' count keeps it once one of them is
                        # read ("Grangalan Jr.Jr. A" is one of three); counts never fall within
                        # an encounter, as observation only narrows the matching formations
                        e["count"] = max(count, e.get("count", 1))

                    title = self._battle_title_locked()

                    should_broadcast = added or self.current_battle_title != title
                    if should_broadcast:
                        self.current_battle_title = title
                        logger.info(f"Combat state updated (accumulated): {title}")
                        curr_enemies_snapshot = [dict(e) for e in self.current_enemies]
                        battle_title_to_broadcast = title

                    # Adaptive sampling rate transitions
                    if new_names:
                        # New enemy appeared OR battle just started: boost to (or stay in) active hunting mode
                        if self.combat_phase != "hunting":
                            logger.info(f"Enemy detected ({new_names}). Increasing sample rate to {active_fps} FPS for fast identification.")
                        self.combat_phase = "hunting"
                        self.hunting_frames_remaining = HUNTING_BURST_FRAMES
                    else:
                        # Enemies already registered: decrement hunting burst and transition to settled
                        self._decay_hunting_locked(settled_fps)

            return curr_enemies_snapshot, battle_title_to_broadcast, False

        if is_combat_hud:
            # Battle HUD (HP/MP, commands, party members) is actively visible
            # Retain combat memory during attack animations, limit breaks, and spell casts
            with self._state_lock:
                self.empty_frames_count = 0
                self.battle_seen_at = now
                if len(self.current_enemies) == 0:
                    # In battle, but no enemy registered yet: hunt for enemies at increased sample rate
                    if self.combat_phase != "hunting":
                        logger.info(f"Combat HUD active. Increasing sample rate to {active_fps} FPS to identify enemies.")
                    self.combat_phase = "hunting"
                else:
                    # In battle, enemies already registered: decrement hunting burst and settle
                    self._decay_hunting_locked(settled_fps)
            return None, None, False

        # Neither enemies nor battle HUD are visible (victory summary or field map transition)
        cleared = False
        with self._state_lock:
            if self.current_enemies or self.combat_phase != "idle":
                if self._count_empty_sample_locked(cfg, now):
                    logger.info("Combat concluded (no combat HUD). Clearing encounter state and returning to idle sample rate.")
                    cleared = True
        if cleared:
            self._clear_battle_state()
        return None, None, cleared

    def process_frame(self, frame_bgra: np.ndarray, cfg: Optional[Dict[str, Any]] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
        """
        Runs one sampling step on a captured region (BGRA): text-change gate, OCR when
        the text may have changed, enemy matching, battle state and phase transitions,
        and the battle callbacks. now is the time.monotonic() of the grab (default: the current
        time; the tests pass their own clock). Returns timings and the outcome of the step.
        """
        cfg = cfg if cfg is not None else calibration_manager.get_config()
        now = time.monotonic() if now is None else now
        with self._state_lock:
            phase = self.combat_phase
            in_battle = bool(self.current_enemies) or phase != "idle"

        # 1. Text-change gate (sub-millisecond): skip OCR when the text-like pixels are unchanged
        t_gate = time.perf_counter()
        gate_enabled = cfg["text_gate"]
        signature = self.text_gate.signature(frame_bgra) if gate_enabled else None
        changed_px = -1
        if (not gate_enabled or self._last_ocr_signature is None
                or self._frames_since_ocr >= MAX_GATED_SKIPS.get(phase, 4)):
            run_ocr = True
        else:
            changed_px = self.text_gate.changed_pixels(signature, self._last_ocr_signature)
            run_ocr = changed_px >= self.text_gate.min_changed_px
        gate_ms = (time.perf_counter() - t_gate) * 1000.0

        # 2. OCR (or reuse of the last recognized lines)
        cvt_color_ms = 0.0
        ocr_total_ms = 0.0
        ocr_timing = {"det_ms": 0.0, "rec_ms": 0.0, "boxes": 0, "recognized": 0, "reused": 0}
        if run_ocr:
            t_cvt = time.perf_counter()
            frame = self._prepare_ocr_frame(frame_bgra)
            cvt_color_ms = (time.perf_counter() - t_cvt) * 1000.0
            t_ocr = time.perf_counter()
            lines = self.detector.detect_text_lines(frame, use_cache=True)
            ocr_total_ms = (time.perf_counter() - t_ocr) * 1000.0
            ocr_timing = dict(self.detector.last_ocr_timing)
            self._last_ocr_signature = signature
            self._frames_since_ocr = 0
            with self._state_lock:
                self.last_recognized_lines = lines
                self.last_ocr_frame = frame
                self.ocr_frames += 1
        else:
            self._frames_since_ocr += 1
            with self._state_lock:
                lines = list(self.last_recognized_lines)
                self.gated_frames += 1

        # 3. Matching, deduction and adaptive phase transitions
        t_match = time.perf_counter()
        detected_enemies, is_combat_hud = self._deduce_with_cache(lines, in_battle)
        snapshot, title, cleared = self._update_battle_state(detected_enemies, is_combat_hud, run_ocr, cfg, now)
        phase_after = self._sync_current_fps(cfg)
        match_ms = (time.perf_counter() - t_match) * 1000.0

        # 4. Callbacks, outside _state_lock (_loop still holds _capture_lock). Encounter broadcasts
        # carry the phase themselves; a phase change on its own (hunting -> settled) needs a
        # dedicated notification.
        if snapshot:
            self._fire_detected(snapshot, title)
        if cleared:
            self._fire_cleared()
        elif phase_after != phase and not snapshot:
            self._fire_phase_changed()

        return {
            "ocr_ran": run_ocr,
            "gate_changed_px": changed_px,
            "lines": lines,
            "enemies": detected_enemies,
            "combat_hud": is_combat_hud,
            "broadcast": snapshot is not None,
            "cleared": cleared,
            "gate_ms": gate_ms,
            "cvt_color_ms": cvt_color_ms,
            "ocr_total_ms": ocr_total_ms,
            "ocr_timing": ocr_timing,
            "match_ms": match_ms,
        }

    # ------------------------------------------------------------------ loop
    def _loop(self, stop_event: Optional[threading.Event] = None,
              previous: Optional[threading.Thread] = None):
        # Below-normal priority always; EcoQoS (efficiency cores) outside the hunting phase.
        # Keeps the background OCR loop off the performance cores FFVII runs on, while
        # still identifying new enemies at full speed.
        stop_event = stop_event or self._stop_event
        if previous is not None and previous.is_alive():
            previous.join()  # a loop whose stop() timed out finishes its step first
        cpu_prev = time.thread_time()
        wall_prev = time.monotonic()

        while not stop_event.is_set():
            start_time = time.monotonic()
            cfg = calibration_manager.get_config()
            self._sync_detector_settings(cfg)
            mode = cfg["mode"]

            current_phase = self._sync_current_fps(cfg)
            self._apply_thread_qos(efficient=bool(cfg["eco_mode"]) and current_phase != "hunting")


            try:
                t_loop0 = time.perf_counter()

                # The window search and the capture region are under _capture_lock like the grab:
                # capture_snapshot() and save_detection_report() use the same finder and capture
                # engine from request threads. A clear of a missing window never interleaves with
                # a CLEAR from the dashboard (clear_battle) either.
                with self._capture_lock:
                    t_bbox = time.perf_counter()
                    win = self.window_finder.find_ffvii_window() if mode == "auto_window" else None
                    window_missing = mode == "auto_window" and not win
                    if window_missing:
                        if self._note_window_missing(cfg, time.monotonic()):
                            self._sync_current_fps(cfg)
                            self._fire_cleared()
                    else:
                        bbox = self._calculate_capture_bbox(win)
                        window_find_ms = (time.perf_counter() - t_bbox) * 1000.0
                        t_grab = time.perf_counter()
                        frame_bgra = self._grab(bbox)
                        grab_ms = (time.perf_counter() - t_grab) * 1000.0
                        outcome = self.process_frame(frame_bgra, cfg, time.monotonic())

                if window_missing:
                    # No game window (closed, or minimized by an alt-tab out of fullscreen).
                    # During a battle, wait like a sampling step, so the battle clears close to
                    # idle_timeout_seconds after the game was last seen (the finder's backed-off
                    # scan interval, up to 2.5 s, overshot it); otherwise until the finder's
                    # next scan.
                    with self._state_lock:
                        in_battle = bool(self.current_enemies) or self.combat_phase != "idle"
                        fps = self._phase_fps(cfg, self.combat_phase)
                    if stop_event.wait(1.0 / fps if in_battle else self.window_finder.scan_interval):
                        break
                    continue

                total_frame_ms = (time.perf_counter() - t_loop0) * 1000.0
                cpu_now = time.thread_time()
                wall_now = time.monotonic()
                with self._state_lock:
                    active_count = len(self.current_enemies)
                    phase_after = self.combat_phase
                timing = outcome["ocr_timing"]
                perf_tracker.record_frame(
                    window_find_ms=window_find_ms,
                    grab_ms=grab_ms,
                    cvt_color_ms=outcome["cvt_color_ms"],
                    ocr_det_ms=timing.get("det_ms", 0.0),
                    ocr_rec_ms=timing.get("rec_ms", 0.0),
                    ocr_total_ms=outcome["ocr_total_ms"],
                    match_ms=outcome["match_ms"],
                    total_frame_ms=total_frame_ms,
                    width=frame_bgra.shape[1],
                    height=frame_bgra.shape[0],
                    lines_count=len(outcome["lines"]),
                    enemies_count=active_count,
                    in_combat=active_count > 0,
                    preset_name=cfg["target_menu_preset"],
                    ocr_ran=outcome["ocr_ran"],
                    gate_ms=outcome["gate_ms"],
                    cpu_ms=(cpu_now - cpu_prev) * 1000.0,
                    interval_ms=(wall_now - wall_prev) * 1000.0,
                    phase=phase_after,
                )
                cpu_prev, wall_prev = cpu_now, wall_now

            except Exception as e:
                self._log_error(f"Error in capture loop: {e}")

            # Sleep until the next sample at the rate of the phase this step ended in, so a
            # battle starting from idle is hunted at once and settled sleeps its full interval.
            # Nothing is grabbed between samples: settled_fps is the real sampling rate.
            with self._state_lock:
                fps = self._phase_fps(cfg, self.combat_phase)
            elapsed = time.monotonic() - start_time
            if stop_event.wait(max(0.01, (1.0 / fps) - elapsed)):
                break

capture_service = CaptureService()
