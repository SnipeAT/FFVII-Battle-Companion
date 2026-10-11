"""
Regenerates the battle data in src/ffvii_companion/data/enemies.json and formations.json from the game's
scene.bin (enemies, formations) and kernel.bin (item names).

The compendium files were extracted once (by an earlier extractor, not published with the project)
and curated afterwards, so this tool keeps the curated fields and the order of the entries, and
rewrites everything that comes from the game data. It adds no enemy and removes none.

enemies.json (an entry is joined to its scene.bin records by punctuation-insensitive name):
  kept           id, name (the key OCR matching and formations.json use), notes
  display_name   the name as the game writes it ("Jenova·LIFE", "Turks:Reno", "CMD.Grand Horn")
  zones          "Scene #N" for every scene that has a record of the enemy
  variant fields (VARIANT_FIELDS in ffvii_companion/compendium.py), taken from the default variant:
    level, hp, mp, exp, ap, gil, stats      stats = strength, defense, magic, magic_defense,
                                            dexterity, luck, evade
    weaknesses, resistances, absorbs, elemental_immunities, full_cure
                                            element names (ELEMENT_NAMES, ids 0-15); a rate 0
                                            element is listed in weaknesses as "X (Instant KO)"
    healing_damages                         Restorative absorbed (rate 6): healing magic and items
                                            damage the enemy (undead); Restorative is then not
                                            listed in absorbs
    status_immunities                       ailments of COMBAT_STATUS_AILMENTS it is immune to
    steals                                  every steal slot in game order, [{"item", "rate",
                                            "base_chance", "is_key_steal", "note"}]
    drops, morph
    manipulate                              attacks it can be ordered to use under Manipulate
    enemy_skills                            Enemy Skills in its attack list [{"name", "manipulate"}];
                                            the attack list says the enemy has the attack, not
                                            under which conditions its AI uses it
  variants       only when the scene.bin records of one name differ (bosses fought several
                 times, Battle Arena tiers, forms of one battle): one entry per distinct record,
                 {"enemy_ids", "scenes", <variant fields>}, ordered by first scene. The top-level
                 variant fields are a copy of variants[0]. compendium.py picks the variant
                 of a battle from the matching formations (EnemyCompendium.resolve_variants).

formations.json (matched by scene and formation_index, which is the 1-based formation slot):
  enemy_ids      {name: [scene.bin enemy id of each instance, in formation order]}
  escapable      False when the formation's battle flags forbid escaping (bit 0x0004 cleared)
  layout         normal, preemptive, back_attack, side_attack or pincer
  unused         True for formations of debug scenes (UNUSED_FORMATIONS); the compendium
                 ignores them

Running it again on generated files produces identical output.

Usage:
  .\\.venv\\Scripts\\python.exe -m tools.enrich_compendium [--game-dir DIR] [--check] [--variants]
"""
import argparse
import json
import re
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ffvii_companion.compendium import (ELEMENT_NAMES, ENEMY_SKILLS, FORMATIONS_PATH, JSON_PATH, VARIANT_FIELDS,
                                        EnemyCompendium, name_key)

DEFAULT_GAME_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition")
SCENE_REL = Path("ff7") / "workingdir" / "data" / "lang-en" / "battle" / "scene.bin"
KERNEL_REL = Path("ff7") / "workingdir" / "data" / "lang-en" / "kernel" / "kernel.bin"

# scene.bin layout (one gzip block per scene)
ENEMY_IDS_OFFSET = 0x0000          # 3 x u16
BATTLE_SETUP_OFFSET = 0x0008       # 4 x 20 bytes
BATTLE_SETUP_SIZE = 20
SETUP_FLAGS = 0x10                 # u16, inverted: a cleared bit enables the flag
SETUP_LAYOUT = 0x12                # u8
FLAG_CANNOT_ESCAPE = 0x0004
FORMATION_OFFSET = 0x0118          # 4 formations x 6 enemies x 16 bytes
FORMATION_SIZE = 6 * 16
ENEMY_OFFSET = 0x0298              # 3 x 184 bytes
ENEMY_SIZE = 0xB8
ATTACK_IDS_OFFSET = 0x0840         # 32 x u16
ATTACK_NAMES_OFFSET = 0x0880       # 32 x 32 bytes

# Offsets inside a 184-byte enemy record
E_LEVEL = 0x20
E_STATS = 0x21                     # dexterity, luck, evade, strength, defense, magic, magic defense
E_ELEMENTS = 0x28                  # 8 element ids
E_ELEMENT_RATES = 0x30             # 8 rates
E_ATTACKS = 0x48                   # 16 x u16 attack IDs
E_ITEM_RATES = 0x88                # 4 x u8: < 0x80 drop chance, >= 0x80 steal value + 0x80
E_ITEMS = 0x8C                     # 4 x u16 item ids
E_MANIPULATE = 0x94                # 3 x u16 attack IDs
E_MP = 0x9C
E_AP = 0x9E
E_MORPH = 0xA0
E_HP = 0xA4
E_EXP = 0xA8
E_GIL = 0xAC
E_STATUS_MASK = 0xB0               # u32, a cleared bit is an immunity

# Element rates
RATE_DEATH, RATE_DOUBLE, RATE_HALF, RATE_NULL, RATE_ABSORB, RATE_FULL_CURE = 0, 2, 4, 5, 6, 7
RESTORATIVE = "Restorative"

STATUS_BIT_NAMES = (
    "Death", "Near-death", "Sleep", "Poison", "Sadness", "Fury", "Confusion", "Silence", "Haste",
    "Slow", "Stop", "Frog", "Small", "Slow-numb", "Petrify", "Regen", "Barrier", "MBarrier",
    "Reflect", "Dual", "Shield", "Doom", "Manipulate", "Berserk", "Peerless", "Paralysis", "Darkness",
)
COMBAT_STATUS_AILMENTS = {
    "Death", "Sleep", "Poison", "Sadness", "Fury", "Confusion", "Silence", "Slow", "Stop", "Frog",
    "Small", "Slow-numb", "Petrify", "Doom", "Manipulate", "Berserk", "Paralysis", "Darkness",
}

# Steal items worth the effort (kernel.bin names as formatted by format_item_name)
KEY_STEALS = {
    "Hardedge": "High-value Cloud weapon (+32 Atk, 4 Materia slots).",
    "Striking Staff": "High-value Aeris weapon (+32 Atk).",
    "Carbon Bangle": "Best early-to-mid armor (+27 Def, 3 slots).",
    "Atomic Scissors": "Key Barret weapon (+32 Atk).",
    "Boomerang": "Key Yuffie weapon (+30 Atk).",
    "Right Arm": "Deals 1500 fixed damage to all enemies.",
    "Speed Drink": "Instantly casts Haste in battle.",
    "Curse Ring": "Accessory with large stat boosts that inflicts Death Sentence at the start of battle.",
    "Champion Belt": "Top accessory (+30 Str, +30 Vit).",
}

# Formations of debug scenes: every other formation of the scene uses placeholder enemies.
# Scene 6 formation 4 (6 x Adamantaimai) would otherwise keep the real Adamantaimai battle
# (scene 44, alone) from ever counting as a known formation.
UNUSED_FORMATIONS = {(6, 4)}

ENEMY_SKILL_FIRST_ID = 0x48
LAYOUTS = {0: "normal", 1: "preemptive", 2: "back_attack", 3: "side_attack", 4: "pincer",
           5: "pincer", 6: "side_attack", 7: "side_attack", 8: "normal"}

# FF7 text encoding: 0x00-0x5E are ASCII 0x20-0x7E; 0xC1 is the middle dot of "Jenova·LIFE"
FF7_EXTRA_CHARS = {0xC1: "\u00b7"}


def decode_ff7_string(raw: bytes) -> str:
    out = []
    for b in raw:
        if b == 0xFF:
            break
        if b <= 0x5E:
            out.append(chr(b + 0x20))
        elif b in FF7_EXTRA_CHARS:
            out.append(FF7_EXTRA_CHARS[b])
    return " ".join("".join(out).split())


def is_placeholder_name(raw: bytes) -> bool:
    """Debug scenes name their dummy enemies with characters outside the FF7 Latin charset."""
    return any(b > 0x5E and b not in FF7_EXTRA_CHARS for b in raw.split(b"\xff")[0])


def read_gzip_blocks(path: Path) -> List[bytes]:
    raw = path.read_bytes()
    blocks, idx = [], 0
    while True:
        idx = raw.find(b"\x1f\x8b", idx)
        if idx == -1:
            return blocks
        try:
            blocks.append(zlib.decompress(raw[idx:], 16 + zlib.MAX_WBITS))
        except zlib.error:
            pass
        idx += 2


def read_scenes(scene_path: Path) -> List[bytes]:
    return read_gzip_blocks(scene_path)


def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def format_item_name(name: str) -> str:
    """Capitalizes each word of a kernel item name, keeping hyphens, slashes and apostrophes."""
    out = []
    for part in re.split(r"(\s+|-|/)", name):
        if part in (" ", "-", "/") or not part:
            out.append(part)
        elif "'" in part:
            head, tail = part.split("'", 1)
            out.append(head.capitalize() + "'" + tail.lower())
        else:
            out.append(part.capitalize())
    return "".join(out)


def kernel_item_names(kernel_path: Path) -> Callable[[int], Optional[str]]:
    """Item id -> name (items 0-127, weapons 128-255, armor 256-287, accessories 288-319)."""
    sections = read_gzip_blocks(kernel_path)

    def text_section(data: bytes) -> List[str]:
        count = u16(data, 0) // 2
        offsets = [u16(data, i * 2) for i in range(count)]
        ends = offsets[1:] + [len(data)]
        return [decode_ff7_string(data[a:b]) for a, b in zip(offsets, ends)]

    tables = [(0, text_section(sections[19])), (128, text_section(sections[20])),
              (256, text_section(sections[21])), (288, text_section(sections[22]))]

    def resolve(item_id: int) -> Optional[str]:
        for base, names in reversed(tables):
            if item_id >= base:
                idx = item_id - base
                return format_item_name(names[idx]) if idx < len(names) and names[idx] else None
        return None

    return resolve


def element_fields(rec: bytes, notes: Dict[str, set]) -> dict:
    lists = {"weaknesses": [], "resistances": [], "absorbs": [], "elemental_immunities": [], "full_cure": []}
    healing_damages = False
    for elem_id, rate in zip(rec[E_ELEMENTS:E_ELEMENTS + 8], rec[E_ELEMENT_RATES:E_ELEMENT_RATES + 8]):
        if elem_id == 0xFF or rate == 0xFF:
            continue
        if elem_id >= len(ELEMENT_NAMES):
            notes["element ids outside 0-15 (not shown)"].add(f"0x{elem_id:02X}")
            continue
        elem = ELEMENT_NAMES[elem_id]
        if rate == RATE_DEATH:
            lists["weaknesses"].append(f"{elem} (Instant KO)")
        elif rate == RATE_DOUBLE:
            lists["weaknesses"].append(elem)
        elif rate == RATE_HALF:
            lists["resistances"].append(elem)
        elif rate == RATE_NULL:
            lists["elemental_immunities"].append(elem)
        elif rate == RATE_ABSORB and elem == RESTORATIVE:
            healing_damages = True
        elif rate == RATE_ABSORB:
            lists["absorbs"].append(elem)
        elif rate == RATE_FULL_CURE:
            lists["full_cure"].append(elem)
        else:
            notes["unknown element rates (not shown)"].add(f"{elem} rate {rate}")
    fields = {k: list(dict.fromkeys(v)) for k, v in lists.items()}
    fields["healing_damages"] = healing_damages
    return fields


def item_fields(rec: bytes, item_name: Callable[[int], Optional[str]]) -> dict:
    steals, drops = [], []
    for i in range(4):
        rate, item_id = rec[E_ITEM_RATES + i], u16(rec, E_ITEMS + i * 2)
        if rate == 0xFF or item_id == 0xFFFF:
            continue
        name = item_name(item_id)
        if not name:
            continue
        if rate & 0x80:
            base = rate & 0x7F
            steals.append({"item": name, "rate": EnemyCompendium.steal_rate_label(base), "base_chance": base,
                           "is_key_steal": name in KEY_STEALS, "note": KEY_STEALS.get(name)})
        elif not any(d["item"] == name for d in drops):
            # Dropped when Rnd(0..63) <= rate
            drops.append({"item": name, "rate": "100%" if rate >= 63 else f"{(rate + 1) / 64 * 100:.2f}%"})
    morph_id = u16(rec, E_MORPH)
    return {"steals": steals, "drops": drops, "morph": item_name(morph_id) if morph_id != 0xFFFF else None}


def parse_record(rec: bytes, attack_names: Dict[int, str], item_name, notes) -> dict:
    dex, luck, evade, strength, defense, magic, mdef = rec[E_STATS:E_STATS + 7]
    attacks = [a for a in (u16(rec, E_ATTACKS + i * 2) for i in range(16)) if a != 0xFFFF]
    manip_ids = [a for a in (u16(rec, E_MANIPULATE + i * 2) for i in range(3)) if a != 0xFFFF]
    manipulate: List[str] = []
    for aid in manip_ids:
        name = attack_names.get(aid)
        if name and name not in manipulate:
            manipulate.append(name)
    skills = sorted({aid for aid in attacks if ENEMY_SKILL_FIRST_ID <= aid < ENEMY_SKILL_FIRST_ID + len(ENEMY_SKILLS)})
    status_mask = u32(rec, E_STATUS_MASK)
    fields = {
        "level": rec[E_LEVEL],
        "hp": u32(rec, E_HP),
        "mp": u16(rec, E_MP),
        "exp": u32(rec, E_EXP),
        "ap": u16(rec, E_AP),
        "gil": u32(rec, E_GIL),
        "stats": {"strength": strength, "defense": defense, "magic": magic, "magic_defense": mdef,
                  "dexterity": dex, "luck": luck, "evade": evade},
        **element_fields(rec, notes),
        "status_immunities": [s for bit, s in enumerate(STATUS_BIT_NAMES)
                              if not status_mask & (1 << bit) and s in COMBAT_STATUS_AILMENTS],
        **item_fields(rec, item_name),
        "manipulate": manipulate,
        "enemy_skills": [{"name": ENEMY_SKILLS[aid - ENEMY_SKILL_FIRST_ID], "manipulate": aid in manip_ids}
                         for aid in skills],
    }
    assert set(fields) == set(VARIANT_FIELDS), sorted(set(fields) ^ set(VARIANT_FIELDS))
    return {k: fields[k] for k in VARIANT_FIELDS}


def parse_scenes(scenes: List[bytes], item_name, notes):
    """
    Returns (records by name key, enemy id -> name key, formations by (scene, slot)). A record is
    {"enemy_id", "scene", "display_name", "fields"}; formations are lists of enemy ids.
    """
    attack_names: Dict[int, str] = {}
    for scene in scenes:
        for i in range(32):
            aid = u16(scene, ATTACK_IDS_OFFSET + i * 2)
            name = decode_ff7_string(scene[ATTACK_NAMES_OFFSET + i * 32: ATTACK_NAMES_OFFSET + (i + 1) * 32])
            if aid != 0xFFFF and name:
                attack_names.setdefault(aid, name)

    records: Dict[str, List[dict]] = defaultdict(list)
    formations: Dict[Tuple[int, int], List[int]] = {}
    for scene_idx, scene in enumerate(scenes):
        for slot in range(3):
            rec = scene[ENEMY_OFFSET + slot * ENEMY_SIZE: ENEMY_OFFSET + (slot + 1) * ENEMY_SIZE]
            name = decode_ff7_string(rec[:32])
            if not name or rec[E_LEVEL] in (0, 0xFF) or is_placeholder_name(rec[:32]):
                continue
            records[name_key(name)].append({
                "enemy_id": u16(scene, ENEMY_IDS_OFFSET + slot * 2), "scene": scene_idx, "display_name": name,
                "fields": parse_record(rec, attack_names, item_name, notes)})
        for slot in range(4):
            base = FORMATION_OFFSET + slot * FORMATION_SIZE
            formations[(scene_idx, slot + 1)] = [eid for eid in (u16(scene, base + i * 16) for i in range(6))
                                                 if eid != 0xFFFF]
    return records, formations


def build_variants(records: List[dict]) -> List[dict]:
    """Distinct records of one enemy, ordered by first scene, then enemy id."""
    variants: List[dict] = []
    for rec in sorted(records, key=lambda r: (r["scene"], r["enemy_id"])):
        same = next((v for v in variants if v["fields"] == rec["fields"]), None)
        if same is None:
            same = {"enemy_ids": [], "scenes": [], "fields": rec["fields"]}
            variants.append(same)
        if rec["enemy_id"] not in same["enemy_ids"]:
            same["enemy_ids"].append(rec["enemy_id"])
        if rec["scene"] not in same["scenes"]:
            same["scenes"].append(rec["scene"])
    return [{"enemy_ids": sorted(v["enemy_ids"]), "scenes": v["scenes"], **v["fields"]} for v in variants]


def enrich_enemies(enemies: List[dict], records: Dict[str, List[dict]]) -> List[str]:
    """Rewrites the scene.bin fields of the entries in place. Returns warnings."""
    warnings = []
    for i, enemy in enumerate(enemies):
        recs = records.get(name_key(enemy["name"]))
        if not recs:
            warnings.append(f"no scene.bin record for {enemy['name']!r}")
            continue
        variants = build_variants(recs)
        display = next(r["display_name"] for r in sorted(recs, key=lambda r: (r["scene"], r["enemy_id"])))
        entry = {"id": enemy["id"], "name": enemy["name"], "display_name": display,
                 "zones": [f"Scene #{s}" for s in sorted({r["scene"] for r in recs})]}
        entry.update({k: variants[0][k] for k in VARIANT_FIELDS})
        entry["notes"] = enemy.get("notes")
        if len(variants) > 1:
            entry["variants"] = variants
        enemies[i] = entry
    known = {name_key(e["name"]) for e in enemies}
    for key in sorted(set(records) - known):
        warnings.append(f"scene.bin enemy not in the compendium: {sorted({r['display_name'] for r in records[key]})}")
    return warnings


def enrich_formations(formations: List[dict], scenes: List[bytes], scene_formations, records) -> List[str]:
    warnings = []
    names_by_id = {r["enemy_id"]: key for key, recs in records.items() for r in recs}
    for form in formations:
        scene_idx, slot = form["scene"], form["formation_index"] - 1
        if not (0 <= scene_idx < len(scenes) and 0 <= slot < 4):
            warnings.append(f"formation {scene_idx}/{form['formation_index']} is outside scene.bin")
            continue
        base = BATTLE_SETUP_OFFSET + slot * BATTLE_SETUP_SIZE
        flags = u16(scenes[scene_idx], base + SETUP_FLAGS)
        layout = scenes[scene_idx][base + SETUP_LAYOUT]
        enemy_ids: Dict[str, List[int]] = {}
        for name in form["enemies"]:
            enemy_ids[name] = [eid for eid in scene_formations[(scene_idx, slot + 1)]
                               if names_by_id.get(eid) == name_key(name)]
            if len(enemy_ids[name]) != form["enemies"][name]:
                warnings.append(f"formation {scene_idx}/{slot + 1}: {name} x{form['enemies'][name]} "
                                f"but scene.bin has ids {enemy_ids[name]}")
        form.pop("unused", None)
        form["enemy_ids"] = enemy_ids
        form["escapable"] = bool(flags & FLAG_CANNOT_ESCAPE)
        form["layout"] = LAYOUTS.get(layout, "normal")
        if (scene_idx, slot + 1) in UNUSED_FORMATIONS:
            form["unused"] = True
    return warnings


def variant_report(enemies: List[dict]) -> List[str]:
    lines = []
    for e in enemies:
        variants = e.get("variants") or []
        if len(variants) < 2:
            continue
        lines.append(f"{e['display_name']} ({len(variants)} variants)")
        for v in variants:
            lines.append(f"  ids {v['enemy_ids']} scenes {v['scenes']}: Lv {v['level']}, HP {v['hp']}, "
                         f"MP {v['mp']}, EXP {v['exp']}, AP {v['ap']}, Gil {v['gil']}")
    return lines


def dump(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR,
                        help="FINAL FANTASY VII Steam Edition install directory")
    parser.add_argument("--check", action="store_true",
                        help="Only report whether the data files are up to date (exit code 1 if not)")
    parser.add_argument("--variants", action="store_true", help="List the enemies with several variants")
    args = parser.parse_args(argv)

    scene_path, kernel_path = args.game_dir / SCENE_REL, args.game_dir / KERNEL_REL
    for path in (scene_path, kernel_path):
        if not path.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 2
    scenes = read_scenes(scene_path)
    notes: Dict[str, set] = defaultdict(set)
    records, scene_formations = parse_scenes(scenes, kernel_item_names(kernel_path), notes)

    enemies_raw = JSON_PATH.read_text(encoding="utf-8")
    formations_raw = FORMATIONS_PATH.read_text(encoding="utf-8")
    enemies = json.loads(enemies_raw)
    formations = json.loads(formations_raw)
    warnings = enrich_enemies(enemies, records) + enrich_formations(formations, scenes, scene_formations, records)
    for w in warnings:
        print("warning:", w)
    for label, values in sorted(notes.items()):
        print(f"note: {label}: {', '.join(sorted(values))}")
    if args.variants:
        print("\n".join(variant_report(enemies)))

    changed = [(path, text) for path, text, before in ((JSON_PATH, dump(enemies), enemies_raw),
                                                       (FORMATIONS_PATH, dump(formations), formations_raw))
               if text != before]
    if args.check:
        for path, _ in changed:
            print(f"out of date: {path}")
        return 1 if changed else 0
    for path, text in changed:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"updated {path}")

    with_variants = sum(1 for e in enemies if e.get("variants"))
    print(f"{len(enemies)} enemies ({with_variants} with several variants), {len(formations)} formations "
          f"({sum(1 for f in formations if f.get('escapable') is False)} cannot be escaped, "
          f"{sum(1 for f in formations if f.get('unused'))} unused)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
