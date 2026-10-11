"""
The OCR models and their post-processing, from RapidOCR (used by engine/ocr_engine.py).

Third-party files under the Apache License 2.0 (LICENSE.txt in this folder), not under the MIT
License of the rest of the project:
- ch_PP-OCRv4_det_infer.onnx (DBNet text detection) and ch_PP-OCRv4_rec_infer.onnx (SVTR text
  recognition; its metadata holds the character list): the PP-OCRv4 models of PaddleOCR
  (https://github.com/PaddlePaddle/PaddleOCR, PaddlePaddle Authors) in RapidOCR's ONNX
  conversion (https://github.com/RapidAI/RapidOCR, Copyright (c) 2021 RapidOCR Authors), copied
  unchanged from rapidocr_onnxruntime 1.4.4.
- postprocess.py: adapted from rapidocr_onnxruntime 1.4.4 (its header lists the changes).

Bundled instead of depending on rapidocr_onnxruntime: its last release, 1.4.4, installs only on
Python 3.12 and older.
"""
from ffvii_companion.paths import PACKAGE_DIR

PPOCR_DIR = PACKAGE_DIR / "ppocr"
DET_MODEL_PATH = PPOCR_DIR / "ch_PP-OCRv4_det_infer.onnx"
REC_MODEL_PATH = PPOCR_DIR / "ch_PP-OCRv4_rec_infer.onnx"
