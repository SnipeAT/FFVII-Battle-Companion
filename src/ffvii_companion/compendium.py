import json
import re
import functools
import logging
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from rapidfuzz import process, fuzz

from ffvii_companion.paths import PACKAGE_DIR

logger = logging.getLogger("ffvii-compendium")

DATA_DIR = PACKAGE_DIR / "data"
JSON_PATH = DATA_DIR / "enemies.json"
FORMATIONS_PATH = DATA_DIR / "formations.json"

RE_ROW_DESIGNATION = re.compile(r"\s*[\(\[]?(?:back|front)(?:\s*row)?[\)\]]?", re.IGNORECASE)
RE_TRAILING_INSTANCE_LETTER = re.compile(r"(?:\s+|(?<=[a-zA-Z]))[A-Ha-h]$")
RE_TRAILING_DIGIT = re.compile(r"\s+[0-9]$")
RE_NON_ALPHANUMERIC = re.compile(r"[^a-zA-Z0-9]+")
RE_NAME_KEY = re.compile(r"[^a-z0-9]")

# FFVII steal mechanics constants (see EnemyCompendium.calculate_steal_rate)
STEAL_LEVEL_BASE = 40
STEAL_LEVEL_DIFF_CAP = 100
STEAL_ROLLS = 64

# The 24 Enemy Skills in Enemy Skill materia order (battle attack IDs 0x48-0x5F in scene.bin).
ENEMY_SKILLS = (
    "Frog Song", "L4 Suicide", "Magic Hammer", "White Wind", "Big Guard", "Angel Whisper",
    "Dragon Force", "Death Force", "Flame Thrower", "Laser", "Matra Magic", "Bad Breath",
    "Beta", "Aqualung", "Trine", "Magic Breath", "????", "Goblin Punch", "Chocobuckle",
    "L5 Death", "Death Sentence", "Roulette", "Shadow Flare", "Pandora's Box",
)

# Formation layouts stored in formations.json (tools/enrich_compendium.py)
FORMATION_LAYOUTS = ("normal", "preemptive", "back_attack", "side_attack", "pincer")

# Elements by scene.bin element id 0-15 (also the sort order of the battle summary)
ELEMENT_NAMES = ("Fire", "Ice", "Lightning", "Earth", "Poison", "Gravity", "Water", "Wind", "Holy", "Restorative",
                 "Cut", "Hit", "Punch", "Shoot", "Shout", "Hidden")

# Enemy fields that can differ between the scene.bin records of one name. An entry with several
# distinct records has them in "variants" ({"enemy_ids", "scenes", <these fields>}) and a copy of
# the first variant at the top level (tools/enrich_compendium.py).
VARIANT_FIELDS = ("level", "hp", "mp", "exp", "ap", "gil", "stats", "weaknesses", "resistances", "absorbs",
                  "elemental_immunities", "full_cure", "healing_damages", "status_immunities", "steals", "drops",
                  "morph", "manipulate", "enemy_skills")

# Statuses listed in the battle summary when no enemy of the encounter is immune to them
# (the inflictable ailments a player actually aims at enemies).
SUMMARY_STATUSES = ("Death", "Sleep", "Poison", "Silence", "Confusion", "Berserk", "Stop", "Slow",
                    "Frog", "Small", "Petrify", "Paralysis", "Darkness", "Manipulate")
RE_INSTANT_KO = re.compile(r"\s*\(Instant KO\)$")


def name_key(name: str) -> str:
    """Punctuation/case-insensitive key used to join formation names to enemy names."""
    return RE_NAME_KEY.sub("", name.lower())


class EnemyCompendium:
    def __init__(self, json_path: Path = JSON_PATH, formations_path: Path = FORMATIONS_PATH):
        self.json_path = json_path
        self.formations_path = formations_path
        self._enemies_cache: Dict[str, Dict[str, Any]] = {}
        self._name_map: Dict[str, str] = {} # lower name -> id
        self._formations: List[Dict[str, Any]] = []

        self._init_db()

    def _init_db(self):
        # Load enemies directly from JSON for instant startup and zero OneDrive sync churn
        if self.json_path.exists():
            with open(self.json_path, "r", encoding="utf-8") as f:
                enemies = json.load(f)
                for enemy in enemies:
                    e_id = enemy["id"]
                    e_name = enemy["name"]
                    self._enemies_cache[e_id] = enemy
                    self._name_map[e_name.lower()] = e_id
                # The game's spelling ("Jenova·LIFE", "CMD.Grand Horn") finds the entry as well
                for enemy in enemies:
                    self._name_map.setdefault(enemy.get("display_name", enemy["name"]).lower(), enemy["id"])

        # Formations: enemy names are joined to the compendium through a punctuation-insensitive
        # key, so "Ho-chu" / "Hochu" or "Schizo(right)" / "Schizoright" refer to the same enemy.
        key_to_name = {name_key(e["name"]): e["name"] for e in self._enemies_cache.values()}
        if self.formations_path.exists():
            with open(self.formations_path, "r", encoding="utf-8") as f:
                raw_formations = json.load(f)
            unresolved = set()
            for form in raw_formations:
                if form.get("unused"):
                    continue  # debug-scene formation (tools/enrich_compendium.py UNUSED_FORMATIONS)
                enemies: Dict[str, int] = {}
                enemy_ids: Dict[str, List[int]] = {}
                for fname, count in form.get("enemies", {}).items():
                    canonical = key_to_name.get(name_key(fname))
                    if canonical is None:
                        unresolved.add(fname)
                        continue
                    enemies[canonical] = enemies.get(canonical, 0) + int(count)
                    enemy_ids.setdefault(canonical, []).extend((form.get("enemy_ids") or {}).get(fname, []))
                if enemies:
                    self._formations.append({**form, "enemies": enemies, "enemy_ids": enemy_ids})
            if unresolved:
                logger.warning(f"Formations reference unknown enemies (ignored): {sorted(unresolved)}")

        # Precompute normalized choices for O(1) exact and rapid fuzzy matching
        self._normalized_name_map: Dict[str, str] = {
            self._normalize_text(name, strip_suffix=False): eid for name, eid in self._name_map.items()
        }

        # Aliases for OCR variants, punctuation-stripped names, and boss phases
        ALIASES = {
            "lifeform hojo na": "lifeform-hojo-n",
            "lifeform hojo": "lifeform-hojo-n",
            "lifeformhojo na": "lifeform-hojo-n",
            "lifeformhojo": "lifeform-hojo-n",
            "hojo na": "lifeform-hojo-n",
            "bizarro sephiroth": "bizarrosephiroth",
            "safer sephiroth": "safersephiroth",
            "jenova birth": "jenovabirth",
            "jenova life": "jenovalife",
            "jenova death": "jenovadeath",
            "jenova synthesis": "jenovasynthesis",
            "soldier 3rd": "soldier-3rd",
            "soldier 2nd": "soldier-2nd",
            "soldier 1st": "soldier-1st",
            "1st ray": "1st-ray",
            "carry armor": "carry-armor",
            "right arm": "right-arm",
            "left arm": "left-arm",
            "schizo right": "schizo(right)",
            "schizo left": "schizo(left)"
        }
        for alias, target_id in ALIASES.items():
            if target_id in self._enemies_cache:
                self._normalized_name_map[alias] = target_id
            elif target_id.replace("-", "") in self._enemies_cache:
                self._normalized_name_map[alias] = target_id.replace("-", "")

        self._normalized_names_list: List[str] = list(self._normalized_name_map.keys())
        self._match_cache: OrderedDict[Tuple[str, float], Optional[Dict[str, Any]]] = OrderedDict()

        # scene.bin enemy id -> (entry id, variant index) for entries with several variants
        self._variant_by_enemy_id: Dict[int, Tuple[str, int]] = {}
        for e in self._enemies_cache.values():
            for idx, variant in enumerate(e.get("variants") or []):
                for enemy_id in variant["enemy_ids"]:
                    self._variant_by_enemy_id[enemy_id] = (e["id"], idx)

        # Build inverted index of formations by enemy name for O(1) candidate lookup
        self._formations_by_enemy: Dict[str, List[Dict[str, Any]]] = {}
        for form in self._formations:
            for ename in form.get("enemies", {}).keys():
                if ename not in self._formations_by_enemy:
                    self._formations_by_enemy[ename] = []
                self._formations_by_enemy[ename].append(form)

        # Precompute scene-level partners in O(N) using inverted zone-to-enemies index
        self._scene_partners_cache: Dict[str, List[Dict[str, Any]]] = {}
        zone_to_enemies: Dict[str, List[Dict[str, Any]]] = {}
        all_enemies = list(self._enemies_cache.values())
        for e in all_enemies:
            for z in e.get("zones", []):
                if z not in zone_to_enemies:
                    zone_to_enemies[z] = []
                zone_to_enemies[z].append(e)

        for e in all_enemies:
            e_id = e["id"]
            e_zones = e.get("zones", [])
            if not e_zones:
                self._scene_partners_cache[e_id] = []
                continue
            seen_ids = {e_id}
            partners = []
            for z in e_zones:
                for other in zone_to_enemies.get(z, []):
                    o_id = other["id"]
                    if o_id not in seen_ids:
                        seen_ids.add(o_id)
                        partners.append(other)
            self._scene_partners_cache[e_id] = partners

    def get_all(self) -> List[Dict[str, Any]]:
        return list(self._enemies_cache.values())

    def get_by_id(self, enemy_id: str) -> Optional[Dict[str, Any]]:
        return self._enemies_cache.get(enemy_id)

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        target = name.strip().lower()
        if target in self._name_map:
            return self._enemies_cache[self._name_map[target]]
        return None

    def get_by_normalized_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        The enemy whose name, in-game spelling or OCR alias equals `name` apart from case and
        punctuation: OCR reads the game's middle dot ("Grosspanzer·Big") as "-", ".", ":" or a
        space. Nothing is stripped (no row designation or instance letter, unlike match_enemy).
        """
        target_id = self._normalized_name_map.get(self._normalize_text(name, strip_suffix=False))
        return self._enemies_cache[target_id] if target_id else None

    def find_matching_formations(self, observed: Dict[str, int]) -> List[Dict[str, Any]]:
        """
        Filters all formations to those that contain all observed enemies with at least the observed counts.
        observed: dict of {enemy_name_or_id: min_count}, e.g. {'nerosuferoth': 2, 'formula': 1}
        """
        if not observed or not self._formations:
            return []

        # Normalize observed keys to canonical title-case enemy names
        norm_observed: Dict[str, int] = {}
        for k, count in observed.items():
            enemy = self.get_by_id(k) or self.get_by_name(k)
            if enemy:
                norm_observed[enemy["name"]] = max(count, norm_observed.get(enemy["name"], 0))

        if not norm_observed:
            return []

        # If any observed enemy appears in 0 formations across the entire game,
        # then no valid formation can possibly contain all observed enemies.
        for name in norm_observed.keys():
            if name not in self._formations_by_enemy:
                return []

        # Select the candidate list from the observed enemy that appears in the FEWEST formations
        # to minimize loop iterations to the absolute minimum
        candidate_lists = [self._formations_by_enemy[name] for name in norm_observed.keys()]
        candidates = min(candidate_lists, key=len) if candidate_lists else self._formations

        matches = []
        for form in candidates:
            form_enemies = form["enemies"]
            possible = True
            for obs_name, obs_count in norm_observed.items():
                if form_enemies.get(obs_name, 0) < obs_count:
                    possible = False
                    break
            if possible:
                matches.append(form)

        return matches

    def deduce_formation(self, observed: Dict[str, int]) -> Dict[str, Any]:
        """
        Deduces the exact or candidate formation based on observed enemies and their instance counts.
        Returns whether the formation is disambiguated, the allowed enemies, candidate_partners
        (unseen enemies of ANY matching formation: possible partners) and certain_partners
        (unseen enemies that EVERY matching formation contains: present for sure).
        """
        matches = self.find_matching_formations(observed)
        if not matches:
            return {
                "is_disambiguated": False,
                "matched_formation": None,
                "candidate_partners": [],
                "certain_partners": [],
                "allowed_enemies": set()
            }

        observed_names = set()
        for k in observed.keys():
            e = self.get_by_id(k) or self.get_by_name(k)
            if e:
                observed_names.add(e["name"])

        # Fast path for single matching formation
        if len(matches) == 1:
            all_allowed_names = set(matches[0]["enemies"].keys())
            partner_names = all_allowed_names - observed_names
            candidate_partners = [self.get_by_name(p) for p in sorted(partner_names) if self.get_by_name(p)]
            return {
                "is_disambiguated": True,
                "matched_formation": matches[0],
                "candidate_partners": candidate_partners,
                "certain_partners": list(candidate_partners),
                "allowed_enemies": all_allowed_names
            }

        first_keys = set(matches[0]["enemies"].keys())
        all_same_enemies = all(set(m["enemies"].keys()) == first_keys for m in matches)

        all_allowed_names = set()
        partner_names = set()
        for m in matches:
            for ename in m["enemies"].keys():
                all_allowed_names.add(ename)
                if ename not in observed_names:
                    partner_names.add(ename)

        candidate_partners = [self.get_by_name(p) for p in sorted(partner_names) if self.get_by_name(p)]
        in_every_match = set.intersection(*(set(m["enemies"].keys()) for m in matches))
        certain_partners = [p for p in candidate_partners if p["name"] in in_every_match]

        return {
            "is_disambiguated": all_same_enemies,
            "matched_formation": matches[0] if all_same_enemies else None,
            "candidate_partners": candidate_partners,
            "certain_partners": certain_partners,
            "allowed_enemies": all_allowed_names
        }

    def get_partners(self, enemy_id: str, current_observed: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        """
        Finds other enemies that spawn in the same battle formations.
        If current_observed is provided (e.g. {'nerosuferoth': 2, 'formula': 1}) and contains the
        enemy, narrows down partners strictly to formations compatible with observed combatants;
        a battle without the enemy says nothing about its partners and is ignored.
        """
        target = self.get_by_id(enemy_id)
        if not target:
            return []

        if current_observed and self._formations:
            observed_names = {(self.get_by_id(k) or self.get_by_name(k) or {}).get("name") for k in current_observed}
            if target["name"] in observed_names:
                return self.deduce_formation(current_observed)["candidate_partners"]

        # Fast O(1) fallback to precomputed scene-level partners
        return self._scene_partners_cache.get(enemy_id, [])

    @staticmethod
    def _normalize_text(text: str, strip_suffix: bool = True) -> str:
        """Lowercases and strips punctuation. With strip_suffix, also removes row
        designations and a trailing instance letter or digit (OCR queries only:
        canonical names are indexed with strip_suffix=False so that "Unknown 2" and
        "Unknown 3" stay distinct from "Unknown" and "Devil Ride" is not truncated)."""
        if not text:
            return ""
        cleaned = text
        if strip_suffix:
            cleaned = RE_ROW_DESIGNATION.sub("", cleaned).strip()
            without_letter = RE_TRAILING_INSTANCE_LETTER.sub("", cleaned).strip()
            if without_letter != cleaned:
                # "Unknown 2 A" -> "Unknown 2": the digit belongs to the name, keep it
                # (same rule as detector.parse_enemy_line).
                cleaned = without_letter
            else:
                cleaned = RE_TRAILING_DIGIT.sub("", cleaned).strip()
        cleaned = RE_NON_ALPHANUMERIC.sub(" ", cleaned).lower()
        return " ".join(cleaned.split())
    def match_enemy(self, query: str, score_cutoff: float = 80.0) -> Optional[Dict[str, Any]]:
        """
        Fuzzy match an OCR-recognized string to an enemy in the database.
        Cleans and normalizes common OCR imperfections (e.g. SOLDIER:3rd vs SOLDIER 3rd, 1st Ray vs 1stRay, etc.)
        Prevents false positives on short strings or command keywords.
        Utilizes bounded in-memory memoization to resolve repeated OCR candidate strings in O(1).
        """
        cache_key = (query.strip(), score_cutoff)
        if cache_key in self._match_cache:
            res = self._match_cache[cache_key]
            self._match_cache.move_to_end(cache_key)
            return res

        norm_query = self._normalize_text(query)
        # The unstripped form first: it is the exact name of enemies such as "Unknown 3"
        exact = self._normalize_text(RE_ROW_DESIGNATION.sub("", query).strip(), strip_suffix=False)
        if not norm_query or len(norm_query) < 2:
            res = None
        elif exact in self._normalized_name_map:
            # 1. Direct O(1) lookup of the canonical name (or alias)
            res = self._enemies_cache[self._normalized_name_map[exact]]
        elif norm_query in self._normalized_name_map:
            # 1b. Direct lookup with the instance letter / digit removed
            res = self._enemies_cache[self._normalized_name_map[norm_query]]
        elif len(norm_query) < 4:
            # For very short queries (< 4 chars), require exact match only
            res = None
        else:
            # 2. Fast fuzzy match over precomputed normalized names list
            result = process.extractOne(
                norm_query,
                self._normalized_names_list,
                scorer=fuzz.ratio
            )
            if result:
                matched_norm, score, _ = result
                # Only match if score meets cutoff and length disparity is minimal (prevents substring word matches)
                if score >= score_cutoff and abs(len(norm_query) - len(matched_norm)) <= 2:
                    matched_id = self._normalized_name_map[matched_norm]
                    res = self._enemies_cache[matched_id]
                else:
                    res = None
            else:
                res = None

        if len(self._match_cache) >= 1024:
            self._match_cache.popitem(last=False)
        self._match_cache[cache_key] = res

        return res

    def enemy_skill_sources(self) -> List[Dict[str, Any]]:
        """All Enemy Skills in materia order with the enemies that have them in their attack list."""
        sources: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ENEMY_SKILLS}
        for e in sorted(self._enemies_cache.values(), key=lambda x: x["name"].lower()):
            # Every variant counts: a boss may know a skill in one of its battles only
            via: Dict[str, bool] = {}
            for record in [e] + list(e.get("variants") or []):
                for skill in record.get("enemy_skills") or []:
                    via[skill["name"]] = via.get(skill["name"], False) or bool(skill.get("manipulate"))
            for name, manipulate in via.items():
                if name in sources:
                    sources[name].append({"id": e["id"], "name": e.get("display_name", e["name"]),
                                          "manipulate": manipulate})
        return [{"name": name, "sources": sources[name]} for name in ENEMY_SKILLS]

    def resolve_variants(self, enemies: List[Dict[str, Any]], party_level: Optional[int] = None
                         ) -> List[Dict[str, Any]]:
        """
        Copies of the battle's enemy dicts (optional "count") with the scene.bin variant of this
        battle applied. The variant is the one whose enemy ids appear in the formations matching
        the enemies on screen (a boss fought several times, a Battle Arena tier); when several
        remain (same enemies in different battles, or several forms in one formation), the one
        closest to party_level, then the first. Every entry with several variants gets
          variant = {"index", "count", "enemy_ids", "scenes", "resolved", "candidates"}
        where candidates summarizes the remaining variants when resolved is False. The
        "variants" list itself is left out of the copies.
        """
        if not enemies:
            return []
        observed = {e["name"]: max(1, int(e.get("count") or 1)) for e in enemies}
        formations = self.find_matching_formations(observed)
        resolved = []
        for e in enemies:
            out = dict(e)
            out.pop("variants", None)
            entry = self.get_by_id(e.get("id", "")) or self.get_by_name(e["name"]) or e
            variants = entry.get("variants") or []
            if len(variants) < 2:
                resolved.append(out)
                continue
            ids = {i for f in formations for i in f.get("enemy_ids", {}).get(entry["name"], [])}
            candidates = [i for i, v in enumerate(variants) if ids & set(v["enemy_ids"])] or list(range(len(variants)))
            pick = min(candidates, key=lambda i: (abs(variants[i]["level"] - int(party_level))
                                                  if party_level else 0, i))
            chosen = variants[pick]
            out.update({k: chosen[k] for k in VARIANT_FIELDS})
            out["variant"] = {
                "index": pick, "count": len(variants), "enemy_ids": chosen["enemy_ids"], "scenes": chosen["scenes"],
                "resolved": len(candidates) == 1,
                "candidates": [] if len(candidates) == 1 else [
                    {"index": i, "scenes": variants[i]["scenes"],
                     **{k: variants[i][k] for k in ("level", "hp", "exp", "ap", "gil")}} for i in candidates],
            }
            resolved.append(out)
        return resolved

    def _enemy_id_totals(self, enemy_ids: Dict[str, List[int]]) -> Optional[Dict[str, int]]:
        """EXP / AP / Gil of a formation from its scene.bin enemy ids, None when an id is unknown."""
        totals = {"exp": 0, "ap": 0, "gil": 0}
        for name, ids in enemy_ids.items():
            entry = self.get_by_name(name)
            if entry is None or not ids:
                return None
            for enemy_id in ids:
                record = entry
                if entry.get("variants"):
                    owner = self._variant_by_enemy_id.get(enemy_id)
                    if owner is None or owner[0] != entry["id"]:
                        return None
                    record = entry["variants"][owner[1]]
                for key in totals:
                    totals[key] += int(record.get(key) or 0)
        return totals

    def battle_summary(self, enemies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Encounter-wide tactics for the enemies on screen (dicts with an optional "count", already
        passed through resolve_variants); enemies are named by display_name:
          weak_all       elements every enemy is weak to
          weak_some      elements some enemies are weak to and no enemy absorbs, nullifies or is cured by
          avoid          [{"element", "absorbed_by", "nullified_by", "full_cure_by"}]: elements that heal
                         or miss someone
          status_all     ailments (SUMMARY_STATUSES) no enemy is immune to
          healing_damages enemies that healing magic and items damage (Restorative absorbed)
          enemy_skills   [{"name", "sources", "manipulate"}]: learnable Enemy Skills in this battle
          totals         EXP / AP / Gil for the whole formation once it is known (formation_known,
                         formation = {display name: count}; per instance from the formation's
                         scene.bin enemy ids when they are known), otherwise for the enemies seen so far
          cannot_escape  True / False when every matching formation agrees, else None
        Returns None without enemies.
        """
        if not enemies:
            return None

        def elements(enemy: Dict[str, Any], key: str) -> set:
            return {RE_INSTANT_KO.sub("", v) for v in enemy.get(key) or []}

        def label(enemy: Dict[str, Any]) -> str:
            return enemy.get("display_name") or enemy["name"]

        weak_sets = [elements(e, "weaknesses") for e in enemies]
        weak_all = set.intersection(*weak_sets)
        avoid: Dict[str, Dict[str, List[str]]] = {}
        for e in enemies:
            for key, field in (("absorbs", "absorbed_by"), ("elemental_immunities", "nullified_by"),
                               ("full_cure", "full_cure_by")):
                for element in sorted(elements(e, key)):
                    entry = avoid.setdefault(element, {"absorbed_by": [], "nullified_by": [], "full_cure_by": []})
                    entry[field].append(label(e))
        weak_some = set.union(*weak_sets) - weak_all - set(avoid)
        immune = set().union(*(set(e.get("status_immunities") or []) for e in enemies))

        skills: Dict[str, Dict[str, Any]] = {}
        for e in enemies:
            for skill in e.get("enemy_skills") or []:
                entry = skills.setdefault(skill["name"], {"name": skill["name"], "sources": [], "manipulate": False})
                entry["sources"].append(label(e))
                entry["manipulate"] = entry["manipulate"] or bool(skill.get("manipulate"))
        skill_order = {name: i for i, name in enumerate(ENEMY_SKILLS)}

        observed = {e["name"]: max(1, int(e.get("count") or 1)) for e in enemies}
        formations = self.find_matching_formations(observed)
        escape_flags = {f.get("escapable") for f in formations}
        cannot_escape = None
        if len(escape_flags) == 1 and None not in escape_flags:
            cannot_escape = not escape_flags.pop()

        # The whole formation is known when every matching formation has the same enemy counts
        member_counts = {tuple(sorted(f["enemies"].items())) for f in formations}
        formation_known = len(member_counts) == 1
        counts = dict(next(iter(member_counts))) if formation_known else observed
        totals = None
        member_ids = {tuple(sorted((n, tuple(sorted(i))) for n, i in f.get("enemy_ids", {}).items()))
                      for f in formations}
        if formation_known and len(member_ids) == 1:
            # Exact: each instance with its own variant (Mighty Grunt, Flower Prong forms)
            totals = self._enemy_id_totals(formations[0]["enemy_ids"])
        if totals is None:
            totals = {"exp": 0, "ap": 0, "gil": 0}
            for name, count in counts.items():
                # The battle's (variant-resolved) dict first, the compendium entry for unseen members
                enemy = next((e for e in enemies if e["name"] == name), None) or self.get_by_name(name)
                if enemy:
                    for key in totals:
                        totals[key] += int(enemy.get(key) or 0) * count
        labels = {e["name"]: label(e) for e in enemies}
        for name in counts:
            if name not in labels:
                entry = self.get_by_name(name)
                labels[name] = label(entry) if entry else name

        return {
            "weak_all": [x for x in ELEMENT_NAMES if x in weak_all],
            "weak_some": [x for x in ELEMENT_NAMES if x in weak_some],
            "avoid": [{"element": x, **avoid[x]} for x in ELEMENT_NAMES if x in avoid],
            "status_all": [s for s in SUMMARY_STATUSES if s not in immune],
            "healing_damages": [label(e) for e in enemies if e.get("healing_damages")],
            "enemy_skills": sorted(skills.values(), key=lambda s: skill_order.get(s["name"], len(skill_order))),
            "totals": totals,
            "formation_known": formation_known,
            "formation": {labels[n]: c for n, c in sorted(counts.items())} if formation_known else None,
            "cannot_escape": cannot_escape,
        }

    @classmethod
    def party_steal_odds(cls, enemy: Dict[str, Any], party_level: int, sneak_glove: bool) -> Optional[float]:
        """Chance (percent) that a Steal gets any item at the party's level, None when nothing can be stolen."""
        odds = cls.party_steal_item_odds(enemy, party_level, sneak_glove)
        if not odds:
            return None
        return round(sum(odds), 2)

    @classmethod
    def party_steal_item_odds(cls, enemy: Dict[str, Any], party_level: int, sneak_glove: bool) -> List[float]:
        """Chance (percent) that a Steal gets each item of enemy["steals"], in slot order."""
        bases = [int(s.get("base_chance", 0)) for s in enemy.get("steals") or []]
        return [cls.steal_item_chance(int(party_level), int(enemy.get("level") or 1), bases, i, bool(sneak_glove))
                for i in range(len(bases))]

    @classmethod
    def steal_item_chance(cls, stealer_level: int, target_level: int, base_chances: List[int], index: int,
                          sneak_glove: bool = False) -> float:
        """
        Chance (percent) that a Steal gets the item of slot `index`: the game tries the steal slots
        in order and stops at the first success, so every earlier slot has to fail first.
        """
        chance = 1.0
        for i, base in enumerate(base_chances[:index + 1]):
            p = cls._calc_steal_rate_cached(int(stealer_level), int(target_level), int(base),
                                            bool(sneak_glove))["total_success_percentage"] / 100.0
            chance *= p if i == index else 1.0 - p
        return round(100.0 * chance, 2)

    @classmethod
    def calculate_steal_rate(cls, stealer_level: int, target_level: int, base_chance: int, sneak_glove: bool = False) -> Dict[str, Any]:
        """
        Calculates the FFVII Steal success chance for one item slot (game formula, as
        documented in the Final Fantasy Wiki / Battle Mechanics FAQ):
          LVdiff   = min(100, 40 + Stealer_Level - Target_Level)   (Sneak Glove: LVdiff = 100)
          LVfactor = floor(512 * LVdiff / 100)
          Chance   = floor(Item_Steal_Value * LVfactor / 256)
          Success when Rnd(0..63) <= Chance  ->  P = (Chance + 1) / 64, capped at 100%
        base_chance is the item's raw steal value from the enemy data (e.g. 8, 32, 63).
        This is the chance of one slot on its own; with several steal slots the game tries
        them in order until one succeeds (steal_item_chance).
        Returns a shallow copy to protect the internal LRU cache against external mutation.
        """
        return cls._calc_steal_rate_cached(int(stealer_level), int(target_level), int(base_chance), bool(sneak_glove)).copy()

    @staticmethod
    @functools.lru_cache(maxsize=1024)
    def _calc_steal_rate_cached(stealer_level: int, target_level: int, base_chance: int, sneak_glove: bool = False) -> Dict[str, Any]:
        stealer_level = max(1, min(99, stealer_level))
        target_level = max(1, min(99, target_level))
        base_chance = max(0, min(255, base_chance))

        lv_diff = STEAL_LEVEL_BASE + stealer_level - target_level
        if sneak_glove:
            # The Sneak Glove makes the attacker count as 60 levels above the target (LVdiff 100)
            lv_diff = STEAL_LEVEL_DIFF_CAP
        lv_diff = max(0, min(STEAL_LEVEL_DIFF_CAP, lv_diff))
        lv_factor = (512 * lv_diff) // 100
        chance = (base_chance * lv_factor) // 256
        winning_rolls = min(STEAL_ROLLS, chance + 1)
        total_rate = round(100.0 * winning_rolls / STEAL_ROLLS, 2)

        if sneak_glove:
            lv_diff_note = f"LVdiff = {lv_diff} (Sneak Glove)"
        else:
            lv_diff_note = f"LVdiff = min(100, 40 + {stealer_level} - {target_level}) = {lv_diff}"
        return {
            "stealer_level": stealer_level,
            "target_level": target_level,
            "sneak_glove": sneak_glove,
            "base_chance_val": base_chance,
            "level_difference": lv_diff,
            "level_factor": lv_factor,
            "chance_value": chance,
            "total_success_percentage": total_rate,
            "formula_note": (
                f"{lv_diff_note}; "
                f"LVfactor = 512*{lv_diff}/100 = {lv_factor}; Chance = {base_chance}*{lv_factor}/256 = {chance}; "
                f"P = ({chance}+1)/64 = {total_rate}%"
            ),
        }

    @staticmethod
    def steal_rate_label(base_chance: int) -> str:
        """Human-readable steal value label with the odds at equal levels (LVdiff 40)."""
        equal = EnemyCompendium._calc_steal_rate_cached(50, 50, int(base_chance), False)["total_success_percentage"]
        return f"Value {int(base_chance)} ({equal:.1f}% at equal level)"

compendium = EnemyCompendium()
