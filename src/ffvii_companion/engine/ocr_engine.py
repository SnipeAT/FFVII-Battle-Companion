"""
Lean OCR engine for the FFVII battle HUD.

Runs the PP-OCRv4 detection (DBNet) and recognition (SVTR/CTC) ONNX models that
ship with rapidocr_onnxruntime, reusing its DB post-processing and CTC decoding,
but owning the ONNX Runtime sessions and the det -> rec orchestration:

- Only the detection and recognition models are loaded. Battle text is always
  horizontal, so the text-angle classifier RapidOCR loads by default is skipped.
- Sessions default to a single intra-op thread with thread spinning disabled.
  With RapidOCR's defaults every session owns a thread pool whose workers
  busy-wait after each inference, which burned more CPU than the inference itself
  for this app's intermittent workload.
- det_limit_side_len is honored. (RapidOCR's "max" limit mode silently ignores
  the configured value and detects at up to 1500/2000 px.)
- Recognition results are cached per text box: when a box is at the same place
  and its pixels have not changed since the previous run, its text is reused and
  only new or changed boxes go through the recognition model.

- Recognition runs one crop per inference instead of RapidOCR's padded batches
  of 6 (same text, less padded work).

The pipeline (letterbox, preprocessing, box ordering, crop warping) otherwise
mirrors RapidOCR 1.4.x so recognition output matches it.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
import onnxruntime as ort

import rapidocr_onnxruntime
from rapidocr_onnxruntime.ch_ppocr_det.utils import DBPostProcess
from rapidocr_onnxruntime.ch_ppocr_rec.utils import CTCLabelDecode

logger = logging.getLogger("ffvii-ocr")

MODELS_DIR = Path(rapidocr_onnxruntime.__file__).resolve().parent / "models"
DET_MODEL_PATH = MODELS_DIR / "ch_PP-OCRv4_det_infer.onnx"
REC_MODEL_PATH = MODELS_DIR / "ch_PP-OCRv4_rec_infer.onnx"

# Recognition input geometry (PP-OCRv4 rec, same as RapidOCR). Crops are recognized one at
# a time: RapidOCR batches 6 and pads every crop to the widest one, which was ~18% slower on
# the battle fixtures with identical text.
REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_BATCH = 1

# RapidOCR pads very wide or very short images (letterbox) before detection
LETTERBOX_MAX_RATIO = 8
LETTERBOX_MIN_HEIGHT = 30

# Recognition cache: a box is "unchanged" when it overlaps the previous box at
# IoU >= CACHE_MIN_IOU and fewer than CACHE_MAX_CHANGED_FRACTION of its grayscale
# signature pixels moved by more than CACHE_PIXEL_DELTA levels.
CACHE_SIG_SIZE = (128, 16)
CACHE_MIN_IOU = 0.6
CACHE_PIXEL_DELTA = 48
CACHE_MAX_CHANGED_FRACTION = 0.01


def create_session(model_path: Path, threads: int = 1) -> ort.InferenceSession:
    """Creates a CPU ONNX Runtime session tuned for low, intermittent CPU usage."""
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, int(threads))
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # No arena: memory returns to the OS between frames instead of growing to the peak.
    opts.enable_cpu_mem_arena = False
    opts.log_severity_level = 3
    # Never busy-wait for more work between runs (only matters when threads > 1).
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])


@dataclass
class OCRLine:
    text: str
    score: float
    box: Tuple[int, int, int, int]  # x0, y0, x1, y1 in input-image pixels


@dataclass
class OCRResult:
    lines: List[OCRLine] = field(default_factory=list)
    det_ms: float = 0.0
    rec_ms: float = 0.0
    total_ms: float = 0.0
    boxes: int = 0
    recognized: int = 0  # boxes sent through the recognition model
    reused: int = 0      # boxes whose text came from the cache


@dataclass
class _CacheEntry:
    bbox: Tuple[int, int, int, int]
    signature: np.ndarray
    text: str
    score: float


def _order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    x_sorted = pts[np.argsort(pts[:, 0]), :]
    left = x_sorted[:2, :]
    right = x_sorted[2:, :]
    tl, bl = left[np.argsort(left[:, 1]), :]
    tr, br = right[np.argsort(right[:, 1]), :]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _sort_boxes(boxes: List[np.ndarray]) -> List[np.ndarray]:
    """Top-to-bottom, then left-to-right within a text row (RapidOCR ordering)."""
    ordered = sorted(boxes, key=lambda b: (b[0][1], b[0][0]))
    for i in range(len(ordered) - 1):
        for j in range(i, -1, -1):
            if abs(ordered[j + 1][0][1] - ordered[j][0][1]) < 10 and ordered[j + 1][0][0] < ordered[j][0][0]:
                ordered[j], ordered[j + 1] = ordered[j + 1], ordered[j]
            else:
                break
    return ordered


def _crop_box(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Perspective-rectifies a quadrilateral text box into a horizontal crop."""
    width = int(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[2] - pts[3])))
    height = int(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2])))
    dst = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(pts.astype(np.float32), dst)
    crop = cv2.warpPerspective(img, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if crop.shape[0] * 1.0 / crop.shape[1] >= 1.5:
        crop = np.rot90(crop)
    return crop


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _signature(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return cv2.resize(gray, CACHE_SIG_SIZE, interpolation=cv2.INTER_AREA)


class OCREngine:
    """Thread-safe, lazily loaded det + rec OCR engine (see module docstring)."""

    def __init__(self, threads: int = 1, det_limit_side_len: int = 640, min_box_height: int = 8):
        self.threads = max(1, int(threads))
        self.det_limit_side_len = int(det_limit_side_len)
        self.min_box_height = int(min_box_height)
        self._det_session: Optional[ort.InferenceSession] = None
        self._rec_session: Optional[ort.InferenceSession] = None
        self._det_input = ""
        self._rec_input = ""
        self._decoder: Optional[CTCLabelDecode] = None
        self._init_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._cache: List[_CacheEntry] = []
        self._post = DBPostProcess(
            thresh=0.3, box_thresh=0.5, max_candidates=1000,
            unclip_ratio=1.6, score_mode="fast", use_dilation=True,
        )

    # ------------------------------------------------------------------ setup
    @property
    def loaded(self) -> bool:
        return self._det_session is not None and self._rec_session is not None

    def load(self) -> None:
        """Creates the ONNX sessions (normally deferred to the first run)."""
        if self.loaded:
            return
        with self._init_lock:
            if self.loaded:
                return
            t0 = time.perf_counter()
            det = create_session(DET_MODEL_PATH, self.threads)
            rec = create_session(REC_MODEL_PATH, self.threads)
            characters = rec.get_modelmeta().custom_metadata_map["character"].splitlines()
            self._decoder = CTCLabelDecode(character=characters)
            self._det_input = det.get_inputs()[0].name
            self._rec_input = rec.get_inputs()[0].name
            self._rec_session = rec
            self._det_session = det
            logger.info(f"OCR models loaded in {(time.perf_counter() - t0) * 1000:.0f} ms "
                        f"(threads={self.threads}, det_limit={self.det_limit_side_len})")

    def set_threads(self, threads: int) -> None:
        """Changes the inference thread count; sessions are recreated on the next run."""
        threads = max(1, int(threads))
        if threads == self.threads:
            return
        with self._run_lock, self._init_lock:
            self.threads = threads
            self._det_session = None
            self._rec_session = None

    def set_det_limit_side_len(self, limit: int) -> None:
        self.det_limit_side_len = int(limit)

    # -------------------------------------------------------------- pipeline
    def run(self, img_bgr: np.ndarray, use_cache: bool = True) -> OCRResult:
        """
        Detects and recognizes text in a BGR uint8 image.
        Returns every non-empty recognized line with its confidence score and box,
        in reading order. Confidence filtering is left to the caller.
        use_cache=False forces recognition of every box (diagnostics) and leaves
        the cache untouched.
        """
        with self._run_lock:
            self.load()
            t0 = time.perf_counter()
            result = OCRResult()

            img, pad_top = self._letterbox(img_bgr)
            boxes = self._detect(img)
            result.det_ms = (time.perf_counter() - t0) * 1000.0
            result.boxes = len(boxes)
            if not boxes:
                if use_cache:
                    self._cache = []
                result.total_ms = result.det_ms
                return result

            crops, bboxes, signatures = [], [], []
            for pts in boxes:
                crop = _crop_box(img, pts)
                crops.append(crop)
                x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min()) - pad_top
                x1, y1 = int(pts[:, 0].max()), int(pts[:, 1].max()) - pad_top
                bboxes.append((x0, y0, x1, y1))
                signatures.append(_signature(crop))

            texts: List[Optional[Tuple[str, float]]] = [None] * len(boxes)
            pending: List[int] = []
            for i in range(len(boxes)):
                hit = self._lookup(bboxes[i], signatures[i]) if use_cache else None
                if hit is not None:
                    texts[i] = hit
                    result.reused += 1
                else:
                    pending.append(i)

            t_rec = time.perf_counter()
            if pending:
                recognized = self._recognize([crops[i] for i in pending])
                for i, res in zip(pending, recognized):
                    texts[i] = res
            result.rec_ms = (time.perf_counter() - t_rec) * 1000.0
            result.recognized = len(pending)

            if use_cache:
                self._cache = [
                    _CacheEntry(bboxes[i], signatures[i], texts[i][0], texts[i][1]) for i in range(len(boxes))
                ]

            for i in range(len(boxes)):
                text, score = texts[i]
                if text.strip():
                    result.lines.append(OCRLine(text=text, score=float(score), box=bboxes[i]))
            result.total_ms = (time.perf_counter() - t0) * 1000.0
            return result

    # ------------------------------------------------------------- internals
    @staticmethod
    def _letterbox(img: np.ndarray) -> Tuple[np.ndarray, int]:
        h, w = img.shape[:2]
        if h <= LETTERBOX_MIN_HEIGHT or (w / h) > LETTERBOX_MAX_RATIO:
            new_h = max(int(w / LETTERBOX_MAX_RATIO), LETTERBOX_MIN_HEIGHT) * 2
            pad = int(abs(new_h - h) / 2)
            padded = cv2.copyMakeBorder(img, pad, pad, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            return padded, pad
        return img, 0

    def _detect(self, img: np.ndarray) -> List[np.ndarray]:
        h, w = img.shape[:2]
        ratio = min(1.0, float(self.det_limit_side_len) / max(h, w))
        resize_h = int(round(int(h * ratio) / 32) * 32)
        resize_w = int(round(int(w * ratio) / 32) * 32)
        if resize_h <= 0 or resize_w <= 0:
            return []
        resized = cv2.resize(img, (resize_w, resize_h))
        tensor = resized.astype(np.float32)
        tensor *= 1.0 / 127.5  # (x / 255 - 0.5) / 0.5
        tensor -= 1.0
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[np.newaxis])
        pred = self._det_session.run(None, {self._det_input: tensor})[0]
        raw_boxes, _scores = self._post(pred, (h, w))

        boxes = []
        for box in raw_boxes:
            pts = _order_points_clockwise(box.astype(np.float32))
            pts[:, 0] = np.clip(pts[:, 0], 0, w - 1).astype(np.int32)
            pts[:, 1] = np.clip(pts[:, 1], 0, h - 1).astype(np.int32)
            rect_w = int(np.linalg.norm(pts[0] - pts[1]))
            rect_h = int(np.linalg.norm(pts[0] - pts[3]))
            if rect_w <= 3 or rect_h <= 3 or rect_h < self.min_box_height:
                continue
            boxes.append(pts)
        return _sort_boxes(boxes)

    def _recognize(self, crops: Sequence[np.ndarray]) -> List[Tuple[str, float]]:
        ratios = [c.shape[1] / float(c.shape[0]) for c in crops]
        order = np.argsort(np.array(ratios))
        results: List[Tuple[str, float]] = [("", 0.0)] * len(crops)
        for start in range(0, len(crops), REC_BATCH):
            idx = order[start:start + REC_BATCH]
            max_ratio = max(REC_MIN_WIDTH / REC_HEIGHT, max(ratios[i] for i in idx))
            width = int(REC_HEIGHT * max_ratio)
            batch = np.zeros((len(idx), 3, REC_HEIGHT, width), dtype=np.float32)
            for j, i in enumerate(idx):
                crop = crops[i]
                h, w = crop.shape[:2]
                resized_w = min(width, int(math.ceil(REC_HEIGHT * w / float(h))))
                resized = cv2.resize(crop, (resized_w, REC_HEIGHT)).astype(np.float32)
                resized *= 1.0 / 127.5
                resized -= 1.0
                batch[j, :, :, :resized_w] = resized.transpose(2, 0, 1)
            preds = self._rec_session.run(None, {self._rec_input: batch})[0]
            for j, (text, score) in enumerate(self._decoder(preds)):
                results[idx[j]] = (text, float(score))
        return results

    def _lookup(self, bbox: Tuple[int, int, int, int], signature: np.ndarray) -> Optional[Tuple[str, float]]:
        limit = max(6, int(signature.size * CACHE_MAX_CHANGED_FRACTION))
        for entry in self._cache:
            if _iou(entry.bbox, bbox) < CACHE_MIN_IOU:
                continue
            diff = cv2.absdiff(entry.signature, signature)
            if int(np.count_nonzero(diff > CACHE_PIXEL_DELTA)) <= limit:
                return entry.text, entry.score
        return None
