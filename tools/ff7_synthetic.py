"""
Synthetic FFVII battle/field frame generator for tests and benchmarks.

The layout mirrors a real 16:9 FFNx / 7th Heaven battle screen with the
enhanced UI mods (references: captures of Bandersnatch, Devil Ride and
Custom Sweeper battles at native 16:9):

- The targeted enemy name ("Devil Ride", "Custom Sweeper B") is centered in
  a wide, semi-transparent blue window spanning ~13%-87% of the width at
  ~70%-78% of the height. Over bright scenes (snow) the window nearly
  disappears and the outlined text sits on a light background.
- Compact semi-transparent menu windows sit along the bottom edge: NAME
  (party), the command window (two columns), and the HP / MP / BARRIER /
  WAIT-LIMIT window on the right.
- The 3D scene is animated (moving shapes), optionally bright (snow field).

All layout coordinates are fractions of the frame size, so any resolution
renders the same composition. Frames are BGRA uint8 arrays, matching what
mss returns. They are not pixel-accurate copies of the game; they exist so the
OCR pipeline, the text-change gate and the capture state machine can be
exercised without the game running.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

TEXT_FILL = (240, 240, 240)
TEXT_OUTLINE = (30, 32, 48)
HEADER_FILL = (232, 236, 248)
WIN_LEFT = (24, 40, 150)        # translucent window gradient (left edge)
WIN_RIGHT = (92, 104, 196)      # translucent window gradient (right edge)
WIN_ALPHA = 200                 # 0-255 opacity of window fill
WIN_BORDER = (236, 236, 244)
HP_GAUGE = (64, 176, 255)
MP_GAUGE = (72, 208, 120)
LIMIT_GAUGE = (255, 150, 200)
WAIT_GAUGE = (246, 240, 190)
BARRIER_GAUGE = (170, 170, 178)

DEFAULT_ROI = {"top_pct": 0.66, "left_pct": 0.20, "width_pct": 0.60, "height_pct": 0.18}


@lru_cache(maxsize=32)
def _font(size_px: int) -> ImageFont.ImageFont:
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in ("arial.ttf", "segoeui.ttf", "verdana.ttf"):
        path = os.path.join(fonts_dir, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size_px)
    return ImageFont.load_default(size=size_px)


class _Canvas:
    """Draws using normalized (0..1) coordinates; windows are alpha-blended overlays."""

    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        self.draw = ImageDraw.Draw(self.img)

    def px(self, fx: float, fy: float) -> Tuple[int, int]:
        return int(fx * self.w), int(fy * self.h)

    def window(self, x0: float, y0: float, x1: float, y1: float) -> None:
        X0, Y0 = self.px(x0, y0)
        X1, Y1 = self.px(x1, y1)
        ww, wh = max(1, X1 - X0), max(1, Y1 - Y0)
        t = np.linspace(0.0, 1.0, ww, dtype=np.float32)[None, :, None]
        left = np.array(WIN_LEFT, np.float32)[None, None, :]
        right = np.array(WIN_RIGHT, np.float32)[None, None, :]
        rgb = np.broadcast_to(left + (right - left) * t, (wh, ww, 3))
        rgba = np.empty((wh, ww, 4), np.uint8)
        rgba[..., :3] = rgb.astype(np.uint8)
        rgba[..., 3] = WIN_ALPHA
        self.img.alpha_composite(Image.fromarray(rgba, "RGBA"), (X0, Y0))
        bw = max(1, self.h // 540)
        self.draw.rectangle([X0, Y0, X1, Y1], outline=WIN_BORDER, width=bw)

    def text(self, fx: float, fy: float, s: str, size: float = 0.040,
             fill=TEXT_FILL, anchor: str = "la") -> None:
        """size is the font size as a fraction of frame height; outlined like the game font."""
        font = _font(max(6, int(size * self.h)))
        stroke = max(1, self.h // 540)
        self.draw.text(self.px(fx, fy), s, font=font, fill=fill, anchor=anchor,
                       stroke_width=stroke, stroke_fill=TEXT_OUTLINE)

    def gauge(self, x0: float, y0: float, x1: float, frac: float, color, thick: float = 0.008) -> None:
        X0, Y0 = self.px(x0, y0)
        X1, Y1 = self.px(x1, y0 + thick)
        self.draw.rectangle([X0, Y0, X1, Y1], fill=(70, 72, 92))
        fw = int((X1 - X0) * max(0.0, min(1.0, frac)))
        if fw > 0:
            self.draw.rectangle([X0, Y0, X0 + fw, Y1], fill=color)

    def hand_cursor(self, fx: float, fy: float) -> None:
        s = 0.012
        pts = [(fx, fy), (fx + 2.2 * s, fy), (fx + 3.2 * s, fy + 0.6 * s), (fx + 2.2 * s, fy + 1.2 * s),
               (fx, fy + 1.2 * s), (fx - 0.6 * s, fy + 0.6 * s)]
        self.draw.polygon([self.px(x, y) for x, y in pts], fill=(250, 250, 250), outline=(90, 90, 90))


def _scene(c: _Canvas, frame_index: int, seed: int, bright: bool) -> None:
    """Animated pseudo-3D scene covering the whole frame (menus are drawn on top)."""
    rng = np.random.default_rng(seed)
    base = (232, 236, 242) if bright else (96, 88, 80)
    c.draw.rectangle([0, 0, c.w, c.h], fill=base)
    for _ in range(40):
        x, y = rng.uniform(0, 1), rng.uniform(0.0, 0.35)
        r = rng.uniform(0.02, 0.08)
        col = (120, 140, 170) if bright else (70, 56, 44)
        X0, Y0 = c.px(x - r, y - r * 0.5)
        X1, Y1 = c.px(x + r, y + r * 0.5)
        c.draw.ellipse([X0, Y0, X1, Y1], fill=col)
    # Ground texture so the target-name band is not a flat color
    for _ in range(60):
        x, y = rng.uniform(0, 1), rng.uniform(0.62, 1.0)
        r = rng.uniform(0.01, 0.04)
        col = (210, 214, 222) if bright else (120, 112, 100)
        X0, Y0 = c.px(x - r, y - r * 0.3)
        X1, Y1 = c.px(x + r, y + r * 0.3)
        c.draw.ellipse([X0, Y0, X1, Y1], fill=col)
    # Animated combatants (idle animation = small periodic motion), some reaching the name band
    for i in range(6):
        cx, cy = rng.uniform(0.1, 0.9), rng.uniform(0.38, 0.62)
        r = rng.uniform(0.025, 0.06)
        phase = rng.uniform(0, math.tau)
        dx = 0.004 * math.sin(frame_index * 0.9 + phase)
        dy = 0.003 * math.cos(frame_index * 0.6 + phase)
        col = [(40, 40, 52), (190, 96, 40), (60, 140, 170)][i % 3]
        X0, Y0 = c.px(cx - r + dx, cy - r * 1.6 + dy)
        X1, Y1 = c.px(cx + r + dx, cy + r * 1.6 + dy)
        c.draw.ellipse([X0, Y0, X1, Y1], fill=col)


def _to_bgra(c: _Canvas) -> np.ndarray:
    rgba = np.asarray(c.img, dtype=np.uint8)
    bgra = np.empty_like(rgba)
    bgra[..., 0] = rgba[..., 2]
    bgra[..., 1] = rgba[..., 1]
    bgra[..., 2] = rgba[..., 0]
    bgra[..., 3] = 255
    return bgra


def render_battle_frame(
    width: int = 3840,
    height: int = 2160,
    *,
    frame_index: int = 0,
    party: Sequence[str] = ("Cloud", "Nanaki", "Cid"),
    hp: Sequence[int] = (3675, 5435, 3939),
    mp: Sequence[int] = (767, 541, 696),
    wait: Optional[Sequence[float]] = None,
    limit: Sequence[float] = (0.85, 0.1, 0.75),
    command_menu: bool = True,
    commands: Sequence[Tuple[str, str]] = (("Attack", "Mime"), ("Magic", "E.Skill"), ("Steal", "Throw"), ("Item", "")),
    target_name: Optional[str] = None,
    target_cursor: bool = True,
    damage_text: Optional[str] = None,
    scene_seed: int = 7,
    bright_scene: bool = False,
) -> np.ndarray:
    """Renders a synthetic in-battle frame (BGRA)."""
    c = _Canvas(width, height)
    _scene(c, frame_index, scene_seed, bright_scene)

    if wait is None:
        # ATB (WAIT) gauges fill continuously as frames advance
        wait = [((frame_index * 0.07) + k * 0.31) % 1.0 for k in range(len(party))]

    if target_name:
        c.window(0.131, 0.698, 0.869, 0.778)
        c.text(0.500, 0.738, target_name, size=0.042, anchor="mm")
        if target_cursor:
            c.hand_cursor(0.37, 0.47)

    # NAME window
    c.window(0.010, 0.800, 0.168, 0.982)
    c.text(0.042, 0.806, "NAME", size=0.020, fill=HEADER_FILL)
    for row, name in enumerate(party):
        c.text(0.070, 0.830 + row * 0.050, name, size=0.040)

    # Command window (two columns)
    if command_menu:
        c.window(0.168, 0.789, 0.392, 0.991)
        for i, (left, right) in enumerate(commands):
            c.text(0.188, 0.800 + i * 0.046, left, size=0.040)
            if right:
                c.text(0.292, 0.800 + i * 0.046, right, size=0.040)

    # HP / MP / BARRIER / WAIT-LIMIT window
    c.window(0.688, 0.800, 0.988, 0.982)
    c.text(0.699, 0.805, "HP", size=0.018, fill=HEADER_FILL)
    c.text(0.760, 0.805, "MP", size=0.018, fill=HEADER_FILL)
    c.text(0.820, 0.805, "BARRIER", size=0.018, fill=HEADER_FILL)
    c.text(0.905, 0.805, "WAIT / LIMIT", size=0.018, fill=HEADER_FILL)
    for row in range(len(party)):
        y = 0.826 + row * 0.050
        c.text(0.698, y, f"{hp[row]}", size=0.040)
        c.gauge(0.698, y + 0.040, 0.750, 0.8, HP_GAUGE, thick=0.004)
        c.text(0.772, y, f"{mp[row]}", size=0.040)
        c.gauge(0.760, y + 0.040, 0.810, 0.7, MP_GAUGE, thick=0.004)
        c.gauge(0.820, y + 0.008, 0.895, 0.0, BARRIER_GAUGE, thick=0.014)
        c.gauge(0.905, y + 0.006, 0.980, wait[row], WAIT_GAUGE, thick=0.012)
        c.gauge(0.905, y + 0.022, 0.980, limit[row], LIMIT_GAUGE, thick=0.008)

    if damage_text:
        c.text(0.300, 0.600, damage_text, size=0.045, anchor="mt")

    return _to_bgra(c)


def render_field_frame(
    width: int = 3840,
    height: int = 2160,
    *,
    frame_index: int = 0,
    scene_seed: int = 3,
    dialog: Optional[str] = None,
    bright_scene: bool = False,
) -> np.ndarray:
    """Renders a synthetic field-exploration frame (no battle HUD) as BGRA."""
    c = _Canvas(width, height)
    _scene(c, frame_index, scene_seed, bright_scene)
    if dialog:
        c.window(0.10, 0.04, 0.90, 0.20)
        c.text(0.13, 0.07, dialog, size=0.040)
    return _to_bgra(c)


def crop_roi(frame_bgra: np.ndarray, roi: dict) -> np.ndarray:
    """Crops a frame with calibration ROI percentages (same math as the capture service)."""
    h, w = frame_bgra.shape[:2]
    top = int(h * roi["top_pct"])
    left = int(w * roi["left_pct"])
    rw = max(64, int(w * roi["width_pct"]))
    rh = max(64, int(h * roi["height_pct"]))
    return np.ascontiguousarray(frame_bgra[top:top + rh, left:left + rw])


def save_png(frame_bgra: np.ndarray, path: str) -> None:
    Image.fromarray(np.ascontiguousarray(frame_bgra[..., [2, 1, 0]])).save(path)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    target = render_battle_frame(target_name="Custom Sweeper B")
    save_png(target, os.path.join(out, "synthetic_battle_target.png"))
    save_png(crop_roi(target, DEFAULT_ROI), os.path.join(out, "synthetic_roi_target.png"))
    save_png(crop_roi(render_battle_frame(target_name="Bandersnatch A", bright_scene=True), DEFAULT_ROI),
             os.path.join(out, "synthetic_roi_target_snow.png"))
    save_png(render_field_frame(), os.path.join(out, "synthetic_field.png"))
    print("Wrote synthetic frames to", os.path.abspath(out))
