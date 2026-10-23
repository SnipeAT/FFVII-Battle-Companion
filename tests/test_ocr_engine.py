"""OCR engine tests on frames recorded from a real FFNx battle session (tests/fixtures/real)."""
import json
import sys
import unittest

import numpy as np

from ffvii_companion.engine.calibration import DEFAULT_CONFIG
from ffvii_companion.engine.detector import BattleDetector
from ffvii_companion.engine.ocr_engine import OCREngine, create_session, DET_MODEL_PATH
from ffvii_companion.ppocr.postprocess import DBPostProcess
from tests.helpers import FIXTURES_DIR, load_fixture_bgr, load_manifest

# What the engine reads in every fixture frame, at the default detection size and at RapidOCR's
# (1500). Recorded on 2026-10-11 with rapidocr_onnxruntime 1.4.4's own post-processing code, while
# a test still checked that the engine's text matched RapidOCR's pipeline; the frames added later
# (field_menu_status*.png, prowler_wide_crop.png) with the bundled copy in ffvii_companion/ppocr,
# whose output matched RapidOCR's on 2392 recorded frames. After a deliberate change of what the
# engine reads, re-record with: python -m tests.test_ocr_engine --record
EXPECTED_OCR_PATH = FIXTURES_DIR / "ocr_expected.json"
EXPECTED_OCR_LIMITS = (DEFAULT_CONFIG["det_limit_side_len"], 1500)


def ocr_snapshot() -> dict:
    """{det limit: {frame: [[text, score, [x0, y0, x1, y1]], ...]}} for every fixture frame, uncached."""
    snapshot = {}
    for limit in EXPECTED_OCR_LIMITS:
        engine = OCREngine(threads=1, det_limit_side_len=limit)
        snapshot[str(limit)] = {
            name: [[l.text, round(l.score, 4), list(l.box)] for l in engine.run(load_fixture_bgr(name), use_cache=False).lines]
            for name in sorted(load_manifest())
        }
    return snapshot


def record_expected_ocr() -> None:
    """Writes ocr_snapshot() to EXPECTED_OCR_PATH, one line per frame."""
    blocks = []
    for limit, frames in ocr_snapshot().items():
        rows = ",\n".join(f"  {json.dumps(name)}: {json.dumps(lines, ensure_ascii=False)}" for name, lines in frames.items())
        blocks.append(f' {json.dumps(limit)}: {{\n{rows}\n }}')
    EXPECTED_OCR_PATH.write_text("{\n" + ",\n".join(blocks) + "\n}\n", encoding="utf-8")


class _ShapeSpy:
    """Wraps an ONNX session to record the input tensor shapes it receives."""

    def __init__(self, session):
        self._session = session
        self.shapes = []

    def run(self, outputs, feeds):
        for value in feeds.values():
            self.shapes.append(tuple(value.shape))
        return self._session.run(outputs, feeds)


class TestOCREngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = OCREngine(threads=1, det_limit_side_len=960)
        cls.engine.load()
        cls.manifest = load_manifest()

    def test_lazy_loading(self):
        engine = OCREngine()
        self.assertFalse(engine.loaded)
        engine.load()
        self.assertTrue(engine.loaded)
        engine.set_threads(2)
        self.assertFalse(engine.loaded, "Changing the thread count must recreate the sessions")

    def test_session_options_disable_spinning(self):
        session = create_session(DET_MODEL_PATH, threads=1)
        opts = session.get_session_options()
        self.assertEqual(opts.intra_op_num_threads, 1)
        self.assertEqual(opts.get_session_config_entry("session.intra_op.allow_spinning"), "0")
        self.assertEqual(opts.get_session_config_entry("session.inter_op.allow_spinning"), "0")

    def test_det_limit_is_honored(self):
        engine = OCREngine(threads=1, det_limit_side_len=512)
        engine.load()
        spy = _ShapeSpy(engine._det_session)
        engine._det_session = spy
        engine.run(load_fixture_bgr("prowler.png"), use_cache=False)
        self.assertEqual(max(spy.shapes[0][2:]), 512)

    def test_recognition_runs_one_crop_per_inference(self):
        # Batching pads every crop to the widest one in the batch (slower, same text)
        engine = OCREngine(threads=1)
        engine.load()
        spy = _ShapeSpy(engine._rec_session)
        engine._rec_session = spy
        result = engine.run(load_fixture_bgr("devil_ride_b.png"), use_cache=False)
        self.assertEqual(len(spy.shapes), result.boxes)
        self.assertTrue(all(shape[0] == 1 for shape in spy.shapes), spy.shapes)

    def test_fixture_expectations(self):
        # At the shipped default detection size (DEFAULT_CONFIG)
        detector = BattleDetector(num_threads=1, det_limit_side_len=DEFAULT_CONFIG["det_limit_side_len"],
                                  confidence_threshold=0.45)
        for name, expect in self.manifest.items():
            with self.subTest(fixture=name):
                lines = detector.detect_text_lines(load_fixture_bgr(name), use_cache=False)
                enemies = {e["name"]: e["count"] for e in detector.identify_enemies_from_lines(lines)}
                self.assertEqual(enemies, expect["enemies"], f"lines={lines}")
                self.assertEqual(detector.is_combat_hud_active(lines), expect["hud"], f"lines={lines}")

    def test_degenerate_frames_read_as_nothing(self):
        # The post-processing in ffvii_companion/ppocr is the project's own code now: blank, noisy
        # and tiny frames (down to 1x1, and very short ones that get letterboxed) must not raise
        rng = np.random.default_rng(0)
        frames = {
            "black": np.zeros((215, 1280, 3), np.uint8),
            "white": np.full((215, 1280, 3), 255, np.uint8),
            "noise": rng.integers(0, 256, (215, 1280, 3), dtype=np.uint8),
            "1x1": np.zeros((1, 1, 3), np.uint8),
            "1280x8": np.full((8, 1280, 3), 255, np.uint8),
            "8x1280": np.full((1280, 8, 3), 255, np.uint8),
        }
        engine = OCREngine(threads=1)
        for name, img in frames.items():
            with self.subTest(frame=name):
                self.assertEqual(engine.run(img, use_cache=False).lines, [])

    def test_cache_returns_identical_text(self):
        sequence = ["prowler.png", "kalm_fang_b.png", "kalm_fang_b.png", "hud_only.png", "devil_ride_a.png",
                    "devil_ride_b.png", "devil_ride_b_static_1.png", "devil_ride_b_static_2.png", "victory_screen.png"]
        cached_engine = OCREngine(threads=1, det_limit_side_len=960)
        for name in sequence:
            img = load_fixture_bgr(name)
            cached = [l.text for l in cached_engine.run(img, use_cache=True).lines]
            fresh = [l.text for l in self.engine.run(img, use_cache=False).lines]
            self.assertEqual(cached, fresh, name)

    def test_cache_reuses_unchanged_boxes(self):
        engine = OCREngine(threads=1, det_limit_side_len=960)
        first = engine.run(load_fixture_bgr("devil_ride_b_static_1.png"))
        second = engine.run(load_fixture_bgr("devil_ride_b_static_2.png"))
        self.assertEqual(first.reused, 0)
        self.assertGreater(second.reused, 0)
        self.assertLess(second.recognized, first.recognized)
        self.assertEqual([l.text for l in first.lines], [l.text for l in second.lines])

    def test_cache_detects_instance_letter_change(self):
        engine = OCREngine(threads=1, det_limit_side_len=960)
        engine.run(load_fixture_bgr("devil_ride_a.png"))
        texts = [l.text for l in engine.run(load_fixture_bgr("devil_ride_b.png")).lines]
        self.assertIn("Devil Ride B", texts)
        self.assertNotIn("Devil Ride A", texts)

    def test_output_matches_the_recorded_ocr(self):
        """
        Every fixture frame reads as recorded in EXPECTED_OCR_PATH, so a change of the models, the
        post-processing, ONNX Runtime or Python shows up here when it changes what the engine reads
        in these frames: battle text, field menus with small boxes (the minimum box size and the
        box scoring) and prowler_wide_crop.png (the letterbox of very wide frames). The text must
        match exactly; boxes may move by 2 px and scores by 0.01 (floating-point differences
        between CPUs and library versions).
        """
        expected = json.loads(EXPECTED_OCR_PATH.read_text(encoding="utf-8"))
        actual = ocr_snapshot()
        self.assertEqual(sorted(actual), sorted(expected))
        for limit, frames in expected.items():
            self.assertEqual(sorted(actual[limit]), sorted(frames), f"det limit {limit}: fixture frames")
            for name, lines in frames.items():
                with self.subTest(limit=limit, frame=name):
                    got = actual[limit][name]
                    self.assertEqual([line[0] for line in got], [line[0] for line in lines])
                    for (text, score, box), (_, exp_score, exp_box) in zip(got, lines):
                        self.assertAlmostEqual(score, exp_score, delta=0.01, msg=text)
                        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(box, exp_box)), (text, box, exp_box))


class TestPostProcess(unittest.TestCase):
    def test_area_and_length_match_shapely(self):
        # unclip() replaced Shapely's Polygon area and length; these boxes (cv2.boxPoints, float32)
        # are ones where the plain shoelace formula missed Shapely's area in the last bit. Expected
        # values from Shapely 2.1, which area_and_length() matched on 1,000,000 random boxes.
        cases = [
            ([[4.5038533210754395, -0.8662948608398438], [83.62586975097656, -7.421756744384766],
              [94.60237121582031, 125.06063842773438], [15.480354309082031, 131.6160888671875]],
             10554.229798923987, 424.65889473114703),
            ([[26.011825561523438, 316.4729309082031], [-1.759061574935913, 311.4219055175781],
              [0.2135980725288391, 300.5761413574219], [27.984485626220703, 305.6271667480469]],
             311.1604496830896, 78.50039306708169),
            ([[413.5007629394531, 118.98301696777344], [417.2511291503906, 13.467774391174316],
              [1226.11279296875, 42.21710968017578], [1222.3624267578125, 147.7323455810547]],
             85455.05249197526, 1829.9085782901075),
        ]
        for box, area, length in cases:
            self.assertEqual(DBPostProcess.area_and_length(np.array(box, dtype=np.float32)), (area, length))


if __name__ == "__main__":
    if "--record" in sys.argv:
        record_expected_ocr()
        print(f"Wrote {EXPECTED_OCR_PATH}")
    else:
        unittest.main()
