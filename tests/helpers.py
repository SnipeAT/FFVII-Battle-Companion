"""Shared helpers for the test suite (real fixtures, synthetic frames, fake capture inputs)."""
import json
from pathlib import Path
from typing import Dict

import cv2
import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "real"


def load_manifest() -> Dict[str, dict]:
    with open(FIXTURES_DIR / "manifest.json", "r", encoding="utf-8") as f:
        return json.load(f)["frames"]


def load_fixture_bgr(name: str) -> np.ndarray:
    img = cv2.imread(str(FIXTURES_DIR / name), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(FIXTURES_DIR / name)
    return img


def load_fixture_bgra(name: str) -> np.ndarray:
    """Real recorded capture region as BGRA, the format the capture loop receives from mss."""
    return cv2.cvtColor(load_fixture_bgr(name), cv2.COLOR_BGR2BGRA)


def make_config(**overrides) -> dict:
    """A capture configuration independent of the user's calibration file."""
    from ffvii_companion.engine.calibration import DEFAULT_CONFIG
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["auto_add_scene_partners"] = False
    cfg.update(overrides)
    return cfg
