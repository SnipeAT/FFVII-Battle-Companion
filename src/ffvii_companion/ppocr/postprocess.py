# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""
Post-processing of the PP-OCR models: text boxes from the DBNet detection map (DBPostProcess)
and text from the CTC output of the recognition model (CTCLabelDecode).

Adapted from rapidocr_onnxruntime 1.4.4 (ch_ppocr_det/utils.py and ch_ppocr_rec/utils.py;
https://github.com/RapidAI/RapidOCR, Copyright (c) 2021 RapidOCR Authors), which ports the
post-processing of PaddleOCR (https://github.com/PaddlePaddle/PaddleOCR). Licensed under the
Apache License 2.0 (LICENSE.txt in this folder).

Changes from rapidocr_onnxruntime 1.4.4, without changing the results (tests/test_ocr_engine.py
compares the engine's output with the output recorded with RapidOCR's own code):
- Only what engine/ocr_engine.py uses: DBPostProcess always scores a box by the mean of the
  detection map inside it (RapidOCR's score_mode "fast"); CTCLabelDecode takes the character
  list as an argument (the engine reads it from the recognition model's metadata) and returns
  no word boxes. DetPreProcess and ResizeImgError are left out (the engine has its own resize).
- cv2.findContours is handled with OpenCV 4's two return values only.
- unclip() computes the box's area and perimeter with NumPy instead of a Shapely Polygon (same
  values on every box of the fixture frames and recordings), so Shapely is not a dependency.
"""
from typing import List, Tuple

import cv2
import numpy as np
import pyclipper


class DBPostProcess:
    """The post process for Differentiable Binarization (DB)."""

    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.7,
        max_candidates: int = 1000,
        unclip_ratio: float = 2.0,
        use_dilation: bool = False,
    ):
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3

        self.dilation_kernel = None
        if use_dilation:
            self.dilation_kernel = np.array([[1, 1], [1, 1]])

    def __call__(
        self, pred: np.ndarray, ori_shape: Tuple[int, int]
    ) -> Tuple[np.ndarray, List[float]]:
        src_h, src_w = ori_shape
        pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh

        mask = segmentation[0]
        if self.dilation_kernel is not None:
            mask = cv2.dilate(
                np.array(segmentation[0]).astype(np.uint8), self.dilation_kernel
            )
        boxes, scores = self.boxes_from_bitmap(pred[0], mask, src_w, src_h)
        return boxes, scores

    def boxes_from_bitmap(
        self, pred: np.ndarray, bitmap: np.ndarray, dest_width: int, dest_height: int
    ) -> Tuple[np.ndarray, List[float]]:
        """
        bitmap: single map with shape (1, H, W),
                whose values are binarized as {0, 1}
        """

        height, width = bitmap.shape

        contours, _ = cv2.findContours(
            (bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        num_contours = min(len(contours), self.max_candidates)

        boxes, scores = [], []
        for index in range(num_contours):
            contour = contours[index]
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue

            score = self.box_score_fast(pred, points.reshape(-1, 2))
            if self.box_thresh > score:
                continue

            box = self.unclip(points)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2:
                continue

            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(
                np.round(box[:, 1] / height * dest_height), 0, dest_height
            )
            boxes.append(box.astype(np.int32))
            scores.append(score)
        return np.array(boxes, dtype=np.int32), scores

    def get_mini_boxes(self, contour: np.ndarray) -> Tuple[np.ndarray, float]:
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

        index_1, index_2, index_3, index_4 = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_1 = 0
            index_4 = 1
        else:
            index_1 = 1
            index_4 = 0

        if points[3][1] > points[2][1]:
            index_2 = 2
            index_3 = 3
        else:
            index_2 = 3
            index_3 = 2

        box = np.array(
            [points[index_1], points[index_2], points[index_3], points[index_4]]
        )
        return box, min(bounding_box[1])

    @staticmethod
    def box_score_fast(bitmap: np.ndarray, _box: np.ndarray) -> float:
        h, w = bitmap.shape[:2]
        box = _box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype(np.int32), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype(np.int32), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype(np.int32), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype(np.int32), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype(np.int32), 1)
        return cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0]

    def unclip(self, box: np.ndarray) -> np.ndarray:
        unclip_ratio = self.unclip_ratio
        x, y = np.asarray(box, dtype=np.float64).T
        area = abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0  # shoelace
        length = np.hypot(x - np.roll(x, -1), y - np.roll(y, -1)).sum()
        distance = area * unclip_ratio / length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = np.array(offset.Execute(distance)).reshape((-1, 1, 2))
        return expanded


class CTCLabelDecode:
    def __init__(self, character: List[str]):
        if not character:
            raise ValueError("character must not be empty")
        # Index 0 is the CTC blank; the model's last class is a space
        self.character = ["blank"] + list(character) + [" "]

    def __call__(self, preds: np.ndarray) -> List[Tuple[str, float]]:
        preds_idx = preds.argmax(axis=2)
        preds_prob = preds.max(axis=2)
        return self.decode(preds_idx, preds_prob, is_remove_duplicate=True)

    def decode(
        self,
        text_index: np.ndarray,
        text_prob: np.ndarray,
        is_remove_duplicate: bool = False,
    ) -> List[Tuple[str, float]]:
        """convert text-index into text-label."""
        result_list = []
        ignored_tokens = self.get_ignored_tokens()
        batch_size = len(text_index)
        for batch_idx in range(batch_size):
            selection = np.ones(len(text_index[batch_idx]), dtype=bool)
            if is_remove_duplicate:
                selection[1:] = text_index[batch_idx][1:] != text_index[batch_idx][:-1]

            for ignored_token in ignored_tokens:
                selection &= text_index[batch_idx] != ignored_token

            conf_list = np.array(text_prob[batch_idx][selection]).tolist()
            if len(conf_list) == 0:
                conf_list = [0]

            char_list = [
                self.character[text_id] for text_id in text_index[batch_idx][selection]
            ]
            text = "".join(char_list)
            result_list.append((text, np.mean(conf_list).tolist()))
        return result_list

    @staticmethod
    def get_ignored_tokens() -> List[int]:
        return [0]  # for ctc blank
