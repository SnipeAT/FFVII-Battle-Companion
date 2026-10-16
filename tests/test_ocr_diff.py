"""tools/ocr_diff.py: recording the OCR output of frame folders and comparing two records."""
import copy
import os
import shutil
import tempfile
import unittest

from tests.helpers import FIXTURES_DIR, load_manifest
from tools.ocr_diff import check_folders, compare, failed, frame_files, record

FIXTURE_FRAMES = ("prowler.png", "devil_ride_b.png", "victory_screen.png")


def _record(frames):
    return {"format": 1, "settings": {"det_limit_side_len": 640, "confidence_threshold": 0.5},
            "frames": frames}


def _frame(texts, enemies=(), hud=(True, True)):
    return {"lines": [[t, 0.99, [0, 0, 10, 10]] for t in texts], "kept": list(texts),
            "enemies": [list(e) for e in enemies], "hud": list(hud)}


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.before = _record({"s/roi_0000.png": _frame(["Prowler", "HP"], [("Prowler", 1, None)]),
                               "s/roi_0001.png": _frame(["Item"], hud=(False, False))})

    def test_identical_records_have_no_differences(self):
        diff = compare(self.before, copy.deepcopy(self.before))
        self.assertEqual(diff["frames"], 2)
        for kind in ("outcome", "kept", "text", "scores_or_boxes", "only_before", "only_after"):
            self.assertEqual(diff[kind], [], kind)
        self.assertFalse(diff["settings_differ"])

    def test_text_change_without_outcome_change(self):
        after = copy.deepcopy(self.before)
        after["frames"]["s/roi_0001.png"] = _frame(["ltem"], hud=(False, False))
        diff = compare(self.before, after)
        self.assertEqual(diff["outcome"], [])
        self.assertEqual([r["frame"] for r in diff["text"]], ["s/roi_0001.png"])
        self.assertEqual([r["frame"] for r in diff["kept"]], ["s/roi_0001.png"])

    def test_outcome_change_is_reported_with_both_sides(self):
        after = copy.deepcopy(self.before)
        after["frames"]["s/roi_0000.png"] = _frame(["Prowler B", "HP"], [("Prowler", 2, "B")])
        diff = compare(self.before, after)
        self.assertEqual(len(diff["outcome"]), 1)
        self.assertEqual(diff["outcome"][0]["before"]["enemies"], [["Prowler", 1, None]])
        self.assertEqual(diff["outcome"][0]["after"]["enemies"], [["Prowler", 2, "B"]])

    def test_hud_change_is_an_outcome_change(self):
        after = copy.deepcopy(self.before)
        after["frames"]["s/roi_0001.png"]["hud"] = [False, True]
        self.assertEqual(len(compare(self.before, after)["outcome"]), 1)

    def test_score_or_box_change_alone(self):
        after = copy.deepcopy(self.before)
        after["frames"]["s/roi_0000.png"]["lines"][0][1] = 0.98
        diff = compare(self.before, after)
        self.assertEqual(diff["scores_or_boxes"], ["s/roi_0000.png"])
        self.assertEqual(diff["text"], [])

    def test_frames_in_only_one_record(self):
        after = copy.deepcopy(self.before)
        del after["frames"]["s/roi_0001.png"]
        diff = compare(self.before, after)
        self.assertEqual(diff["only_before"], ["s/roi_0001.png"])
        self.assertEqual(diff["frames"], 1)
        self.assertTrue(failed(diff))

    def test_only_outcome_changes_fail_a_comparison(self):
        after = copy.deepcopy(self.before)
        after["frames"]["s/roi_0001.png"] = _frame(["ltem"], hud=(False, False))
        self.assertFalse(failed(compare(self.before, after)))
        after["frames"]["s/roi_0001.png"]["hud"] = [True, True]
        self.assertTrue(failed(compare(self.before, after)))

    def test_comparing_no_frames_fails(self):
        # Two records of empty folders must not pass as "nothing changed"
        self.assertTrue(failed(compare(_record({}), _record({}))))


class TestCheckFolders(unittest.TestCase):
    def _folder(self, parent, *parts, frames=("roi_0000.png",)):
        path = os.path.join(parent, *parts)
        os.makedirs(path)
        for name in frames:
            open(os.path.join(path, name), "wb").close()
        return path

    def test_folders_with_the_same_name_are_refused(self):
        # Their frames would share keys and one recording would silently replace the other
        with tempfile.TemporaryDirectory() as tmp:
            a = self._folder(tmp, "day1", "session")
            b = self._folder(tmp, "day2", "session")
            with self.assertRaisesRegex(ValueError, "same|named"):
                check_folders([a, b])
            check_folders([a])

    def test_folder_without_frames_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = self._folder(tmp, "session1", frames=())
            with self.assertRaisesRegex(ValueError, "No PNG frames"):
                check_folders([empty])


class TestRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ocr_diff_test_")
        cls.folder = os.path.join(cls.tmp, "fixtures")
        os.mkdir(cls.folder)
        for name in FIXTURE_FRAMES:
            shutil.copy(FIXTURES_DIR / name, cls.folder)
        cls.result = record([cls.folder])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_frames_are_keyed_by_folder_and_file(self):
        self.assertEqual(sorted(self.result["frames"]), sorted(f"fixtures/{n}" for n in FIXTURE_FRAMES))

    def test_outcomes_match_the_fixture_manifest(self):
        manifest = load_manifest()
        for name in FIXTURE_FRAMES:
            frame = self.result["frames"][f"fixtures/{name}"]
            self.assertEqual({e[0]: e[1] for e in frame["enemies"]}, manifest[name]["enemies"], name)
            self.assertTrue(frame["lines"], name)
            # Kept lines are the raw lines the detector accepts
            self.assertTrue(set(frame["kept"]) <= {l[0].strip() for l in frame["lines"]}, name)

    def test_recording_again_gives_no_differences(self):
        diff = compare(self.result, record([self.folder]))
        self.assertEqual((diff["outcome"], diff["text"], diff["scores_or_boxes"]), ([], [], []))

    def test_recordings_use_only_their_roi_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("roi_0001.png", "roi_0000.png", "preview.png"):
                open(os.path.join(folder, name), "wb").close()
            self.assertEqual([os.path.basename(p) for p in frame_files(folder)], ["roi_0000.png", "roi_0001.png"])


if __name__ == "__main__":
    unittest.main()
