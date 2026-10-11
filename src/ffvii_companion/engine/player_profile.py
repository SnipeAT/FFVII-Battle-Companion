"""
Player profile: party level and Sneak Glove (for the steal odds on the enemy cards) and the
Enemy Skills already learned. Stored on the server, so the desktop window and every tablet
on the LAN show the same odds and checklist.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

from ffvii_companion.compendium import ENEMY_SKILLS
from ffvii_companion.paths import get_user_data_dir

logger = logging.getLogger("ffvii-profile")

PROFILE_FILE = get_user_data_dir() / "data" / "profile.json"

DEFAULT_PROFILE: Dict[str, Any] = {
    "party_level": 15,                # Level of the party member who steals
    "sneak_glove": False,             # Sneak Glove equipped (steal LVdiff = 100)
    "learned_enemy_skills": [],       # Names from compendium.ENEMY_SKILLS, in materia order
}
PARTY_LEVEL_RANGE = (1, 99)


class ProfileValidationError(ValueError):
    pass


def validate_profile(data: Dict[str, Any], clamp: bool = False) -> Dict[str, Any]:
    """
    Validates and normalizes a (partial) profile. Unknown keys are dropped. Invalid values raise
    ProfileValidationError, unless clamp=True (loading a hand-edited file): then the party level
    is clamped, unknown skill names are dropped and other invalid values are ignored.
    """
    clean: Dict[str, Any] = {}
    for key, value in data.items():
        try:
            if key == "party_level":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ProfileValidationError("party_level must be a number")
                lo, hi = PARTY_LEVEL_RANGE
                if not lo <= value <= hi:
                    if not clamp:
                        raise ProfileValidationError(f"party_level must be between {lo} and {hi}")
                    value = min(hi, max(lo, value))
                clean[key] = int(round(value))
            elif key == "sneak_glove":
                if not isinstance(value, bool):
                    raise ProfileValidationError("sneak_glove must be true or false")
                clean[key] = value
            elif key == "learned_enemy_skills":
                if not isinstance(value, list):
                    raise ProfileValidationError("learned_enemy_skills must be a list")
                unknown = [v for v in value if v not in ENEMY_SKILLS]
                if unknown and not clamp:
                    raise ProfileValidationError(f"unknown Enemy Skills: {unknown}")
                # Hand-edited files may hold unhashable items (dicts, lists): ignore those.
                learned = {v for v in value if isinstance(v, str)}
                clean[key] = [name for name in ENEMY_SKILLS if name in learned]
        except ProfileValidationError:
            if not clamp:
                raise
            logger.warning(f"Ignoring invalid profile value for {key!r}: {value!r}")
    return clean


class PlayerProfile:
    def __init__(self, file_path: Path = PROFILE_FILE):
        self.file_path = file_path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        data = json.loads(json.dumps(DEFAULT_PROFILE))
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    data.update(validate_profile(stored, clamp=True))
            except Exception as e:
                logger.warning(f"Could not read profile {self.file_path}: {e}. Using defaults.")
        return data

    def _save(self):
        """Writes the profile atomically (temp file + replace)."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.file_path)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Validates, applies and persists a partial profile. Raises ProfileValidationError on bad input."""
        clean = validate_profile(changes)
        with self._lock:
            if clean:
                self._data.update(clean)
                self._save()
            return json.loads(json.dumps(self._data))


player_profile = PlayerProfile()
