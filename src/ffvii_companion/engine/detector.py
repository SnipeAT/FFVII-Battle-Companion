"""
Turns OCR output into enemies. BattleDetector owns the OCR engine (engine/ocr_engine.py, loaded on
first use) and parses its lines:

- parse_enemy_line(): drops stop words (commands, HUD labels, party names, overlay text) and row
  designations, and splits off an instance letter ("Devil Ride B": at least two Devil Rides). A
  letter counts only up to the most of that enemy one formation holds (compendium.max_count()),
  so a misread name cannot multiply an enemy.
- identify_enemies_from_lines(): fuzzy-matches the candidates against the compendium, one result
  per enemy with its highest count.
- is_combat_hud_active(): whether the battle HUD is on screen, telling it apart from field menus
  that show the same words (its docstring has the rules).

The constructor's defaults are the settings' defaults (DEFAULT_CONFIG in engine/calibration.py).
"""
import os
# Keep numerical libraries single-threaded; OCR parallelism is controlled explicitly
# through the ONNX Runtime session options in engine/ocr_engine.py.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import re
import functools
from typing import List, Dict, Any, Optional, Tuple
import cv2
cv2.setNumThreads(1)
import numpy as np
from ffvii_companion.compendium import compendium
from ffvii_companion.engine.calibration import DEFAULT_CONFIG
from ffvii_companion.engine.ocr_engine import OCREngine, OCRResult

OVERLAY_KEYWORDS = ("FPS", "CPU", "GPU")

# Largest OCR input: bigger frames are scaled down to fit, for a 2.5x-3x speedup that keeps the
# game's font readable
OCR_MAX_SIZE = (1280, 720)


def prepare_ocr_frame(frame: np.ndarray) -> np.ndarray:
    """The OCR input of a captured frame: scaled down to fit OCR_MAX_SIZE, BGRA converted to BGR.
    A frame that needs neither is returned as it is."""
    h, w = frame.shape[:2]
    max_w, max_h = OCR_MAX_SIZE
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame

BATTLE_STOP_WORDS = {
    # Battle Menu Commands
    "ATTACK", "MAGIC", "SUMMON", "ITEM", "MUG", "STEAL", "SENSE", "E.SKILL", "ENEMY SKILL",
    "MANIP.", "MANIP", "MANIPULATE", "THROW", "COIN", "MORPH", "DEATHBLOW", "D.BLOW", "DOUBLE CUT", "2X-CUT",
    "4X-CUT", "FLASH", "W-MAGIC", "W-SUMMON", "W-ITEM", "DEFEND", "CHANGE", "LIMIT", "LIMIT BREAK",
    "MIME", "SLASH-ALL", "SLASH ALL",
    # HUD / ATB Labels
    "NAME", "HP", "MP", "TIME", "WAIT", "BARRIER", "WAIT/LIMIT", "WAIT / LIMIT", "ATB", "TARGET", "CLOSE",
    # Party Members
    "CLOUD", "BARRET", "TIFA", "AERIS", "AERITH", "RED XIII", "NANAKI", "YUFFIE",
    "CAIT SITH", "VINCENT", "CID",
    # Hardware / Overlay Indicators
    "FPS", "CPU", "GPU", "MS", "D3D", "DX11", "DX12", "VRAM",
    # Spells & Materia Magic
    "FIRE", "FIRA", "FIRAGA", "FIRE1", "FIRE2", "FIRE3",
    "BLIZZARD", "BLIZZARA", "BLIZZAGA", "ICE", "ICE1", "ICE2", "ICE3",
    "THUNDER", "THUNDARA", "THUNDAGA", "BOLT", "BOLT1", "BOLT2", "BOLT3",
    "CURE", "CURA", "CURAGA", "CURE1", "CURE2", "CURE3", "REGEN",
    "POISON", "BIO", "BIORA", "BIOGA", "BIO1", "BIO2", "BIO3",
    "EARTH", "QUAKE", "QUAKE2", "QUAKE3",
    "HEAL", "ESUNA", "RESIST",
    "REVIVE", "LIFE", "LIFE2", "FULL-LIFE", "FULL LIFE",
    "SEAL", "SLEEP", "SILENCE",
    "MYSTIFY", "CONFUSE", "CONFUSION", "BERSERK",
    "TRANSFORM", "MINI", "TOAD", "FROG",
    "TIME", "HASTE", "SLOW", "STOP",
    "BARRIER", "MBARRIER", "REFLECT", "WALL",
    "DESTRUCT", "DEBARRIER", "DESPELL", "DEATH",
    "ESCAPE", "EXIT", "REMOVE", "ALL",
    "CONTAIN", "FREEZE", "BREAK", "TORNADO", "FLARE",
    "FULL CURE", "SHIELD", "ULTIMA", "COMET", "COMET2",
    "GRAVITY", "DEMI", "DEMI1", "DEMI2", "DEMI3",
    # Enemy Skills
    "MATRA MAGIC", "L4 SUICIDE", "BETA", "WHITE WIND", "AQUALUNG", "FROG SONG",
    "FLAME THROWER", "BIG GUARD", "LASER", "DEATH FORCE", "DRAGON FORCE",
    "SHADOW FLARE", "TRINE", "MAGIC BREATH", "PANDORA'S BOX", "GOBLIN PUNCH",
    "CHOCORUCKLE", "MAGIC HAMMER", "BAD BREATH", "ROULETTE", "????", "DEATH SENTENCE", "???",
    # Summons
    "CHOCO/MOG", "SHIVA", "IFRIT", "RAMUH", "TITAN", "ODIN", "LEVIATHAN",
    "BAHAMUT", "KJATA", "ALEXANDER", "NEO BAHAMUT", "PHOENIX", "HADES",
    "BAHAMUT ZERO", "TYPHON", "KNIGHTS OF ROUND",
    # Common Consumables / Items (excluding names shared with enemies like Grenade, Right Arm)
    "POTION", "HI-POTION", "HIPOTION", "X-POTION", "ETHER", "TURBO ETHER",
    "ELIXIR", "MEGALIXIR", "PHOENIX DOWN", "ANTIDOTE", "EYE DROPS", "SOFT",
    "MAIDEN'S KISS", "CORNUCOPIA", "ECHO SCREEN", "HYPER", "TRANQUILIZER",
    "REMEDY", "SMOKE BOMB", "SPEED DRINK", "HERO DRINK", "VACCINE",
    "HOURGLASS", "WARP", "TENT"
}

# Enemies named like items (compendium names). The battle Item menu shows them in the capture
# region, so they are not stop words; the capture loop ignores one that cannot join the battle.
ITEM_NAMED_ENEMIES = {"Grenade", "Right Arm"}

RE_LEADING_MODIFIERS = re.compile(r"^[+*>\-#\.\s]+")
RE_DIGITS_AND_SYMBOLS = re.compile(r"^[0-9\s/%\.:\-,+*#]+$")
RE_ROW_DESIGNATION = re.compile(r"[\(\[]?(?:back|front)(?: ?row)?[\)\]]?", re.IGNORECASE)
RE_INSTANCE_LETTER = re.compile(r"(?: |(?<=[a-zA-Z]))([A-Ha-h])$")

# Battle HUD labels and commands. They count only as a whole line or followed by a value ("HP",
# "HP 9999"): field menus use the same words inside longer text ("Sneak Attack", "HP Absorb").
COMBAT_HUD_TOKENS = {
    "HP", "MP", "BARRIER", "WAIT/LIMIT", "WAIT / LIMIT", "WAIT", "LIMIT", "ATB", "NAME",
    "ATTACK", "MAGIC", "SUMMON", "ITEM", "E.SKILL", "MUG", "STEAL", "MANIP.", "MIME"
}
RE_TRAILING_VALUE = re.compile(r"\s*\d[\d\s/]*$")       # " 9999", " 3845/3939", " 506/"
# Column headers of the battle window, on screen for the whole battle (in every capture preset)
HUD_HEADERS = {"HP", "MP"}
PARTY_MEMBERS = {
    "CLOUD", "BARRET", "TIFA", "AERIS", "AERITH", "RED XIII", "NANAKI",
    "YUFFIE", "CAIT SITH", "VINCENT", "CID"
}
# Whole lines that only the field menus show (main menu, status, materia screens). Their HP / MP
# labels and command words (Item, Magic, Limit) would otherwise read as the battle HUD.
FIELD_MENU_LINES = {
    "LIMIT LEVEL", "NEXT LEVEL", "GIL", "VITALITY", "MAXHP", "MAXMP",
    "MATERIA", "EQUIP", "STATUS", "ORDER", "CONFIG", "PHS", "SAVE", "QUIT",
}
RE_LEVEL_LINE = re.compile(r"^LV\.?\s*(\d*)$")         # "LV", "Lv 61" (field menus), "Lv3" (battle limit menu)
RE_CURRENT_OF_MAX = re.compile(r"\d+\s*/\s*\d*$")       # "3845/3939", "506/" (field HP / MP)
MAX_LIMIT_LEVEL = 4                                     # A higher level is a character level: field menus only


class BattleDetector:
    def __init__(self, num_threads: int = DEFAULT_CONFIG["ocr_threads"],
                 det_limit_side_len: int = DEFAULT_CONFIG["det_limit_side_len"],
                 confidence_threshold: float = DEFAULT_CONFIG["confidence_threshold"]):
        """
        Initializes detector metadata. Defers ONNX model instantiation to first use
        (keeps cold application launch fast).
        The defaults are the settings' defaults (DEFAULT_CONFIG in engine/calibration.py), so a
        detector built without arguments reads text like the app does.
        Caps inference to num_threads (default: 1; ONNX Runtime thread spinning is
        disabled) so OCR never saturates cores on high-core CPUs (e.g. Intel Core i9-13900K).
        det_limit_side_len caps the longest side fed to the DBNet detector (lower = less CPU).
        confidence_threshold drops recognized lines below this recognition score.
        """
        self.num_threads = num_threads
        self.det_limit_side_len = det_limit_side_len
        self.confidence_threshold = confidence_threshold
        self._ocr = OCREngine(threads=num_threads, det_limit_side_len=det_limit_side_len)

        self.last_ocr_timing: Dict[str, float] = {
            "det_ms": 0.0,
            "rec_ms": 0.0,
            "total_ms": 0.0,
            "boxes": 0,
            "recognized": 0,
            "reused": 0,
        }

    @property
    def ocr_loaded(self) -> bool:
        return self._ocr.loaded

    def set_det_limit_side_len(self, limit: int):
        """Updates maximum tensor side length dynamically for text detection."""
        self.det_limit_side_len = limit
        self._ocr.set_det_limit_side_len(limit)

    def set_num_threads(self, threads: int):
        """Updates the OCR inference thread count (sessions are recreated on next use)."""
        self.num_threads = threads
        self._ocr.set_threads(threads)

    @staticmethod
    @functools.lru_cache(maxsize=512)
    def parse_enemy_line(raw_text: str) -> Tuple[Optional[str], Optional[str], int]:
        """
        Parses an OCR line and extracts:
        1. Cleaned candidate name (e.g. 'Nerosuferoth')
        2. Instance letter if present (e.g. 'B')
        3. Minimum enemy count (e.g. 2 for 'B', 3 for 'C', 1 for 'A' or none)
        """
        text = " ".join(raw_text.split())
        if not text or len(text) < 2:
            return None, None, 1

        # Strip leading modifier symbols (+, *, >, -, #) common in modded FFVII materia / spell lists
        cleaned = RE_LEADING_MODIFIERS.sub("", text).strip()
        if not cleaned or len(cleaned) < 2:
            return None, None, 1

        # Filter out numbers and symbols (e.g. HP/MP values, timers, mod tags)
        if cleaned.isdigit() or RE_DIGITS_AND_SYMBOLS.match(cleaned):
            return None, None, 1

        # Strip row designations: (Back row), (Front row), [Back row], Back row, etc.
        cleaned = " ".join(RE_ROW_DESIGNATION.sub("", cleaned).split())

        # Whole-line commands and HUD labels are rejected before any letter stripping
        # (otherwise "Mime" would be read as "Mim" + instance letter E)
        if cleaned.upper() in BATTLE_STOP_WORDS:
            return None, None, 1

        # Check for trailing instance letter (e.g. "Nerosuferoth B", "Sweeper A")
        # In FFVII, instance letters are single uppercase letters A-H (max 8 enemies in formation)
        instance_letter = None
        min_count = 1
        # A whole enemy name keeps its last letter, also when OCR changed its punctuation
        # ("Grosspanzer-Big" is not "Grosspanzer-Bi" + G, i.e. seven of them)
        exact_name = compendium.get_by_normalized_name(cleaned) is not None
        letter_match = RE_INSTANCE_LETTER.search(cleaned)
        if letter_match and not exact_name:
            stem = cleaned[:letter_match.start()].strip()
            spaced = cleaned[letter_match.start()].isspace()
            letter = letter_match.group(1).upper()
            letter_count = ord(letter) - ord('A') + 1
            stem_enemy = compendium.get_by_name(stem) or compendium.match_enemy(stem)
            # The formations say how many of an enemy a battle can hold: a letter that claims
            # more is not an instance letter ("Jenova·BlRTH" is no H, eight Jenovas)
            most = compendium.max_count(stem_enemy["name"]) if stem_enemy else None
            possible = most is None or letter_count <= most
            # A letter glued to the name ("SweeperA", OCR dropped the space) only counts as an
            # instance letter when it is a capital, as the game shows them (a glued lowercase a-h
            # is the end of a misread name, "Grosspanzer Blg"), the remaining stem is itself
            # a known enemy name, and the count is possible. Otherwise the letter is part of the
            # name, misread somewhere else.
            glued_ok = letter_match.group(1).isupper() and stem_enemy is not None and possible
            if spaced or glued_ok:
                # A separate letter beyond the formations is a misread letter: it is dropped, and
                # the count stays within what a battle can hold
                instance_letter = letter if possible else None
                min_count = letter_count if possible else most
                cleaned = stem
        # A trailing digit stays: it can belong to the name ("Unknown 2"), and match_enemy tries
        # the name with and without it

        cleaned_upper = cleaned.upper()

        # Check against battle stop words and overlays
        if cleaned_upper in BATTLE_STOP_WORDS:
            # Special case: 'MP' is a common HUD gauge label, but 'MP A' or 'MP B' is the Shinra soldier
            if cleaned_upper == "MP" and instance_letter is not None:
                pass
            else:
                return None, None, 1
        if any(sw in cleaned_upper for sw in OVERLAY_KEYWORDS):
            return None, None, 1

        return cleaned, instance_letter, min_count

    @staticmethod
    def is_combat_hud_active(lines: List[str], in_battle: bool = False) -> bool:
        """
        Determines whether the battle HUD is actively visible on screen.
        Checks for battle UI labels and commands (COMBAT_HUD_TOKENS: HP, MP, BARRIER, Attack, ...)
        and party member names (Cloud, Cid, Nanaki, etc.).
        A HUD token counts only as a whole line or followed by a value, as OCR groups numbers with
        labels ('HP 9999'): field menus show the same words inside longer text (the materia
        screen's "Sneak Attack" and "HP Absorb"), and so does battle help text ("Restores HP by
        500"), which never shows without the HP / MP headers. Party names count as words of a line
        ('Cloud 3500').
        Returns True during combat, active commands, and attack animations.
        Returns False when on the field map, world map, field menus, or post-battle victory summary.
        Field menus also show HP / MP and party names: a field-only line (FIELD_MENU_LINES, a
        character level such as "Lv 61") rules the HUD out, and a current/max value ("506/")
        rules out the weak evidence of one HUD word plus a party name (the item menu's "MP" +
        "Cid"). A current/max value never vetoes two HUD words: the battle HUD's HP and MP headers
        are always on screen.
        A level that can be a limit level ("Lv3", or "LV" with its number read apart) is also the
        battle limit menu's header: it rules the HUD out only next to a current/max value (field
        menus show the level beside HP and MP as current/max) and only while in_battle is False
        (idle, no enemy registered: field menus cannot open during a battle).
        While in_battle is False the HUD also needs its HP or MP header (HUD_HEADERS): command
        words without it are the field main menu (its closing animation reads "Item", "Magic"). In
        a battle, commands without the headers still count, so a frame whose headers OCR missed
        does not end the battle.
        """
        hud_matches = 0
        hud_header = False
        party_matches = 0
        current_of_max = False
        limit_level = False
        for line in lines:
            cleaned = line.strip().upper()
            if not cleaned:
                continue
            if cleaned in FIELD_MENU_LINES:
                return False
            level = RE_LEVEL_LINE.match(cleaned)
            if level:
                if level.group(1) and int(level.group(1)) > MAX_LIMIT_LEVEL:
                    return False
                limit_level = True
                continue
            if RE_CURRENT_OF_MAX.search(cleaned):
                current_of_max = True
            label = RE_TRAILING_VALUE.sub("", cleaned)
            if label in COMBAT_HUD_TOKENS:
                hud_matches += 1
                hud_header = hud_header or label in HUD_HEADERS
            elif cleaned in PARTY_MEMBERS or any(w in PARTY_MEMBERS for w in cleaned.split()):
                party_matches += 1

        if not in_battle and (not hud_header or (limit_level and current_of_max)):
            return False
        return hud_matches >= 2 or (hud_matches >= 1 and party_matches >= 1 and not current_of_max)

    def detect_text_lines(self, image_np: np.ndarray, use_cache: bool = True) -> List[str]:
        """
        Runs OCR on the input frame or ROI and returns list of recognized text strings.
        Also records granular stage timings (detection, recognition) and box statistics.
        Game HUD/battle text is strictly horizontal, so no angle classification runs.
        The frame goes through prepare_ocr_frame() first (nothing to do for one it already
        prepared, as the capture loop's). use_cache=True reuses recognized text for unchanged
        text boxes.
        """
        image_np = prepare_ocr_frame(image_np)

        result: OCRResult = self._ocr.run(image_np, use_cache=use_cache)
        self.last_ocr_timing = {
            "det_ms": round(result.det_ms, 2),
            "rec_ms": round(result.rec_ms, 2),
            "total_ms": round(result.total_ms, 2),
            "boxes": result.boxes,
            "recognized": result.recognized,
            "reused": result.reused,
        }

        threshold = self.confidence_threshold
        return [line.text.strip() for line in result.lines
                if line.score >= threshold and len(line.text.strip()) > 1]

    def identify_enemies_from_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        """
        Matches a pre-extracted list of text lines against the FFVII compendium.
        Avoids re-running OCR when lines have already been detected.
        Utilizes O(1) hash map for deduplication and instance count tracking.
        """
        matched_enemies: List[Dict[str, Any]] = []
        id_to_enemy: Dict[str, Dict[str, Any]] = {}

        for line in lines:
            candidate, inst_letter, count = self.parse_enemy_line(line)
            if not candidate:
                continue

            match = compendium.match_enemy(candidate)
            if match:
                m_id = match["id"]
                if m_id not in id_to_enemy:
                    match_copy = dict(match)
                    match_copy["instance_letter"] = inst_letter
                    match_copy["count"] = count
                    matched_enemies.append(match_copy)
                    id_to_enemy[m_id] = match_copy
                else:
                    # The highest count wins, with its letter; at the same count, a letter beats
                    # a line read without one ("Devil Ride" and "Devil Ride A")
                    existing = id_to_enemy[m_id]
                    if count > existing.get("count", 1) or (
                            count == existing.get("count", 1) and existing.get("instance_letter") is None):
                        existing["count"] = count
                        existing["instance_letter"] = inst_letter

        return matched_enemies
