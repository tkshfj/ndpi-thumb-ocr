# qr_utils.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
from PIL import Image
import cv2


@dataclass(frozen=True)
class QrCfg:
    enabled: bool = False
    rotation_candidates: tuple[int, ...] = (0, -90)


def _to_cv(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert("RGB"))
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def decode_qr(img: Image.Image, cfg: QrCfg) -> Optional[str]:
    if not cfg.enabled:
        return None
    det = cv2.QRCodeDetector()
    for deg in cfg.rotation_candidates:
        cand = img.rotate(deg, expand=True)
        data, _, _ = det.detectAndDecode(_to_cv(cand))
        if data:
            return data.strip()
    return None
