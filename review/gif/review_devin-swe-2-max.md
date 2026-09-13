## Findings, ranked

### 1. Alpha compositing double-applies alpha — `source.rs:36-46`
`tiny_skia::Pixmap` is **premultiplied** RGBA; `pixmap.take()` returns premultiplied bytes. The composite treats them as straight alpha: `out = (r·a + 255·(255−a))/255` multiplies `r` by `a` a second time. Correct for premultiplied input is `out = r + 255 − a`. Concrete: a 50%-covered white edge pixel is stored `r=128, a=128`; the code emits 191 instead of 255. Every antialiased boundary of a light-on-transparent drawing gets a grey fringe — precisely the case the comment claims to fix. The cheaper honest answer is to `pixmap.fill(Color::WHITE)` before `resvg::render` (`svg.rs:142-146`) and let the renderer do the math, then drop the alpha byte. The test only asserts `a == 255` on an opaque rect, so nothing catches this.

### 2. The never-grow exemption doesn't reach `search` or `search_by_proxy` — `lib.rs:415-420`, `lib.rs:441-445`, `lib.rs:746`
`optimize()` exempts `converted_from` at line 746, but `Mode::Quality` calls `search(..., bytes.len(), ...)` at line 687 and `Mode::Balanced` calls `search_by_proxy(..., bytes.len(), ...)` at line 712; both run their own `>= original_bytes` checks on the input length. A 398-byte SVG in fast mode now produces a 13 KB JPEG; the same file in quality mode still errors `NoSmallerResult` from inside `search`. The announced "HEIC larger-than-source now produced" is likewise fast-mode-only. Same input, contradictory answers depending on mode — that's worse than either policy alone.

And the proxy itself isn't sound even where it holds. The engine can't enforce "written beside"; it can only mark the result. Concrete path where a converted result lands on its source: `squint drawing.svg --mode fast --out drawing.svg` — `main.rs:255` writes whatever bytes came back to whatever path it was given. Before this patch, `NoSmallerResult` refused that write (JPEG > SVG always); now nothing does. The JPEG-over-.svg file is the exact "file macOS could open becoming one it cannot" incident class. `JobQueue.swift:150` does guard its write, but the pre-flight at `JobQueue.swift:137-140` only checks `ftyp` — an SVG dropped in a plain (suffix-less) preset rasterizes, encodes, then fails `destination == url` with "a HEIC can only be shrunk beside the original", a wrong-format message after paying for the whole render.

### 3. `strip_gif` can return `Some` on a desynced walk — `gif.rs:95-133`
Stray `0x3B` bytes inside LZW data and colour tables are handled by structure — they're consumed inside length-prefixed spans. What isn't: a **lying length**. Set an image descriptor's local-colour-table size two entries too large (`gif.rs:103-110`): the walk skips into the LZW stream, reads length bytes out of compressed data, and wherever that chain happens to land, a `0x00` byte is accepted as the sub-block terminator and a following `0x3B` byte as the true trailer — `gif.rs:48-52` pushes it and returns `Some`. Output: header + a truncated image + trailer, a partial file macOS will decode, returned as success. That is the 2026-08-19 headers-only JPEG in miniature. It needs an adversarial or coincidentally corrupt length field rather than a stray byte, but the trailer-check defence is luck, not structure.

Related: `bytes[pos-1] != 0x00` (`gif.rs:86`, `gif.rs:129`) is dead validation. When the inner loop exits via `pos == bytes.len()`, `bytes[pos-1]` is the last *data* byte, not a terminator; a `0x00` there passes, and the run is only caught by `trailer_reached` later. The check reads as "must end cleanly" and verifies nothing the trailer check doesn't — and on the trailer-check's blind spot above, it also verifies nothing.

### 4. `MAX_PIXELS` gates the pixmap, not the work — `svg.rs:104-146`
`usvg::Tree::from_data` fully parses and resolves before the size check runs. A document declaring `width="10" height="10"` with a chain of `<g>` elements each containing two `<use>` references doubles the resolved tree per level — exponential expansion at parse time, before `tree.size()` is ever read, with no `use`-depth cap in `usvg::Options` 0.48. Billion-laughs via XML entities is dead (roxmltree doesn't expand DTDs); this one is alive. Filter regions with huge `stdDeviation` and dense `patternUnits` similarly burn render time inside a small pixmap. `resvg` has no resource limits to configure — the cap needs to be on document complexity or the parse needs a timeout/watchdog at the caller level.

Separately: `usvg::Options::default()`'s `image_href_resolver` reads the filesystem — `<image href="file:///…">` and absolute paths are loaded during parse. This is a metadata-removal tool performing unrequested local file reads (and blocking on anything slow: pipes, network mounts). Set a resolver that accepts `data:` URLs only.

### 5. The keep-list drops rendered content and colour — `gif.rs:91`
- **Plain Text extension (`0x21 0x01`) is picture, not metadata** — it rasterizes text into the frame. Dropping it changes pixels on the (admittedly rare) GIFs that use it.
- **ICCRGBG1012**: the ICC profile leaves as an unnamed application extension, while the PNG stripper deliberately keeps `iCCP` (`lib.rs:929`). A wide-gamut GIF comes back untagged and shifts colour — the same defect `encode_jpeg`'s comment attributes to ImageOptim.
- The `NETSCAPE2.0` match is `sub_len >= 11` prefix-compare (`gif.rs:75-78`). An application extension whose first sub-block is `"NETSCAPE2.0"` + arbitrary payload is kept whole — a spoofable keep that survives stripping in a privacy tool. Require `sub_len == 11` plus the `0x03 0x01` second sub-block shape.

### 6. Harness/engine divergence and small regressions — `main.rs`
- `main.rs:133-135`: `--mode balanced` on a PNG gets `measure = mode == "quality"` → `false`, `Effort::Quick` — while `optimize()` (`lib.rs:649-659`) would run Thorough with the target. The harness once again measures something the application doesn't do.
- `main.rs:210`: the `\`-continuation became literal spaces; the ceiling message prints a column of whitespace mid-sentence.
- `source.rs:151-153`: the comment still says the never-grow refusal "is correct behaviour" for the fast path that no longer has it.

### 7. Corpus tooling breaks on GIF/SVG seeds — `corpus-run.py`
- GIF strip output stays `.gif`; `resembles` calls `binary --against out.gif` (line 102), which decodes via `Source::open` — and the `image` crate is built `default-features = false, features = ["png","jpeg"]`, so GIF decode fails, score is `None`, and every successful GIF strip run is judged "output could not be scored" → dangerous. The harness goes red on correct output.
- `seed_png` (line 223) is *the first PNG seed*, used to score every family's output regardless of `entry["seed"]` (line 174). That only works if all seeds are the same artwork; an SVG seed of a different drawing produces garbage resemblance scores in both directions.
- `dimensions()` shells out to `sips` (line 66), which reports nothing for many SVGs → `src_dims = None` → `expected_dimensions` returns `None` → the output-size check is silently skipped for exactly the new format family.
- Nothing in `EXPECT_REFUSED`/`PIXELS_CORRUPTED` names `gif`/`svg`; a `trunc` GIF in strip mode is correctly refused, but no variant asserts it must be.

### Minor
- `rasterize`/`Source::open` runs outside `catch_unwind` (`encode_jpeg` is wrapped, `lib.rs:268`); a panic in the 42 new crates unwinds into the caller — `Error::Panicked` only helps if the FFI boundary catches it.
- `is_svg` (`svg.rs:58-65`) scans a DOCTYPE for the first `>`, which lands inside an internal subset `<!DOCTYPE svg [ … ]>` → false negative → valid SVG falls through to the raster decoders. `<?php`-style PIs before `<svg` also fail detection.
- `fontdb::load_system_fonts()` (`svg.rs:15`) walks and mmaps every font on the machine on first SVG — once per process, so tolerable, but it's unbounded filesystem work inside a library; worth a comment at minimum.
- `optimize()` in Strip mode on an SVG reports "not a JPEG or PNG" (`lib.rs:613`) — correct refusal, wrong message.
- resvg's feature set: the 42 crates are mostly the font stack (fontdb/harfrust/skrifa/read-fonts/unicode-*) which text rendering needs; usvg 0.48 doesn't feature-gate it. The cuttable surface is the filesystem half of `image_href_resolver`, addressed above.
