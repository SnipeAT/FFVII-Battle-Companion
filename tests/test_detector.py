import unittest

from ffvii_companion.engine.detector import BattleDetector


class TestLineParsing(unittest.TestCase):
    def test_parser_strips_instance_letter(self):
        self.assertEqual(BattleDetector.parse_enemy_line("Sweeper A")[0], "Sweeper")

    def test_parser_instance_and_rows(self):
        cand, inst, count = BattleDetector.parse_enemy_line("Formula [Back row]")
        self.assertEqual(cand, "Formula")
        self.assertEqual(count, 1)

        cand2, inst2, count2 = BattleDetector.parse_enemy_line("Nerosuferoth C")
        self.assertEqual(cand2, "Nerosuferoth")
        self.assertEqual(inst2, "C")
        self.assertEqual(count2, 3)

        # MP with instance letter is Shinra MP enemy
        cand3, inst3, count3 = BattleDetector.parse_enemy_line("MP B")
        self.assertEqual(cand3, "MP")
        self.assertEqual(inst3, "B")
        self.assertEqual(count3, 2)

        # Bare MP HUD label is filtered
        cand4, _, _ = BattleDetector.parse_enemy_line("MP")
        self.assertIsNone(cand4)

    def test_cached_parse_enemy_line(self):
        c1, l1, cnt1 = BattleDetector.parse_enemy_line("Grunt B")
        c2, l2, cnt2 = BattleDetector.parse_enemy_line("Grunt B")
        self.assertEqual((c1, l1, cnt1), ("Grunt", "B", 2))
        self.assertEqual((c1, l1, cnt1), (c2, l2, cnt2))

    def test_commands_party_and_overlay_are_rejected(self):
        for line in ("Attack", "Mime", "Magic", "Manip.", "Manip", "E.Skill", "Throw", "Cloud", "Nanaki", "Cid",
                     "WAIT / LIMIT", "BARRIER", "FPS 60 158 161 CPU 9%/1110%", "3675", "767"):
            self.assertIsNone(BattleDetector.parse_enemy_line(line)[0], line)

    def test_glued_instance_letter_requires_known_stem(self):
        # OCR sometimes drops the space before the instance letter
        self.assertEqual(BattleDetector.parse_enemy_line("GruntB"), ("Grunt", "B", 2))
        self.assertEqual(BattleDetector.parse_enemy_line("MPA"), ("MP", "A", 1))
        # ...but names and ordinary words ending in a-h are not split into name + letter
        self.assertEqual(BattleDetector.parse_enemy_line("Bandersnatch"), ("Bandersnatch", None, 1))
        self.assertEqual(BattleDetector.parse_enemy_line("Speed"), ("Speed", None, 1))

    def test_exact_names_ending_in_digits_are_kept(self):
        self.assertEqual(BattleDetector.parse_enemy_line("Unknown 2")[0], "Unknown 2")

    def test_names_with_punctuation_keep_their_last_letter(self):
        # OCR reads the game's middle dot ("Grosspanzer·Big") as "-", ".", ":" or a space; the
        # name's own last letter then became an instance letter: one Grosspanzer·Big showed as
        # "7x" (g = G, the 7th letter) and Grosspanzer·Mobile as "5x" (e = E)
        for line in ("Grosspanzer-Big", "Grosspanzer.Big", "Grosspanzer:Big", "Grosspanzer Big",
                     "Grosspanzer-Mobile", "Jenova-LIFE", "Jenova LIFE"):
            with self.subTest(line=line):
                self.assertEqual(BattleDetector.parse_enemy_line(line), (line, None, 1))
        # A real instance letter after such a name still counts
        self.assertEqual(BattleDetector.parse_enemy_line("SOLDIER:3rd B"), ("SOLDIER:3rd", "B", 2))

    def test_instance_letters_stay_within_the_formations(self):
        # CLAUDE-20261012-B02: one misread letter in Jenova·BIRTH left its glued H as an instance
        # letter (8x for the whole battle); no formation holds more than one Jenova·BIRTH
        for line, name in (("Jenova·BlRTH", "Jenova·BlRTH"), ("Jen0va·DEATH", "Jen0va·DEATH"),
                           ("Jenova·LlFE", "Jenova·LlFE"), ("Jenova-BlRTH", "Jenova-BlRTH")):
            with self.subTest(line=line):
                self.assertEqual(BattleDetector.parse_enemy_line(line), (name, None, 1))
        # a separate letter beyond the formations is a misread letter: the count stays possible
        self.assertEqual(BattleDetector.parse_enemy_line("Grosspanzer-Mobile B"), ("Grosspanzer-Mobile", None, 1))
        self.assertEqual(BattleDetector.parse_enemy_line("Devil Ride H"), ("Devil Ride", None, 2))
        self.assertEqual(BattleDetector.parse_enemy_line("Devil Ride B"), ("Devil Ride", "B", 2))

    def test_no_enemy_name_reads_as_an_instance_letter(self):
        # Every name and in-game spelling, also with its middle dot as OCR reads it (Safer·Sephiroth
        # and Jenova·DEATH became "8x", Jenova·LIFE "5x")
        from ffvii_companion.compendium import compendium
        spellings = set()
        for enemy in compendium.get_all():
            for name in (enemy["name"], enemy.get("display_name", enemy["name"])):
                spellings.update(name.replace("·", sep) for sep in ("·", "-", ".", ":", " "))
        for line in sorted(spellings):
            with self.subTest(line=line):
                self.assertIsNone(BattleDetector.parse_enemy_line(line)[1])

    def test_glued_lowercase_letter_is_part_of_the_name(self):
        # Instance letters are capitals; a lowercase a-h glued to a misread name is its own letter
        for line in ("Grosspanzer Blg", "Bandersnateh"):
            with self.subTest(line=line):
                self.assertEqual(BattleDetector.parse_enemy_line(line), (line, None, 1))


class TestEnemyIdentification(unittest.TestCase):
    def setUp(self):
        self.det = BattleDetector()

    def test_defaults_are_the_settings_defaults(self):
        # A detector built without arguments (tests, tools) must read text like the app, which
        # passes the settings: its own threshold used to be 0.5 against the setting's 0.45
        from ffvii_companion.engine.calibration import DEFAULT_CONFIG
        self.assertEqual(self.det.confidence_threshold, DEFAULT_CONFIG["confidence_threshold"])
        self.assertEqual(self.det.det_limit_side_len, DEFAULT_CONFIG["det_limit_side_len"])
        self.assertEqual(self.det.num_threads, DEFAULT_CONFIG["ocr_threads"])

    def test_punctuated_name_counts_once(self):
        enemies = self.det.identify_enemies_from_lines(["Grosspanzer-Big", "Attack", "HP", "MP"])
        self.assertEqual({e["name"]: e["count"] for e in enemies}, {"Grosspanzerbig": 1})

    def test_detector_line_identification(self):
        enemies = self.det.identify_enemies_from_lines(["Trickplay", "Boundfat (Back row)", "Attack", "HP", "MP"])
        names = [e["name"] for e in enemies]
        self.assertIn("Trickplay", names)
        self.assertIn("Boundfat", names)
        self.assertNotIn("Attack", names)

    def test_multiple_instance_letters_in_lines(self):
        enemies = self.det.identify_enemies_from_lines(["Nerosuferoth A", "Nerosuferoth C", "Attack", "HP"])
        self.assertEqual(len(enemies), 1)
        self.assertEqual(enemies[0]["name"], "Nerosuferoth")
        # Should record the highest observed count (C -> 3)
        self.assertEqual(enemies[0]["count"], 3)
        self.assertEqual(enemies[0]["instance_letter"], "C")

    def test_letter_is_kept_next_to_an_unlettered_line(self):
        # GEMINI-20261009-01: a line read without its letter must not hide the letter of the same count
        for lines in (["Devil Ride", "Devil Ride A"], ["Devil Ride A", "Devil Ride"]):
            enemies = self.det.identify_enemies_from_lines(lines)
            self.assertEqual([(e["count"], e["instance_letter"]) for e in enemies], [(1, "A")], lines)

    def test_real_ocr_lines(self):
        # Lines recognized from recorded gameplay frames
        enemies = self.det.identify_enemies_from_lines(["Custom Sweeper B", "ttack", "Mime", "HP", "MP"])
        self.assertEqual([(e["name"], e["count"]) for e in enemies], [("Custom Sweeper", 2)])
        self.assertEqual(self.det.identify_enemies_from_lines(["Attack all opponents", "lash-All", "Morph", "HP", "MP"]), [])


class TestMisreadNames(unittest.TestCase):
    """CLAUDE-20261012-B02 / -B03: names with one OCR misreading must give the right enemy (or
    none) and count 1. Before the fix, 10 of these spellings gave another enemy ("Unkn0wn 2" ->
    Unknown, "Turks:Rnde" -> Reno) and 12 a count of 5 or 8 (the Jenova names)."""
    CONFUSIONS = [("O", "0"), ("0", "O"), ("o", "0"), ("l", "I"), ("I", "l"), ("i", "l"),
                  ("l", "1"), ("1", "l"), ("rn", "m"), ("m", "rn"), ("u", "n"), ("n", "u"),
                  ("e", "c"), ("c", "e"), ("S", "5"), ("B", "8"), ("·", "-"), ("·", "."),
                  ("·", " "), (" ", "")]

    @classmethod
    def misreadings(cls, name):
        out = set()
        for old, new in cls.CONFUSIONS:
            start = 0
            while (i := name.find(old, start)) != -1:
                out.add(name[:i] + new + name[i + len(old):])
                start = i + 1
        out.discard(name)
        return out

    def test_one_misread_character_never_gives_another_enemy_or_count(self):
        from ffvii_companion.compendium import compendium
        det = BattleDetector()
        wrong, miscounted, total = [], [], 0
        for enemy in compendium.get_all():
            for name in {enemy["name"], enemy.get("display_name", enemy["name"])}:
                for line in self.misreadings(name):
                    total += 1
                    found = det.identify_enemies_from_lines([line])
                    if not found:
                        continue  # unmatched is safe: nothing wrong is shown
                    if found[0]["id"] != enemy["id"]:
                        wrong.append((line, enemy["name"], found[0]["name"]))
                    elif found[0]["count"] != 1:
                        miscounted.append((line, found[0]["count"]))
        self.assertGreater(total, 1500)
        self.assertEqual(wrong, [])
        self.assertEqual(miscounted, [])

    def test_reviewer_examples(self):
        det = BattleDetector()
        for line, enemy_id in (("Unkn0wn 2", "unknown-2"), ("Unknovvn 3", "unknown-3"),
                               ("Turks:Rnde", "turks-rude"), ("Jenova·BlRTH", "jenovabirth")):
            with self.subTest(line=line):
                found = det.identify_enemies_from_lines([line])
                self.assertEqual([(e["id"], e["count"]) for e in found], [(enemy_id, 1)])


class TestDetectorGuards(unittest.TestCase):
    """Guards that mutations removed without a failing test (CLAUDE-20261012-B09)."""

    def test_only_letters_a_to_h_are_instance_letters(self):
        # a formation holds at most 8 enemies, so the game's letters stop at H
        self.assertEqual(BattleDetector.parse_enemy_line("Grunt K")[1:], (None, 1))
        det = BattleDetector()
        self.assertEqual([e["count"] for e in det.identify_enemies_from_lines(["Grunt K"])], [1])

    def test_field_menu_lines_rule_out_the_hud(self):
        # the field menu shows HP / MP and party names next to lines only it has
        for line in ("LIMIT LEVEL", "NEXT LEVEL", "MATERIA", "EQUIP", "SAVE"):
            with self.subTest(line=line):
                self.assertFalse(BattleDetector.is_combat_hud_active(["Cloud", "HP", "MP", line]))
        self.assertTrue(BattleDetector.is_combat_hud_active(["Cloud", "HP", "MP"]))

    def test_low_confidence_lines_are_dropped(self):
        from unittest import mock
        import numpy as np
        from ffvii_companion.engine.ocr_engine import OCRLine, OCRResult
        det = BattleDetector(confidence_threshold=0.45)
        result = OCRResult(lines=[OCRLine("Prowler", 0.9, (0, 0, 1, 1)), OCRLine("Grunt", 0.3, (0, 0, 1, 1))])
        with mock.patch.object(det._ocr, "run", return_value=result):
            self.assertEqual(det.detect_text_lines(np.zeros((8, 8, 3), np.uint8)), ["Prowler"])


class TestCombatHud(unittest.TestCase):
    def test_hud_detection(self):
        self.assertTrue(BattleDetector.is_combat_hud_active(["HP", "MP"]))
        self.assertTrue(BattleDetector.is_combat_hud_active(["Cloud", "ttack", "Mime", "HP", "MP"]))
        self.assertFalse(BattleDetector.is_combat_hud_active(["Level: 58", "next level:", "18539p", "Cid", "EXP:"]))
        self.assertFalse(BattleDetector.is_combat_hud_active([]))

    def test_idle_hud_needs_the_hp_or_mp_header(self):
        # The field main menu closing, from a recorded session: whole command words, no HP / MP header
        self.assertFalse(BattleDetector.is_combat_hud_active(["Item", "Magic", "Mater"]))
        self.assertFalse(BattleDetector.is_combat_hud_active(["Attack", "Cloud"]))
        self.assertTrue(BattleDetector.is_combat_hud_active(["Attack", "Cloud", "HP"]))
        # During a battle, commands still count when OCR missed the headers
        self.assertTrue(BattleDetector.is_combat_hud_active(["Attack", "Cloud"], in_battle=True))
        self.assertTrue(BattleDetector.is_combat_hud_active(["ttack", "Mime", "Manip."], in_battle=True))

    def test_field_menus_are_not_the_battle_hud(self):
        # OCR lines of field menus from a recorded session: they show HP / MP and party names too
        field_menus = [
            ["Lv 61", "HP", "3845/3939", "Limit Level", "Gil", "696/ 732", "MP", "Time"],          # status
            ["LV", "61", "HP", "3845/3939", "Limit Level", "Gil", "696/ 732", "83", "MP", "Time"],  # status
            ["MP", "506/", "551", "Soft", ": 1", "Maiden's Kiss", ": 1", "Cid", "Cornucopia"],    # item menu
            ["MP", "506/", "551", "Cid"],                                                         # item menu
            ["Vitality", "-01", "Exit", "Magic", "+02", "Transform", "Magic def +01"],            # materia
            ["Item", "Magic", "Materia", "Equip", "Status", "Order", "Limit", "Config", "PHS", "Save", "Quit"],
        ]
        for lines in field_menus:
            self.assertFalse(BattleDetector.is_combat_hud_active(lines), lines)
        # A character header on its own: the level beside HP and MP as current/max, also with the
        # level number read apart or a level that could be a limit level
        for lines in (["Lv 61", "HP", "3845/3939", "MP", "506/551"],
                      ["LV", "61", "HP", "3845/3939", "MP", "506/551"],
                      ["Lv 3", "HP", "300/ 300", "MP", "50/ 50"]):
            self.assertFalse(BattleDetector.is_combat_hud_active(lines), lines)
        # A character level (above the limit levels 1-4) is never the battle HUD
        self.assertFalse(BattleDetector.is_combat_hud_active(["Lv 61", "HP", "MP"], in_battle=True))

    def test_hud_words_inside_longer_text_do_not_count(self):
        # Materia screen lines from a recorded session (fixtures/real/materia_sneak_attack*.png):
        # the HUD words Attack and HP inside ability names
        for lines in (["Sneak Attack", "O Elemental", "O HP Absorb"],
                      ["O Sneak Attack", "O Elemental", "O HP Absorb"],
                      ["Sneak Attack", "Sneak Attack", "O Elemental"]):
            self.assertFalse(BattleDetector.is_combat_hud_active(lines), lines)
            self.assertFalse(BattleDetector.is_combat_hud_active(lines, in_battle=True), lines)
        # Battle help text alone is not the HUD either (it always shows under the HP / MP headers)
        self.assertFalse(BattleDetector.is_combat_hud_active(["Restores HP by 500", "Attack with equipped weapon"]))
        # A HUD word followed by its value still counts (OCR groups numbers with labels)
        for lines in (["HP 9999", "MP 999"], ["HP9999", "MP 506/"], ["HP 9999", "Cloud 3500"]):
            self.assertTrue(BattleDetector.is_combat_hud_active(lines), lines)

    def test_limit_menu_is_the_battle_hud(self):
        # The limit menu's "LIMIT  Lv 3" header (limit levels 1-4) under the HUD's HP / MP headers;
        # the first lines were recognized from a real frame (fixtures/real/limit_menu_lv3.png)
        for lines in (["Attacks all opponents", "IT", "Lv3", "HP", "MP"],
                      ["LIMIT", "Lv 3", "HP", "MP"],
                      ["Lv", "3", "HP", "MP"]):
            self.assertTrue(BattleDetector.is_combat_hud_active(lines), lines)
            self.assertTrue(BattleDetector.is_combat_hud_active(lines, in_battle=True), lines)
        # Next to a current/max value it reads like a field menu's character header, unless a battle
        # is on: field menus cannot open during a battle (the 1998 HUD may show HP as cur/max)
        lines = ["Lv3", "HP", "MP", "3587/3675", "Cloud"]
        self.assertFalse(BattleDetector.is_combat_hud_active(lines))
        self.assertTrue(BattleDetector.is_combat_hud_active(lines, in_battle=True))

    def test_battle_hud_from_recorded_battle(self):
        # Battle lines of the same session (target help text, command window, HUD headers)
        for lines in (["Attack with equipped weapon", "ttack", "Mime", "HP", "MP"],
                      ["Extreme summoned monsters", "MP", "HP", "MP"],
                      ["All opponents", "MP", "HP", "MP"],
                      ["Devil Ride", "ttack", "Mime", "HP", "MP"]):
            self.assertTrue(BattleDetector.is_combat_hud_active(lines), lines)
        # A current/max value never vetoes both HUD headers (the 1998 HUD may show HP as cur/max)
        self.assertTrue(BattleDetector.is_combat_hud_active(["HP", "MP", "3587/3675", "Cloud"]))


if __name__ == "__main__":
    unittest.main()
