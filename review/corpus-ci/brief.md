# Review: squint — malformed-input corpus with a HEIC family + CI that compiles — round 1

You are reviewing a patch to `squint`, a macOS image optimizer (Rust engine in `core/`, Python tools in `tools/`, GPL-3, public). Answer as an engineer who has built fuzz harnesses and been fooled by them. Be specific: file, line, mechanism, the input that breaks it. No compliments; do not restate the patch. Rank findings by expected impact. Under 1200 words. Read the files listed at the end; run nothing; write nothing; answer in your final message.

## The target

This morning's HEIC decoder shipped two defects that no test caught: rows read at the wrong stride (sheared picture, success reported) and a truncated file drawn as a black JPEG with exit 0. This patch is meant to make that class of defect fail CI. A harness that reports a clean corpus it never really ran is worse than no harness — that has happened to this project three times (missing binary → every file "passed"; SIGALRM scored as a clean exit; `sips -g pixelWidth` answering yes on a headers-only JPEG). The tools already guard against those three. Your job is to find the fourth.

## What the patch does

- `tools/corpus-seeds.py` (new): writes `seed.png` (1201x800, pixel (x,y) = (x mod 256, y mod 256, 200) + LCG noise 0..7; Pillow if importable, else a hand-rolled zlib PNG writer), `seed.jpg` (via `sips` on macOS), `seed.heic` (via `sips`; exits 3 if it cannot). Prints sha256s.
- `tools/corpus-gen.py`: gains `heif_variants` — truncation at 10/35/60/85/99%, `iloc-past-end-K` (first extent of items 0..2 rewritten so offset+length ends 1 MiB past EOF; the field rewritten is the offset, or the length when offset_size is 0), `ispe-wrong-K-{huge,one,double}` on the first two `ispe` boxes under `meta/iprp/ipco`. Dispatch on `d[4:8] == b"ftyp"`.
- `tools/corpus-run.py`: `MODES` gains `email` (= `--mode fast --max-dimension 2048`, output extension `.jpg`); a run whose output decodes but whose long edge exceeds 2048 in email mode is DANGEROUS with reason "wrong size"; per-family summary table.
- `.github/workflows/ci.yml` (new): ubuntu `cargo check --locked --all-targets` + `cargo test --locked`; macos `cargo test`, `cargo build --release`, seeds → corpus → run, report uploaded as an artifact `if: always()`.
- `tools/README.md`: a section for the three tools.

Measured on this Mac: 74 files (32 PNG, 28 JPEG, 14 HEIC variants) × 4 modes = 296 invocations, 0 dangerous, 0 timeouts; `iloc-past-end-*` refused in every mode with "this HEIC is cut short"; `ispe-wrong-0-*` decode normally (the first `ispe` is a tile's, which Image I/O ignores); `ispe-wrong-1-*` refused by Image I/O ("found no images"). Linux (`cargo check` + `cargo test` in a sandbox): 37 tests pass, no warnings.

Settled and not under review: the engine code, the choice of 2048, the decision to keep real photographs out of the repository.

## Questions

1. **False clean.** Find a way this harness reports 0 dangerous while the engine has shipped a bad file. Consider: `decodes()` on an output that is a valid but wrong picture (black, sheared, wrong orientation) — what would catch that, cheaply, without a reference image? Is the "wrong size" check sufficient for email mode, and what is the analogue for the other modes?
2. **Manifest and determinism.** Is every variant byte-identical across runs and machines? Anything in `corpus-seeds.py` (Pillow vs fallback writer, `sips` version) that changes seed bytes between the developer's Mac and `macos-latest`, and does that matter given the manifest records seed sha256?
3. **HEIF parsing in `heif_variants`.** Wrong offsets, wrong field widths, an `iloc` version 2 or a `base_offset_size` of 8 that would make the mutation land on the wrong bytes (and so mutate nothing meaningful while still being counted as a variant). Would the `ispe` mutation ever hit the primary item's `ispe`, and how would you target it (`ipma` associations)?
4. **CI workflow.** Anything that makes the macOS job pass vacuously (a step that cannot fail, an exit code swallowed by a pipe, `sips` absent or refusing HEIC on the runner, `python3` version), or fail spuriously (runner image differences, time). Should the corpus step's exit code 2 (harness failure) and 1 (dangerous) both fail the job — and do they, as written?
5. **Regressions** in the pre-existing JPEG/PNG families or in `corpus-run.py`'s three existing guards.

Read the files listed below. Do not edit any files.

## Files to read (absolute paths)

- /Users/bemeadows/Projects/.lanes/squint-heic-wt/review/corpus-ci/patch.diff — the whole change against origin/main; read this first
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-seeds.py
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-gen.py
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/corpus-run.py
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/.github/workflows/ci.yml
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/tools/README.md
- /Users/bemeadows/Projects/.lanes/squint-heic-wt/core/src/heif.rs — the engine-side HEIF parser the tools should agree with
