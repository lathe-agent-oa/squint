Let me read the remainder of the patch.Reviewed all seven files. Findings ranked by expected impact.

## 1. Nothing checks pixel content or output size outside email — both of this morning's defects pass clean

`corpus-run.py:112-118`: a run is DANGEROUS only if the output fails `sips` decode, or — email mode only — its long edge exceeds 2048. The stride bug emits a valid 1201×800 file: decodes, pass. The truncated-HEIC-drawn-as-black bug emits a valid black JPEG: decodes, pass in all four modes. The `before` gate doesn't save it — sips draws a truncated HEIC without error, so `before` is True and the bug still slips. The harness as written cannot see the defect class it was built for; `decodes()` answers "is this a file," never "is this the right picture."

Cheap catches without a stored reference: (a) call `get_dimensions(src)` once per file and require output dims == input dims for fast/quality/strip, expected-scaled dims for email — catches wrong-size, dropped orientation, shortened rows; (b) the probe PNG already rendered in `decodes()` — keep it. A 1201×800 high-entropy gradient compresses to ~1–2 MB; a black frame to a few KB. A floor on probe size catches black; a row-hash check against the known seed function `f(x,y)` catches shear. Both are nearly free.

## 2. The `before` gate makes most of the HEIC family structurally incapable of flagging

`corpus-run.py:102,112` — anything sips refuses gets zero scrutiny. Per your own measurement, `ispe-wrong-1-*` is refused by Image I/O, so squint could decode it into garbage, exit 0, and never be dangerous. Combined with finding 1, the only HEIC variants that can ever flag are the truncations — and only via "output doesn't decode," the weakest check. The iloc and ispe families are decorative.

## 3. The corpus can shrink or empty and the run still exits 0

`corpus-gen.py:283-285` prints "skipping" and `continue`s, then exits 0 — an unparseable `seed.heic` (sips returning 0 with garbage on a new runner image) silently removes the family this patch exists to exercise. `heif_variants` returns only truncations when `meta` is absent (`corpus-gen.py:152`) and emits zero iloc/ispe variants when those boxes are missing — no assertion that any family produced ≥1 variant. Then `corpus-run` on an empty manifest: `readable` is 0, so the HARNESS FAILED guard at `:152` (`wrote == 0 and readable`) doesn't fire → "0 files, DANGEROUS RUNS: 0" → exit 0. Both tools should fail when an expected family is absent.

## 4. Success-with-no-output, crashes, and timeouts are invisible

`:112` requires `wrote_output`; `exit==0` + no output on a decodable input lands in none of the summary columns (`:144-146` count only wrote/refused/timeout). An engine that reports success but writes nothing — a sibling of the morning's defect — scores clean. A signal death (-11, -6) is counted as "Refused." `timed_out` (`:65`) is printed but never reaches `return 1 if dangerous` (`:162`): an engine that hangs on three files exits green.

## 5. The gen-side `iloc` parser desyncs from the engine's, so mutations can land nowhere

`corpus-gen.py:163` sets `base_offset_size = 0` for `iloc` version 0; `heif.rs:155` reads it unconditionally (the spec agrees with the Rust — the second size byte exists in all versions). On a v0-with-base `iloc` the Python walk misreads `extent_count` and every offset after it. `p` is never bounded against the box (`:166-199`), and `b[pos:pos+n] = ...` past EOF *appends* to the bytearray — the "variant" becomes the seed plus four garbage bytes at EOF, still recorded in the manifest as `iloc-past-end`. `construction_method` (`:181-183`; `heif.rs:187-189` filters `method != 0`) is skipped without being read: a method-1 extent is `idat`-relative, and rewriting it to `len+1MiB` mutates nothing meaningful. Today's sips seed dodges all three; the next encoder won't.

## 6. `ispe` is aimed by position, not association

`corpus-gen.py:225-226` mutates `ispes[:2]` in `ipco` order. Measured: ispe-0 belongs to a tile (ignored), ispe-1 breaks input decode (gate 2). To hit the primary: read `pitm` for the primary item id, walk `ipma` to find which `ipco` index is associated with it, and mutate that `ispe`.

## 7. The watchdog is cooperative; nothing else bounds the run

`corpus-run.py:8`: `perl alarm` delivers SIGALRM, which a process can block or ignore — then `subprocess.run` (`:51`, no `timeout=` backstop) hangs until the GitHub job limit (~6h; `ci.yml` sets no `timeout-minutes`). Worse, `capture_output` waits for pipe EOF: a grandchild inheriting stdout survives the alarm and hangs the run even when the watchdog fires. Use a Python-side timeout with `killpg`, plus `timeout-minutes` on the step.

## 8. Seeds are not byte-identical across machines, and nothing gates on it

`corpus-seeds.py:73-85`: Pillow present → one PNG; absent (macos-latest) → the fallback writer, whose deflate output can still shift with the zlib version. `:89-97` silently swaps the JPEG encoder to Pillow when sips fails. sips' HEIC output varies across macOS versions. The manifest sha256 is bookkeeping, not a gate — the developer's "74 files, 0 dangerous" describes a different corpus than CI's, and a CI-found bad file can't be regenerated byte-identically locally. These seeds are synthetic — the no-photographs rule doesn't apply — so commit them, or pin the fallback writer and assert the printed hashes.

## 9. Smaller

- `:115-118`: `dimensions` is None when `sips -g` prints nothing while the probe decode succeeded — silently skips the only size check.
- `:44`: output format is whatever squint wrote; a `.jpg` containing PNG bytes still "decodes."
- `:47`: email runs `--mode fast`, so quality-mode resize is untested.
- `:293`: the family regex makes `ispe-wrong-0-huge` its own family and merges all three `trunc-*` kinds into one row.
- `:86`: `-o` parsed positionally; `... -o` without a value silently defaults.
- `ci.yml`: `cargo test` on ubuntu now skips the `source.rs` tests via the new `cfg` — the Linux "37 pass" covers less than the name implies.
- `corpus-gen.py:135-138` appends the overrun box before the bounds check where `heif.rs:73-76` checks first — harmless on a valid seed, wrong on a corrupt one.

On the questions: no regressions found in the JPEG/PNG walks or the three existing guards (binary check `:92`, -14/142 scoring `:65`, full-conversion probe `:22` all still do what they claim); and yes, `corpus-run` exit codes 1 and 2 both fail the CI step as written — the hole isn't the exit code, it's that the exit code is computed over checks that can't see the defects.
