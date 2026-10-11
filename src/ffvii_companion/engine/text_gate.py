"""
Text-change gate for the capture loop.

OCR is by far the most expensive step of a capture frame, yet most frames show
exactly the same battle text as the previous OCR pass while the 3D scene keeps
animating behind it. The gate decides, for well under a millisecond, whether the
text-like content of the capture region could have changed since the last OCR.

Signature: the region is downscaled (area average) to roughly `rows` rows and
reduced to the per-pixel minimum of its B, G and R channels. FFVII's menu and
target-name text is white/light gray, so text pixels stay bright in that channel
while saturated scene colors, the blue menu windows, gauges and dark geometry
drop out.

Comparison: a pixel counts as changed when it moved by more than
`diff_threshold` levels and was bright (> `bright_threshold`) in either frame.
Dark or colored scene animation is ignored; any appearing, disappearing or
changed glyph is counted. Bright scene changes (snow fields, spell flashes)
also count, which only costs an extra OCR pass and never hides a name change.
"""
from typing import Optional

import cv2
import numpy as np


class TextChangeGate:
    def __init__(self, rows: int = 96, diff_threshold: int = 48, bright_threshold: int = 150,
                 min_changed_px: int = 10):
        self.rows = max(8, int(rows))
        self.diff_threshold = int(diff_threshold)
        self.bright_threshold = int(bright_threshold)
        self.min_changed_px = int(min_changed_px)

    def signature(self, roi_bgra: np.ndarray) -> np.ndarray:
        """Returns the min-channel thumbnail (uint8, ~rows high) of a BGR or BGRA region."""
        h, w = roi_bgra.shape[:2]
        factor = max(1, h // self.rows)
        if factor > 1:
            small = cv2.resize(roi_bgra, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)
        else:
            small = roi_bgra
        channels = cv2.split(small)
        mono = cv2.min(channels[0], channels[1])
        cv2.min(mono, channels[2], dst=mono)
        return mono

    def changed_pixels(self, signature: np.ndarray, reference: Optional[np.ndarray]) -> int:
        """Number of text-like pixels that differ between two signatures."""
        if reference is None or reference.shape != signature.shape:
            return int(signature.size)
        _, moved = cv2.threshold(cv2.absdiff(signature, reference), self.diff_threshold, 255, cv2.THRESH_BINARY)
        _, bright = cv2.threshold(cv2.max(signature, reference), self.bright_threshold, 255, cv2.THRESH_BINARY)
        return int(cv2.countNonZero(cv2.bitwise_and(moved, bright)))

    def has_changed(self, signature: np.ndarray, reference: Optional[np.ndarray]) -> bool:
        return self.changed_pixels(signature, reference) >= self.min_changed_px
