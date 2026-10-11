"""Structural checks on the compendium data files (src/ffvii_companion/data/enemies.json, formations.json)."""
import json
import re
import unittest
from collections import Counter

from ffvii_companion.compendium import (JSON_PATH, FORMATIONS_PATH, ELEMENT_NAMES, ENEMY_SKILLS,
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
