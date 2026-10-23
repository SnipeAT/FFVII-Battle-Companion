"""tools/ocr_diff.py: recording the OCR output of frame folders and comparing two records; the
--tree option it shares with tools/benchmark_loop.py (tools/source_tree.py)."""
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import FIXTURES_DIR, load_manifest
from tools import ocr_diff
from tools.ocr_diff import compare, failed, folder_frames, frame_files, load, record
from tools.source_tree import PROJECT_ROOT, use_tree

FIXTURE_FRAMES = ("prowler.png", "devil_ride_b.png", "victory_screen.png")


def _record(frames):
    return {"format": 1, "settings": {"det_limit_side_len": 640, "confidence_threshold": 0.5},
            "frames": frames}


def _frame(texts, enemies=(), hud=(True, True)):
    return {"lines": [[t, 0.99, [0, 0, 10, 10]] for t in texts], "kept": list(texts),
            "enemies": [list(e) for e in enemies], "hud": list(hud)}


def _write(folder, name, data):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    return path


@contextlib.contextmanager
def _isolated_tree_setup():
    """Restores sys.path and the environment, which use_tree() changes."""
    with mock.patch.object(sys, "path", list(sys.path)), mock.patch.dict(os.environ):
        yield


def run_main(*argv):
    """ocr_diff.main() with these arguments: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["ocr_diff", *argv]), _isolated_tree_setup(), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            ocr_diff.main()
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


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

    def test_load_refuses_files_of_another_format(self):
        # compare() would misread their frames: every frame changed, or a KeyError
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(_write(tmp, "ok.json", self.before)), self.before)
            for name, data in (("v2.json", dict(self.before, format=2)), ("list.json", []),
                               ("no_frames.json", {"format": 1}), ("text.json", "not json"),
                               ("empty_frame.json", _record({"s/roi_0000.png": {}}))):
                with self.assertRaisesRegex(ValueError, "not an ocr_diff record|Expecting value"):
                    load(_write(tmp, name, data))


class TestMain(unittest.TestCase):
    """The command line: exit codes and usage errors (no OCR runs here)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocr_diff_main_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.before = _record({"s/roi_0000.png": _frame(["Prowler", "HP"], [("Prowler", 1, None)])})

    def compare_with(self, after, *options):
        return run_main("compare", _write(self.tmp, "before.json", self.before),
                        _write(self.tmp, "after.json", after), *options)

    def test_compare_exit_codes(self):
        code, out, _ = self.compare_with(self.before)
        self.assertEqual(code, 0)
        self.assertIn("Detection outcome changed: 0", out)
        text_only = copy.deepcopy(self.before)
        text_only["frames"]["s/roi_0000.png"]["lines"][0][0] = "Prowlr"
        self.assertEqual(self.compare_with(text_only)[0], 0)
        outcome = copy.deepcopy(self.before)
        outcome["frames"]["s/roi_0000.png"]["enemies"] = []
        code, out, _ = self.compare_with(outcome)
        self.assertEqual(code, 1)
        self.assertIn("Detection outcome changed: 1", out)

    def test_compare_refuses_a_file_of_another_format(self):
        code, _, err = self.compare_with(dict(self.before, format=2))
        self.assertEqual(code, 2)
        self.assertIn("not an ocr_diff record", err)
        self.assertEqual(run_main("compare", os.path.join(self.tmp, "missing.json"), "x.json")[0], 2)

    def test_show_below_zero_is_refused(self):
        # rows[:-1] listed every frame but the last under "first -1 of N"
        self.assertEqual(self.compare_with(self.before, "--show", "-1")[0], 2)
        self.assertEqual(self.compare_with(self.before, "--show", "0")[0], 0)

    def test_record_checks_its_inputs_before_the_ocr(self):
        frames = os.path.join(self.tmp, "session1")
        os.mkdir(frames)
        open(os.path.join(frames, "roi_0000.png"), "wb").close()
        out = os.path.join(self.tmp, "out.json")
        no_tree = os.path.join(self.tmp, "no_tree")
        os.mkdir(no_tree)
        with mock.patch.object(ocr_diff, "record", side_effect=AssertionError("ran the OCR")):
            for args, message in (([os.path.join(self.tmp, "missing")], "No folder"),
                                  ([os.path.join(self.tmp)], "No PNG frames"),
                                  ([frames, "--tree", no_tree], "No src/ffvii_companion/")):
                with self.subTest(message):
                    code, _, err = run_main("record", out, *args)
                    self.assertEqual(code, 2)
                    self.assertIn(message, err)
        self.assertFalse(os.path.exists(out))


class TestUseTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocr_diff_tree_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_folder_without_the_package_is_refused(self):
        # On sys.path it would be skipped and the editable install would answer every import, so
        # a run would measure this copy's code under the tree's name
        with _isolated_tree_setup():
            path_before = list(sys.path)
            with self.assertRaisesRegex(ValueError, "No src/ffvii_companion/"):
                use_tree(os.path.join(self.tmp, "missing"), "ocr_diff_test_")
            os.mkdir(os.path.join(self.tmp, "engine"))
            with self.assertRaisesRegex(ValueError, "No src/ffvii_companion/ in"):
                use_tree(self.tmp, "ocr_diff_test_")
            self.assertEqual(sys.path, path_before)

    def test_the_flat_layout_needs_flat_layout(self):
        os.mkdir(os.path.join(self.tmp, "engine"))
        with _isolated_tree_setup():
            self.assertFalse(use_tree(self.tmp, "ocr_diff_test_", flat_layout=True))
            self.assertEqual(sys.path[0], self.tmp)

    def test_the_project_itself_is_accepted(self):
        with _isolated_tree_setup():
            self.assertTrue(use_tree(PROJECT_ROOT, "ocr_diff_test_"))
            self.assertEqual(sys.path[0], os.path.join(PROJECT_ROOT, "src"))
            home = os.environ["FFVII_COMPANION_HOME"]
            self.assertTrue(os.path.isdir(home))
            self.assertEqual(os.listdir(home), [])

    def test_a_package_already_imported_from_elsewhere_is_refused(self):
        # Once imported from the project, the tree's copy would never load (imported here, so the
        # test does not depend on an earlier test having imported it)
        import ffvii_companion  # noqa: F401
        os.makedirs(os.path.join(self.tmp, "src", "ffvii_companion"))
        with _isolated_tree_setup(), self.assertRaisesRegex(ValueError, "already imported"):
            use_tree(self.tmp, "ocr_diff_test_")

    def test_the_trees_code_wins_over_the_editable_install(self):
        package = os.path.join(self.tmp, "src", "ffvii_companion")
        os.makedirs(package)
        open(os.path.join(package, "__init__.py"), "w").close()
        script = ("import sys; from tools.source_tree import use_tree; use_tree(sys.argv[1], 'ocr_diff_test_'); "
                  "import ffvii_companion; print(ffvii_companion.__file__)")
        done = subprocess.run([sys.executable, "-c", script, self.tmp], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(os.path.normcase(done.stdout.strip()), os.path.normcase(os.path.join(package, "__init__.py")))


class TestFolderFrames(unittest.TestCase):
    def _folder(self, parent, *parts, frames=("roi_0000.png",)):
        path = os.path.join(parent, *parts)
        os.makedirs(path)
        for name in frames:
            open(os.path.join(path, name), "wb").close()
        return path

    def test_frames_are_keyed_by_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._folder(tmp, "day1", "session1", frames=("roi_0001.png", "roi_0000.png"))
            b = self._folder(tmp, "session2")
            self.assertEqual(folder_frames([a, b]), {
                "session1": [os.path.join(a, "roi_0000.png"), os.path.join(a, "roi_0001.png")],
                "session2": [os.path.join(b, "roi_0000.png")]})

    def test_folders_with_the_same_name_are_refused(self):
        # Their frames would share keys and one recording would silently replace the other
        with tempfile.TemporaryDirectory() as tmp:
            a = self._folder(tmp, "day1", "session")
            b = self._folder(tmp, "day2", "session")
            with self.assertRaisesRegex(ValueError, "same|named"):
                folder_frames([a, b])
            folder_frames([a])

    def test_missing_folder_or_one_without_frames_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = self._folder(tmp, "session1", frames=())
            with self.assertRaisesRegex(ValueError, "No PNG frames"):
                folder_frames([empty])
            with self.assertRaisesRegex(ValueError, "No folder"):
                folder_frames([os.path.join(tmp, "session2")])

    def test_recordings_use_only_their_roi_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("roi_0001.png", "roi_0000.png", "preview.png"):
                open(os.path.join(folder, name), "wb").close()
            self.assertEqual([os.path.basename(p) for p in frame_files(folder)], ["roi_0000.png", "roi_0001.png"])


class TestRecord(unittest.TestCase):
    """A record written by the command line, read back as compare does."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ocr_diff_test_")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.folder = os.path.join(cls.tmp, "fixtures")
        os.mkdir(cls.folder)
        for name in FIXTURE_FRAMES:
            shutil.copy(FIXTURES_DIR / name, cls.folder)
        out = os.path.join(cls.tmp, "record.json")
        code, _, err = run_main("record", out, cls.folder)
        if code:
            raise AssertionError(f"record exited with {code}: {err}")
        cls.result = load(out)

    def test_frames_are_keyed_by_folder_and_file(self):
        self.assertEqual(sorted(self.result["frames"]), sorted(f"fixtures/{n}" for n in FIXTURE_FRAMES))
        self.assertEqual(self.result["tree"], PROJECT_ROOT)

    def test_outcomes_match_the_fixture_manifest(self):
        manifest = load_manifest()
        for name in FIXTURE_FRAMES:
            frame = self.result["frames"][f"fixtures/{name}"]
            self.assertEqual({e[0]: e[1] for e in frame["enemies"]}, manifest[name]["enemies"], name)
            self.assertTrue(frame["lines"], name)
            # Kept lines are the raw lines the detector accepts
            self.assertTrue(set(frame["kept"]) <= {l[0].strip() for l in frame["lines"]}, name)

    def test_recording_again_gives_no_differences(self):
        # Also checks the JSON round trip: the file read back equals a record made in memory
        diff = compare(self.result, record(folder_frames([self.folder])))
        self.assertEqual(diff["frames"], len(FIXTURE_FRAMES))
        self.assertEqual((diff["outcome"], diff["kept"], diff["text"], diff["scores_or_boxes"]), ([], [], [], []))
        self.assertFalse(diff["settings_differ"])


if __name__ == "__main__":
    unittest.main()
