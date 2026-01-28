# ocr_utils.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import re
import numpy as np
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrCfg:
    enabled: bool = False
    lang_candidates: tuple[str, ...] = ("jpn+eng", "jpn")  # "eng", "jpn_vert"
    psm_candidates: tuple[int, ...] = (6, 11)  # 6=block, 11=sparse, 4=column, 3=auto

    # lang: str = "jpn+eng"          # e.g. "eng", "jpn", "jpn+eng"
    oem: int = 3               # Tesseract OCR Engine Mode
    # psm: int = 6               # Page Segmentation Mode
    # preprocessing
    upscale: int = 6           # enlarge small thumbs before OCR
    threshold: Optional[int] = None  # e.g. 160; None disables binarization
    sharpen: bool = True
    # rotation control
    auto_rotate: bool = True          # try 0/90/180/270 and pick best
    rotate_degrees: Optional[int] = None  # force rotation: 0/90/180/270, overrides auto_rotate
    # only try these rotations when auto_rotate=True
    rotation_candidates: tuple[int, ...] = (0, -90)
    # crop label area before OCR (critical for macro thumbnails)
    crop_label: bool = True
    # fallback crop width ratio if heuristic fails
    label_width_ratio: float = 0.33  # left 33% of image
    early_stop_conf: float = 75.0


# _word_re = re.compile(r"[A-Za-z0-9]")
_word_re = re.compile(r"[A-Za-z0-9]|[一-龯ぁ-んァ-ン]")


def _mean_confidence(data: dict) -> float:
    confs = []
    texts = data.get("text", [])
    for c, t in zip(data.get("conf", []), texts):
        try:
            ci = float(c)
        except Exception:
            continue
        if ci < 0:
            continue
        if not t or not _word_re.search(t):
            continue
        confs.append(ci)
    return sum(confs) / len(confs) if confs else 0.0


def _score_candidate(img: Image.Image, cfg: "OcrCfg", *, lang: str, psm: int) -> float:
    config = f"--oem {cfg.oem} --psm {psm}"
    pre = preprocess_for_ocr(img, cfg)
    try:
        data = pytesseract.image_to_data(
            pre, lang=lang, config=config, output_type=pytesseract.Output.DICT
        )
        return _mean_confidence(data)
    except pytesseract.TesseractError:
        return -1.0


def _final_ocr(img: Image.Image, cfg: "OcrCfg", *, lang: str, psm: int) -> str:
    config = f"--oem {cfg.oem} --psm {psm}"
    pre = preprocess_for_ocr(img, cfg)
    return pytesseract.image_to_string(pre, lang=lang, config=config)


def _rotate(img: Image.Image, deg: int) -> Image.Image:
    return img.rotate(deg, expand=True)


def _get_rotations(cfg: "OcrCfg") -> tuple[int, ...]:
    if cfg.rotate_degrees is not None:
        return (cfg.rotate_degrees,)
    if cfg.auto_rotate:
        return cfg.rotation_candidates
    return (0,)


def _get_regions(img: Image.Image, cfg: "OcrCfg") -> list[Image.Image]:
    if not cfg.crop_label:
        return [img]  # if user explicitly disables crop, OCR full

    box = find_label_crop_box(img, cfg)
    label = img.crop(box)
    # w, h = label.size
    # label_no_qr = label.crop((0, 0, w, int(h * 0.75)))
    # return [label, label_no_qr]
    return [label, img]


def _trim_bottom(img: Image.Image, frac: float = 0.25) -> Image.Image:
    w, h = img.size
    cut = int(h * frac)
    return img.crop((0, 0, w, max(1, h - cut)))


def _pad_white(img: Image.Image, px: int = 20) -> Image.Image:
    # pad in original pixels (after upscale it becomes bigger anyway)
    return ImageOps.expand(img, border=px, fill="white")


def _iter_candidates(img: Image.Image, cfg: "OcrCfg") -> Iterable[tuple[Image.Image, str, int]]:
    rotations = _get_rotations(cfg)
    regions = _get_regions(img, cfg)

    for lang in cfg.lang_candidates:
        for psm in cfg.psm_candidates:
            for rimg in regions:
                for deg in rotations:
                    rot = _rotate(rimg, deg)

                    # Always include the full rotated label
                    yield (_pad_white(rot), lang, psm)

                    # Optional: QR-trim variant, but do it AFTER rotation
                    # and trim BOTTOM only (never trim LEFT)
                    if deg == 0:
                        yield (_pad_white(_trim_bottom(rot, 0.25)), lang, psm)


def ocr_image(img: Image.Image, cfg: "OcrCfg") -> str:
    best = None  # (score, img, lang, psm)

    for cand_img, lang, psm in _iter_candidates(img, cfg):
        score = _score_candidate(cand_img, cfg, lang=lang, psm=psm)
        if best is None or score > best[0]:
            best = (score, cand_img, lang, psm)
        if score >= cfg.early_stop_conf:
            best = (score, cand_img, lang, psm)
            break

    if best is None:
        return ""

    _, best_img, best_lang, best_psm = best
    return _final_ocr(best_img, cfg, lang=best_lang, psm=best_psm)


def write_text_atomic(out: Path, text: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)


def preprocess_for_ocr(img: Image.Image, cfg: OcrCfg) -> Image.Image:
    """
    Practical preprocessing for small JPEG thumbs:
      - grayscale
      - upscale
      - autocontrast
      - optional threshold
      - optional sharpen
    """
    g = img.convert("L")

    # upscale (helps for 512px thumbs)
    if cfg.upscale and cfg.upscale > 1:
        w, h = g.size
        g = g.resize((w * cfg.upscale, h * cfg.upscale), resample=Image.Resampling.LANCZOS)

    g = ImageOps.autocontrast(g)

    # light contrast boost
    g = ImageEnhance.Contrast(g).enhance(1.5)

    if cfg.threshold is not None:
        t = int(cfg.threshold)
        g = g.point(lambda x: 255 if x >= t else 0, mode="1").convert("L")

    if cfg.sharpen:
        g = g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

    return g


def ocr_image_path(path: Path, cfg: OcrCfg) -> str:
    if not cfg.enabled:
        return ""
    img = Image.open(path)
    try:
        return ocr_image(img, cfg)
    finally:
        img.close()


def find_label_crop_box(img: Image.Image, cfg: OcrCfg) -> tuple[int, int, int, int]:
    """
    Return a crop box (left, top, right, bottom) for the label region.

    Heuristic:
      1) Compute column mean intensity using only the middle vertical band (reduces corner/border noise).
      2) Find the widest dark vertical run (separator bar) within a constrained X-range (avoids left border).
      3) Crop everything left of that bar.
      4) Fallback: left cfg.label_width_ratio of the image.
    """
    w, h = img.size
    g = img.convert("L")
    arr = np.array(g, dtype=np.uint8)

    # Use only middle band of rows to avoid top/bottom artifacts
    y0 = int(h * 0.15)
    y1 = int(h * 0.85)
    if y1 <= y0:  # safety for tiny images
        y0, y1 = 0, h

    band = arr[y0:y1, :]
    col_mean = band.mean(axis=0)

    # Slightly higher threshold than before
    dark = col_mean < 60

    # Search for separator bar only within 10%..70% width
    x_start = int(w * 0.10)
    x_end = int(w * 0.70)
    if x_end <= x_start:  # safety for tiny images
        x_start, x_end = 0, w

    best: tuple[int, int, int] | None = None  # (run_len, start, end)
    i = x_start
    min_run = max(6, int(w * 0.01))  # minimum width for a "bar"

    while i < x_end:
        if not dark[i]:
            i += 1
            continue

        j = i
        while j < x_end and dark[j]:
            j += 1

        run_len = j - i
        if run_len >= min_run and (best is None or run_len > best[0]):
            best = (run_len, i, j)

        i = j

    if best is not None:
        _, start, _ = best
        # right = max(1, start - 1)
        right = max(int(w * 0.20), start + 10)   # ensure not too narrow; include margin
        right = min(w, right)
        return (0, 0, right, h)

    # Fallback: left portion
    right = int(w * cfg.label_width_ratio)
    return (0, 0, max(1, right), h)
