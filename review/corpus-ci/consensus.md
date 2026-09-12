# Consensus: malformed-input corpus + CI — round 1 (2026-09-12)

## Lanes

| seat | route | result |
|---|---|---|
| Gemini | agyx | 8,121 B; 5 findings |
| DeepSeek V4.1 Flash | ccx | 7,230 B; 7 findings |
| SWE-2 max | Devin CLI | 6,649 B; 9 findings |
| Codex | ccx | dropped — `usage_limit_reached` |

## Verified defects — fixed in this branch

1. **The harness could not see the defects it was built for** (all three lanes). `decodes()` answers "is this a file"; a sheared or black output is a valid file. PASS by reading, and CONFIRMED by substitution: a stand-in engine writing a black JPEG for every input scored 0 dangerous under the old checks. Fixed: output dimensions must match the mode's expectation (input size, or exactly 2048 on the long edge for email); the output is scored against the seed PNG with the engine's own `--against` (the seed decodes through the `image` crate, not the path under test) and must reach 40 (controls score 67.8–100; the black frame −719.8). The stub now scores 199 dangerous on 61 files.
2. **`before` gate** (SWE-2, DeepSeek): a HEIC the system decoder refuses got no scrutiny at all, which is the truncated-HEIC case exactly. Fixed: HEIC `trunc` and `iloc-past-end` are expect-refused; output there is dangerous whatever `sips` says. A truncated JPEG/PNG is a real partial picture (the system decoder opens it too) and stays judged on decode + size.
3. **Nothing asserted the corpus was exercised** (SWE-2, DeepSeek): no positive control; `readable == 0` exited clean; an empty manifest exited clean; a missing family printed "skipping" and exited 0. Fixed: seeds enter the corpus as the `control` family and must be present; `readable == 0` is a harness failure; the generator exits 2 on a missing required family or a zero-diff variant.
4. **`ispe` aimed by position** (all three): the first `ispe` was a tile's. Fixed: `pitm` → `ipma` → the primary item's `ispe`. Measured: the primary `ispe` variants are now refused in every mode — by Image I/O ("found no images"), before the engine's own size check runs, so this variant proves refusal rather than the bomb check. Recorded as such.
5. **`iloc` rewrite arithmetic** (Gemini, SWE-2): clamp-to-zero wrote an in-bounds extent while the note claimed "past end"; a zero-width field raised `OverflowError`; `base_offset_size` was read only for version ≥ 1 where `heif.rs` (and the spec) read it always. Fixed: parse matches `heif.rs`; a value that does not fit its field width, or an `idat`-relative extent, is skipped rather than mutated into something the note misdescribes.
6. **Timeouts, signal deaths, exit-0-with-no-output were invisible** (SWE-2): now dangerous with their own reasons; `subprocess.run` has a Python-side timeout behind the cooperative `perl alarm`; both CI jobs have `timeout-minutes`.
7. **Email seed below the cap** (SWE-2): 1201 px never engaged the 2048 resize. Fixed: seeds are 2401x1601, width still odd.
8. **Email output assumed JPEG**: a PNG input comes out PNG. Found by the harness itself on its first run (`email output is not a JPEG` on a PNG control). Fixed: extension and magic follow the input kind.
9. **`--against` never ran with a PNG reference** — `main.rs` took the PNG branch first. Found by the harness on its first run (every JPEG/HEIC output "could not be scored"). Fixed in `core/src/main.rs`: `--against` is handled before mode dispatch.
10. Smaller: no-op `±1` length variants skipped in the JPEG and PNG generators (SWE-2); family aggregation fixed by renaming `ispe-K-huge` → `ispe-huge`; `py_compile` of the tools on the Linux job; seed hashes verified against the manifest before a run (Gemini, SWE-2); one PNG writer, no Pillow branch (Gemini, SWE-2).

## Refuted / not adopted

- **`new_offset` overflows `to_bytes(4)` for multi-megabyte files** (Gemini #5): `len + 1 MiB` fits in 32 bits for any file under 4 GiB; the real hazard was the zero-width field and the clamp, both fixed. Refuted as stated.
- **Commit the seeds** (SWE-2 #8): a 2401x1601 noisy PNG is 7.6 MB; the manifest hash check gives the reproducibility without the bytes. Not adopted.
- **Row/column mean statistics as the content check** (SWE-2 #2, DeepSeek #3): the `--against` score is the same idea with the project's own metric and no new code. Adopted in that form.

## Convergence

3 of 3 on the content-check gap, the `ispe` aim and seed determinism; 2 of 3 on the `before` gate, the vacuous-run paths and `iloc` arithmetic. Each confirmed by reading or by the stub run, not by agreement.

## Side findings (one line each)

- Strip on the `sips`-made JPEG/PNG controls refuses with "nothing to remove" — correct, and it means Strip has no positive control on synthetic seeds; a seed with an EXIF block would give it one.
- The email preset in quality mode is untested by the corpus (email = fast + cap by definition).
