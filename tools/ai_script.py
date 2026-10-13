"""
Prints an enemy's attacks and battle AI scripts from the game's scene.bin, to check tactical notes
against what the enemy really does (who it targets, when it counters, what its attacks inflict).

    .\\.venv\\Scripts\\python.exe -m tools.ai_script "Motor Ball"
    .\\.venv\\Scripts\\python.exe -m tools.ai_script jenova --scene 227
    .\\.venv\\Scripts\\python.exe -m tools.ai_script "Guard Scorpion" --pseudo

Read-only; needs the Steam edition installed (--game-dir as in tools/enrich_compendium.py). Every
scene.bin record whose name contains the text (case and punctuation ignored) is printed (bosses fought several times have one
record per scene). For each record: its attacks (targets, power, status effects, elements),
then each AI script (Init, Main, counters, ...), disassembled or, with --pseudo, decompiled into
readable pseudo-code (decompile(): if / else, switch, assignments, "use <attack>").

Opcode names follow Terence Fergusson's notes (Qhimm forums, "Enemy AI (Scripters only!)") and the
Qhimm wiki's Battle Script and Battle AI Addresses pages (wiki.ffrtt.ru). The stack effect of every
opcode is known (every script in scene.bin decodes with a balanced stack), but the meaning of some
addresses is not: those print as their number ([4024]). Read a script as "what it does under which
condition", and confirm anything surprising with a second source (ff7speedruns.com has decoded
scripts for many bosses). Offsets (left column, jump targets, @labels) are relative to the enemy's AI block.
"""
import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ffvii_companion.compendium import ELEMENT_NAMES, ENEMY_SKILLS, name_key
from tools.enrich_compendium import (ATTACK_IDS_OFFSET, ATTACK_NAMES_OFFSET, DEFAULT_GAME_DIR, E_ATTACKS,
                                     ENEMY_OFFSET, ENEMY_SIZE, ENEMY_SKILL_FIRST_ID, SCENE_REL, STATUS_BIT_NAMES,
                                     decode_ff7_string, read_scenes, u16, u32)

ATTACK_DATA_OFFSET = 0x04C0        # 32 x 28-byte attack records, in the order of ATTACK_IDS_OFFSET
ATTACK_SIZE = 28
A_MP, A_TARGET, A_DAMAGE_CALC, A_POWER, A_STATUS_CHANGE, A_STATUS, A_ELEMENTS = 0x04, 0x0C, 0x0E, 0x0F, 0x11, 0x14, 0x18
AI_OFFSET = 0x0E80                 # 3 x u16 offsets of the enemies' AI blocks (0xFFFF: none)
SCRIPT_NAMES = ("Init", "Main", "Counter-General", "Counter-Death", "Counter-Physical", "Counter-Magic",
                "Battle End", "Pre-Action", "Custom 1", "Custom 2", "Custom 3", "Custom 4", "Custom 5",
                "Custom 6", "Custom 7", "Custom 8")
# Scripts the game runs in reaction to something; an attack there is a counter (reacts())
REACTION_SCRIPTS = {"Counter-General": "any action", "Counter-Physical": "physical", "Counter-Magic": "magic",
                    "Counter-Death": "death"}

# Status bits: address 0x4000 + n read as a bit is status n (bit 31, Imprisoned, is what Pyramid,
# Bottomswell's Waterpolo and Carry Armor's arms check)
STATUS_NAMES = STATUS_BIT_NAMES + ("Dual-drain", "DeathForce", "Resist", "Lucky Girl", "Imprisoned")
# 0x0000-0x03FF are the enemy's own variables (VarXXXX); names from the Qhimm wiki's Battle AI Addresses
ADDRESSES = {
    0x2000: "LastCommand", 0x2008: "LastAction", 0x2010: "BankValue", 0x2020: "BattleLayout",
    0x2050: "ActiveActors", 0x2060: "Self", 0x2070: "Targets", 0x2080: "Allies", 0x2090: "ActiveAllies",
    0x20A0: "Opponents", 0x20B0: "ActiveOpponents", 0x20C0: "ActiveParty", 0x20E0: "AllActors",
    0x2110: "BattleEndFlags", 0x2111: "EndEscaped", 0x2112: "EndVictory", 0x2113: "EndNoReward",
    0x2114: "EndBattle", 0x2116: "NoVictoryPose", 0x2120: "LastElements", 0x2140: "FormationIndex",
    0x2150: "LastActionIndex", 0x2170: "SpecialAttackFlags", 0x21C0: "PartyGil",
    0x4000: "Statuses", 0x4020: "SituationFlags", 0x4021: "IsAlly", 0x4025: "Defending", 0x4026: "BackRow",
    0x4027: "AttackConnects", 0x4028: "PhysicalImmune", 0x4029: "MagicImmune", 0x402B: "Covered",
    0x402C: "DeathImmune", 0x402D: "Dead", 0x402E: "Invisible", 0x4040: "ActorIndex", 0x4048: "Level",
    0x4060: "CharID", 0x4068: "Attack", 0x4070: "MagicAttack", 0x4078: "Evade", 0x4080: "IdleAnimation",
    0x4088: "DamagedAnimation", 0x4098: "ModelSize", 0x40A0: "Dexterity", 0x40C0: "LastTargets",
    0x40D0: "LastAttacker", 0x40E0: "LastPhysicalAttacker", 0x40F0: "LastMagicAttacker",
    0x4100: "Defense", 0x4110: "MagicDefense", 0x4120: "Index", 0x4130: "AbsorbedElements", 0x4140: "MP",
    0x4150: "MaxMP", 0x4160: "HP", 0x4180: "MaxHP", 0x4220: "InitialStatuses", 0x4268: "MagicEvade",
    0x4270: "Row", 0x42B0: "APWorth", 0x42C0: "GilWorth", 0x42E0: "EXPWorth",
}
# CharID of party members is the character id + 0x10
CHARACTERS = ("Cloud", "Barret", "Tifa", "Aeris", "Red XIII", "Yuffie", "Cait Sith", "Vincent", "Cid",
              "Young Cloud", "Sephiroth")

PUSH_CONST = (0x60, 0x61, 0x62)
JUMP_IF_ZERO, JUMP_IF_NE, JUMP, END, POP, PERFORM, SHOW_TEXT, DEBUG_TEXT = (
    0x70, 0x71, 0x72, 0x73, 0x91, 0x92, 0x93, 0xA0)
OPERATORS = {
    0x30: "Add", 0x31: "Sub", 0x32: "Mul", 0x33: "Div", 0x34: "Mod", 0x35: "BitAnd", 0x36: "BitOr",
    0x37: "BitNot", 0x40: "Eq", 0x41: "Ne", 0x42: "Ge", 0x43: "Le", 0x44: "Gt", 0x45: "Lt", 0x50: "And",
    0x51: "Or", 0x52: "Not", 0x74: "Pop", 0x75: "LinkScripts", 0x80: "Mask", 0x81: "RandomWord",
    0x82: "RandomBit", 0x83: "CountBits", 0x84: "HighestOf", 0x85: "LowestOf", 0x86: "MPCost", 0x87: "ToBit",
    0x90: "Store", 0x91: "Pop", 0x92: "Perform", 0x94: "CopyUnitData", 0x95: "MemoryBank", 0x96: "ElementDefense",
    0xA1: "Pop2",
}
# Perform (0x92) command types: 0x20 an enemy attack, 0x22 runs one of the enemy's scripts
# (SCRIPT_NAMES index), 0x24 pauses the battle while the text shown before it is displayed
CMD_ATTACK, CMD_RUN_SCRIPT, CMD_TEXT_PAUSE = 0x20, 0x22, 0x24

# Battle text: 0x00-0xDF are Mac Roman shifted by 0x20 (0xA9 the ellipsis, 0xB2 / 0xB3 the curly
# quotes, 0xC1 the middle dot); 0xEA + 2 bytes inserts a name
TEXT_NAME_VARIABLE = 0xEA


def decode_battle_text(raw: bytes) -> str:
    out, i = [], 0
    while i < len(raw) and raw[i] != 0xFF:
        b = raw[i]
        if b == TEXT_NAME_VARIABLE:
            out.append("[name]")
            i += 3
            continue
        if b < 0xE0:
            out.append(bytes([b + 0x20]).decode("mac_roman"))
        i += 1
    return " ".join("".join(out).split())


@dataclass(frozen=True)
class Instruction:
    at: int           # offset in the enemy's AI block
    end: int          # offset of the next instruction
    op: int
    arg: object = None  # address or constant (int), absolute jump target (int), text (str), (count, text) for 0xA0


def is_push(op: int) -> bool:
    return op <= 0x07 or 0x10 <= op <= 0x17


def decode(block: bytes, start: int, end: int) -> List[Instruction]:
    """Instructions of one script, from start to end (offsets in the enemy's AI block)."""
    out, pc = [], start
    while pc < end:
        op, at = block[pc], pc
        pc += 1
        if op == 0xFF:
            break  # not an opcode: the 0xFF padding after the last script of the scene
        arg: object = None
        if is_push(op):
            arg = u16(block, pc)
            pc += 2
        elif op in PUSH_CONST:
            size = op - 0x5F
            arg = int.from_bytes(block[pc:pc + size], "little")
            pc += size
        elif op in (JUMP_IF_ZERO, JUMP_IF_NE, JUMP):
            arg = start + u16(block, pc)  # jump targets are relative to the start of the script
            pc += 2
        elif op == SHOW_TEXT:
            stop = block.index(b"\xff", pc)
            arg = decode_battle_text(block[pc:stop])
            pc = stop + 1
        elif op == DEBUG_TEXT:
            count = block[pc]
            stop = block.find(b"\x00", pc + 1)
            stop = stop if stop != -1 else pc + 1
            arg = (count, block[pc + 1:stop].decode("latin-1"))
            pc = stop + 1
        out.append(Instruction(at, pc, op, arg))
    return out


def address(value: int, op: int = 0x02) -> str:
    """Name of an address; op is the push opcode (its low bits: 0 bit, 1 byte, 2 word, 3 dword)."""
    if value < 0x2000:
        return f"Var{value:04X}"
    if 0x4000 <= value < 0x4020 and op & 0x07 == 0:
        return STATUS_NAMES[value - 0x4000]
    return ADDRESSES.get(value, f"[{value:04X}]")


def attack_label(attack_id: int, attack_names: Dict[int, str]) -> str:
    return attack_names.get(attack_id) or f"(unnamed {attack_id:04X})"


def perform_operands(ins: List[Instruction], k: int) -> Tuple[Optional[int], Optional[int]]:
    """Command type and attack id of the Perform at ins[k], when both are pushed as constants."""
    if k >= 2 and ins[k - 1].op in PUSH_CONST and ins[k - 2].op in PUSH_CONST:
        return ins[k - 2].arg, ins[k - 1].arg
    return None, None


def disassemble(block: bytes, start: int, end: int, attack_names: Dict[int, str]) -> List[str]:
    """Instruction listing of one script, from start to end (offsets in the enemy's AI block)."""
    ins = decode(block, start, end)
    lines = []
    for k, x in enumerate(ins):
        if is_push(x.op):
            text = f"Push{'Value' if x.op < 0x10 else 'Address'} {address(x.arg, x.op)}[{x.arg:04X}]"
        elif x.op in PUSH_CONST:
            text = f"Push {x.arg:0{(x.op - 0x5F) * 2}X}"
        elif x.op in (JUMP_IF_ZERO, JUMP_IF_NE, JUMP):
            text = f"{('JumpIfZero', 'JumpIfNotEqual', 'Jump')[x.op - JUMP_IF_ZERO]} {x.arg:04X}"
        elif x.op == END:
            text = "End"
        elif x.op == SHOW_TEXT:
            text = f'ShowText "{x.arg}"'
        elif x.op == DEBUG_TEXT:
            text = f"DebugText {x.arg[0]} {x.arg[1]!r}"
        else:
            text = OPERATORS.get(x.op, f"Op{x.op:02X}?")
            command, attack_id = perform_operands(ins, k) if x.op == PERFORM else (None, None)
            if command == CMD_ATTACK:
                text += f"  attack {attack_id:04X} {attack_names.get(attack_id, '')}".rstrip()
            elif command == CMD_RUN_SCRIPT and attack_id < len(SCRIPT_NAMES):
                text += f"  run {SCRIPT_NAMES[attack_id]}"
            elif command == CMD_TEXT_PAUSE:
                text += "  pause for the text"
        lines.append(f"  {x.at:04X}: {text}")
    return lines


# ---------------------------------------------------------------------------------------------
# Decompiler: the AI compiler emits structured code (if / else, C-style switch), so a single pass
# over the instructions with an expression stack recovers it. Jumps that fit no pattern print as
# "goto @XXXX" with an "@XXXX:" label, so no instruction is ever lost.

BINARY = {0x30: ("+", 60), 0x31: ("-", 60), 0x32: ("*", 70), 0x33: ("/", 70), 0x34: ("%", 70), 0x35: ("&", 50),
          0x36: ("|", 45), 0x40: ("==", 40), 0x41: ("!=", 40), 0x42: (">=", 40), 0x43: ("<=", 40),
          0x44: (">", 40), 0x45: ("<", 40), 0x50: ("and", 30), 0x51: ("or", 20)}
FUNCTIONS = {0x82: "random_one", 0x83: "count", 0x84: "highest", 0x85: "lowest", 0x86: "mp_cost", 0x87: "bit"}


def stack_effect(x: Instruction) -> Tuple[int, int]:
    """(values popped, values pushed) of an instruction. Store pops three values for a unit's field,
    but its Mask then pushes nothing, so counting Mask as 2 -> 1 and Store as 2 -> 0 adds up."""
    op = x.op
    if is_push(op) or op in PUSH_CONST or op == 0x81:
        return 0, 1
    if op in BINARY or op in (0x80, 0x96):
        return 2, 1
    if op in FUNCTIONS or op in (0x37, 0x52):
        return 1, 1
    if op in (0x90, PERFORM, 0x94, 0x95, 0xA1):
        return 2, 0
    if op in (JUMP_IF_ZERO, JUMP_IF_NE, POP, 0x74, 0x75):
        return 1, 0
    if op == DEBUG_TEXT:
        return x.arg[0], 0
    return 0, 0


@dataclass(frozen=True)
class Expr:
    text: str
    prec: int = 100                 # binding strength: 100 atoms, 90 unary, 20-70 binary operators
    value: Optional[int] = None     # constant value (pushed by 0x60-0x62)
    field: Optional[int] = None     # the address read, for a plain or masked address
    op: Optional[str] = None        # binary operator
    left: Optional["Expr"] = None


UNKNOWN = Expr("?")


def wrap(e: Expr, prec: int) -> str:
    return f"({e.text})" if e.prec < prec else e.text


def binary(op: int, a: Expr, b: Expr) -> Expr:
    symbol, prec = BINARY[op]
    if symbol in ("==", "!=") and a.field == 0x4060 and b.value is not None and 0x10 <= b.value < 0x10 + len(CHARACTERS):
        b = Expr(CHARACTERS[b.value - 0x10])
    return Expr(f"{wrap(a, prec)} {symbol} {wrap(b, prec + 1)}", prec, op=symbol, left=a)


class Decompiler:
    def __init__(self, ins: List[Instruction], attack_names: Dict[int, str]):
        self.ins = ins
        self.attack_names = attack_names
        self.index = {x.at: i for i, x in enumerate(ins)}
        if ins:
            self.index[ins[-1].end] = len(ins)
        self.targets = {x.arg for x in ins if x.op in (JUMP_IF_ZERO, JUMP_IF_NE, JUMP)}
        self.stack: List[Expr] = []
        self.labels: set = set()      # goto targets, printed as "@XXXX:"
        self.wanted: set = set()

    def run(self) -> list:
        # The first pass finds the goto targets, the second prints their labels
        self.parse(0, len(self.ins), None)
        self.labels, self.stack = self.wanted, []
        return self.parse(0, len(self.ins), None)

    def pop(self) -> Expr:
        return self.stack.pop() if self.stack else UNKNOWN

    def at(self, i: int) -> int:
        return self.ins[i].at if i < len(self.ins) else (self.ins[-1].end if self.ins else 0)

    def goto(self, target: int) -> tuple:
        self.wanted.add(target)
        return ("text", f"goto @{target:04X}")

    def thread(self, target: int) -> int:
        """Where a jump really goes when it lands on another jump (a case body that jumps to the
        default body's break)."""
        seen = set()
        while target not in seen:
            seen.add(target)
            k = self.index.get(target)
            if k is None or k >= len(self.ins) or self.ins[k].op != JUMP:
                break
            target = self.ins[k].arg
        return target

    def skip_dead(self, i: int, hi: int) -> int:
        """After an unconditional jump or End, code up to the next jump target is unreachable (the
        compiler leaves a jump into the next case body after each break)."""
        while i < hi and self.ins[i].at not in self.targets:
            i += 1
        return i

    def parse(self, lo: int, hi: int, brk: Optional[int], fall: Optional[Tuple[int, str]] = None) -> list:
        """Statements of ins[lo:hi]; brk is the offset a break jumps to (the enclosing switch's end),
        fall the (offset, name) of the next case body a case without a break continues into."""
        out: list = []
        i = lo
        start = lo          # where the expression on the stack started (a while loop's head)
        while i < hi:
            x = self.ins[i]
            if x.at in self.labels:
                out.append(("text", f"@{x.at:04X}:"))
            if not self.stack:
                start = i
            op = x.op
            if is_push(op):
                self.stack.append(Expr(address(x.arg, op), field=x.arg))
            elif op in PUSH_CONST:
                self.stack.append(Expr(str(x.arg), value=x.arg))
            elif op in BINARY:
                b, a = self.pop(), self.pop()
                self.stack.append(binary(op, a, b))
            elif op == 0x37:
                self.stack.append(Expr(f"~{wrap(self.pop(), 90)}", 90))
            elif op == 0x52:
                self.stack.append(Expr(f"not {wrap(self.pop(), 90)}", 90))
            elif op == 0x80:
                b, a = self.pop(), self.pop()
                self.stack.append(Expr(f"{wrap(a, 100)}.{wrap(b, 100)}", field=b.field))
            elif op == 0x81:
                self.stack.append(Expr("random()"))
            elif op == 0x83:
                # the bit count of a value; of a unit's field, the value (of the first unit)
                e = self.pop()
                self.stack.append(Expr(f"{'value' if (e.field or 0) >= 0x4000 else 'count'}({e.text})", field=e.field))
            elif op in FUNCTIONS:
                self.stack.append(Expr(f"{FUNCTIONS[op]}({self.pop().text})"))
            elif op == 0x90:
                value, target = self.pop(), self.pop()
                out.append(("text", self.assignment(target, value)))
            elif op in (POP, 0x74):
                self.pop()
            elif op == 0xA1:
                self.pop(), self.pop()
            elif op == PERFORM:
                attack, command = self.pop(), self.pop()
                out.append(("text", self.perform(command, attack)))
            elif op == SHOW_TEXT:
                out.append(("text", f'show "{x.arg}"'))
            elif op == DEBUG_TEXT:
                count, text = x.arg
                args = [self.pop().text for _ in range(count)][::-1]
                out.append(("text", f"debug {text!r}" + (f" % ({', '.join(args)})" if args else "")))
            elif op == 0x94:
                second, first = self.pop(), self.pop()
                out.append(("text", f"copy_unit_data(from {second.text} to {first.text})"))
            elif op == 0x95:
                offset, mode = self.pop(), self.pop()
                where = f"bank[0x{offset.value:02X}]" if offset.value is not None else f"bank[{offset.text}]"
                if mode.value == 1:
                    out.append(("text", f"{where} = BankValue"))
                elif mode.value == 0:
                    out.append(("text", f"BankValue = {where}"))
                else:
                    out.append(("text", f"memory_bank({mode.text}, {offset.text})"))
            elif op == 0x96:
                b, a = self.pop(), self.pop()
                self.stack.append(Expr(f"element_defense({a.text}, {b.text})"))
            elif op == 0x75:
                out.append(("text", f"link_scripts({self.pop().text})"))
            elif op == JUMP_IF_ZERO:
                stmt, i = self.parse_if(i, hi, brk, start, fall)
                out.append(stmt)
                continue
            elif op == JUMP_IF_NE:
                parsed = self.parse_switch(i, hi)
                if parsed:
                    out.append(parsed[0])
                    i = parsed[1]
                    continue
                case = self.pop()
                value = self.stack[-1] if self.stack else UNKNOWN
                out.append(("if-goto", f"{value.text} != {case.text}", x.arg))
                self.wanted.add(x.arg)
            elif op == JUMP:
                target = self.thread(x.arg)
                if target == brk:
                    out.append(("break",))
                elif fall is not None and x.arg == fall[0]:
                    out.append(("text", f"fall through to {fall[1]}"))
                else:
                    out.append(self.goto(target))
                i = self.skip_dead(i + 1, hi)
                continue
            elif op == END:
                out.append(("end",))
                i = self.skip_dead(i + 1, hi)
                continue
            else:
                out.append(("text", f"op{op:02X}?"))
            i += 1
        return out

    def assignment(self, target: Expr, value: Expr) -> str:
        if value.op in ("+", "-") and value.left is not None and value.left.text == target.text:
            rest = value.text[len(wrap(value.left, 60)) + len(value.op) + 2:]
            return f"{target.text} {value.op}= {rest}"
        return f"{target.text} = {value.text}"

    def perform(self, command: Expr, attack: Expr) -> str:
        if command.value == CMD_ATTACK:
            if attack.value is None:
                return f"use attack {attack.text}"
            return f"use {attack_label(attack.value, self.attack_names)}"
        if command.value == CMD_RUN_SCRIPT and attack.value is not None and attack.value < len(SCRIPT_NAMES):
            return f"run {SCRIPT_NAMES[attack.value]}"
        if command.value == CMD_TEXT_PAUSE:
            return "pause for the text"
        return f"perform({command.text}, {attack.text})"

    def parse_if(self, i: int, hi: int, brk: Optional[int], head: int,
                 fall: Optional[Tuple[int, str]]) -> Tuple[tuple, int]:
        cond = self.pop()
        x = self.ins[i]
        t = self.index.get(x.arg)
        if t is None or t <= i or t > hi:
            self.wanted.add(x.arg)
            return ("if-goto", f"not {wrap(cond, 90)}", x.arg), i + 1
        then_hi, else_hi = t, None
        last = self.ins[t - 1] if t - 1 > i else None
        if last is not None and last.op == JUMP and last.arg == self.ins[head].at:
            # the body jumps back to the condition: a while loop
            saved = list(self.stack)
            body = self.parse(i + 1, t - 1, None)
            self.stack = saved
            return ("while", cond, body), t
        if last is not None and last.op == JUMP:
            u = self.index.get(last.arg)
            if u == t:
                then_hi = t - 1                  # a jump to the next instruction
            elif u is not None and t < u <= hi:
                then_hi, else_hi = t - 1, u      # then-branch ends by jumping over the else-branch
        saved = list(self.stack)
        then = self.parse(i + 1, then_hi, brk, fall)
        after = self.stack
        orelse: list = []
        if else_hi is not None:
            self.stack = list(saved)
            orelse = self.parse(t, else_hi, brk, fall)
        self.stack = after
        return ("if", cond, then, orelse), (else_hi if else_hi is not None else t)

    def is_test(self, k: int, hi: int) -> bool:
        return k + 1 < hi and self.ins[k].op in PUSH_CONST and self.ins[k + 1].op == JUMP_IF_NE

    def switch_end(self, i: int, last: int, hi: int) -> int:
        """Index of the switch's end: the Pop of the switch value, found by following the stack
        depth from the last test (statements leave it balanced). Without one, where most breaks go."""
        depth = 1
        for k in range(last, hi):
            x = self.ins[k]
            if x.op == POP and depth == 1:
                return k
            pops, pushes = stack_effect(x)
            depth += pushes - pops
            if depth < 1:
                break
        breaks: Counter = Counter()
        for k in range(i, last):
            x = self.ins[k]
            if x.op == JUMP and self.ins[k - 1].op not in (JUMP, END):
                t = self.index.get(self.thread(x.arg))
                if t is not None and last <= t <= hi:
                    breaks[t] += 1
        return min(breaks, key=lambda t: (-breaks[t], t)) if breaks else last

    def connector_target(self, b: int) -> int:
        """Where a case test's jump into its body lands: with three or more values, the jump of
        each test goes to the next test's jump (case 2, 3, 4: 2 -> 3's jump -> body)."""
        seen = set()
        while b < len(self.ins) and self.ins[b].op == JUMP and self.ins[b - 1].op == JUMP_IF_NE and b not in seen:
            seen.add(b)
            nxt = self.index.get(self.ins[b].arg)
            if nxt is None:
                break
            b = nxt
        return b

    def parse_switch(self, i: int, hi: int) -> Optional[Tuple[tuple, int]]:
        """
        A switch as the compiler emits it: the value stays on the stack while each case pushes a
        constant and jumps to the next test when they differ (0x71 pops only the constant). The
        tests of a multi-value case jump straight to its body; every body ends with a break (a
        jump to the end, where 0x91 pops the value) followed by an unreachable jump into the
        next body. The tests' last jump goes to the default body, or to the end.
        """
        if i < 1 or not self.is_test(i - 1, hi + 1):
            return None
        groups, values, connectors, j = [], [], [], i
        while True:
            values.append(self.ins[j - 1].arg)
            n = self.index.get(self.ins[j].arg)
            if n is None or n <= j or n > hi:
                return None
            nxt = self.ins[j + 1] if j + 1 < hi else None
            b = self.index.get(nxt.arg) if nxt is not None and nxt.op == JUMP else None
            if b is not None and self.is_test(n, hi) and self.ins[b - 1].op == JUMP_IF_NE:
                connectors.append(self.connector_target(b))  # another value of the same case
                j = n + 1
                continue
            if any(c != j + 1 for c in connectors):
                return None
            groups.append((values, j + 1, n))
            values, connectors = [], []
            if self.is_test(n, hi):
                j = n + 1
                continue
            last = n
            break
        end = self.switch_end(i, last, hi)
        self.pop()
        value = self.pop()
        saved = list(self.stack)
        brk = self.at(end)
        cases = []
        for n, (case_values, start, stop) in enumerate(groups):
            if n + 1 < len(groups):
                fall = (self.at(groups[n + 1][1]), f"case {', '.join(str(v) for v in groups[n + 1][0])}")
            else:
                fall = (self.at(last), "default") if end > last else None
            self.stack = list(saved)
            cases.append((case_values, self.parse(start, stop, brk, fall)))
        default = None
        if end > last:
            self.stack = list(saved)
            default = self.parse(last, end, brk)
        self.stack = saved
        if end < hi and self.ins[end].op == POP and self.ins[end].at not in self.labels:
            end += 1                             # pops the switch value
        return ("switch", value, cases, default), end


def render(stmts: list, depth: int = 0) -> List[str]:
    pad = "  " * depth
    lines: List[str] = []
    for s in stmts:
        kind = s[0]
        if kind == "text":
            lines.append(pad + s[1])
        elif kind == "break":
            lines.append(pad + "break")
        elif kind == "end":
            lines.append(pad + "end")
        elif kind == "if-goto":
            lines.append(f"{pad}if {s[1]}: goto @{s[2]:04X}")
        elif kind == "if":
            cond, then, orelse = s[1:]
            if not then and orelse:
                cond, then, orelse = Expr(f"not {wrap(cond, 90)}", 90), orelse, []
            lines.append(f"{pad}if {cond.text}:")
            lines.extend(render(then, depth + 1) or [pad + "  (nothing)"])
            while len(orelse) == 1 and orelse[0][0] == "if" and orelse[0][2]:
                cond, then, orelse = orelse[0][1:]
                lines.append(f"{pad}elif {cond.text}:")
                lines.extend(render(then, depth + 1))
            if orelse:
                lines.append(f"{pad}else:")
                lines.extend(render(orelse, depth + 1))
        elif kind == "while":
            lines.append(f"{pad}while {s[1].text}:")
            lines.extend(render(s[2], depth + 1) or [pad + "  (nothing)"])
        elif kind == "switch":
            value, cases, default = s[1:]
            lines.append(f"{pad}switch {value.text}:")
            for values, body in cases:
                lines.append(f"{pad}  case {', '.join(str(v) for v in values)}:")
                lines.extend(render(strip_break(body), depth + 2) or [pad + "    (nothing)"])
            if default is not None and strip_break(default):
                lines.append(f"{pad}  default:")
                lines.extend(render(strip_break(default), depth + 2))
    return lines


def strip_break(body: list) -> list:
    return body[:-1] if body and body[-1] == ("break",) else body


def decompile(block: bytes, start: int, end: int, attack_names: Dict[int, str]) -> List[str]:
    """Pseudo-code of one script, from start to end (offsets in the enemy's AI block)."""
    stmts = Decompiler(decode(block, start, end), attack_names).run()
    if stmts and stmts[-1] == ("end",):
        stmts = stmts[:-1]                       # every script ends with End
    return render(stmts)


# ---------------------------------------------------------------------------------------------
# One enemy record: attacks and scripts

def target_label(flags: int) -> str:
    """Who an attack targets, from its target flags (approximate: the AI usually picks the targets)."""
    if not flags:
        return "-"
    if flags & 0x80:
        return "random"
    return "all" if flags & 0x04 else "one"


def status_mode(change: int) -> str:
    """What an attack does with its statuses (bits 0x40 / 0x80 of its status change byte: White Wind
    and Poisona remove theirs, Frog Song and Mini toggle theirs; the low 6 bits are the chance)."""
    if change & 0x80 and change != 0xFF:
        return "toggle"
    return "remove" if change & 0x40 and change != 0xFF else "inflict"


def attack_records(scene: bytes, slot: int, attack_names: Dict[int, str]) -> List[dict]:
    """The attacks in an enemy's attack list, in its order."""
    rec = scene[ENEMY_OFFSET + slot * ENEMY_SIZE: ENEMY_OFFSET + (slot + 1) * ENEMY_SIZE]
    scene_attacks = {u16(scene, ATTACK_IDS_OFFSET + i * 2): i for i in range(32)}
    out = []
    for aid in (u16(rec, E_ATTACKS + i * 2) for i in range(16)):
        if aid == 0xFFFF:
            continue
        attack = {"id": aid, "name": attack_names.get(aid, ""),
                  "enemy_skill": ENEMY_SKILL_FIRST_ID <= aid < ENEMY_SKILL_FIRST_ID + len(ENEMY_SKILLS)}
        index = scene_attacks.get(aid)
        if index is not None:
            data = scene[ATTACK_DATA_OFFSET + index * ATTACK_SIZE: ATTACK_DATA_OFFSET + (index + 1) * ATTACK_SIZE]
            status = u32(data, A_STATUS)
            attack.update({
                "target": target_label(data[A_TARGET]),
                "power": data[A_POWER],
                "mp": u16(data, A_MP),
                "elements": [ELEMENT_NAMES[b] for b in range(16) if u16(data, A_ELEMENTS) & (1 << b)],
                "statuses": [n for b, n in enumerate(STATUS_NAMES) if status & (1 << b)] if status != 0xFFFFFFFF else [],
                "status_mode": status_mode(data[A_STATUS_CHANGE]),
                "calc": data[A_DAMAGE_CALC],
            })
        out.append(attack)
    return out


def ai_block(scene: bytes, slot: int) -> Optional[bytes]:
    offsets = [u16(scene, AI_OFFSET + i * 2) for i in range(3)]
    if offsets[slot] == 0xFFFF:
        return None
    block_start = AI_OFFSET + offsets[slot]
    later = sorted(AI_OFFSET + o for o in offsets if o != 0xFFFF and AI_OFFSET + o > block_start)
    return scene[block_start:later[0] if later else len(scene)]


def scripts(block: bytes) -> List[Tuple[str, int, int]]:
    """(name, start, end) of each script in an AI block."""
    starts = [u16(block, i * 2) for i in range(16)]
    used = sorted({s for s in starts if s != 0xFFFF})
    out = []
    for name, start in zip(SCRIPT_NAMES, starts):
        if start == 0xFFFF:
            continue
        following = [s for s in used if s > start]
        out.append((name, start, following[0] if following else len(block)))
    return out


def reacts(block: bytes, attack_names: Dict[int, str]) -> List[str]:
    """What the enemy answers with a named attack: its counter / death scripts that can use one
    (unnamed attacks are mostly animations, like Guard Scorpion lowering its tail)."""
    out = []
    for name, start, end in scripts(block):
        if name not in REACTION_SCRIPTS:
            continue
        ins = decode(block, start, end)
        for k, x in enumerate(ins):
            command, attack_id = perform_operands(ins, k) if x.op == PERFORM else (None, None)
            if command == CMD_ATTACK and attack_names.get(attack_id):
                out.append(REACTION_SCRIPTS[name])
                break
    return out


def attack_line(a: dict) -> str:
    if "power" not in a:
        return f"  attack {a['id']:04X} {a['name']} (no record in this scene)"
    return (f"  attack {a['id']:04X} {a['name']:<20} target={a['target']:<6} power={a['power']:<3} mp={a['mp']:<3} "
            f"calc={a['calc']:02X} {a['status_mode'] if a['statuses'] else 'status'}={','.join(a['statuses']) or '-'} "
            f"elements={','.join(a['elements']) or '-'}")


def describe(scene: bytes, slot: int, attack_names: Dict[int, str], pseudo: bool = False) -> List[str]:
    out = [attack_line(a) for a in attack_records(scene, slot, attack_names)]
    block = ai_block(scene, slot)
    if block is None:
        return out + ["  no AI scripts"]
    react = reacts(block, attack_names)
    if react:
        out.append(f"  reacts to: {', '.join(react)}")
    for name, start, end in scripts(block):
        out.append(f" -- {name} @{start:04X}")
        if pseudo:
            out.extend("  " + line for line in decompile(block, start, end, attack_names))
        else:
            out.extend(disassemble(block, start, end, attack_names))
    return out


def name_matches(query: str, name: str) -> bool:
    """Whether a scene.bin name contains the query, ignoring case and punctuation: the compendium's
    "Jenovasynthesis" and "jenova synthesis" both find "Jenova·SYNTHESIS"."""
    key = name_key(query)
    return bool(key) and key in name_key(name)


def read_attack_names(scenes: List[bytes]) -> Dict[int, str]:
    attack_names: Dict[int, str] = {}
    for scene in scenes:
        for i in range(32):
            aid = u16(scene, ATTACK_IDS_OFFSET + i * 2)
            name = decode_ff7_string(scene[ATTACK_NAMES_OFFSET + i * 32: ATTACK_NAMES_OFFSET + (i + 1) * 32])
            if aid != 0xFFFF and name:
                attack_names.setdefault(aid, name)
    return attack_names


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("name", help="Text the enemy's scene.bin name contains (any case and punctuation)")
    parser.add_argument("--scene", type=int, help="Only this scene")
    parser.add_argument("--pseudo", action="store_true", help="Print the scripts as pseudo-code")
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
    attack_names = read_attack_names(scenes)
    found = 0
    for index, scene in enumerate(scenes):
        if args.scene is not None and index != args.scene:
            continue
        for slot in range(3):
            rec = scene[ENEMY_OFFSET + slot * ENEMY_SIZE: ENEMY_OFFSET + (slot + 1) * ENEMY_SIZE]
            name = decode_ff7_string(rec[:32])
            if not name or not name_matches(args.name, name):
                continue
            found += 1
            print(f"=== scene {index}, slot {slot}, enemy id {u16(scene, slot * 2)}: {name}")
            print("\n".join(describe(scene, slot, attack_names, args.pseudo)))
    if not found:
        print(f"no scene.bin enemy named like {args.name!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
