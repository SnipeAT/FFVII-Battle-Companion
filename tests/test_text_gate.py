import unittest

from ffvii_companion.engine.text_gate import TextChangeGate
from tests.helpers import load_fixture_bgra
from tools.ff7_synthetic import DEFAULT_ROI, crop_roi, render_battle_frame


def _roi(**kwargs):
    return crop_roi(render_battle_frame(1920, 1080, **kwargs), DEFAULT_ROI)


class TestTextChangeGateReal(unittest.TestCase):
    """Decisions on consecutive frames recorded from a real battle."""

    def setUp(self):
        self.gate = TextChangeGate()

    def _changed(self, a: str, b: str) -> bool:
        return self.gate.has_changed(self.gate.signature(load_fixture_bgra(b)),
                                     self.gate.signature(load_fixture_bgra(a)))

    def test_static_text_with_live_scene_is_unchanged(self):
        self.assertFalse(self._changed("devil_ride_b_static_1.png", "devil_ride_b_static_2.png"))

    def test_instance_letter_change_is_detected(self):
        self.assertTrue(self._changed("devil_ride_a.png", "devil_ride_b.png"))

    def test_target_name_appearing_is_detected(self):
        self.assertTrue(self._changed("hud_only.png", "prowler.png"))
        self.assertTrue(self._changed("prowler.png", "kalm_fang_b.png"))

    def test_battle_end_is_detected(self):
        self.assertTrue(self._changed("hud_only.png", "victory_screen.png"))
        self.assertTrue(self._changed("victory_screen.png", "field_no_text.png"))

    def test_signature_is_small(self):
        sig = self.gate.signature(load_fixture_bgra("prowler.png"))
        self.assertLessEqual(sig.shape[0], 2 * self.gate.rows)
        self.assertEqual(sig.ndim, 2)


class TestTextChangeGateSynthetic(unittest.TestCase):
    def setUp(self):
        self.gate = TextChangeGate()

    def test_animated_scene_and_atb_gauges_are_ignored(self):
        ref = self.gate.signature(_roi(frame_index=0, target_name="Devil Ride B"))
        for i in range(1, 6):
            sig = self.gate.signature(_roi(frame_index=i, target_name="Devil Ride B"))
            self.assertFalse(self.gate.has_changed(sig, ref), f"frame {i}")

    def test_name_changes_are_detected(self):
        a = self.gate.signature(_roi(target_name="Custom Sweeper A"))
        b = self.gate.signature(_roi(target_name="Custom Sweeper B"))
        none = self.gate.signature(_roi(target_name=None))
        self.assertTrue(self.gate.has_changed(b, a))
        self.assertTrue(self.gate.has_changed(a, none))
        self.assertTrue(self.gate.has_changed(none, a))

    def test_command_menu_toggle_is_detected(self):
        open_menu = self.gate.signature(_roi(command_menu=True))
        closed_menu = self.gate.signature(_roi(command_menu=False))
        self.assertTrue(self.gate.has_changed(closed_menu, open_menu))

    def test_missing_reference_counts_as_change(self):
        sig = self.gate.signature(_roi())
        self.assertTrue(self.gate.has_changed(sig, None))


if __name__ == "__main__":
    unittest.main()
