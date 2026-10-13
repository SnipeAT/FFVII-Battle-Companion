"""tools/ai_script.py parts that need no game install: the disassembler, the decompiler and the name matching."""
import struct
import unittest

from tools.ai_script import address, decompile, disassemble, name_matches, reacts

NAMES = {0x011A: "Rifle", 0x011C: "Search Scope"}


def use(attack_id: int) -> bytes:
    """Push 20, Push <attack id>, Perform: an enemy attack."""
    return bytes([0x60, 0x20, 0x61]) + struct.pack("<H", attack_id) + bytes([0x92])


def jump(op: int, target: int) -> bytes:
    return bytes([op]) + struct.pack("<H", target)


class TestDisassemble(unittest.TestCase):
    def test_stops_at_the_padding(self):
        # Push 05, Push FFFF (0xFF bytes as an operand), End, then the scene's 0xFF padding
        # (GEMINI-20261012-05: every padding byte was printed as an OpFF? instruction)
        block = bytes([0x60, 0x05, 0x61, 0xFF, 0xFF, 0x73]) + b"\xff" * 40
        self.assertEqual(disassemble(block, 0, len(block), {}),
                         ["  0000: Push 05", "  0002: Push FFFF", "  0005: End"])

    def test_names_only_enemy_attacks(self):
        # Command 0x24 with id 0 pauses for a text; it was printed as "attack 0000 Cure"
        block = bytes([0x60, 0x24, 0x60, 0x00, 0x92]) + use(0x011A) + bytes([0x73])
        lines = disassemble(block, 0, len(block), {0: "Cure", **NAMES})
        self.assertEqual(lines[2], "  0004: Perform  pause for the text")
        self.assertEqual(lines[5], "  000A: Perform  attack 011A Rifle")


class TestDecompile(unittest.TestCase):
    def pseudo(self, block: bytes, names=NAMES):
        return decompile(block, 0, len(block), names)

    def test_if_else(self):
        block = (bytes([0x02, 0x00, 0x00]) + jump(0x70, 0x0F)  # if Var0000:
                 + use(0x011C) + jump(0x72, 0x15)               #   use Search Scope
                 + use(0x011A) + bytes([0x73]))                 # else: use Rifle
        self.assertEqual(self.pseudo(block), ["if Var0000:", "  use Search Scope", "else:", "  use Rifle"])

    def test_switch_with_several_values_and_a_default(self):
        block = (bytes([0x02, 0x60, 0x00])                       # switch Var0060
                 + bytes([0x60, 0x00]) + jump(0x71, 0x0B) + jump(0x72, 0x10)   # case 0 -> body
                 + bytes([0x60, 0x02]) + jump(0x71, 0x1C)                      # case 2, else default
                 + use(0x011C) + jump(0x72, 0x25) + jump(0x72, 0x1C)           # body, break, dead jump
                 + use(0x011A) + jump(0x72, 0x25)                              # default, break
                 + bytes([0x91, 0x73]))                                        # pop the value, end
        self.assertEqual(self.pseudo(block), ["switch Var0060:", "  case 0, 2:", "    use Search Scope",
                                              "  default:", "    use Rifle"])

    def test_case_without_break_falls_through(self):
        block = (bytes([0x02, 0x60, 0x00, 0x60, 0x00]) + jump(0x71, 0x11)      # case 0
                 + use(0x011C) + jump(0x72, 0x16)                              # no break
                 + bytes([0x60, 0x01]) + jump(0x71, 0x1F)                      # case 1
                 + use(0x011A) + jump(0x72, 0x1F) + bytes([0x91, 0x73]))
        self.assertEqual(self.pseudo(block), ["switch Var0060:", "  case 0:", "    use Search Scope",
                                              "    fall through to case 1", "  case 1:", "    use Rifle"])

    def test_while_loop(self):
        block = (bytes([0x02, 0x00, 0x00, 0x60, 0x03, 0x45]) + jump(0x70, 0x16)   # while Var0000 < 3:
                 + bytes([0x12, 0x00, 0x00, 0x02, 0x00, 0x00, 0x60, 0x01, 0x30, 0x90])  # Var0000 += 1
                 + jump(0x72, 0x00) + bytes([0x73]))
        self.assertEqual(self.pseudo(block), ["while Var0000 < 3:", "  Var0000 += 1"])

    def test_unstructured_jump_keeps_a_label(self):
        block = use(0x011A) + jump(0x72, 0x00) + bytes([0x73])
        self.assertEqual(self.pseudo(block), ["@0000:", "use Rifle", "goto @0000"])

    def test_text_and_commands(self):
        # [name] "Hi..." in battle text: 0xEA + 2 bytes a name, 0xB2 / 0xB3 quotes, 0xA9 the ellipsis
        text = bytes([0xEA, 0x00, 0x00, 0x00, 0xB2, 0x28, 0x49, 0xA9, 0xB3, 0xFF])
        block = (bytes([0x93]) + text + bytes([0x60, 0x24, 0x60, 0x00, 0x92])   # pause for the text
                 + bytes([0x60, 0x22, 0x60, 0x0F, 0x92, 0x73]))                  # run script 15
        self.assertEqual(self.pseudo(block), ['show "[name] “Hi…”"', "pause for the text",
                                              "run Custom 8"])

    def test_unit_fields_and_targets(self):
        # Targets = random_one(lowest(Opponents.HP)); Self.MagicDefense = 384; if not Targets.Death: ...
        block = (bytes([0x12, 0x70, 0x20, 0x02, 0xA0, 0x20, 0x03, 0x60, 0x41, 0x80, 0x85, 0x82, 0x90])
                 + bytes([0x12, 0x60, 0x20, 0x12, 0x10, 0x41, 0x80, 0x61, 0x80, 0x01, 0x90])
                 + bytes([0x02, 0x70, 0x20, 0x00, 0x00, 0x40, 0x80, 0x52]) + jump(0x70, 0x29)
                 + use(0x011A) + bytes([0x73]))
        self.assertEqual(self.pseudo(block), ["Targets = random_one(lowest(Opponents.HP))",
                                              "Self.MagicDefense = 384", "if not Targets.Death:", "  use Rifle"])

    def test_status_addresses(self):
        self.assertEqual(address(0x401F, 0x00), "Imprisoned")   # read as a bit: status 31
        self.assertEqual(address(0x4000, 0x00), "Death")
        self.assertEqual(address(0x4000, 0x03), "Statuses")     # read as a dword: every status
        self.assertEqual(address(0x0060), "Var0060")
        self.assertEqual(address(0x4024), "[4024]")


class TestReacts(unittest.TestCase):
    def block(self) -> bytes:
        # 16 script offsets: Main at 0x20 (End), Counter-Physical at 0x21 (an attack)
        starts = [0xFFFF] * 16
        starts[1], starts[4] = 0x20, 0x21
        return struct.pack("<16H", *starts) + bytes([0x73]) + use(0x011A) + bytes([0x73])

    def test_counter_with_a_named_attack(self):
        self.assertEqual(reacts(self.block(), NAMES), ["physical"])

    def test_unnamed_attacks_are_not_counters(self):
        # Unnamed attacks are animations (Guard Scorpion lowering its tail)
        self.assertEqual(reacts(self.block(), {}), [])


class TestNameMatches(unittest.TestCase):
    def test_case_and_punctuation_are_ignored(self):
        # The compendium's names drop the middle dot of scene.bin's (GEMINI-20261012-06)
        for query in ("Jenovasynthesis", "jenova synthesis", "Jenova·SYNTHESIS", "synth"):
            self.assertTrue(name_matches(query, "Jenova·SYNTHESIS"), query)
        self.assertTrue(name_matches("Schizoright", "Schizo(right)"))
        self.assertFalse(name_matches("Jenovalife", "Jenova·SYNTHESIS"))

    def test_punctuation_alone_matches_nothing(self):
        self.assertFalse(name_matches("·", "Jenova·LIFE"))


if __name__ == "__main__":
    unittest.main()
