"""Player profile validation and persistence (engine/player_profile.py)."""
import codecs
import json
import tempfile
import unittest
from pathlib import Path

from ffvii_companion.paths import get_user_data_dir
from ffvii_companion.engine.player_profile import (
    DEFAULT_PROFILE, PROFILE_FILE, PlayerProfile, ProfileValidationError, validate_profile
)


class TestValidation(unittest.TestCase):
    def test_valid_partial_update(self):
        self.assertEqual(validate_profile({"party_level": 30.4, "other": 1}), {"party_level": 30})
        self.assertEqual(validate_profile({"learned_enemy_skills": ["Laser", "Frog Song", "Laser"]}),
                         {"learned_enemy_skills": ["Frog Song", "Laser"]})

    def test_invalid_values_are_rejected(self):
        for bad in ({"party_level": 100}, {"party_level": True}, {"sneak_glove": 1},
                    {"learned_enemy_skills": ["Ultima"]}, {"learned_enemy_skills": {"Laser": True}}):
            with self.assertRaises(ProfileValidationError, msg=bad):
                validate_profile(bad)

    def test_clamp_mode_repairs_hand_edited_files(self):
        clean = validate_profile({"party_level": 250, "sneak_glove": "yes",
                                  "learned_enemy_skills": ["Laser", "Ultima"]}, clamp=True)
        self.assertEqual(clean, {"party_level": 99, "learned_enemy_skills": ["Laser"]})

    def test_clamp_mode_ignores_unhashable_skill_entries(self):
        # GEMINI-20261007-11
        clean = validate_profile({"learned_enemy_skills": [{"name": "Laser"}, ["Laser"], "Frog Song", 3]},
                                 clamp=True)
        self.assertEqual(clean, {"learned_enemy_skills": ["Frog Song"]})
        with self.assertRaises(ProfileValidationError):
            validate_profile({"learned_enemy_skills": [{"name": "Laser"}]})


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "profile.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_file_is_in_the_isolated_test_home(self):
        self.assertEqual(PROFILE_FILE.resolve(), (get_user_data_dir() / "data" / "profile.json").resolve())

    def test_defaults_without_file(self):
        self.assertEqual(PlayerProfile(self.path).get(), DEFAULT_PROFILE)

    def test_update_persists_and_reloads(self):
        profile = PlayerProfile(self.path)
        profile.update({"party_level": 33, "learned_enemy_skills": ["Beta"]})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["party_level"], 33)
        reloaded = PlayerProfile(self.path).get()
        self.assertEqual((reloaded["party_level"], reloaded["learned_enemy_skills"]), (33, ["Beta"]))

    def test_get_returns_a_copy(self):
        profile = PlayerProfile(self.path)
        profile.get()["learned_enemy_skills"].append("Laser")
        self.assertEqual(profile.get()["learned_enemy_skills"], [])

    def test_corrupt_file_falls_back_to_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(PlayerProfile(self.path).get(), DEFAULT_PROFILE)

    def test_file_with_byte_order_mark_is_read(self):
        # CLAUDE-20261012-A03: Notepad's "UTF-8 with BOM" made the profile fall back to the defaults
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(codecs.BOM_UTF8 + json.dumps({"party_level": 50}).encode("utf-8"))
        self.assertEqual(PlayerProfile(self.path).get()["party_level"], 50)

    def test_failed_save_changes_nothing(self):
        # CLAUDE-20261012-A04: the new value stayed in memory although the file kept the old one
        from unittest import mock
        profile = PlayerProfile(self.path)
        profile.update({"party_level": 20})
        with mock.patch("ffvii_companion.engine.player_profile.os.replace",
                        side_effect=PermissionError("held by another program")):
            with self.assertRaises(OSError):
                profile.update({"party_level": 60})
        self.assertEqual(profile.get()["party_level"], 20)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["party_level"], 20)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
