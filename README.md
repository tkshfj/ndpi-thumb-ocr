# NDPI Whole-Slide Organizer — Thumbnails, OCR & QR Extraction

A command-line tool that organizes NDPI whole-slides into per-slide folders, generates Explorer-friendly JPEG thumbnails, and OCRs label/thumbnail text into adjacent .ocr.txt files for indexing and search.

## 1. Scope and objective

We are building a command-line repository that:

* Organizes NDPI whole-slide images into per-slide folders.
* Generates Explorer-friendly JPEG thumbnails (`folder.jpg`).
* Extracts text from slide label/thumbnail images using OCR and writes results next to the thumbnail as `folder.ocr.txt`.
* Optionally decodes QR codes from the same label/thumbnail image and includes the decoded payload alongside OCR output.

Primary pain points addressed during refactoring:

* Poor OCR accuracy due to rotated label images.
* OCR output losing edge characters due to rotation + cropping interactions.
* Excessive runtime caused by too many OCR candidate combinations.
* Dependency issues on macOS regarding OpenSlide dylib availability.
* Code-quality issues (unused parameters, undefined names, cognitive complexity, unexpected keyword args).
* Consistent configuration and CLI behavior for OCR/QR features.

---

## 2. Repository behavior and outputs

### 2.1 Folder layout

Each slide `X.ndpi` is moved into a folder `X/slide.ndpi`:

* Input: `/path/X.ndpi`
* Output: `/path/X/slide.ndpi`

Within each slide folder:

* `folder.jpg` — JPEG thumbnail used by Windows Explorer folder preview.
* `folder.ocr.txt` — OCR and optional QR decoded results.

### 2.2 CLI usage

Example run (OCR + QR):

Generates `folder.jpg` and writes `[QR]` and `[OCR]` sections into `folder.ocr.txt`:

```bash
python make_thumbs.py --root ./data --ocr --qr
```

OCR only:

Generates `folder.jpg` and writes OCR output to `folder.ocr.txt`:

```bash
python make_thumbs.py --root ./data --ocr
```

Dry run:

Shows what would be moved/written, without modifying files:

```bash
python make_thumbs.py --root ./data --ocr --qr --dry-run
```

#### Common options

* Enable OCR output:

  * `--ocr`
* Enable QR decoding (only runs when specified):

  * `--qr`
* Set OCR language candidates:

  * `--ocr-lang-candidates "jpn+eng,jpn,eng"`
* Set OCR PSM candidates:

  * `--ocr-psm-candidates "6,11"`
* Force a specific rotation (if needed):

  * `--ocr-rotate -90` (example)
* Disable label cropping:

  * `--ocr-no-crop-label`
* Disable auto-rotate:

  * `--no-ocr-auto-rotate`

#### Expected outputs per slide folder

After running, each slide folder contains:

* `slide.ndpi`
* `folder.jpg`
* `folder.ocr.txt` (only when `--ocr` is enabled)

---

## 3. Dependency and environment notes

### 3.1 Tesseract (macOS Homebrew)

We validated Tesseract installation:

* `tesseract --version` showed Tesseract 5.5.2.
* `tesseract --list-langs` confirmed `eng`, `jpn`, and many others installed.
* Homebrew prefix: `/opt/homebrew/opt/tesseract`

We are using `lang_candidates` including `"jpn+eng"` to support mixed Japanese/English labels.

### 3.2 OpenSlide dylib issue (macOS)

When running OCR/QR extraction, we hit an OpenSlide loader error indicating that `openslide-python` bindings were present but the OpenSlide dynamic library was not available.

Fix: include `openslide-bin` in dependencies (macOS-friendly wheel shipping OpenSlide library).

### 3.3 Requirements

Refactored `requirements.txt` includes:

* `openslide-python`
* `openslide-bin`
* `Pillow`
* `pytesseract`
* `numpy`
* `opencv-python` (for QR decoding via `cv2.QRCodeDetector`)

---

## 4. OCR pipeline refactor

### 4.1 Problem: OCR on rotated thumbnails

Initial OCR output was poor because label images were not aligned with expected text orientation.

We introduced rotation search and scoring:

* Try candidate rotations and select the best result based on confidence derived from `pytesseract.image_to_data`.
* Then run `image_to_string` only once for the best candidate.

### 4.2 OCR config (`OcrCfg`)

Final refactored `OcrCfg`:

```python
@dataclass(frozen=True)
class OcrCfg:
    enabled: bool = False
    lang_candidates: tuple[str, ...] = ("jpn+eng", "jpn", "eng")
    psm_candidates: tuple[int, ...] = (6, 11)

    oem: int = 3
    upscale: int = 6
    threshold: Optional[int] = None
    sharpen: bool = True

    auto_rotate: bool = True
    rotate_degrees: Optional[int] = None
    rotation_candidates: tuple[int, ...] = (0, -90)

    crop_label: bool = True
    label_width_ratio: float = 0.33
    early_stop_conf: float = 75.0
```

### 4.3 Candidate generation approach

* Crop label region first.
* Rotate each candidate from the configured rotation set.
* Pad with white border to reduce edge glyph loss.
* Optionally generate a “trim bottom” candidate only for the unrotated orientation.

Key logic:

```python
for deg in rotations:
    rot = _rotate(rimg, deg)
    yield (_pad_white(rot), lang, psm)
    if deg == 0:
        yield (_pad_white(_trim_bottom(rot, 0.25)), lang, psm)
```

### 4.4 Preprocessing

`preprocess_for_ocr` includes:

* grayscale
* upscale (LANCZOS)
* autocontrast
* contrast boost
* optional threshold
* unsharp mask

```python
g = img.convert("L")
g = g.resize((w * cfg.upscale, h * cfg.upscale), resample=Image.Resampling.LANCZOS)
g = ImageOps.autocontrast(g)
g = ImageEnhance.Contrast(g).enhance(1.5)
if cfg.threshold is not None:
    g = g.point(lambda x: 255 if x >= t else 0, mode="1").convert("L")
if cfg.sharpen:
    g = g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
```

---

## 5. Label crop heuristic improvements

### 5.1 Problem: crop heuristic was sensitive

Earlier logic using full-height column means could be thrown off by borders, dark tissue/pen marks, or slide-holder regions.

### 5.2 Fix: compute mean over middle band and constrain search width

Implemented in `find_label_crop_box`:

* Use only middle band of rows.
* Use a slightly higher “dark” threshold.
* Search for separator only within a bounded X-range.
* Add crop margin and minimum crop width.

Core code:

```python
y0 = int(h * 0.15)
y1 = int(h * 0.85)
band = arr[y0:y1, :]
col_mean = band.mean(axis=0)
dark = col_mean < 60

x_start = int(w * 0.10)
x_end = int(w * 0.70)

# find longest dark run
...
right = max(int(w * 0.20), start + 10)
right = min(w, right)
return (0, 0, right, h)
```

Fallback remains:

```python
right = int(w * cfg.label_width_ratio)
return (0, 0, max(1, right), h)
```

---

## 6. Runtime performance improvements

### 6.1 Problem: OCR became slower after adding candidate search

A brute-force search across:

* regions × rotations × lang candidates × psm candidates

can be expensive.

### 6.2 Fixes applied

* Restrict rotation candidates to a small set.
* Keep region set small (label-first).
* Two-stage OCR:

  * `image_to_data` for scoring.
  * `image_to_string` once for final.
* Early stop when confidence exceeds `early_stop_conf`.

---

## 7. QR decoding integration

### 7.1 Goal

In addition to OCR, decode QR codes embedded on label images and include decoded payload in output.

### 7.2 QR config (`QrCfg`)

```python
@dataclass(frozen=True)
class QrCfg:
    enabled: bool = False
    rotation_candidates: tuple[int, ...] = (0, -90)
```

### 7.3 QR decoding (`decode_qr`)

Using OpenCV:

```python
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
```

### 7.4 Combined output format

When QR is enabled:

```
[QR]
<decoded payload>

[OCR]
<ocr text>
```

When QR is disabled:

* Output remains OCR-only (backward compatible).

---

## 8. `make_thumbs.py` refactor and structure

### 8.1 Cover/thumbnail generation

* Prefer associated images for cover: `thumbnail`, `macro`, `label`.
* Fallback to rendered thumbnail.

### 8.2 OCR source selection

Prefer:

* `label`
* `macro`
* `thumbnail`

Fallback to larger render for OCR:

```python
return slide.get_thumbnail(cfg.ocr_render_size).convert("RGB")
```

### 8.3 Atomic writes

* JPEG: write `.tmp` then replace.
* Text: write `.tmp` then replace.

This reduces partial writes on SMB.

### 8.4 Output orchestration

Refactored `_process_outputs` delegates to `_write_cover` and `_write_text`:

```python
if not is_up_to_date(ndpi_inside, out_jpg):
    _write_cover(...)

if out_txt is not None and not is_up_to_date(ndpi_inside, out_txt):
    _write_text(..., qr_cfg=qr_cfg)
```

Text builder merges QR + OCR:

```python
ocr_text = ocr_image(ocr_src, ocr_cfg)
qr_text = decode_qr(ocr_src, qr_cfg) if qr_cfg.enabled else None
```

---

## 9. Code quality and linting concerns addressed

We encountered and addressed issues including:

* Removing unused function parameters in earlier versions.
* Fixing undefined names by aligning imports and helper usage.
* Eliminating unexpected named arguments by aligning function signatures.
* Reducing cognitive complexity by extracting nested logic into helpers.
* Resolving static-analysis warnings by simplifying nested conditions and loop structure.

---

## 10. Remaining recommended refinements

### 10.1 Ensure `--qr` is required for QR decoding

QR decoding should run only when `--qr` is specified, and QR configuration should be created in `__main__` and passed down to processing functions.

### 10.2 Align QR rotation config with chosen rotation set

Ensure QR decoding uses the same rotation candidate set as OCR.

### 10.3 Make OCR crop toggle effective

Honor `--ocr-no-crop-label` when building `OcrCfg`:

```python
crop_label=not args.ocr_no_crop_label
```

### 10.4 Ensure auto-rotate toggle is honored

Use the dedicated argparse toggle (`--no-ocr-auto-rotate`) consistently when constructing `OcrCfg`, and avoid duplicate flags that control the same behavior.

---

## 11. Reference code excerpts (final refactored core)

### 11.1 OCR scoring + final OCR

```python
def ocr_image(img: Image.Image, cfg: "OcrCfg") -> str:
    best = None
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
```

### 11.2 Output writer

```python
def _write_text(..., qr_cfg: Optional["QrCfg"] = None) -> None:
    ocr_src = choose_image_for_ocr(slide, cfg)
    text = _build_text_output(ocr_src, ocr_cfg, qr_cfg=qr_cfg)
    write_text_atomic(out_txt, text)
```

---

## 12. Summary of decisions

* Include `openslide-bin` to avoid OpenSlide loader errors on macOS.
* Use Tesseract language candidates including `"jpn+eng"` for mixed-language labels.
* Improve label cropping via middle-band intensity analysis and bounded separator detection.
* Improve OCR robustness with preprocessing, candidate scoring, padding, and early stopping.
* Integrate optional QR decoding via OpenCV and merge QR payload with OCR output.
* Refactor for maintainability by separating concerns into helper functions and utility modules.
