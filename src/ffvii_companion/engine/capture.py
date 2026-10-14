"""
mss screen grabber: a screen region (virtual-screen coordinates) as a BGRA array, and the monitor
captured when the region is not the game window's (modes other than auto_window, snapshots and
reports without the window).
"""
from typing import Dict, Optional
import mss
import numpy as np


class ScreenCaptureEngine:
    def __init__(self, monitor_index: Optional[int] = None):
        """
        mss screen grabber with multi-monitor awareness. monitor_index (mss numbering, 1-based)
        is the monitor captured instead of the game window (modes other than auto_window, and
        snapshots / reports when the window is not found); when it is None or out of range,
        the first 4K display (or monitor 1) is used.
        """
        self.sct = mss.MSS()
        self.monitors = self.sct.monitors
        self.select_monitor(monitor_index)

    def select_monitor(self, monitor_index: Optional[int]):
        """Captures monitor_index from now on (the setting as saved; `monitor_index` is the
        monitor actually used, after the fallback described in __init__)."""
        self.requested_monitor = monitor_index
        if monitor_index is not None and 1 <= monitor_index < len(self.monitors):
            self.monitor_index = monitor_index
        else:
            self.monitor_index = self._find_primary_4k_monitor()

    def _find_primary_4k_monitor(self) -> int:
        """Finds the 4K display or defaults to monitor 1."""
        candidate = 1 if len(self.monitors) > 1 else 0
        for idx, m in enumerate(self.monitors):
            if idx == 0:
                continue
            if m["width"] >= 3840 or m["height"] >= 2160:
                return idx
        return candidate

    def grab(self, bbox: Dict[str, int]) -> np.ndarray:
        """Grabs a screen region (virtual-screen coordinates) as a BGRA array."""
        shot = self.sct.grab(bbox)
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape((shot.height, shot.width, 4))
