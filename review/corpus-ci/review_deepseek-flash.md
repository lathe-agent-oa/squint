## 1. The harness cannot see the two defects it exists to catch (false clean, highest impact)

`corpus-run.py`, `main()` — every dangerous check is gated on `if before and r.get("exit") == 0 and r.get("wrote_output")`, where `before = decodes(src, ...)`. The truncation family is the family most likely to produce the black-JPEG defect, and it is exactly the family where Apple's decoder is most likely to refuse the input. For any file where `before` is False, **no output check runs at all**: squint can exit 0 and write a black JPEG over a truncated HEIC and the run scores clean. The shipped defect B is structurally invisible.

Worse, nothing asserts that anything was *tested*. `readable = sum(... r["input_decodes"])` is printed (`inputs the system decoder opens: 0/74`) but only `wrote == 0 and readable` fails the run. `readable == 0` — every probe refused, the entire corpus scored vacuous — exits 0 and prints a clean table.

Fix: (a) add a positive control — one unmutated `seed.{png,jpg,heic}` through each mode, asserting exit 0, output decodes, dimensions preserved, and for `strip` that output bytes ≠ input bytes; that single control catches missing binary, wrong binary, and engine-broken-on-healthy-input, none of which the current corpus tests. (b) Make refusal an *assertion*, not a precondition: split families in the manifest into `expect-refused` (trunc-*, iloc-past-end-*) and `expect-output`; in the first, exit 0 + wrote output is DANGEROUS regardless of `before`. (c) `readable == 0 and wrote > 0` is also a harness failure.

## 2. `decodes()` checks format, not picture

`decodes()` (corpus-run.py:12) does a full sips conversion — correct, and better than `-g pixelWidth` — but a sheared raster and a correctly-strided one are both valid PNGs. Defect A passes every check in the file: exit 0, decodes, and the "wrong size" branch never runs outside email mode. There is no cheap content check anywhere.

The seed is synthetic with a *known separable structure* (R depends on x, G on y, B ≈ 200, bounded noise). So the cheap check is real: after each run, `sips` the output down to a small PNG (or read `-g` plus a 16×16 block-mean sample), and compare per-column mean(R) and per-row mean(G) against the same statistics from the input. A stride error decorrelates rows and moves the column means sharply; an orientation flip transposes them; an all-black output collapses them to zero against a seed whose red channel spans 0–255. No reference image needed, no per-pixel JPEG reconciliation, tolerant of the lossy mode.

## 3. The email size check is vacuous on this corpus

`seed.png` is 1201×800. `email` mode is `--max-dimension 2048`, so the long edge is already 1201 and the resize path never executes; `max(w, h) > 2048` is unreachable unless the engine *upscales*. The cap logic itself — the thing the mode exists to test — is not exercised at all. Make the seed larger than the cap (2401×1601 keeps the odd width for stride bugs and forces a real downscale), or add a second oversized seed.

The mode-agnostic analogue is missing entirely: `fast`/`quality`/`strip` must preserve the input's dimensions, and nothing compares output dims to input dims. An output of 1×1 in any non-email mode passes. `input_decodes` is computed but `get_dimensions` is never called on the input.

## 4. HEIF mutations that mutate nothing, and are counted anyway

Three places where a variant is generated, hashed, listed and scored without any evidence it changed something meaningful:

- `heif_variants`, iloc: `new_offset = max(0, target_end - ext_len - base_offset)`. With a large `base_offset` (the `base_offset_size == 8` case the review prompt asks about), this clamps to 0 and writes a **fully in-bounds extent at offset 0** — reading from `ftyp`/`meta` — while the manifest note still claims "ends 1 MiB past file end". The engine accepts it as complete. Same clamp on the length branch. The mutation's *intent* is asserted only in prose.
- `heif_variants`, iloc: when `offset_size == 0` and `length_size == 0`, the else-branch executes `new_length.to_bytes(0, "big")` → `OverflowError`, crashing the generator mid-corpus with a bare traceback.
- `jpeg_variants`: `struct.pack(">H", max(2, ln + delta))` is a no-op when `ln == 2` and `delta == -1`; the variant is byte-identical to the seed and is still written and counted. `struct.pack(">H", 65536)` raises on a segment declaring 0xFFFF.

Cheap universal guard: in `corpus-gen.py`'s `main()`, assert `blob != d` for every variant and record `bytes` changed; a zero-diff variant is a generator bug, not a pass.

On targeting the primary item: `ispes[:2]` walks `ipco` in file order and ignores `ipma` entirely, so which `ispe` is the primary item's is accident. The measured result confirms the accident — `ispe-wrong-0-*` decodes normally because index 0 is a tile's. `ipco` ordering is not stable across macOS versions or single-tile vs grid files; a different seed silently turns all six `ispe` variants into no-ops that score as passes. Parse `pitm` for the primary item id, then `ipma` for its property indices, and mutate that `ispe`; assert the association was found.

## 5. Determinism and the manifest

Not byte-identical, and it matters more than the `seed_sha256` field suggests. `seed.png` differs between Pillow (compression level, filter selection) and the fallback writer, and even fallback-vs-fallback across zlib versions. `seed.jpg`/`seed.heic` are `sips`/ImageIO output, so they differ across macOS builds. The manifest records `seed_sha256` but **nothing ever verifies it** — not `corpus-gen.py`, not `corpus-run.py`. The seed itself isn't shipped. A red macOS job therefore cannot be reproduced locally, and a silent seed change (new runner image) rewrites every variant hash with no signal. Verify the recorded hash in `corpus-run.py` before running, and fail if the seed files aren't the ones the manifest was built from.

## 6. CI

Exit codes: `corpus-run.py` returns 2 (harness) or 1 (dangerous), and the `run corpus` step has no `continue-on-error`, so **both fail the job** — as written, correct. `if: always()` on the artifact keeps the report. Failures are loud elsewhere too: missing/broken `sips` → seeds exit 3; missing binary → exit 2.

The vacuous path is `readable == 0` (§1). Also: the corpus only ever runs in the macOS job, so a Python syntax error in `tools/*.py` reaches CI only via macOS; the Linux job never even `py_compile`s them. `python3` on macos-latest resolves to whatever Homebrew/CLT provides — the scripts use nothing above 3.8, so 3.9 is fine.

## 7. Regressions

- `family` is `re.sub(r"-\d+$", "", suffix)`, which aggregates `iloc-past-end-0/1/2` and `seglen-...-N` but leaves `ispe-wrong-0-huge`, `ispe-wrong-1-huge` as separate families. The table doesn't aggregate the variants most in need of it.
- `email` forces the output extension to `.jpg` and never checks the output's magic bytes, so "engine wrote PNG named `.jpg`" passes.
- `core/src/source.rs` moving to `cfg(all(test, target_os = "macos"))` removes those tests from the Linux job silently; fine if every path in them is genuinely macOS-only, otherwise it is lost coverage with no marker.
