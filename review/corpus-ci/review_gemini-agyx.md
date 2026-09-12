# Review Findings: `squint` HEIC & CI Patch

### 1. `heif_variants` byte offset & parser desyncs (Questions 3 & 4)
- **Mechanism:** In `tools/corpus-gen.py`, `iloc` parsing reads `base_offset_size` and `index_size` at offset `body_start + 5`. But in ISOBMFF / HEIF (`ISO/IEC 14496-12`), if `version >= 1`, the byte at `body_start + 4` contains `offset_size` (bits 4–7) and `length_size` (bits 0–3), while `body_start + 5` contains `base_offset_size` (bits 4–7) and `index_size` (bits 0–3). However, if `version == 0`, `body_start + 5` does NOT exist or carries reserved/count bits; `base_offset_size` and `index_size` are strictly 0. Furthermore, when parsing items:
  ```python
  if version >= 1:
      p += 2  # skips construction_method
  ```
  `construction_method` is 2 bytes only in `version >= 1`. But `corpus-gen.py` unconditionally reads `base_offset` using `base_offset_size` regardless of whether `version >= 1`.
  More critically, when rewriting the extent offset or length:
  ```python
  if offset_size > 0:
      new_offset = max(0, target_end - ext_len - base_offset)
      b[ext_off_pos:ext_off_pos + offset_size] = new_offset.to_bytes(offset_size, "big")
  ```
  If `offset_size` is 4 bytes and `target_end` is `len(d) + 1048576`, `new_offset` frequently exceeds $2^{32}-1$ (4,294,967,295) for files larger than a few megabytes or with large base offsets. `to_bytes(4, "big")` throws `OverflowError: int too big to convert`, causing `corpus-gen.py` to crash during generation.
- **Primary item `ispe` targeting:** `heif_variants` mutates `ispes[:2]` under `meta/iprp/ipco`. `ipco` contains properties in array index order (1-indexed). The primary item (or its tiles) is linked via `ipma` (item property association). In HEIC files created by Apple `sips`, index 0 in `ipco` is often the image spatial extents (`ispe`) for grid tiles or thumbnail items, not the primary image item. As noted in the PR description, mutating `ispe-0` decodes normally because macOS Image I/O ignores tile `ispe`s in favor of the primary item's `ispe` or `meta` layout. To reliably mutate the primary item's `ispe`, `corpus-gen.py` must inspect `pitm` (primary item ID), look up its associated property indices in `ipma`, and mutate that specific `ispe` index in `ipco`.

---

### 2. CI Pipeline Exit Code Swallowing & False Clean Invocations (Question 4)
- **Mechanism:** In `.github/workflows/ci.yml`:
  ```yaml
  - name: run corpus
    run: python3 tools/corpus-run.py core/target/release/squint corpus -o corpus-report.json
  ```
  `corpus-run.py` returns exit code 1 when `dangerous` runs are found (line 599) and 2 when harness errors occur (line 591). However, in GitHub Actions, if step exit code 1 or 2 is returned, the step fails—which is correct. BUT on `macos-latest` runners in GitHub Actions:
  1. `sips` on GitHub's macOS runner images can behave differently or fail to render HEIC depending on missing codec extensions or headless hardware context.
  2. If `sips` cannot decode the seed file or fails during `corpus-seeds.py`, `corpus-seeds.py` exits with code 3 (line 721):
     ```python
     if res.returncode != 0:
         err_msg = res.stderr.strip()
         print("HEIC seed unavailable: %s" % err_msg)
         sys.exit(3)
     ```
     This causes the GHA step `generate seeds` to fail completely on macOS runners if `sips` HEIC generation is unsupported, blocking CI rather than testing PNG/JPEG.
  3. Conversely, if `python3 tools/corpus-run.py` fails due to an unexpected exception inside `run_one` (e.g. `sips` error parsing output), `run_one` catches `OSError` but NOT `subprocess.CalledProcessError` or JSON decode errors, which could abort execution mid-run or crash before evaluating `dangerous`.

---

### 3. Ineffective `decodes()` check & Missing cheap validation for Non-Email Modes (Question 1)
- **Mechanism:** `corpus-run.py` checks `decodes(out, workdir)` using `sips -s format png --out probe.png path`.
  `sips` (Apple Image I/O) is extremely permissive:
  - **Sheared/Corrupted images:** If `squint` processes a HEIC/JPEG and writes an output with incorrect stride or missing line padding (causing pixel shearing), `sips` still successfully converts the raw decompressed pixel buffer to PNG (returning exit code 0). `decodes()` returns `True`, and `dangerous` is **0**.
  - **Black/Solid-color output:** If `squint` output is truncated or corrupted such that the engine fills the buffer with black/zeros (the second defect described in the prompt), `sips` successfully encodes a solid black PNG. `decodes()` returns `True`, `dangerous` remains **0**, and CI reports a **false clean**.
- **Cheap reference-less checks needed:**
  1. **Dimensions match check:** For `fast`, `quality`, and `strip` modes, output pixel dimensions MUST equal input pixel dimensions exactly (`dims_out == dims_in`). Currently, `corpus-run.py` ONLY checks `dimensions` for `email` mode (max edge $\le 2048$). If `fast` or `strip` mode outputs a 1x1 or cropped image, `corpus-run.py` ignores the dimension mismatch.
  2. **Non-zero / Non-uniform entropy check:** A simple, cheap header-less sanity check on the decoded output file size or standard deviation of pixel bytes (e.g., checking if the output file compressed size is suspiciously small or if raw decompressed sample entropy is near 0) flags black/blank images without requiring full visual image comparison against a reference.

---

### 4. Non-Deterministic Seeds Across Environments (Question 2)
- **Mechanism:** In `tools/corpus-seeds.py`:
  - `seed.png`: Uses `PIL.Image` if installed, otherwise `write_fallback_png`. Pillow uses `libpng` and `zlib` with default compression levels and chunk ordering (e.g., `sRGB`, `gAMA` chunks inserted by Pillow), whereas `write_fallback_png` writes raw `IHDR`, `IDAT`, `IEND` with `zlib.compress()`. The resulting `seed.png` byte stream differs completely between a dev machine with Pillow and a CI runner without it.
  - `seed.jpg` and `seed.heic`: Created via `sips -s format jpeg` / `heic`. `sips` output is dependent on macOS system version, libJPEG/HEIF encoder settings, and OS release build numbers. `seed.jpg` and `seed.heic` generated on macOS 14 will have different SHA-256 digests than those generated on `macos-latest` (macOS 15+).
- **Impact:** While `corpus-gen.py` records `seed_sha256` in `manifest.json`, mutation logic that relies on fixed byte offsets (e.g. `jpeg_segments`, `png_chunks`, `ispe` positions) will target different internal structures if seed layouts shift across OS/dependency versions, breaking cross-machine test outcome determinism.

---

### 5. `iloc-past-end-K` Extent Arithmetic Bug in `corpus-gen.py` (Question 3)
- **Mechanism:** In `tools/corpus-gen.py` (lines 327–336):
  ```python
  if offset_size > 0:
      new_offset = max(0, target_end - ext_len - base_offset)
      b[ext_off_pos:ext_off_pos + offset_size] = new_offset.to_bytes(offset_size, "big")
  ```
  `target_end` is calculated as `len(d) + 1024 * 1024`.
  When `base_offset` is non-zero (or when `offset_size` is less than 8 bytes, e.g., `offset_size == 4`), `new_offset` can easily exceed `0xFFFFFFFF` if `len(d)` is large or `base_offset` is offset relative to the file start. When `offset_size == 4`, any value $\ge 2^{32}$ causes `to_bytes(4, 'big')` to throw an unhandled `OverflowError`, crashing `corpus-gen.py`.

---

### Summary Table of Findings

| Rank | Issue | File & Line | Impact |
| :--- | :--- | :--- | :--- |
| 1 | `sips` solid/sheared image false clean | `tools/corpus-run.py:459` | CI passes corrupted/black outputs as 0 dangerous. |
| 2 | `iloc` offset arithmetic `OverflowError` | `tools/corpus-gen.py:329` | Script crashes on 4-byte `offset_size` HEIC inputs. |
| 3 | Un-targeted `ispe` mutations | `tools/corpus-gen.py:348` | Mutates ignored tile `ispe`s instead of primary image `ispe`. |
| 4 | Seed generation non-determinism | `tools/corpus-seeds.py:690` | Seed SHA-256 varies by OS version and Pillow presence. |
| 5 | Lack of dimension equality check for non-email modes | `tools/corpus-run.py:552` | Aspect/size changes in `fast`/`strip` modes missed. |
