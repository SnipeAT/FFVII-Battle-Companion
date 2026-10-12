"""
Prints an enemy's attacks and battle AI scripts from the game's scene.bin, to check tactical notes
against what the enemy really does (who it targets, when it counters, what its attacks inflict).

    .\\.venv\\Scripts\\python.exe -m tools.ai_script "Motor Ball"
    .\\.venv\\Scripts\\python.exe -m tools.ai_script jenova --scene 227

Read-only; needs the Steam edition installed (--game-dir as in tools/enrich_compendium.py). Every
scene.bin record whose name contains the text is printed (bosses fought several times have one
record per scene). For each record: its attacks (target flags, power, status effects, elements),
then each AI script (Init, Main, counters, ...) disassembled.

The opcode names follow the Qhimm wiki's battle AI page and are approximate: the push, compare,
jump and Perform (attack) instructions are reliable, the meaning of some addresses and of a few
list operations is not. Read a script as "what it does under which condition", and confirm
anything surprising with a second source (ff7speedruns.com has decoded scripts for many bosses).
Offsets (left column, jump targets) are relative to the enemy's AI block.
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from ffvii_companion.compendium import ELEMENT_NAMES
from tools.enrich_compendium import (ATTACK_IDS_OFFSET, ATTACK_NAMES_OFFSET, DEFAULT_GAME_DIR, E_ATTACKS,
                                     ENEMY_OFFSET, ENEMY_SIZE, SCENE_REL, STATUS_BIT_NAMES, decode_ff7_string,
                                     read_scenes, u16, u32)

ATTACK_DATA_OFFSET = 0x04C0        # 32 x 28-byte attack records, in the order of ATTACK_IDS_OFFSET
ATTACK_SIZE = 28
A_TARGET, A_DAMAGE_CALC, A_POWER, A_STATUS, A_ELEMENTS = 0x0C, 0x0E, 0x0F, 0x14, 0x18
AI_OFFSET = 0x0E80                 # 3 x u16 offsets of the enemies' AI blocks (0xFFFF: none)
SCRIPT_NAMES = ("Init", "Main", "Counter-General", "Counter-Death", "Counter-Physical", "Counter-Magic",
                "Battle End", "Pre-Action", "Custom 1", "Custom 2", "Custom 3", "Custom 4", "Custom 5",
                "Custom 6", "Custom 7", "Custom 8")

# Well-known addresses (Qhimm wiki); 0x0000-0x1FFF are the script's own variables
ADDRESSES = {
    0x2000: "Command", 0x2060: "Self", 0x2070: "Targets", 0x2080: "Allies", 0x2090: "ActiveAllies",
    0x20A0: "Opponents", 0x20B0: "ActiveOpponents", 0x4000: "Status", 0x4140: "MP", 0x4150: "MaxMP",
    0x4160: "HP", 0x4180: "MaxHP", 0x40C0: "Level",
}
OPERATORS = {
    0x30: "Add", 0x31: "Sub", 0x32: "Mul", 0x33: "Div", 0x34: "Mod", 0x35: "BitAnd", 0x36: "BitOr",
    0x37: "BitNot", 0x40: "Eq", 0x41: "Ne", 0x42: "Ge", 0x43: "Le", 0x44: "Gt", 0x45: "Lt", 0x50: "And",
    0x51: "Or", 0x52: "Not", 0x74: "Pop", 0x75: "Link", 0x80: "Mask", 0x81: "RandomWord", 0x82: "RandomBit",
    0x83: "CountBits", 0x84: "Highest/Lowest", 0x85: "Max?", 0x86: "Min?", 0x90: "Store", 0x91: "Wait?",
    0x92: "Perform",
}


def address(value: int) -> str:
    if value < 0x2000:
        return f"Var{value:04X}"
    return f"{ADDRESSES.get(value, '?')}[{value:04X}]"


def bit_names(mask: int, names) -> List[str]:
    return [name for bit, name in enumerate(names) if mask & (1 << bit)]


def disassemble(block: bytes, start: int, end: int, attack_names: Dict[int, str]) -> List[str]:
    """Instructions of one script, from start to end (offsets in the enemy's AI block)."""
    lines, pc, pushed = [], start, []
    while pc < end:
        op, at = block[pc], pc
        pc += 1
        if op <= 0x07 or 0x10 <= op <= 0x17:
            value = u16(block, pc)
            pc += 2
            text = f"Push{'Value' if op < 0x10 else 'Address'} {address(value)}"
            pushed.append(None)
        elif op in (0x60, 0x61, 0x62):
            size = op - 0x5F
            value = int.from_bytes(block[pc:pc + size], "little")
            pc += size
            text = f"Push {value:0{size * 2}X}"
            pushed.append(value)
        elif op in (0x70, 0x71, 0x72):
            # jump targets are relative to the start of the script
            text = f"{('JumpIfZero', 'JumpIfNotEqual', 'Jump')[op - 0x70]} {start + u16(block, pc):04X}"
            pc += 2
        elif op == 0x73:
            text = "End"
        elif op == 0x93:
            stop = block.index(b"\xff", pc)
            text = f'ShowText "{decode_ff7_string(block[pc:stop])}"'
            pc = stop + 1
        elif op == 0xA0:
            count = block[pc]
            stop = block.find(b"\x00", pc + 1)
            stop = stop if stop != -1 else pc + 1
            text = f"DebugText {count} {block[pc + 1:stop]!r}"
            pc = stop + 1
        else:
            text = OPERATORS.get(op, f"Op{op:02X}?")
            if op == 0x92 and len(pushed) >= 1 and isinstance(pushed[-1], int):
                text += f"  attack {pushed[-1]:04X} {attack_names.get(pushed[-1], '')}"
        lines.append(f"  {at:04X}: {text}")
    return lines


def describe(scene: bytes, slot: int, attack_names: Dict[int, str]) -> List[str]:
    rec = scene[ENEMY_OFFSET + slot * ENEMY_SIZE: ENEMY_OFFSET + (slot + 1) * ENEMY_SIZE]
    out = []
    scene_attacks = {u16(scene, ATTACK_IDS_OFFSET + i * 2): i for i in range(32)}
    for aid in (u16(rec, E_ATTACKS + i * 2) for i in range(16)):
        if aid == 0xFFFF:
            continue
        index = scene_attacks.get(aid)
        if index is None:
            out.append(f"  attack {aid:04X} {attack_names.get(aid, '')} (no record in this scene)")
            continue
        data = scene[ATTACK_DATA_OFFSET + index * ATTACK_SIZE: ATTACK_DATA_OFFSET + (index + 1) * ATTACK_SIZE]
        status = u32(data, A_STATUS)
        statuses = bit_names(status, STATUS_BIT_NAMES) if status != 0xFFFFFFFF else []
        elements = bit_names(u16(data, A_ELEMENTS), ELEMENT_NAMES)
        out.append(f"  attack {aid:04X} {attack_names.get(aid, ''):<20} targets={data[A_TARGET]:02X} "
                   f"calc={data[A_DAMAGE_CALC]:02X} power={data[A_POWER]} "
                   f"status={','.join(statuses) or '-'} elements={','.join(elements) or '-'}")
    offsets = [u16(scene, AI_OFFSET + i * 2) for i in range(3)]
    if offsets[slot] == 0xFFFF:
        return out + ["  no AI scripts"]
    block_start = AI_OFFSET + offsets[slot]
    later = sorted(AI_OFFSET + o for o in offsets if o != 0xFFFF and AI_OFFSET + o > block_start)
    block = scene[block_start:later[0] if later else len(scene)]
    starts = [u16(block, i * 2) for i in range(16)]
    used = sorted({s for s in starts if s != 0xFFFF})
    for name, start in zip(SCRIPT_NAMES, starts):
        if start == 0xFFFF:
            continue
        following = [s for s in used if s > start]
        out.append(f" -- {name} @{start:04X}")
        out.extend(disassemble(block, start, following[0] if following else len(block), attack_names))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("name", help="Text the enemy's scene.bin name contains (any case)")
    parser.add_argument("--scene", type=int, help="Only this scene")
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR,
                        help="FINAL FANTASY VII Steam Edition install directory")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the middle dot of Jenova·LIFE
    scene_path = args.game_dir / SCENE_REL
    if not scene_path.exists():
        print(f"not found: {scene_path}", file=sys.stderr)
        return 2
    scenes = read_scenes(scene_path)
    attack_names: Dict[int, str] = {}
    for scene in scenes:
        for i in range(32):
            aid = u16(scene, ATTACK_IDS_OFFSET + i * 2)
            name = decode_ff7_string(scene[ATTACK_NAMES_OFFSET + i * 32: ATTACK_NAMES_OFFSET + (i + 1) * 32])
            if aid != 0xFFFF and name:
                attack_names.setdefault(aid, name)
    found = 0
    for index, scene in enumerate(scenes):
        if args.scene is not None and index != args.scene:
            continue
        for slot in range(3):
            rec = scene[ENEMY_OFFSET + slot * ENEMY_SIZE: ENEMY_OFFSET + (slot + 1) * ENEMY_SIZE]
            name = decode_ff7_string(rec[:32])
            if not name or args.name.lower() not in name.lower():
                continue
            found += 1
            print(f"=== scene {index}, slot {slot}, enemy id {u16(scene, slot * 2)}: {name}")
            print("\n".join(describe(scene, slot, attack_names)))
    if not found:
        print(f"no scene.bin enemy named like {args.name!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
