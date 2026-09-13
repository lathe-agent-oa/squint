### 1. `strip_gif` Desync, Out-of-Bounds Read, and False Trailer Success
- **Files & Lines:** [`core/src/gif.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/gif.rs#L663-L691), [`L722-L734`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/gif.rs#L722-L734)
- **Mechanism:** In both extension (`0x21`) and image descriptor (`0x2C`) sub-block parsing loops, the `while pos < bytes.len()` loop terminates when `pos` reaches `bytes.len()`. The validation check `if pos > bytes.len() || bytes[pos - 1] != 0x00` inspects `bytes[pos - 1]`, which points to the *last byte of the sub-block payload* rather than a zero-length terminator byte (`0x00`). If a truncated sub-block's final payload byte happens to be `0x00`, the parser accepts the block as cleanly terminated. If `pos` desynchronizes into LZW compressed image data or an unknown extension block and encounters byte `0x3B`, `strip_gif` treats it as a valid GIF trailer, appends `0x3B`, sets `trailer_reached = true`, and returns `Some((out, bytes_removed))`.
- **Breaking Input:** A GIF with a truncated LZW stream or extension block whose last payload byte is `0x00`, followed by a `0x3B` byte inside the compressed pixel data. `strip_gif` returns `Some(...)` with a truncated image buffer instead of `None`.

---

### 2. Unsafe In-Place Overwrite and Never-Grow Bypass
- **Files & Lines:** [`core/src/lib.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/lib.rs#L746), [`core/src/main.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/main.rs#L255), [`app/Sources/JobQueue.swift`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/app/Sources/JobQueue.swift#L137-L152)
- **Mechanism:** Skipping `if src.converted_from.is_none() && data.len() >= bytes.len()` in `lib.rs` means `optimize()` now succeeds and returns a JPEG buffer even when the JPEG is dramatically larger than the source. `squint_core` does not accept destination paths or enforce file placement.
  1. **CLI Overwrite:** `main.rs` line 255 writes `r.data` directly to `--out`. Running `squint input.svg --out input.svg` or `squint image.heic --out image.heic` will overwrite the SVG vector or HEIC file with a JPEG raster.
  2. **Swift In-Place Replacement:** In `JobQueue.swift`, `isHeif` only guards HEIC inputs. If a user hands Squint an SVG file named `graphic.jpg` (SVG content inside a `.jpg` extension), `Source::open` identifies it as SVG (`converted_from = Some("SVG")`), `destination` resolves to `graphic.jpg` (matching `url`), `result.converted` is true, but `isHeif` is false, leading to an in-place overwrite of the original source.
  3. **HEIC Regression:** A full-resolution HEIC conversion resulting in a JPEG larger than the original HEIC is returned as `Ok` by `optimize()`, producing oversized files without warning.

---

### 3. `resvg` Unbounded Allocation, XML Expansion, and Local File Leakage
- **Files & Lines:** [`core/src/svg.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/svg.rs#L101-L105)
- **Mechanism:** `usvg::Options::default()` is passed to `usvg::Tree::from_data()` without specifying security options or resource limits. `MAX_PIXELS` only checks the top-level declared `width` and `height`.
- **Breaking Inputs:**
  - **Local File Inclusion:** `<image href="file:///etc/passwd"/>` or relative `xlink:href` paths cause `resvg`/`usvg` to read local files from the filesystem during rasterization.
  - **Entity / Expansion Bomb:** Nested `<use>` tags or deep DOM trees exhaust CPU and memory during tree construction before `tiny_skia::Pixmap` allocation is reached.
  - **Filter Region Allocation:** An SVG with a small canvas (`width="10" height="10"`) containing elements with huge filter regions (`filterUnits="userSpaceOnUse"` or large `stdDeviation`) forces `resvg` to allocate massive intermediate surface buffers regardless of `MAX_PIXELS`.

---

### 4. Spoofable `NETSCAPE2.0` Application Extension Matching
- **Files & Lines:** [`core/src/gif.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/gif.rs#L678-L685)
- **Mechanism:** `strip_gif` matches `NETSCAPE2.0` by checking `label == 0xFF` and whether the first sub-block length `sub_len >= 11` with slice `&bytes[pos..pos + 11] == b"NETSCAPE2.0"`. It does not check whether `sub_len == 11` exactly, nor does it validate subsequent sub-blocks for the loop count payload identifier (`0x01`).
- **Breaking Input:** Any application extension (`0x21 0xFF`) containing metadata, tracking identifiers, or arbitrary payloads can prepend `"NETSCAPE2.0"` to its first sub-block payload (e.g. `\x21\xFF\x15NETSCAPE2.0_TRACKING_DATA...`). `strip_gif` will identify `is_netscape = true` and retain the entire block.

---

### 5. Invisible Rasterization for Light-on-Transparent SVGs
- **Files & Lines:** [`core/src/source.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/source.rs#L35-L46)
- **Mechanism:** `Source::open` hardcodes alpha compositing onto solid white `[255, 255, 255]`.
- **Impact:** SVGs containing light or white artwork designed for dark backgrounds (such as white logos, dark-mode icons, or white text on transparent canvases) blend into solid white `[255, 255, 255]`, rendering the output JPEG completely blank/invisible.

---

### 6. Unbounded System Font Loading
- **Files & Lines:** [`core/src/svg.rs`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/svg.rs#L13-L18)
- **Mechanism:** `fontdb::Database::load_system_fonts()` scans and parses every font file across system directories (`/System/Library/Fonts`, `/Library/Fonts`, `~/Library/Fonts`).
- **Impact:** On systems with extensive font collections, the initial `OnceLock` initialization performs synchronous disk I/O across hundreds of files, causing latency spikes (~0.68s) and memory overhead before rasterization begins.

---

### 7. Over-Inclusive Crate Dependencies in `resvg`
- **Files & Lines:** [`core/Cargo.toml`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/Cargo.toml#L16), [`core/Cargo.lock`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/core/Cargo.lock)
- **Mechanism:** `resvg = "0.48"` is included with default features enabled. This pulls in `usvg`'s default `raster-images` feature, introducing 42 unnecessary crates (including `image-webp`, `gif`, `zune-jpeg`, and `png`).
- **Correction:** Disabling default features (`default-features = false`) on `resvg` removes unneeded raster image decoders while retaining core vector and text rendering functionality.

---

### 8. Test Harness (`tools/corpus-run.py`) Incompatibilities
- **Files & Lines:** [`tools/corpus-run.py`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-run.py#L22), [`L107-L108`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-run.py#L107-L108), [`L141`](file:///Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-run.py#L141)
- **Mechanism:**
  1. `EXPECT_REFUSED` does not include `"gif"` or `"svg"`. Adding a malformed GIF to the corpus will cause `optimize()` to return `Error::ReadOnlyFormat { format: "GIF" }`, which `corpus-run.py` flags as a `DANGEROUS` run failure.
  2. `email_extension()` assumes non-PNG inputs are converted to JPEG (`.jpg`). If an SVG seed is added, `resembles()` scales `seed_png` assuming a raster image, which fails if the seed file is SVG format.
