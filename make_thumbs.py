# make_thumbs.py
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import openslide
from PIL import Image

from ocr_utils import OcrCfg, ocr_image, write_text_atomic
from qr_utils import QrCfg, decode_qr

logger = logging.getLogger(__name__)


# ------------------------------
# Config
# ------------------------------
@dataclass(frozen=True)
class ThumbCfg:
    # Prefer embedded associated images first (fast, stable)
    preferred_assoc: tuple[str, ...] = ("thumbnail", "macro", "label")

    # For OCR, often "label" or "macro" contains printed text.
    preferred_assoc_for_ocr: tuple[str, ...] = ("label", "macro", "thumbnail")

    # Final thumbnail max size (Explorer cover)
    max_size: tuple[int, int] = (512, 512)

    # If we must render (no assoc), render larger for OCR than for folder cover
    ocr_render_size: tuple[int, int] = (2048, 2048)

    # JPEG quality for cover image
    jpeg_quality: int = 85

    # Folder layout
    ndpi_inside_name: str = "slide.ndpi"     # inside folder
    folder_thumb_name: str = "folder.jpg"    # cover image used by Explorer folder preview
    folder_ocr_name: str = "folder.ocr.txt"  # OCR output next to folder.jpg


CFG = ThumbCfg()


# ------------------------------
# Helpers
# ------------------------------

def is_up_to_date(src: Path, out: Path) -> bool:
    return out.exists() and out.stat().st_mtime >= src.stat().st_mtime


def choose_assoc_image(slide: openslide.OpenSlide, keys: tuple[str, ...]) -> Optional[Image.Image]:
    assoc = slide.associated_images  # name -> PIL.Image
    for key in keys:
        if key in assoc:
            return assoc[key].convert("RGB")
    return None


def choose_image_for_cover(slide: openslide.OpenSlide, cfg: ThumbCfg) -> Image.Image:
    """Prefer embedded associated images; fallback to a rendered thumbnail."""
    img = choose_assoc_image(slide, cfg.preferred_assoc)
    if img is not None:
        return img
    return slide.get_thumbnail(cfg.max_size).convert("RGB")


def choose_image_for_ocr(slide: openslide.OpenSlide, cfg: ThumbCfg) -> Image.Image:
    img = choose_assoc_image(slide, cfg.preferred_assoc_for_ocr)
    if img is not None:
        return img
    return slide.get_thumbnail(cfg.ocr_render_size).convert("RGB")


def write_jpeg_atomic(out: Path, img: Image.Image, cfg: ThumbCfg) -> None:
    """
    Write to a temp file then replace.
    This reduces chances of leaving a partial JPEG on SMB.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    img.save(tmp, "JPEG", quality=cfg.jpeg_quality, optimize=True)
    tmp.replace(out)


def move_into_slide_folder(ndpi: Path, cfg: ThumbCfg, dry_run: bool = False) -> Path:
    """
    Move:
      /path/P1911642x40.ndpi
    into:
      /path/P1911642x40/slide.ndpi

    If already organized (…/P1911642x40/slide.ndpi), return as-is.
    """
    if ndpi.name == cfg.ndpi_inside_name:
        return ndpi

    slide_folder = ndpi.parent / ndpi.stem
    dest = slide_folder / cfg.ndpi_inside_name

    if dest.exists():
        raise FileExistsError(f"Destination exists: {dest}")

    if dry_run:
        print(f"[DRY] mkdir -p {slide_folder}")
        print(f"[DRY] move {ndpi} -> {dest}")
        return dest

    slide_folder.mkdir(parents=True, exist_ok=True)

    try:
        ndpi.replace(dest)
    except OSError:
        shutil.move(str(ndpi), str(dest))

    return dest


def _process_outputs(
    ndpi_inside: Path,
    out_jpg: Path,
    out_txt: Optional[Path],
    ocr_cfg: OcrCfg,
    slide: openslide.OpenSlide,
    cfg: ThumbCfg,
    qr_cfg: Optional["QrCfg"] = None,   # NEW, optional
) -> None:
    """Process and write the cover image and OCR/QR output."""
    if not is_up_to_date(ndpi_inside, out_jpg):
        _write_cover(ndpi_inside, out_jpg, slide, cfg)

    if out_txt is not None and not is_up_to_date(ndpi_inside, out_txt):
        _write_text(ndpi_inside, out_txt, slide, cfg, ocr_cfg, qr_cfg=qr_cfg)


def _handle_dry_run(ndpi_inside: Path, out_jpg: Path, out_txt: Optional[Path], jpg_needed: bool, txt_needed: bool, ocr_cfg: OcrCfg, qr_cfg: Optional[QrCfg]) -> tuple[Optional[Path], Optional[Path]]:  # noqa: E501
    """Print dry-run actions and return the paths that would be created."""
    if jpg_needed:
        print(f"[DRY] thumb {ndpi_inside} -> {out_jpg}")
    if txt_needed and out_txt is not None:
        langs = ",".join(ocr_cfg.lang_candidates)
        psms = ",".join(str(p) for p in ocr_cfg.psm_candidates)
        print(f"[DRY] ocr  {ndpi_inside} -> {out_txt} (langs={langs} psms={psms})")
        if qr_cfg and qr_cfg.enabled:
            rots = ",".join(str(r) for r in qr_cfg.rotation_candidates)
            print(f"[DRY] qr   rotations={rots}")
    return (out_jpg if jpg_needed else None), (out_txt if txt_needed else None)


def write_folder_cover_and_ocr(
    ndpi_inside: Path,
    cfg: ThumbCfg,
    ocr_cfg: OcrCfg,
    qr_cfg: Optional[QrCfg] = None,
    dry_run: bool = False,
) -> tuple[Optional[Path], Optional[Path]]:
    """
    Create:
      /path/P1911642x40/folder.jpg
      /path/P1911642x40/folder.ocr.txt   (optional)

    Skips each output if up-to-date.
    """
    out_jpg = ndpi_inside.parent / cfg.folder_thumb_name
    out_txt = ndpi_inside.parent / cfg.folder_ocr_name if ocr_cfg.enabled else None

    jpg_needed = not is_up_to_date(ndpi_inside, out_jpg)
    txt_needed = (out_txt is not None) and (not is_up_to_date(ndpi_inside, out_txt))

    if not jpg_needed and not txt_needed:
        return None, None

    if dry_run:
        return _handle_dry_run(ndpi_inside, out_jpg, out_txt, jpg_needed, txt_needed, ocr_cfg, qr_cfg)

    slide = openslide.OpenSlide(str(ndpi_inside))
    try:
        _process_outputs(ndpi_inside, out_jpg, out_txt, ocr_cfg, slide, cfg, qr_cfg=qr_cfg)
        return (out_jpg if jpg_needed else None), (out_txt if txt_needed else None)

    finally:
        slide.close()


def iter_ndpi_files(root: Path) -> Iterable[Path]:
    return root.rglob("*.ndpi")


def _write_cover(ndpi_inside: Path, out_jpg: Path, slide: openslide.OpenSlide, cfg: ThumbCfg) -> None:
    cover_img = choose_image_for_cover(slide, cfg)
    cover_img.thumbnail(cfg.max_size)
    write_jpeg_atomic(out_jpg, cover_img, cfg)
    print(f"OK  {ndpi_inside} -> {out_jpg}")


def _build_text_output(ocr_src: Image.Image, ocr_cfg: OcrCfg, qr_cfg: Optional["QrCfg"] = None) -> str:
    ocr_text = ocr_image(ocr_src, ocr_cfg)

    # If QR is not used, keep output identical to before
    if qr_cfg is None or not qr_cfg.enabled:
        return ocr_text

    qr_text = decode_qr(ocr_src, qr_cfg)

    parts: list[str] = []
    if qr_text:
        parts.append("[QR]\n" + qr_text)
    parts.append("[OCR]\n" + ocr_text)
    return "\n\n".join(parts)


def _write_text(ndpi_inside: Path, out_txt: Path, slide: openslide.OpenSlide, cfg: ThumbCfg, ocr_cfg: OcrCfg, qr_cfg: Optional["QrCfg"] = None) -> None:
    ocr_src = choose_image_for_ocr(slide, cfg)
    text = _build_text_output(ocr_src, ocr_cfg, qr_cfg=qr_cfg)
    write_text_atomic(out_txt, text)
    suffix = " (OCR+QR)" if (qr_cfg and qr_cfg.enabled) else " (OCR)"
    print(f"OK  {ndpi_inside} -> {out_txt}{suffix}")


# ------------------------------
# Main
# ------------------------------
def main(root: Path, cfg: ThumbCfg, ocr_cfg: OcrCfg, qr_cfg: Optional[QrCfg] = None, dry_run: bool = False) -> int:

    if not root.exists():
        print(f"ERR root does not exist: {root}")
        return 2

    errors = 0
    for ndpi in iter_ndpi_files(root):
        try:
            ndpi_inside = move_into_slide_folder(ndpi, cfg, dry_run=dry_run)
            write_folder_cover_and_ocr(ndpi_inside, cfg, ocr_cfg, qr_cfg=qr_cfg, dry_run=dry_run)
        except Exception as e:
            errors += 1
            print(f"ERR {ndpi}: {e}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Organize NDPI slides into per-slide folders, create folder.jpg, and optionally OCR labels."
    )
    parser.add_argument("--root", required=True, help="Root directory containing NDPI files")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without moving/writing files")

    # OCR flags
    parser.add_argument("--ocr", action="store_true", help="Enable OCR and write folder.ocr.txt")
    parser.add_argument("--ocr-lang", default="jpn+eng", help='Primary OCR language, e.g. "jpn+eng"')
    parser.add_argument(
        "--ocr-lang-candidates",
        default=None,
        help='Comma-separated candidates, e.g. "jpn+eng,jpn,eng,jpn_vert"',
    )
    parser.add_argument(
        "--ocr-psm-candidates",
        default=None,
        help='Comma-separated PSMs, e.g. "6,11,4,3"',
    )
    parser.add_argument("--ocr-oem", type=int, default=3)
    parser.add_argument("--ocr-upscale", type=int, default=6)
    parser.add_argument("--ocr-threshold", type=int, default=None)
    parser.add_argument("--ocr-rotate", type=int, choices=[0, 90, 180, 270, -90], default=None)
    parser.add_argument("--ocr-no-crop-label", action="store_true", help="Disable label cropping")
    parser.add_argument("--ocr-auto-rotate", dest="ocr_auto_rotate", action="store_true", default=True)
    parser.add_argument("--no-ocr-auto-rotate", dest="ocr_auto_rotate", action="store_false")
    parser.add_argument("--qr", action="store_true", help="Decode QR codes and include in folder.ocr.txt")
    parser.add_argument(
        "--qr-rotations",
        default="0,-90",
        help='Comma-separated degrees to try for QR, e.g. "0,-90" or "0,270"',
    )

    args = parser.parse_args()

    if args.ocr_lang_candidates:
        lang_candidates = tuple(s.strip() for s in args.ocr_lang_candidates.split(",") if s.strip())
    else:
        # sensible defaults derived from primary
        lang_candidates = (args.ocr_lang, "jpn+eng", "jpn")  # "eng", "jpn_vert"

    if args.ocr_psm_candidates:
        psm_candidates = tuple(int(x.strip()) for x in args.ocr_psm_candidates.split(",") if x.strip())
    else:
        psm_candidates = (6, 11)  # 4, 3

    qr_cfg: Optional[QrCfg] = None
    if args.qr:
        qr_rots = tuple(int(x.strip()) for x in args.qr_rotations.split(",") if x.strip())
        qr_cfg = QrCfg(enabled=True, rotation_candidates=qr_rots)

    ocr_cfg = OcrCfg(
        enabled=args.ocr,
        lang_candidates=lang_candidates,
        psm_candidates=psm_candidates,
        # psm_candidates=(args.ocr_psm),
        oem=args.ocr_oem,
        upscale=args.ocr_upscale,
        threshold=args.ocr_threshold,
        auto_rotate=args.ocr_auto_rotate,
        rotate_degrees=args.ocr_rotate,
        crop_label=not args.ocr_no_crop_label
    )

    raise SystemExit(main(Path(args.root), CFG, ocr_cfg, qr_cfg=qr_cfg, dry_run=args.dry_run))
