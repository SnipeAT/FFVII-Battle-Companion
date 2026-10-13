"""tools/ai_script.py parts that need no game install: the disassembler and the name matching."""
import unittest

from tools.ai_script import disassemble, name_matches


class TestDisassemble(unittest.TestCase):
    def test_stops_at_the_padding(self):
        # Push 05, Push FFFF (0xFF bytes as an operand), End, then the scene's 0xFF padding
        # (GEMINI-20261012-05: every padding byte was printed as an OpFF? instruction)
        block = bytes([0x60, 0x05, 0x61, 0xFF, 0xFF, 0x73]) + b"\xff" * 40
        self.assertEqual(disassemble(block, 0, len(block), {}),
                         ["  0000: Push 05", "  0002: Push FFFF", "  0005: End"])


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
