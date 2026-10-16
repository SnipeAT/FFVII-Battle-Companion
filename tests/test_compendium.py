import json
import tempfile
import unittest
from pathlib import Path

from ffvii_companion.compendium import FORMATIONS_PATH, compendium, EnemyCompendium, RE_TRAILING_INSTANCE_LETTER


class TestCompendiumLookup(unittest.TestCase):
    def test_compendium_loaded(self):
        all_enemies = compendium.get_all()
        self.assertGreater(len(all_enemies), 250, "Compendium should contain the full enemy list")

    def test_no_developer_test_entries(self):
        self.assertFalse([e["name"] for e in compendium.get_all() if "|" in e["name"]])

    def test_fuzzy_matching(self):
        test_cases = [
            ("SOLDIER 3rd", "SOLDIER:3rd"),
            ("soldier:3rd", "SOLDIER:3rd"),
            ("1stRay", "1st Ray"),
            ("Guard Scorpion", "Guard Scorpion"),
            ("eligor", "Eligor"),
            ("Custom Sweeper", "Custom Sweeper"),
            ("Air Buster", "Air Buster"),
            ("Midgar Zolom", "Midgar Zolom"),
            ("Devil Ride", "Devil Ride"),
            ("Kalm Fang", "Kalm Fang"),
            ("Bandersnatch", "Bandersnatch"),
        ]
        for query, expected_name in test_cases:
            match = compendium.match_enemy(query)
            self.assertIsNotNone(match, f"Failed to match query: {query}")
            self.assertEqual(match["name"], expected_name)

    def test_short_and_command_strings_do_not_match(self):
        # ("MP" is a real enemy, the Shinra MP; the HUD label is filtered by the line parser instead.)
        for query in ("HP", "ttack", "Mime", "Attack all opponents", "Level: 58"):
            self.assertIsNone(compendium.match_enemy(query), query)

    def test_boss_and_alias_matching(self):
        match_hojo = compendium.match_enemy("Lifeform-Hojo:NA")
        self.assertIn(match_hojo["id"], ["lifeform-hojo-n", "lifeformhojo-n"])

        match_jenova = compendium.match_enemy("Jenova-BIRTH")
        self.assertIsNotNone(match_jenova)
        self.assertEqual(match_jenova["id"], "jenovabirth")

        match_safer = compendium.match_enemy("Safer Sephiroth")
        self.assertIsNotNone(match_safer)
        self.assertEqual(match_safer["id"], "safersephiroth")

    def test_elemental_affinities(self):
        vlak = compendium.get_by_name("Vlakorados")
        self.assertIsNotNone(vlak)
        self.assertEqual(vlak["weaknesses"], [])
        self.assertEqual(vlak["resistances"], ["Gravity"])
        self.assertEqual(vlak["absorbs"], [])

        zem = compendium.get_by_name("Zemzelett")
        self.assertIsNotNone(zem)
        self.assertEqual(zem["weaknesses"], ["Wind"])
        self.assertIn("Earth", zem["elemental_immunities"])
        self.assertIn("Water", zem["elemental_immunities"])

    def test_grangalan_drops(self):
        grangalan = compendium.get_by_name("Grangalan")
        self.assertIsNotNone(grangalan)
        self.assertEqual(len(grangalan["drops"]), 1)
        self.assertEqual(grangalan["drops"][0]["item"], "Hi-Potion")
        self.assertEqual(grangalan["drops"][0]["rate"], "14.06%")

    def test_instance_letter_normalization(self):
        self.assertEqual(RE_TRAILING_INSTANCE_LETTER.sub("", "MPA").strip(), "MP")
        self.assertEqual(RE_TRAILING_INSTANCE_LETTER.sub("", "MP A").strip(), "MP")
        self.assertEqual(RE_TRAILING_INSTANCE_LETTER.sub("", "Nerosuferoth B").strip(), "Nerosuferoth")
        self.assertEqual(RE_TRAILING_INSTANCE_LETTER.sub("", "NerosuferothB").strip(), "Nerosuferoth")


    def test_digit_named_enemies_stay_distinct(self):
        # GEMINI-20261007-01: "Unknown 2" / "Unknown 3" must not collapse into one entry
        for name in ("Unknown", "Unknown 2", "Unknown 3"):
            self.assertEqual(compendium.match_enemy(name)["name"], name)

    def test_digit_named_enemies_keep_digit_with_instance_letter(self):
        # GEMINI-20261007-09: the instance letter is removed, the digit belongs to the name
        for query, expected in (("Unknown 2 A", "Unknown 2"), ("Unknown 3 B", "Unknown 3"),
                                ("Unknown A", "Unknown"), ("Devil Ride B", "Devil Ride")):
            self.assertEqual(compendium.match_enemy(query)["name"], expected)

    def test_canonical_names_resolve_to_themselves(self):
        for enemy in compendium._enemies_cache.values():
            self.assertEqual(compendium.match_enemy(enemy["name"])["name"], enemy["name"])

    def test_aliases_all_resolve(self):
        for query, expected in (("Soldier 3rd", "SOLDIER:3rd"), ("Soldier 2nd", "SOLDIER:2nd"),
                                ("Soldier 1st", "SOLDIER:1st"), ("Schizo right", "Schizo(right)"),
                                ("Schizo left", "Schizo(left)")):
            self.assertEqual(compendium.match_enemy(query)["name"], expected)


class TestMatchGuards(unittest.TestCase):
    """Guards that mutations removed without a failing test (CLAUDE-20261012-B09)."""

    def test_short_queries_must_match_exactly(self):
        self.assertEqual(compendium.match_enemy("Mu")["name"], "Mu")
        self.assertIsNone(compendium.match_enemy("Mux"))  # "mu" scores 80, but 3 letters is too short

    def test_length_must_be_close(self):
        self.assertEqual(compendium.match_enemy("Sweeperrr")["name"], "Sweeper")
        self.assertIsNone(compendium.match_enemy("Sweeperrrr"))  # scores 82, 3 letters longer

    def test_unstripped_query_wins_a_tie(self):
        # No misreading of a real name ties (GEMINI-20261012-07), so two made-up enemies: "nabed"
        # (the letter stripped) scores 80 against Nnbed, "nabed c" (as read) 80 against Nabedo D.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "enemies.json"
            path.write_text(json.dumps([{"id": "nnbed", "name": "Nnbed"},
                                        {"id": "nabedo-d", "name": "Nabedo D"}]), encoding="utf-8")
            small = EnemyCompendium(path, Path(tmp) / "formations.json")
        self.assertEqual(small.match_enemy("Nabed C")["name"], "Nabedo D")

    def test_whitespace_flood_is_linear_time(self):
        # CodeQL #4-#8: unanchored \s* / \s+ quantifiers and ^(.*?) with optional trailing count
        # backtracked quadratically on strings with many spaces
        import time
        from ffvii_companion.engine.detector import BattleDetector
        from ffvii_companion.engine.detection_report import parse_expected
        spaces = " " * 50_000
        t0 = time.perf_counter()
        self.assertIsNone(compendium.match_enemy(f"NotAnEnemy{spaces}Z"))
        self.assertEqual(compendium.match_enemy(f"Sweeper{spaces}(Back row) A")["name"], "Sweeper")
        self.assertEqual(BattleDetector.parse_enemy_line(f"Devil Ride{spaces}(Back row) B"), ("Devil Ride", "B", 2))
        self.assertEqual(parse_expected(f"NotAnEnemy{spaces[:400]}x!"), ({}, ["NotAnEnemy x!"]))
        self.assertEqual(parse_expected("Devil Ride   x2, Kalm Fang"), ({"Devil Ride": 2, "Kalm Fang": 1}, []))
        self.assertLess(time.perf_counter() - t0, 0.5)
        # The linear-time patterns match single spaces, so parse_enemy_line collapses the line
        # first: without that, OCR's "(Back   row)" stayed in the name as "Devil Ride row)"
        self.assertEqual(BattleDetector.parse_enemy_line("Devil Ride (Back   row) B"), ("Devil Ride", "B", 2))


class TestAiScripts(unittest.TestCase):
    def test_entries_know_whether_they_have_scripts(self):
        self.assertTrue(compendium.get_by_name("Guard Scorpion")["has_ai"])
        self.assertFalse(compendium.get_by_name("Manhole(lid)")["has_ai"])  # no AI scripts in scene.bin

    def test_ai_box(self):
        record, = compendium.get_ai("guard-scorpion")["records"]
        self.assertEqual(record["enemy_ids"], [22])
        self.assertIn("Tail Laser", [a["name"] for a in record["attacks"]])
        self.assertIsNone(compendium.get_ai("does-not-exist"))
        self.assertIsNone(compendium.get_ai(compendium.get_by_name("Manhole(lid)")["id"]))

    def test_summary_goes_out_with_the_ai_box_only(self):
        # Battle updates send the entry dicts as they are: the summary must not ride along
        summary = [{"title": "Each turn", "rows": [["Always", "Bites"]]}]
        with tempfile.TemporaryDirectory() as tmp:
            path, ai_path = Path(tmp) / "enemies.json", Path(tmp) / "ai_scripts.json"
            path.write_text(json.dumps([{"id": "nnbed", "name": "Nnbed", "ai_summary": summary}]), encoding="utf-8")
            ai_path.write_text(json.dumps({"nnbed": [{"enemy_ids": [1], "scenes": [2], "reacts": [], "attacks": [],
                                                      "scripts": []}]}), encoding="utf-8")
            small = EnemyCompendium(path, Path(tmp) / "formations.json", ai_path)
        self.assertNotIn("ai_summary", small.get_by_id("nnbed"))
        self.assertTrue(small.get_by_id("nnbed")["has_ai"])
        self.assertEqual(small.get_ai("nnbed")["summary"], summary)


class TestFormations(unittest.TestCase):
    def test_growing_observation_keeps_earlier_enemies_allowed(self):
        # GEMINI-20261013-03: the capture loop never prunes the enemies it collected. Within an
        # encounter the observation only grows (more names, higher counts), which can only narrow
        # the matching formations, so the enemies seen and the partners that were certain before
        # stay allowed. Checked from each enemy of every real formation.
        formations = [f for f in json.loads(FORMATIONS_PATH.read_text(encoding="utf-8")) if not f.get("unused")]
        for form in formations:
            names = sorted(form["enemies"])
            for first in names:
                before = compendium.deduce_formation({first: 1})
                kept = {first} | {p["name"] for p in before["certain_partners"]}
                grown = [{first: form["enemies"][first]}] + [{first: 1, other: 1} for other in names if other != first]
                for observed in grown:
                    allowed = compendium.deduce_formation(observed)["allowed_enemies"]
                    self.assertTrue(allowed, observed)  # a formation that holds them exists: this one
                    self.assertLessEqual(kept, allowed, observed)

    def test_formation_deduction(self):
        deduction = compendium.deduce_formation({"Nerosuferoth": 2, "Formula": 1})
        self.assertTrue(deduction["is_disambiguated"])
        self.assertIn("Nerosuferoth", deduction["allowed_enemies"])
        self.assertIn("Formula", deduction["allowed_enemies"])

    def test_certain_partners_are_in_every_matching_formation(self):
        # One Devil Ride: alone, 2x Devil Ride, or with 2 Kalm Fangs -> Kalm Fang is possible, not certain
        deduction = compendium.deduce_formation({"Devil Ride": 1})
        self.assertIn("Kalm Fang", [p["name"] for p in deduction["candidate_partners"]])
        self.assertEqual(deduction["certain_partners"], [])
        # Every formation with a Prowler also has Kalm Fangs
        deduction = compendium.deduce_formation({"Prowler": 1})
        self.assertEqual([p["name"] for p in deduction["certain_partners"]], ["Kalm Fang"])

    def test_real_encounters_are_compatible(self):
        # Pairs observed in recorded gameplay must exist in at least one formation,
        # otherwise the capture loop would treat the second name as a Battle Square round change.
        self.assertTrue(compendium.find_matching_formations({"Prowler": 1, "Kalm Fang": 2}))
        self.assertTrue(compendium.find_matching_formations({"Devil Ride": 2}))
        self.assertTrue(compendium.find_matching_formations({"Custom Sweeper": 2}))

    def test_punctuated_names_resolve_to_formations(self):
        # These enemies were stored without punctuation in formations.json before the data cleanup
        for name in ("Ho-chu", "Corneo's Lackey", "Schizo(right)", "Guardian(left)", "2-faced",
                     "Sample:h0512-opt", "Grangalan Jr.", "Lifeform-hojo N", "Scissors(upper)", "Ying/yang"):
            self.assertTrue(compendium.find_matching_formations({name: 1}), name)

    def test_incompatible_enemies_have_no_formation(self):
        self.assertFalse(compendium.find_matching_formations({"Sea Worm": 1, "Dragon": 1}))

    def test_partners(self):
        mp_partners = compendium.get_partners("mp")
        self.assertIsInstance(mp_partners, list)
        self.assertIn("guard-hound", [p["id"] for p in mp_partners])
        narrowed = compendium.get_partners("prowler", current_observed={"Prowler": 1})
        self.assertIn("Kalm Fang", [p["name"] for p in narrowed])

    def test_partners_ignore_a_battle_without_the_enemy(self):
        # GEMINI-20261009-02: the partners of the queried enemy, never those of another battle
        names = lambda partners: [p["name"] for p in partners]
        battle = {"Devil Ride": 1}
        self.assertEqual(names(compendium.get_partners("devil-ride", current_observed=battle)), ["Kalm Fang"])
        for enemy_id in ("guard-hound", "kalm-fang", "sephiroth"):
            self.assertEqual(compendium.get_partners(enemy_id, current_observed=battle),
                             compendium.get_partners(enemy_id), enemy_id)
        self.assertNotIn("Kalm Fang", names(compendium.get_partners("kalm-fang", current_observed=battle)))
        # The battle may name its enemies by id
        self.assertEqual(names(compendium.get_partners("devil-ride", current_observed={"devil-ride": 1})),
                         ["Kalm Fang"])


class TestDeductionGuards(unittest.TestCase):
    def test_matching_formations_name_their_other_enemies(self):
        # auto-add relies on certain_partners. Two Mus match one formation (with a Levrikon);
        # two Prowlers match several formations, all with a Kalm Fang
        for observed, partner in (({"Mu": 2}, "Levrikon"), ({"Prowler": 2}, "Kalm Fang")):
            with self.subTest(observed=observed):
                deduction = compendium.deduce_formation(observed)
                self.assertTrue(deduction["is_disambiguated"])
                self.assertEqual([p["name"] for p in deduction["certain_partners"]], [partner])
        self.assertEqual(len(compendium.find_matching_formations({"Mu": 2})), 1)

    def test_formation_is_not_known_while_several_match(self):
        devil_ride = {**compendium.get_by_name("Devil Ride"), "count": 1}
        self.assertFalse(compendium.battle_summary([devil_ride])["formation_known"])
        fang = {**compendium.get_by_name("Kalm Fang"), "count": 2}
        self.assertTrue(compendium.battle_summary([devil_ride, fang])["formation_known"])


class TestStealFormula(unittest.TestCase):
    """Reference values from the Final Fantasy Wiki 'Steal (Final Fantasy VII ability)' mechanics section."""

    def test_wiki_tonberry_example(self):
        res = compendium.calculate_steal_rate(50, 25, 8)
        self.assertEqual(res["level_difference"], 65)
        self.assertEqual(res["level_factor"], 332)
        self.assertEqual(res["chance_value"], 10)
        self.assertAlmostEqual(res["total_success_percentage"], 17.19, places=2)

    def test_wiki_bagrisk_example(self):
        soft = compendium.calculate_steal_rate(50, 19, 32)
        self.assertEqual(soft["level_factor"], 363)
        self.assertEqual(soft["chance_value"], 45)
        self.assertAlmostEqual(soft["total_success_percentage"], 71.88, places=2)
        claw = compendium.calculate_steal_rate(50, 19, 8)
        self.assertEqual(claw["chance_value"], 11)
        self.assertAlmostEqual(claw["total_success_percentage"], 18.75, places=2)

    def test_level_difference_is_capped(self):
        high = compendium.calculate_steal_rate(99, 1, 8)
        self.assertEqual(high["level_difference"], 100)
        self.assertAlmostEqual(high["total_success_percentage"], 26.56, places=2)

    def test_sneak_glove_sets_level_difference_to_100(self):
        base = compendium.calculate_steal_rate(15, 14, 8)
        glove = compendium.calculate_steal_rate(15, 14, 8, sneak_glove=True)
        self.assertEqual(glove["level_difference"], 100)
        self.assertGreater(glove["total_success_percentage"], base["total_success_percentage"])
        self.assertAlmostEqual(glove["total_success_percentage"], 26.56, places=2)

    def test_guaranteed_and_minimum(self):
        self.assertEqual(compendium.calculate_steal_rate(99, 1, 63)["total_success_percentage"], 100.0)
        # 40+ levels below the target: LVdiff clamps at 0, only the Rnd(0..63) <= 0 roll remains
        self.assertAlmostEqual(compendium.calculate_steal_rate(1, 99, 63)["total_success_percentage"], 1.56, places=2)

    def test_steal_rate_immutability(self):
        res1 = compendium.calculate_steal_rate(50, 20, 32, False)
        res1["mutated_field"] = 999
        res2 = compendium.calculate_steal_rate(50, 20, 32, False)
        self.assertNotIn("mutated_field", res2)

    def test_steal_label(self):
        self.assertEqual(EnemyCompendium.steal_rate_label(32), "Value 32 (40.6% at equal level)")
        self.assertEqual(EnemyCompendium.steal_rate_label(8), "Value 8 (10.9% at equal level)")

    def test_party_steal_odds(self):
        sweeper = compendium.get_by_name("Custom Sweeper")
        expected = compendium.calculate_steal_rate(20, sweeper["level"], sweeper["steals"][0]["base_chance"], True)
        self.assertEqual(EnemyCompendium.party_steal_odds(sweeper, 20, True), expected["total_success_percentage"])
        no_steal = next(e for e in compendium.get_all() if not e["steals"])
        self.assertIsNone(EnemyCompendium.party_steal_odds(no_steal, 20, False))
        self.assertEqual(EnemyCompendium.party_steal_item_odds(no_steal, 20, False), [])

    def test_steal_slots_are_tried_in_order(self):
        # Data audit finding 2: Mighty Grunt has two steal slots (Tranquilizer 32, Grenade 8)
        grunt = compendium.get_by_name("Mighty Grunt")
        self.assertEqual([(s["item"], s["base_chance"]) for s in grunt["steals"]], [("Tranquilizer", 32), ("Grenade", 8)])
        first = compendium.calculate_steal_rate(12, 12, 32)["total_success_percentage"] / 100
        second = compendium.calculate_steal_rate(12, 12, 8)["total_success_percentage"] / 100
        odds = EnemyCompendium.party_steal_item_odds(grunt, 12, False)
        self.assertEqual(odds, [round(100 * first, 2), round(100 * (1 - first) * second, 2)])
        self.assertEqual(EnemyCompendium.party_steal_odds(grunt, 12, False), round(sum(odds), 2))
        self.assertEqual(EnemyCompendium.steal_item_chance(12, 12, [32, 8], 0), odds[0])


def _enemy(name, **fields):
    """Synthetic enemy (no formations) for the element / status logic of battle_summary."""
    base = {"name": name, "level": 10, "exp": 10, "ap": 2, "gil": 5, "weaknesses": [], "absorbs": [],
            "elemental_immunities": [], "status_immunities": [], "enemy_skills": []}
    base.update(fields)
    return base


class TestBattleSummary(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(compendium.battle_summary([]))

    def test_elements_and_statuses(self):
        summary = compendium.battle_summary([
            _enemy("A", weaknesses=["Ice", "Fire (Instant KO)", "Wind"], status_immunities=["Death"]),
            _enemy("B", weaknesses=["Ice", "Fire"], absorbs=["Wind"], status_immunities=["Sleep"], count=3),
            _enemy("C", weaknesses=["Ice", "Fire"], elemental_immunities=["Earth"]),
        ])
        self.assertEqual(summary["weak_all"], ["Fire", "Ice"])  # game element order, "(Instant KO)" folded
        self.assertEqual(summary["weak_some"], [], "Wind is weak for A but absorbed by B")
        self.assertEqual(summary["avoid"], [
            {"element": "Earth", "absorbed_by": [], "nullified_by": ["C"], "full_cure_by": []},
            {"element": "Wind", "absorbed_by": ["B"], "nullified_by": [], "full_cure_by": []},
        ])
        self.assertEqual(summary["healing_damages"], [])
        self.assertNotIn("Death", summary["status_all"])
        self.assertNotIn("Sleep", summary["status_all"])
        self.assertIn("Poison", summary["status_all"])
        # Unknown formation: totals of the enemies seen so far, with their counts
        self.assertFalse(summary["formation_known"])
        self.assertIsNone(summary["formation"])
        self.assertEqual(summary["totals"], {"exp": 50, "ap": 10, "gil": 25})
        self.assertIsNone(summary["cannot_escape"])

    def test_full_cure_and_healing_damages(self):
        summary = compendium.battle_summary([
            _enemy("A", weaknesses=["Fire"], healing_damages=True, display_name="A-Game"),
            _enemy("B", weaknesses=["Fire"], full_cure=["Water"]),
        ])
        self.assertEqual(summary["avoid"], [{"element": "Water", "absorbed_by": [], "nullified_by": [],
                                             "full_cure_by": ["B"]}])
        self.assertEqual(summary["healing_damages"], ["A-Game"])

    def test_weak_some_lists_safe_elements(self):
        summary = compendium.battle_summary([_enemy("A", weaknesses=["Holy"]), _enemy("B")])
        self.assertEqual(summary["weak_all"], [])
        self.assertEqual(summary["weak_some"], ["Holy"])

    def test_enemy_skills_are_merged_in_materia_order(self):
        summary = compendium.battle_summary([
            _enemy("A", enemy_skills=[{"name": "Laser", "manipulate": False}]),
            _enemy("B", enemy_skills=[{"name": "Laser", "manipulate": True},
                                      {"name": "Frog Song", "manipulate": False}]),
        ])
        self.assertEqual(summary["enemy_skills"], [
            {"name": "Frog Song", "sources": ["B"], "manipulate": False},
            {"name": "Laser", "sources": ["A", "B"], "manipulate": True},
        ])

    def test_known_boss_formation(self):
        summary = compendium.battle_summary([dict(compendium.get_by_name("Guard Scorpion"))])
        self.assertTrue(summary["formation_known"])
        self.assertTrue(summary["cannot_escape"])
        self.assertEqual(summary["formation"], {"Guard Scorpion": 1})
        self.assertIn("Lightning", summary["weak_all"])

    def test_formation_totals_include_unseen_partners(self):
        # Every formation with a Custom Sweeper has the same members, so the whole formation is known
        sweeper = dict(compendium.get_by_name("Custom Sweeper"))
        summary = compendium.battle_summary([sweeper])
        self.assertTrue(summary["formation_known"])
        self.assertFalse(summary["cannot_escape"])
        count = summary["formation"]["Custom Sweeper"]
        self.assertEqual(summary["totals"]["exp"], sweeper["exp"] * count)
        self.assertEqual(summary["enemy_skills"][0]["name"], "Matra Magic")

    def test_mixed_escape_flags_are_unknown(self):
        # Bottomswell has escapable and non-escapable formation slots in scene 120
        summary = compendium.battle_summary([dict(compendium.get_by_name("Bottomswell"))])
        self.assertIsNone(summary["cannot_escape"])

    def test_enemy_skill_sources(self):
        skills = compendium.enemy_skill_sources()
        self.assertEqual(len(skills), 24)
        self.assertTrue(all(s["sources"] for s in skills))

    def test_debug_formation_is_ignored(self):
        # Scene 6 formation 4 (6 x Adamantaimai) is a debug formation: the real battle is alone
        self.assertTrue(compendium.find_matching_formations({"Adamantaimai": 1}))
        self.assertEqual(compendium.find_matching_formations({"Adamantaimai": 6}), [])
        summary = compendium.battle_summary([dict(compendium.get_by_name("Adamantaimai"))])
        self.assertTrue(summary["formation_known"])


def _battle(*names, party_level=None):
    """Variant-resolved battle from (name, count) pairs, as server.main.battle_data builds it."""
    enemies = [dict(compendium.get_by_name(n), count=c) for n, c in names]
    return compendium.resolve_variants(enemies, party_level=party_level)


class TestVariants(unittest.TestCase):
    """Data audit finding 1: one name, several scene.bin records (bosses fought several times, arena tiers)."""

    def test_formation_picks_the_variant(self):
        rude = _battle(("Turks:rude", 1), ("Attack Squad", 2))[0]
        self.assertEqual((rude["level"], rude["hp"], rude["exp"]), (42, 9000, 3400))
        self.assertTrue(rude["variant"]["resolved"])
        self.assertEqual(rude["variant"]["scenes"], [204])
        self.assertNotIn("variants", rude)
        trio = {e["name"]: e for e in _battle(("Turks:rude", 1), ("Turks:reno", 1), ("Turks:elena", 1))}
        self.assertEqual((trio["Turks:rude"]["level"], trio["Turks:rude"]["hp"]), (51, 28000))
        self.assertEqual((trio["Turks:reno"]["level"], trio["Turks:reno"]["hp"]), (50, 25000))

    def test_formation_slot_picks_the_variant_within_a_scene(self):
        ninja = _battle(("Mystery Ninja", 2))[0]  # only scene 74 has two
        self.assertEqual((ninja["level"], ninja["variant"]["enemy_ids"]), (42, [358]))
        horn = _battle(("Cmd.grand Horn", 2))[0]  # Battle Arena: only the top tier comes in pairs
        self.assertEqual((horn["level"], horn["hp"]), (37, 8000))
        chocobo = _battle(("Chocobo", 1), ("Mandragora", 2))[0]
        self.assertEqual(chocobo["level"], 13)

    def test_ambiguous_battle_uses_party_level_and_lists_candidates(self):
        early = _battle(("Turks:rude", 1), party_level=25)[0]
        self.assertEqual(early["level"], 23)
        self.assertFalse(early["variant"]["resolved"])
        self.assertEqual(len(early["variant"]["candidates"]), 4)
        late = _battle(("Turks:rude", 1), party_level=53)[0]
        self.assertEqual(late["level"], 51)
        # Ultimate Weapon: every battle is him alone, so the final fight stays one of four candidates
        weapon = _battle(("Ultimate Weapon", 1), party_level=60)[0]
        exps = sorted(c["exp"] for c in weapon["variant"]["candidates"])
        self.assertEqual(exps, [0, 0, 0, 35000])

    def test_single_record_enemies_are_unchanged(self):
        guard = _battle(("Guard Scorpion", 1))[0]
        self.assertNotIn("variant", guard)
        self.assertEqual(guard["hp"], compendium.get_by_name("Guard Scorpion")["hp"])

    def test_resolution_keeps_battle_fields_and_compendium(self):
        before = dict(compendium.get_by_name("Turks:rude"))
        rude = compendium.resolve_variants([dict(before, count=1, instance_letter=None)], party_level=50)[0]
        self.assertEqual(rude["count"], 1)
        self.assertIn("instance_letter", rude)
        self.assertEqual(compendium.get_by_name("Turks:rude"), before)

    def test_formation_totals_use_each_instance(self):
        # Flower Prong x3 is three different records (240 / 220 / 200 EXP)
        summary = compendium.battle_summary(_battle(("Flower Prong", 3)))
        self.assertTrue(summary["formation_known"])
        self.assertEqual(summary["totals"], {"exp": 660, "ap": 66, "gil": 1050})

    def test_variant_enemy_skills_count_as_sources(self):
        sources = {s["name"]: {x["name"] for x in s["sources"]} for s in compendium.enemy_skill_sources()}
        for e in compendium.get_all():
            for variant in e.get("variants") or []:
                for skill in variant["enemy_skills"]:
                    self.assertIn(e["display_name"], sources[skill["name"]])


class TestDisplayNames(unittest.TestCase):
    """Data audit finding 5: names as the game writes them, matching unchanged."""

    def test_display_names(self):
        self.assertEqual(compendium.get_by_name("Jenovalife")["display_name"], "Jenova\u00b7LIFE")
        self.assertEqual(compendium.get_by_name("Cmd.grand Horn")["display_name"], "CMD.Grand Horn")
        self.assertEqual(compendium.get_by_name("Hell Rider Vr2")["display_name"], "Hell Rider VR2")
        self.assertEqual(compendium.get_by_name("Safersephiroth")["display_name"], "Safer\u00b7Sephiroth")

    def test_game_spelling_matches(self):
        for query, name in (("Jenova\u00b7LIFE", "Jenovalife"), ("JENOVA\u00b7LIFE", "Jenovalife"),
                            ("Safer\u00b7Sephiroth", "Safersephiroth"), ("CMD.Grand Horn", "Cmd.grand Horn"),
                            ("Grosspanzer\u00b7Mobile", "Grosspanzermobile"), ("Turks:Rude", "Turks:rude")):
            self.assertEqual(compendium.match_enemy(query)["name"], name, query)
        self.assertEqual(compendium.get_by_name("Jenova\u00b7LIFE")["name"], "Jenovalife")

    def test_summary_uses_display_names(self):
        summary = compendium.battle_summary(_battle(("Turks:rude", 1), ("Attack Squad", 2)))
        self.assertEqual(summary["formation"], {"Attack Squad": 2, "Turks:Rude": 1})


if __name__ == "__main__":
    unittest.main()
