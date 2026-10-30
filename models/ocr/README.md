# Text recognition model for the depth counter

`ch_PP-OCRv4_rec_infer.onnx` is PaddleOCR's PP-OCRv4 mobile text recognizer, converted to ONNX
and taken unchanged from the `rapidocr_onnxruntime` 1.4.4 wheel on PyPI
(`rapidocr_onnxruntime/models/`). Its character list is stored in the model's `character`
metadata.

- License: Apache-2.0 (PaddleOCR, RapidOCR)
- SHA-256: `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b`

Used by `pipe1_core.stop_detection.text_reader` when `onnxruntime` is installed (the server
installs it through the `pipe1-core[ocr]` extra). Only the recognizer is needed: the depth
region is drawn by the user, so there is no text detection step.
