# Council brief — WebP and AVIF join the read tiers of a macOS image optimizer

You are reviewing a Rust diff. Answer in your final message. Read the files listed at the end if your tooling lets you; run nothing, write nothing, edit nothing.

## The project

`squint` is a native macOS image optimizer (GPL-3, public, Rust core + SwiftUI app). It replaces ImageOptim. Its distinguishing behaviours:

- It encodes to a **perceptual target** (SSIMULACRA2 via `fast-ssim2`) rather than a fixed quality number.
- It **never grows a file** — if the smallest result meeting the target is larger than the input, it refuses and keeps the original.
- It **always keeps the ICC colour profile**, in every mode. Dropping it desaturates any wide-gamut photograph, and that is the project's main differentiator against other metadata removers.
- **Strip mode** removes metadata losslessly with no re-encode, *by exclusion* — it keeps a known list and drops everything else, so unknown and future metadata formats go without being named.

## Existing structure you need to know

`Source::open` in `core/src/source.rs` is the single decode door. Every format arrives as RGB8 with orientation baked in. There are three read tiers:

1. **Convert tier** (HEIC, SVG): decode to pixels, re-encode as JPEG written *beside* the original. `converted_from: Some("HEIC")`.
2. **Strip-only tier** (GIF, TIFF): `optimize()` returns `Err(Error::ReadOnlyFormat)` for Fast/Quality; Strip mode works and rewrites the same format.
3. **Native tier** (JPEG, PNG): full re-encode, same format.

`core/src/heif.rs` handles ISOBMFF. Its strip **overwrites metadata payloads in place** rather than removing them, because every `iloc` offset in an ISOBMFF file is absolute and cutting bytes out would require rewriting all of them.

## What this diff adds

**AVIF** joins the convert tier. AVIF is the same ISOBMFF tree as HEIF with AV1 in the tiles instead of HEVC. macOS Image I/O already decodes it and `CGImageSourceCreateWithData` is format-agnostic, so recognising the `ftyp` brand is the whole of the decode work; the existing strip walk is likewise indifferent to the codec. `is_heif` now excludes AVIF and `container_name` supplies the label. Compatible brands are consulted as well as the major brand, because `mif1` leads many real AVIFs and is in the HEIF brand list.

**WebP** joins the convert tier, except animations. A WebP is a RIFF file: a 12-byte header then a chain of chunks, each a 4-byte FourCC, a little-endian u32 payload length, the payload, and a pad byte when the length is odd (the pad is not counted in the length). Unlike ISOBMFF, nothing in a RIFF refers to another part of the file by offset, so the strip genuinely removes chunks and rebuilds, and the file shrinks.

- Still WebP: decoded by the `image` crate (`webp` feature → `image-webp`, pure Rust). Written back as JPEG.
- Animated WebP (`ANIM`/`ANMF` present): refused with `ReadOnlyFormat`, because every decoder hands back only the first frame and there is no animated encoder here. Strip still works.
- The colour profile is read out of the `ICCP` chunk, since the JPEG-oriented `extract_icc` knows nothing of RIFF.
- A `VP8X` extended header announces which optional chunks the file carries; the flags for chunks the strip removes are cleared.

## What has already been verified — do not re-report these as unknowns

- 67 unit/integration tests pass on macOS (Apple silicon, `cargo test --release`).
- Real files, generated with `cwebp` and Pillow and run through the CLI: a bare `VP8 ` still, a `VP8X`+`ICCP`(536 B Display P3)+`VP8 ` still, a `VP8X`+`VP8 `+`EXIF` still, and a 4-frame animation.
- Measured: the EXIF file went 420 → 318 bytes; the `EXIF` chunk is gone, the string `ACME Camera Co` is gone from the bytes, the `VP8X` flag byte went `0x08` → `0x00`, the RIFF size field is consistent, and Pillow still decodes the result with no EXIF.
- The Display P3 profile survives into the output JPEG: 536 bytes in, 536 bytes out.
- The animation was refused with the `ReadOnlyFormat` message.
- The project's malformed-input corpus (`tools/corpus-run.py`, JPEG segment-length and truncation fuzzing) reports `DANGEROUS RUNS: 0`.
- `image-webp` was already resolved in `Cargo.lock` as an optional dependency of `image`, so enabling the feature adds a dependency edge and no new crate.

## Questions, in priority order

1. **Is the RIFF walk in `webp.rs` actually safe on hostile input?** It is reached from a Finder context menu on arbitrary files. Look for integer overflow, panics on slicing, unbounded allocation, and any path where a malformed file is accepted as valid rather than refused. The rule the code is trying to enforce is that the walk must land *exactly* on the end of the file.
2. **Is the strip's keep-list correct?** Is any chunk in `KEPT_CHUNKS` actually metadata that should go, and is any chunk not in it actually picture that must stay? Consider `ALPH`, and consider what a WebP can carry that this list has not anticipated.
3. **Is `is_animated` reliable?** It walks for `ANIM`/`ANMF` rather than reading the `VP8X` animation flag bit. Is there a file where that gives the wrong answer, and what happens downstream if it does?
4. **Is the AVIF/HEIF split in `heif.rs` correct?** Particularly `compatible_brands` — its bounds arithmetic, and whether excluding AVIF from `is_heif` breaks any caller.
5. **Does clearing the `VP8X` flags risk producing an invalid file?** The code assumes `VP8X` is always the first chunk when present. Is that guaranteed, and is the offset arithmetic (`out[20]`) right?
6. **Anything in the error paths that fails silently** — a case that returns success having done nothing, or that reports a file as changed when it was not.

Report each finding with a file and line reference and, where you can, the concrete input that triggers it. Distinguish things you have confirmed by reading the code from things you suspect. If you think a claim in "already verified" above is wrong, say so.

## Files

- `core/src/webp.rs` — the new module (456 lines, including tests)
- `core/src/heif.rs` — ISOBMFF, with the new AVIF split (544 lines)
- `core/src/source.rs` — the decode door (331 lines)
- `review/webp-avif/wiring.diff` — the `lib.rs` and `Cargo.toml` changes
