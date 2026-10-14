"""Structural checks on the compendium data files (src/ffvii_companion/data/enemies.json, formations.json,
ai_scripts.json)."""
import json
import re
import unittest
from collections import Counter

from ffvii_companion.compendium import (AI_SCRIPTS_PATH, JSON_PATH, FORMATIONS_PATH, ELEMENT_NAMES, ENEMY_SKILLS,
                                        FORMATION_LAYOUTS, VARIANT_FIELDS, EnemyCompendium, name_key)

RATE_PERCENT = re.compile(r"^\d{1,3}(\.\d{1,2})?%$")
ELEMENTS = set(ELEMENT_NAMES)
STATUSES = {"Berserk", "Confusion", "Darkness", "Death", "Doom", "Frog", "Fury", "Manipulate", "Paralysis",
            "Petrify", "Poison", "Sadness", "Silence", "Sleep", "Slow", "Slow-numb", "Small", "Stop"}
STAT_KEYS = {"strength", "defense", "magic", "magic_defense", "dexterity", "luck", "evade"}


def records(enemy):
    """The entry itself and each of its scene.bin variants (the variant fields of both)."""
    return [enemy] + list(enemy.get("variants") or [])


class TestEnemyData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            cls.enemies = json.load(f)
        cls.by_name = {e["name"]: e for e in cls.enemies}

    def test_unique_ids_and_names(self):
        ids = Counter(e["id"] for e in self.enemies)
        names = Counter(e["name"].lower() for e in self.enemies)
        self.assertFalse([k for k, v in ids.items() if v > 1])
        self.assertFalse([k for k, v in names.items() if v > 1])

    def test_ids_are_url_path_safe(self):
        # GEMINI-20261007-08: ids go into /api/enemies/{id}; "/" would never route
        for e in self.enemies:
            self.assertTrue(e["id"])
            self.assertNotRegex(e["id"], r"[/?#%\\\s]", e["name"])

    def test_display_names(self):
        # The game's spelling joins to the same entry as the matching name
        displays = Counter(e["display_name"].lower() for e in self.enemies)
        self.assertFalse([k for k, v in displays.items() if v > 1])
        for e in self.enemies:
            self.assertEqual(name_key(e["display_name"]), name_key(e["name"]), e["name"])

    def test_required_fields_and_types(self):
        for e in self.enemies:
            with self.subTest(enemy=e.get("name")):
                for key in ("id", "name", "display_name"):
                    self.assertIsInstance(e[key], str)
                self.assertIsInstance(e["zones"], list)
                for rec in records(e):
                    self.assertEqual(set(VARIANT_FIELDS) - set(rec), set())
                    for key in ("level", "hp", "mp", "exp", "gil"):
                        self.assertIsInstance(rec[key], int)
                    self.assertTrue(1 <= rec["level"] <= 99)
                    self.assertGreater(rec["hp"], 0)
                    for key in ("weaknesses", "resistances", "absorbs", "elemental_immunities", "full_cure",
                                "status_immunities", "steals", "drops"):
                        self.assertIsInstance(rec[key], list)
                    self.assertIsInstance(rec["healing_damages"], bool)

    def test_vocabularies(self):
        for e in self.enemies:
            for rec in records(e):
                for key in ("weaknesses", "resistances", "absorbs", "elemental_immunities", "full_cure"):
                    for value in rec[key]:
                        self.assertIn(value.replace(" (Instant KO)", ""), ELEMENTS, f"{e['name']} {key}: {value}")
                for value in rec["status_immunities"]:
                    self.assertIn(value, STATUSES, f"{e['name']}: {value}")

    def test_restorative_absorb_is_healing_damages(self):
        # Data audit finding 3: absorbing Restorative means healing hurts (undead), not "absorbs"
        for e in self.enemies:
            for rec in records(e):
                self.assertNotIn("Restorative", rec["absorbs"], e["name"])
        undead = sorted(e["name"] for e in self.enemies if e["healing_damages"])
        self.assertIn("Ghost", undead)
        self.assertIn("Dragon Zombie", undead)
        self.assertEqual(self.by_name["Brain Pod"]["absorbs"], ["Poison"])

    def test_steal_entries(self):
        for e in self.enemies:
            for rec in records(e):
                for steal in rec["steals"]:
                    with self.subTest(enemy=e["name"], item=steal["item"]):
                        self.assertTrue(steal["item"])
                        self.assertTrue(0 <= steal["base_chance"] <= 127)
                        self.assertEqual(steal["rate"], EnemyCompendium.steal_rate_label(steal["base_chance"]))
                        self.assertIsInstance(steal["is_key_steal"], bool)
                        self.assertEqual(steal["is_key_steal"], steal["note"] is not None)

    def test_all_steal_slots_and_key_steals(self):
        # Data audit findings 2 and 6
        self.assertEqual([s["item"] for s in self.by_name["Mighty Grunt"]["steals"]], ["Tranquilizer", "Grenade"])
        self.assertEqual([s["item"] for s in self.by_name["Malboro"]["steals"]], ["M-Tentacles", "Echo Screen"])
        weapon_steals = [s for v in self.by_name["Ultimate Weapon"]["variants"] for s in v["steals"]]
        curse = next(s for s in weapon_steals if s["item"] == "Curse Ring")
        self.assertTrue(curse["is_key_steal"])
        self.assertTrue(self.by_name["SOLDIER:3rd"]["steals"][0]["is_key_steal"])

    def test_drop_rates(self):
        for e in self.enemies:
            for rec in records(e):
                for drop in rec["drops"]:
                    self.assertRegex(drop["rate"], RATE_PERCENT, e["name"])

    def test_scene_bin_fields(self):
        for e in self.enemies:
            with self.subTest(enemy=e["name"]):
                for rec in records(e):
                    self.assertIsInstance(rec["ap"], int)
                    self.assertTrue(0 <= rec["ap"] <= 65535)
                    self.assertEqual(set(rec["stats"]), STAT_KEYS)
                    for value in rec["stats"].values():
                        self.assertTrue(0 <= value <= 255)
                    self.assertTrue(all(isinstance(m, str) and m for m in rec["manipulate"]))
                    names = [s["name"] for s in rec["enemy_skills"]]
                    self.assertEqual(len(names), len(set(names)))
                    for skill in rec["enemy_skills"]:
                        self.assertIn(skill["name"], ENEMY_SKILLS)
                        self.assertIsInstance(skill["manipulate"], bool)

    def test_variants(self):
        seen_ids = set()
        zones_ok = True
        for e in self.enemies:
            variants = e.get("variants")
            if variants is None:
                continue
            with self.subTest(enemy=e["name"]):
                self.assertGreaterEqual(len(variants), 2)
                # The top level is a copy of the first variant
                self.assertEqual({k: e[k] for k in VARIANT_FIELDS}, {k: variants[0][k] for k in VARIANT_FIELDS})
                fields = [json.dumps([v[k] for k in VARIANT_FIELDS]) for v in variants]
                self.assertEqual(len(fields), len(set(fields)), "variants must differ")
                for v in variants:
                    self.assertTrue(v["enemy_ids"] and v["scenes"])
                    self.assertFalse(seen_ids & set(v["enemy_ids"]))
                    seen_ids.update(v["enemy_ids"])
                    zones_ok &= all(f"Scene #{s}" in e["zones"] for s in v["scenes"])
        self.assertTrue(zones_ok)
        self.assertEqual(sum(1 for e in self.enemies if e.get("variants")), 16)

    def test_every_enemy_skill_has_a_source(self):
        learnable = {s["name"] for e in self.enemies for rec in records(e) for s in rec["enemy_skills"]}
        self.assertEqual(learnable, set(ENEMY_SKILLS))

    def test_known_scene_bin_values(self):
        by_name = self.by_name
        self.assertEqual(by_name["Death Claw"]["enemy_skills"], [{"name": "Laser", "manipulate": True}])
        self.assertEqual(by_name["Beachplug"]["enemy_skills"], [{"name": "Big Guard", "manipulate": True}])
        self.assertIn("Laser", by_name["Death Claw"]["manipulate"])
        self.assertEqual(by_name["Mighty Grunt"]["ap"], 5)
        self.assertEqual(by_name["Ruby Weapon"]["ap"], 50000)
        self.assertEqual(by_name["Jenovalife"]["display_name"], "Jenova·LIFE")

    def test_known_variants(self):
        # Data audit finding 1: one record per scene.bin variant instead of a mix of several
        rude = {tuple(v["scenes"]): (v["level"], v["hp"], v["exp"]) for v in self.by_name["Turks:rude"]["variants"]}
        self.assertEqual(rude, {(134,): (23, 2000, 720), (201,): (49, 20000, 5500), (204,): (42, 9000, 3400),
                                (210,): (51, 28000, 5500)})
        final = next(v for v in self.by_name["Ultimate Weapon"]["variants"] if v["exp"])
        self.assertEqual((final["exp"], final["ap"], final["gil"]), (35000, 3500, 25000))
        self.assertEqual(set(final["elemental_immunities"]), {"Earth", "Gravity", "Water"})

    def test_yuffie_solo_notes_only_on_pagoda_bosses(self):
        # GEMINI-20261012-01: the Rapps note said Yuffie fights alone; Cloud's party fights him without Materia
        solo = {e["id"] for e in self.enemies if "Yuffie fights alone" in (e.get("notes") or "")}
        self.assertEqual(solo, {"gorkii", "shake", "chekhov", "staniv", "godo"})
        self.assertIn("Materia", self.by_name["Rapps"]["notes"])

    def test_boss_notes_avoid_corrected_claims(self):
        # Claims checked against the decoded AI scripts and attack data in scene.bin. The checks
        # look for the claim, not the old wording (CLAUDE-20261012-B07: rewordings of the old
        # claims passed).
        import re
        notes = {name: self.by_name[name]["notes"] for name in
                 ("Proud Clod", "Air Buster", "Lost Number", "Jenovasynthesis", "Motor Ball")}
        # GEMINI-20261012-02: a Ribbon does not stop Materia Jammer's Reflect
        for sentence in re.split(r"(?<=[.;])\s+", notes["Proud Clod"]):
            if "Ribbon" in sentence:
                self.assertIn("not", sentence, sentence)
        # GEMINI-20261012-04: Big Bomber hits one member, not the party
        self.assertIn("Big Bomber at one member", notes["Air Buster"])
        self.assertNotRegex(notes["Air Buster"], r"Big Bomber[^.]*\b(party|all|everyone)\b")
        # GEMINI-20261012-03: the hit that takes it below half HP picks the form
        self.assertIn("magic leaves the red magic form", notes["Lost Number"])
        self.assertIn("physical leaves the purple physical form", notes["Lost Number"])
        # CLAUDE-20261012-B05: its attacks inflict Fury, Sadness, Poison and Stop, never Silence
        self.assertNotIn("Silence", notes["Jenovasynthesis"])
        for status in ("Fury", "Sadness", "Stop"):
            self.assertIn(status, notes["Jenovasynthesis"])
        # CLAUDE-20261012-B10: Arm Attack comes back in every cycle, not only at the start
        self.assertNotIn("Starts with Arm Attack", notes["Motor Ball"])
        self.assertIn("cycle", notes["Motor Ball"])


class TestAiScripts(unittest.TestCase):
    """ai_scripts.json (generated by tools/ai_script.py --export) and the hand-written ai_summary."""
    TRIGGERS = {"Init", "Main", "Counter-General", "Counter-Death", "Counter-Physical", "Counter-Magic",
                "Battle End", "Pre-Action"} | {f"Custom {n}" for n in range(1, 9)}

    @classmethod
    def setUpClass(cls):
        with open(AI_SCRIPTS_PATH, "r", encoding="utf-8") as f:
            cls.ai = json.load(f)
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            cls.enemies = {e["id"]: e for e in json.load(f)}
        with open(FORMATIONS_PATH, "r", encoding="utf-8") as f:
            cls.entry_of_id = {i: name for form in json.load(f) for name, ids in form["enemy_ids"].items()
                               for i in ids}

    def test_entries_and_enemy_ids(self):
        self.assertEqual(set(self.ai) - set(self.enemies), set())
        names = {self.enemies[k]["name"] for k in set(self.enemies) - set(self.ai)}
        self.assertEqual(names, {"Manhole(lid)", "Ruby's Tentacle"})  # no AI scripts in scene.bin
        owner = {}
        for key, recs in self.ai.items():
            self.assertTrue(recs, key)
            for rec in recs:
                self.assertTrue(rec["enemy_ids"] and rec["scenes"], key)
                for enemy_id in rec["enemy_ids"]:
                    self.assertEqual(owner.setdefault(enemy_id, key), key, f"id {enemy_id} in two entries")
                    if enemy_id in self.entry_of_id:
                        self.assertEqual(self.entry_of_id[enemy_id], self.enemies[key]["name"], enemy_id)

    def test_records(self):
        for key, recs in self.ai.items():
            for rec in recs:
                with self.subTest(enemy=key, ids=rec["enemy_ids"]):
                    self.assertLessEqual(set(rec["reacts"]), {"any action", "physical", "magic", "death"})
                    listed = [json.dumps(a, sort_keys=True) for a in rec["attacks"]]
                    self.assertEqual(len(listed), len(set(listed)), "identical attacks are listed once")
                    for a in rec["attacks"]:
                        self.assertTrue(a["name"])
                        self.assertIn(a["target"], {"one", "all", "random", "-"})
                        self.assertLessEqual(set(a["elements"]), ELEMENTS)
                        self.assertIn(a.get("status_mode", "inflict"), {"inflict", "remove", "toggle"})
                        self.assertEqual("status_mode" in a, bool(a["statuses"]))
                        if a["enemy_skill"]:
                            self.assertIn(a["name"], ENEMY_SKILLS)
                    for script in rec["scripts"]:
                        self.assertIn(script["trigger"], self.TRIGGERS)
                        self.assertTrue(script["lines"])
                        self.assertTrue(all(isinstance(line, str) for line in script["lines"]))

    def test_known_record(self):
        rec, = self.ai["guard-scorpion"]
        self.assertEqual(rec["enemy_ids"], [22])
        self.assertEqual(rec["reacts"], ["any action"])
        counter = next(s["lines"] for s in rec["scripts"] if s["trigger"] == "Counter-General")
        self.assertEqual(counter, ["if Var00E0 == 1:", "  Targets = Opponents", "  use Tail Laser"])

    def test_ai_summaries(self):
        # null, or sections {"title", "rows": [[when, does], ...], "enemy_ids" (optional)}
        for key, e in self.enemies.items():
            summary = e["ai_summary"]
            if summary is None:
                continue
            with self.subTest(enemy=e["name"]):
                self.assertIn(key, self.ai, "a summary needs an AI script to show it")
                ids = {i for rec in self.ai[key] for i in rec["enemy_ids"]}
                self.assertIsInstance(summary, list)
                self.assertTrue(summary)
                for section in summary:
                    self.assertLessEqual(set(section), {"title", "rows", "enemy_ids"})
                    self.assertIsInstance(section["title"], str)
                    self.assertTrue(section["rows"])
                    for row in section["rows"]:
                        self.assertEqual(len(row), 2)
                        self.assertTrue(all(isinstance(cell, str) and cell.strip() for cell in row))
                    if "enemy_ids" in section:
                        self.assertTrue(section["enemy_ids"])
                        self.assertLessEqual(set(section["enemy_ids"]), ids)


class TestFormationData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FORMATIONS_PATH, "r", encoding="utf-8") as f:
            cls.formations = json.load(f)
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            cls.enemies = {e["name"]: e for e in json.load(f)}

    def test_formation_names_match_enemies_exactly(self):
        unknown = sorted({n for form in self.formations for n in form["enemies"] if n not in self.enemies})
        self.assertEqual(unknown, [])
        for form in self.formations:
            self.assertTrue(form["enemies"])
            for count in form["enemies"].values():
                self.assertTrue(1 <= count <= 8)

    def test_formation_enemy_ids(self):
        # One scene.bin enemy id per instance; ids of enemies with variants belong to one of them
        for form in self.formations:
            with self.subTest(formation=(form["scene"], form["formation_index"])):
                self.assertEqual(set(form["enemy_ids"]), set(form["enemies"]))
                for name, ids in form["enemy_ids"].items():
                    self.assertEqual(len(ids), form["enemies"][name])
                    variants = self.enemies[name].get("variants")
                    if variants:
                        self.assertTrue(set(ids) <= {i for v in variants for i in v["enemy_ids"]})

    def test_formation_battle_flags(self):
        for form in self.formations:
            self.assertIsInstance(form["escapable"], bool)
            self.assertIn(form["layout"], FORMATION_LAYOUTS)
            self.assertTrue(1 <= form["formation_index"] <= 4)
        escapable = {(f["scene"], f["formation_index"]): f["escapable"] for f in self.formations}
        self.assertFalse(escapable[(81, 1)], "Guard Scorpion cannot be escaped")
        self.assertTrue(escapable[(5, 1)], "Mighty Grunt random encounter")

    def test_unused_formations(self):
        unused = [(f["scene"], f["formation_index"]) for f in self.formations if f.get("unused")]
        self.assertEqual(unused, [(6, 4)])


if __name__ == "__main__":
    unittest.main()
