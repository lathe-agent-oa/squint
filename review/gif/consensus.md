# Consensus: read GIF (and SVG, parked) — round 1 (2026-09-13)

Three seats read a combined diff: Gemini (agyx, 7,377 B), DeepSeek V4.1 Flash (ccx, 10,702 B), SWE-2 max (Devin, 8,417 B). SWE-2 gave the round's best read. The three lane replies are beside this file.

## A mistake in what they were given

The diff carried Balanced mode as well as GIF and SVG: uncommitted Balanced changes rode along into `git checkout -b` and were committed with the rest. The reviewers were right to reason about `search_by_proxy`; it was there. The three features are now on separate branches and only GIF is proposed for merge.

## Verified by probe, and fixed here

1. **A lying colour-table size desyncs the walk and can still reach a trailer** (SWE-2 #3). An image descriptor declaring a larger local colour table than it wrote sends the walk into the compressed data, where a `0x00` eventually passes for a sub-block terminator and a later byte for the trailer — returning a truncated picture as a success, which is the 2026-08-19 defect in miniature. Fixed structurally: the trailer must be the **last byte of the file**, so any desync leaves bytes over and is refused. Test: `a_lying_colour_table_size_is_refused_rather_than_truncated`, which mutates only that byte and asserts the honest control still strips.
2. **`bytes[pos - 1] != 0x00` was dead validation** (SWE-2 #3). When the sub-block loop ran out of input it checked a data byte, not a terminator. Replaced with an explicit `terminated` flag in both loops.
3. **The `NETSCAPE2.0` match was a prefix compare and spoofable** (SWE-2 #5). An application extension whose first sub-block was the name plus a payload was kept whole — a way to carry anything through a stripper. Now the identifier sub-block must be exactly eleven bytes. Test: `an_application_name_with_a_payload_stuck_to_it_is_not_kept`.
4. **The colour profile was being dropped** (SWE-2 #5). `ICCRGBG1012` left as an unnamed application extension, while this project keeps `iCCP` in PNG and the ICC APP2 in JPEG and calls that its differentiator. Now kept.
5. **The Plain Text extension was being dropped** (SWE-2 #5). A viewer rasterizes it into the frame, so it is picture, not a record of one. Now kept.

## Verified by probe, and the reason SVG is parked rather than merged

6. ☠️ **A converted result could be written over its own source.** `squint drawing.svg --mode fast --out drawing.svg` left a file beginning `ff d8 ff e0` — JPEG bytes on a `.svg`. Reproduced directly. The cause was mine: exempting conversions from the never-grow check removed the accident that had been preventing it. Reverted with SVG.
7. ☠️ **An SVG can make the program read local files.** `<image href="file:///etc/passwd">` renders without complaint, because `usvg::Options::default()` resolves filesystem hrefs. In a tool whose purpose is removing what a picture discloses, that is the wrong direction. Needs a resolver restricted to `data:` URLs before SVG ships.
8. **The never-grow exemption was inconsistent by mode** (SWE-2 #2). `optimize()` exempted conversions, but `search` and `search_by_proxy` keep their own checks against the input length, so Fast produced an SVG and Quality refused the same file. Same input, contradictory answers.
9. **Alpha is composited twice** (SWE-2 #1). `tiny_skia::Pixmap` is premultiplied; the code applies the straight-alpha formula, so an antialiased white edge emits 191 where 255 is correct — a grey fringe on exactly the light-on-transparent drawings the comment claims to handle. The cheap fix is to fill the pixmap white before rendering.
10. **`MAX_PIXELS` gates the pixmap, not the parse** (SWE-2 #4). `usvg::Tree::from_data` fully resolves before any size is read, and nested `<use>` doubles the tree per level, so a document declaring 10x10 can expand exponentially at parse time. resvg 0.48 exposes no limit to configure.

Items 6 to 10, plus 42 new crates in the dependency tree, are why SVG is on `svg-parked` rather than in this PR.

## Refuted

- **Stray `0x3B` inside LZW data or a colour table** (posed in the brief): handled by structure already — those bytes are consumed inside length-prefixed spans. Only a lying length gets past, which is finding 1.
- **XML entity expansion** (billion laughs): dead, roxmltree does not expand DTDs. SWE-2 checked and said so rather than listing it.

## Side findings, not acted on

- `tools/corpus-run.py` would judge a correct GIF strip as dangerous: it scores output with `--against`, which decodes through `Source::open`, and the `image` crate is built without GIF. Adding a GIF seed to the corpus needs that fixed first.
- The harness's PNG branch reads `mode == "quality"` for effort and measurement, so any third mode gets Quick and no metric while `optimize()` would run Thorough — the harness measuring something the application does not do, which this project has been bitten by before.
- `Source::open` runs outside `catch_unwind`; only `encode_jpeg` is wrapped.
