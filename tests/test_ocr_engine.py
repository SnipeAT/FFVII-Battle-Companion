"""OCR engine tests on frames recorded from a real FFNx battle session (tests/fixtures/real)."""
import unittest

from ffvii_companion.engine.calibration import DEFAULT_CONFIG
from ffvii_companion.engine.detector import BattleDetector
from ffvii_companion.engine.ocr_engine import OCREngine, create_session, DET_MODEL_PATH
from tests.helpers import load_fixture_bgr, load_manifest


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

    def test_matches_rapidocr_pipeline(self):
        """With RapidOCR's effective detection size the engine reproduces RapidOCR's text output."""
        from rapidocr_onnxruntime import RapidOCR
        reference = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
        reference.text_det.limit_type = "max"
        engine = OCREngine(threads=1, det_limit_side_len=1500)
        for name in ("prowler.png", "devil_ride_b.png", "victory_screen.png"):
            img = load_fixture_bgr(name)
            res, _ = reference(img, use_cls=False)
            expected = [r[1] for r in (res or [])]
            got = [l.text for l in engine.run(img, use_cache=False).lines if l.score >= 0.5]
            self.assertEqual(got, expected, name)


if __name__ == "__main__":
    unittest.main()
