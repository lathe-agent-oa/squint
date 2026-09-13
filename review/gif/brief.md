# Review: squint — read GIF and SVG — round 1

You are reviewing a patch to `squint`, a macOS image optimizer (Rust engine in `core/`, GPL-3, public). Answer as an engineer who has written container parsers and been bitten by the file that is almost valid. Be specific: file, line, mechanism, the input that breaks it. No compliments; do not restate the patch. Rank findings by expected impact. Under 1300 words. Read the files listed at the end; run nothing; write nothing; answer in your final message.

## What the project already decided

- **Read is tracked separately from write.** A format can be readable without being writable. Strip removes metadata from HEIC and TIFF; neither is re-encoded, and `optimize()` returns `Error::ReadOnlyFormat { format }` for them.
- **A result in a different format is written beside the original, never over it.** `Source.converted_from` carries that. HEIC already works this way: the engine converts, and the caller refuses to put a JPEG on a `.heic`. The guard is derived from bytes, not from the filename.
- **A walk that does not complete returns `None`, never a partial result.** In 2026-08-19 a desynced JPEG walk produced a headers-only file that was smaller than the original, passed the never-grow check, and was written over the photograph. Seven files macOS could open became files it could not.
- **A harness that cannot see a defect is worse than none.** The repository carries a malformed-input corpus (`tools/`) and CI runs it on every push.

## What this patch adds

**GIF, read-only.** `core/src/gif.rs`: `is_gif`, and `strip_gif` which walks the block structure and removes every extension except the Graphic Control Extension (`0xF9`, frame delay and transparency) and the `NETSCAPE2.0` Application Extension (the animation loop count). Removal is by exclusion, so Comment (`0xFE`), Plain Text (`0x01`), XMP, ICC and any future block leave without being named. The walk must reach the trailer `0x3B` or the function returns `None`. `optimize()` refuses GIF for every mode but Strip.

**SVG, rasterized.** `core/src/svg.rs`: `is_svg`, and `rasterize` via `resvg` 0.48 (Apache-2.0 or MIT). The declared canvas is checked against `MAX_PIXELS` before a pixmap is allocated. System fonts are loaded once behind a `OnceLock`. `Source::open` rasterizes an SVG, composites the alpha onto **white** before dropping it (the engine writes JPEG, which has none), and reports `converted_from: Some("SVG")`.

**Three changes I made on top of what the producing lane wrote**, which are the ones most worth your attention:

1. The lane left an unconditional `ReadOnlyFormat { format: "SVG" }` in `optimize()`, so an SVG rasterized and was then refused by the same function. Removed: SVG now flows through exactly as HEIC does.
2. **The never-grow check is now skipped when `converted_from` is set** (`lib.rs`, the check after the encode). A 398-byte drawing rasterizes to a 13 KB JPEG, so the old rule refused every SVG. The reasoning: never-grow protects a file from being replaced by a bigger one, and a converted result is never written over its source. **This also changes shipped HEIC behaviour** — a full-resolution HEIC to JPEG that comes out larger than the HEIC is now produced rather than refused.
3. The CLI no longer prints "% of original" for a conversion; it read "3466.6% of original" on an SVG.

## Measured on an Apple M5

- SVG 4000x3000 with a 1440 cap: 1440x1080 JPEG, 49 KB, 0.04 s. No cap: 4000x3000, 172 KB, 0.21 s. First call 0.68 s including the system font load, 0.02 s after. Text renders (fonts found).
- An SVG declaring 100000x100000: refused with `TooLarge` before allocating.
- A GIF carrying a comment, an XMP application block and a NETSCAPE2.0 loop count: comment and XMP removed, loop count kept, trailer intact, output opens in ImageIO. A GIF truncated to 300 bytes: refused. A GIF with a sub-block whose declared length is one byte short of its contents: refused.
- Fast mode on a GIF: refused, naming Strip.
- 47 tests pass; dependency count goes from 82 to 124 crates, all of it `resvg`'s tree.

## Questions

1. **`strip_gif`'s walk.** Find an input where it desyncs and still reaches a `0x3B` by luck, so a truncated or partial result is returned as success. Consider: a sub-block length that runs past the end; a `0x3B` byte occurring *inside* LZW data or inside a colour table; an image descriptor whose local colour table flag disagrees with what follows; zero-length sub-block chains; an extension label the code does not know. Which of these does the code handle by structure rather than by luck?
2. **What `strip_gif` keeps.** Is Graphic Control Extension plus NETSCAPE2.0 the right keep-list, or does it drop something a viewer needs? Is matching `NETSCAPE2.0` on the first sub-block enough, and can that match be spoofed by a hostile file into keeping something else?
3. **The never-grow exemption.** Is `converted_from.is_some()` a sound proxy for "this will be written beside the original"? Name a caller or a path where a converted result could still land on top of its source. The Swift guard is described in `app/Sources/JobQueue.swift`, which is included for this reason.
4. **`rasterize`.** The `MAX_PIXELS` check reads the declared canvas — can a document declare a small canvas and still make resvg allocate or compute enormously (nested `use`, huge filter regions, `patternUnits`, deep groups, a billion-laughs entity expansion)? Is `resvg` 0.48 configured with any resource limits, and should it be? What does it do with an external `<image href="...">` or an `xlink` to a local file — is that a file read this program should not be making?
5. **Alpha onto white.** Correct for a drawing on a transparent background; what does it do to an SVG that is deliberately light-on-transparent, and is there a cheaper honest answer than picking a colour?
6. **The dependency tree.** 42 new crates for SVG. Anything in `resvg`'s default features that could be turned off without losing text rendering? Is `fontdb`'s system-font load doing filesystem work that should be bounded?
7. **Regressions** anywhere in the JPEG, PNG, HEIC or TIFF paths, and in the corpus tools' assumptions (`tools/corpus-run.py` judges output by size, format magic, and a resemblance score against the seed — do GIF and SVG inputs break any of that if someone adds them as seeds?).

Read the files listed below. Do not edit any files.

## Files to read (absolute paths)

- /Users/bemeadows/Projects/.lanes/squint-heic-wt/review/gif-svg/patch.diff — the whole change against origin/main; read this first
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/gif.rs
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/svg.rs
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/source.rs
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/lib.rs
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/main.rs
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/app/Sources/JobQueue.swift — the caller that refuses to overwrite with a converted result
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/heif.rs — the precedent this follows
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-run.py
