# NDPI Whole-Slide Organizer — Thumbnails, OCR & QR Extraction

A command-line tool that organizes NDPI whole-slides into per-slide folders, generates Explorer-friendly JPEG thumbnails, and OCRs label/thumbnail text into adjacent .ocr.txt files for indexing and search.

## 1. Scope and objective

We are building a command-line repository that:

* Organizes NDPI whole-slide images into per-slide folders.
* Generates Explorer-friendly JPEG thumbnails (`folder.jpg`).
* Extracts label/thumbnail text using OCR and saves results as `folder.ocr.txt` for indexing and search.
* Optionally decodes QR codes from the same label/thumbnail image and includes the decoded payload alongside OCR output (only when `--qr` is specified).

Key issues addressed during refactoring:

* OCR quality degraded by label orientation and small text.
* OCR edge character loss due to crop/rotation interactions.
* Runtime increased due to excessive candidate search.
* macOS OpenSlide dylib load failures.
* Code-quality issues (unexpected kwargs, complexity, toggles not honored).
* Consistent, explicit CLI behavior for OCR/QR.

---

## 2. Repository behavior and outputs

### 2.1 Folder layout

Each slide `X.ndpi` is moved into a folder `X/slide.ndpi`:

* Input: `/path/X.ndpi`
* Output: `/path/X/slide.ndpi`

Within each slide folder:

* `folder.jpg` — JPEG thumbnail used by Windows Explorer folder preview.
* `folder.ocr.txt` — OCR output, and QR payload if enabled.

### Setup

```bash
cd /path/to/repository
python3 -m venv .venv
pip install -r requirements.txt
source .venv/bin/activate
```

### 2.2 CLI usage

Example run (OCR + QR):

```bash
python make_thumbs.py --root ./data --ocr --qr
```

Dry run:

```bash
python make_thumbs.py --root ./data --ocr --qr --dry-run
```

### Basic run (OCR only)

Generates `folder.jpg` and writes OCR output to `folder.ocr.txt`:

```bash
python make_thumbs.py --root ./data --ocr
```

### OCR + QR run

Generates `folder.jpg` and writes `[QR]` and `[OCR]` sections into `folder.ocr.txt`:

```bash
python make_thumbs.py --root ./data --ocr --qr
```

### Dry run (no file changes)

Shows what would be moved/written, without modifying files:

```bash
python make_thumbs.py --root ./data --ocr --qr --dry-run
```

### Common options

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
* Set QR rotation candidates:

  * `--qr-rotations "0,-90"`

### Expected outputs per slide folder

After running, each slide folder contains:

* `slide.ndpi`
* `folder.jpg`
* `folder.ocr.txt` (only when `--ocr` is enabled)

---

## 3. Dependency and environment notes

### 3.1 Tesseract

We use Tesseract via `pytesseract` with mixed-language candidates such as `"jpn+eng"` to support Japanese/English labels.

### 3.2 OpenSlide dylib issue (macOS)

We hit an OpenSlide loader error when `openslide-python` was installed but the OpenSlide dynamic library was not found.

Fix: include `openslide-bin` so the OpenSlide library is available on macOS.

### 3.3 Requirements

Current `requirements.txt` includes:

* `openslide-python`
* `openslide-bin`
* `Pillow`
* `pytesseract`
* `numpy`
* `opencv-python` (QR decoding via OpenCV `QRCodeDetector`)

---

## 4. OCR pipeline refactor

### 4.1 OCR configuration (`OcrCfg`)

The OCR config supports:

* Language candidates (`lang_candidates`)
* PSM candidates (`psm_candidates`)
* Preprocessing: upscale, contrast, optional threshold, sharpen
* Rotation strategy: forced rotation or a small candidate set when auto-rotating
* Label cropping toggle and heuristic fallback
* Early stop threshold for speed

#### Recommendation status integrated

* **8.3 Make OCR crop toggle effective — Implemented.**
  `--ocr-no-crop-label` is honored when building `OcrCfg` (label cropping can be turned off).

* **8.4 Ensure auto-rotate toggle is honored — Implemented.**
  `--no-ocr-auto-rotate` is wired into `OcrCfg.auto_rotate`. Duplicate/competing controls were consolidated so the dedicated toggle is the authoritative control.

### 4.2 Candidate search and performance

We use a two-stage approach:

1. Score candidates using `pytesseract.image_to_data` (confidence-based).
2. Run `pytesseract.image_to_string` once for the best candidate.

We reduce runtime by:

* Restricting rotation candidates to a small set.
* Keeping regions minimal (label-first).
* Early stopping when confidence exceeds `early_stop_conf`.

### 4.3 Edge character preservation

To reduce glyph loss at edges:

* We pad candidate images with a white border before OCR.
* We avoid trimming left edges.
* We only create a “trim bottom” variant in controlled cases.

---

## 5. Label crop heuristic improvements

The crop heuristic was made more robust by:

* Computing column mean intensity over the middle vertical band of rows (reduces border/corner interference).
* Searching for the separator band only in a bounded x-range (avoids locking onto borders).
* Enforcing a minimum crop width and adding a margin.
* Falling back to a fixed left-width ratio when no separator is detected.

---

## 6. QR decoding integration

### 6.1 QR config (`QrCfg`) and decoding

* QR decoding uses OpenCV’s `cv2.QRCodeDetector`.
* We try configured rotation candidates for QR decoding.
* Output is merged into the same `folder.ocr.txt` file.

#### Recommendation status integrated

* **8.1 Ensure `--qr` is required for QR decoding — Implemented.**
  QR decoding runs only when `--qr` is specified. `QrCfg` is built in `__main__` and passed into processing functions.

* **8.2 Align QR rotation config with chosen rotation set — Partially implemented.**
  Defaults are aligned between OCR and QR configs. `--qr-rotations` exists and can be set to match OCR rotation candidates.
  Remaining gap: QR rotations are not automatically derived from OCR rotations. If OCR is forced to a specific rotation, QR still uses `--qr-rotations` unless we also change it explicitly.

### 6.2 Output format

When `--qr` is enabled:

```
[QR]
<decoded payload>

[OCR]
<ocr text>
```

When `--qr` is not enabled:

* Output remains OCR-only.

---

## 7. `make_thumbs.py` refactor and structure

### 7.1 Cover generation

* Prefer embedded associated images (`thumbnail`, `macro`, `label`) for `folder.jpg`.
* Fallback to `slide.get_thumbnail()`.

### 7.2 OCR source selection

* Prefer associated images (`label`, `macro`, `thumbnail`) for OCR.
* Fallback to a larger render size if needed.

### 7.3 Atomic writes

* Both JPEG and text outputs are written via temp files and replaced atomically to reduce partial writes on SMB.

### 7.4 QR is not hardcoded

* QR is enabled only via `--qr`.
* `QrCfg` is constructed in `__main__` and passed down through processing functions.

---

## 8. Summary of decisions

* Use `openslide-bin` to avoid OpenSlide loader failures on macOS.
* Use mixed-language OCR candidates (notably `"jpn+eng"`).
* Improve label cropping robustness with middle-band analysis and bounded separator detection.
* Improve OCR stability and speed using preprocessing, candidate scoring, padding, and early stopping.
* Add optional QR decoding via OpenCV, enabled only via `--qr`, and merge output with OCR in `folder.ocr.txt`.
* Refactor for maintainability by separating cover writing, OCR/QR text building, and low-level OCR/QR utilities.

If we want to close the remaining gap in QR/OCR rotation alignment, we can auto-default QR rotations to the OCR rotation set when `--qr-rotations` is not provided (while still allowing explicit override).
