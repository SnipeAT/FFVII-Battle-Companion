import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from ffvii_companion.paths import get_user_data_dir

logger = logging.getLogger("ffvii-calibration")

# The user's settings: values saved from the dashboard, merged over DEFAULT_CONFIG on load. The
# defaults live only here in code; nothing is bundled, so a developer's saved settings never ship.
CALIBRATION_FILE = get_user_data_dir() / "data" / "calibration.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "auto_window", # "auto_window", "fullscreen_4k", "custom_roi"
    "monitor_index": 2,    # Monitor captured outside auto_window mode, and by snapshots / reports when the game window is not found (mss numbering, 1-based)
    "idle_fps": 0.5,             # Out-of-battle / field exploration sample rate (~2.0s interval)
    "active_fps": 1.5,           # In-battle / hunting / new enemy sample rate (~0.67s interval)
    "settled_fps": 0.35,         # In-battle settled sample rate once enemies are registered (~2.8s interval)
    "confidence_threshold": 0.45, # Minimum OCR recognition score for a text line to be used
    "det_limit_side_len": 640,   # Max DBNet detection tensor side length (OCR frames are at most 1280 wide)
    "idle_timeout_seconds": 6.0, # Time without battle text before reverting to idle
    "roi": {
        # Normalized percentages relative to game window or screen
        # Default captures centered enemy target box (rows 1425-1815, cols 768-3072 in 4K)
        "top_pct": 0.66,
        "left_pct": 0.20,
        "width_pct": 0.60,
        "height_pct": 0.18
    },
    "target_menu_preset": "center_target_box",
    "auto_add_scene_partners": False, # Add partners that every matching formation contains when one enemy is targeted
    "ocr_threads": 1,            # ONNX Runtime threads per OCR session (1 = lowest CPU impact)
    "text_gate": True,           # Skip OCR when the text in the capture region has not changed
    "eco_mode": True,            # Run OCR on efficiency cores (EcoQoS) outside the hunting phase
    "allowed_hosts": [],         # Host names the server answers besides IP addresses, localhost and the PC's name ("*": any); settings file only
}

PRESETS = {
    "full_game_window": {
        "name": "Full Game Window / Full Screen",
        "description": "Scans the entire game viewport (highest CPU cost; use only for troubleshooting)",
        "top_pct": 0.0,
        "left_pct": 0.0,
        "width_pct": 1.0,
        "height_pct": 1.0
    },
    "bottom_half_battle_ui": {
        "name": "Bottom Half (Battle Commands & Target Menu)",
        "description": "Focuses on the bottom 45% where ATB menus & targets appear",
        "top_pct": 0.55,
        "left_pct": 0.0,
        "width_pct": 1.0,
        "height_pct": 0.45
    },
    "center_target_box": {
        "name": "Center Target Box (Modern / FFNx)",
        "description": "Targets centered enemy name above command menus (~rows 1425-1815, cols 768-3072 in 4K)",
        "top_pct": 0.66,
        "left_pct": 0.20,
        "width_pct": 0.60,
        "height_pct": 0.18
    },
}

VALID_MODES = {"auto_window", "fullscreen_4k", "custom_roi"}

# (minimum, maximum) for numeric settings; values outside the range are rejected by
# validate_settings() and clamped when loading a hand-edited file.
NUMERIC_RANGES = {
    "idle_fps": (0.2, 5.0),
    "active_fps": (0.5, 5.0),
    "settled_fps": (0.2, 5.0),
    "confidence_threshold": (0.3, 0.95),
    "det_limit_side_len": (320, 1920),
    "idle_timeout_seconds": (1.0, 120.0),
    "monitor_index": (1, 16),
    "ocr_threads": (1, 4),
}
INTEGER_KEYS = {"det_limit_side_len", "monitor_index", "ocr_threads"}
BOOLEAN_KEYS = {"auto_add_scene_partners", "text_gate", "eco_mode"}
ROI_KEYS = ("top_pct", "left_pct", "width_pct", "height_pct")
# Settings that only a hand edit of the file can change, not the API: allowed_hosts decides which
# requests the server answers at all, so a request must not be able to widen it.
FILE_ONLY_KEYS = {"allowed_hosts"}
MAX_ALLOWED_HOSTS = 32


class ConfigValidationError(ValueError):
    pass


def _coerce_number(key: str, value: Any, clamp: bool) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"{key} must be a number")
    lo, hi = NUMERIC_RANGES[key]
    if not (lo <= value <= hi):
        if not clamp:
            raise ConfigValidationError(f"{key} must be between {lo} and {hi}")
        value = min(hi, max(lo, value))
    return int(round(value)) if key in INTEGER_KEYS else float(value)


def _coerce_roi(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        raise ConfigValidationError("roi must be an object")
    roi = {}
    for key in ROI_KEYS:
        v = value.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigValidationError(f"roi.{key} must be a number")
        roi[key] = float(v)
    if not (0.0 <= roi["top_pct"] < 1.0 and 0.0 <= roi["left_pct"] < 1.0):
        raise ConfigValidationError("roi top/left must be within [0, 1)")
    if not (0.0 < roi["width_pct"] <= 1.0 and 0.0 < roi["height_pct"] <= 1.0):
        raise ConfigValidationError("roi width/height must be within (0, 1]")
    if roi["top_pct"] + roi["height_pct"] > 1.0001 or roi["left_pct"] + roi["width_pct"] > 1.0001:
        raise ConfigValidationError("roi must lie inside the window")
    return roi


def _coerce_hosts(value: Any) -> list:
    if not isinstance(value, list) or len(value) > MAX_ALLOWED_HOSTS:
        raise ConfigValidationError(f"allowed_hosts must be a list of at most {MAX_ALLOWED_HOSTS} host names")
    hosts = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 253:
            raise ConfigValidationError("allowed_hosts entries must be host names")
        host = item.strip().lower().rstrip(".")
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def validate_settings(settings: Dict[str, Any], clamp: bool = False) -> Dict[str, Any]:
    """
    Validates and normalizes a (partial) settings dict.
    Unknown keys are dropped. Invalid values raise ConfigValidationError, unless
    clamp=True, in which case out-of-range numbers are clamped and other invalid
    values are dropped (used when loading a hand-edited calibration file).
    """
    clean: Dict[str, Any] = {}
    for key, value in settings.items():
        if key not in DEFAULT_CONFIG:
            continue
        try:
            if key in NUMERIC_RANGES:
                if key == "monitor_index" and value is None:
                    clean[key] = None
                else:
                    clean[key] = _coerce_number(key, value, clamp)
            elif key in BOOLEAN_KEYS:
                if not isinstance(value, bool):
                    raise ConfigValidationError(f"{key} must be true or false")
                clean[key] = value
            elif key == "mode":
                if value not in VALID_MODES:
                    raise ConfigValidationError(f"mode must be one of {sorted(VALID_MODES)}")
                clean[key] = value
            elif key == "roi":
                clean[key] = _coerce_roi(value)
            elif key == "allowed_hosts":
                clean[key] = _coerce_hosts(value)
            elif key == "target_menu_preset":
                if not isinstance(value, str) or len(value) > 64:
                    raise ConfigValidationError("target_menu_preset must be a short string")
                clean[key] = value
        except ConfigValidationError:
            if not clamp:
                raise
            logger.warning(f"Ignoring invalid calibration value for {key!r}: {value!r}")
    return clean


class CalibrationManager:
    def __init__(self, file_path: Path = CALIBRATION_FILE):
        self.file_path = file_path
        self._lock = threading.Lock()
        self.config: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy so the defaults stay pristine
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Merge defaults with validated (clamped) file values
                cfg.update(validate_settings(data, clamp=True))
            except Exception as e:
                logger.warning(f"Could not read calibration file {self.file_path}: {e}. Using defaults.")
        return cfg

    def save(self):
        """Writes the configuration atomically (temp file + replace) to avoid torn files."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)
        os.replace(tmp, self.file_path)

    def get_config(self) -> Dict[str, Any]:
        return self.config

    def update_config(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validates, applies and persists settings. Raises ConfigValidationError on bad input."""
        file_only = sorted(FILE_ONLY_KEYS.intersection(new_settings))
        if file_only:
            raise ConfigValidationError(f"{', '.join(file_only)} can only be changed in the settings file")
        filtered = validate_settings(new_settings, clamp=False)
        with self._lock:
            if filtered:
                self.config.update(filtered)
                self.save()
            return self.config

    def apply_preset(self, preset_key: str) -> Optional[Dict[str, Any]]:
        if preset_key in PRESETS:
            preset = PRESETS[preset_key]
            with self._lock:
                self.config["target_menu_preset"] = preset_key
                self.config["roi"] = {
                    "top_pct": preset["top_pct"],
                    "left_pct": preset["left_pct"],
                    "width_pct": preset["width_pct"],
                    "height_pct": preset["height_pct"]
                }
                self.save()
                return self.config
        return None

calibration_manager = CalibrationManager()
