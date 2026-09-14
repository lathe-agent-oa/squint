# Council synthesis — WebP and AVIF read (branch `webp-avif-read`)

Reviewed at commit `c7ea926`, three commits off `origin/main` at `5c621d9`. Round run 2026-09-13.

## Lanes

| Lane | Route | Could see | Result |
|---|---|---|---|
| Gemini 3.9 | `agyx` | all four files, line refs | 6.5 KB, both defects found, one fix wrong |
| SWE-2 max | Devin CLI | all four files | 10.8 KB, the round's strongest lane — four defects, two of which no other lane saw |
| DeepSeek V4.1 Flash | `ccx` | **brief only** — its tool access is jailed to the skill directory, so it never opened the repo | 11.5 KB reasoned from the prose; no line-level findings |
| GPT-5.6 Terra | `ccx` | nothing | lane failure: HTTP 429, `usage_limit_reached` on the ChatGPT free plan |

Two of four lanes read the code. DeepSeek's inability to leave the skill directory is a runner limitation worth fixing before the next round; its output is not evidence about this diff.

## Verified defects — both reproduced as failing tests before being changed

### 1. `compatible_brands` panicked on a 12–15 byte `ftyp` (`heif.rs:88`)

Found by Gemini. The compatible-brand list begins at offset 16. The end of the slice range was clamped to `bytes.len()`, the start was not, so a file that is a complete major brand and stops — twelve bytes — evaluated `bytes[16..16]` against a twelve byte slice and panicked.

Reproduced by `heif::tests::a_file_too_short_to_hold_a_brand_list_is_not_read_for_one`, which walks lengths 12 through 15. This is reachable from the Finder context menu on any file, so it is a crash on arbitrary input rather than a theoretical one. Introduced by the AVIF brand work in this branch; no released version is affected.

Fixed by clamping the start as well, so a range beginning past the last byte collapses to an empty one.

### 2. `VP8X` counted as picture, so a metadata-only container stripped to a shell (`webp.rs:23`)

Found independently by Gemini and SWE-2 — the round's only convergence, and on the one defect that produced a wrong *output* rather than a crash.

`VP8X` is the extended header: it announces which optional chunks a file carries and holds no pixels. Listing it in `PICTURE_CHUNKS` meant a container of `VP8X` + `EXIF` and no frame satisfied the has-a-picture check, and `strip_webp` returned a twenty byte header as a success — a file no decoder can draw, reported as a completed strip.

Reproduced inside `webp::tests::a_container_holding_no_picture_is_not_a_picture`.

**Gemini's proposed fix is wrong and was not taken.** It said `has_picture` should "strictly require a true frame (`VP8 ` or `VP8L`)". An animated WebP has neither at the top level — its pixels live in `ANMF` chunks, confirmed against a real four-frame file written by Pillow, whose chunks are `VP8X, ANIM, ANMF, ANMF, ANMF, ANMF`. That fix would have made every animation picture-less and broken animated strip, which is the one thing animations are still allowed to do. `ANMF` replaces `VP8X` in the list instead.

### 3. A zero-length `VP8X` renamed the next chunk (`webp.rs:129`)

Found by SWE-2 alone, in the flag-clearing code added to fix a different problem.

The guard checked that `VP8X` was the first chunk and that the file was long enough, but not that the `VP8X` had a payload. A zero-length `VP8X` is a structurally fine eight byte chunk and the walk accepts one, so `out[20]` was not inside the header at all — it was the first character of the *next* chunk's FourCC. Clearing two bits there turns `V` (0x56) into `R` (0x52), so `VP8 ` became `RP8 ` and the file went back as a success with no frame chunk under any name a decoder knows.

Reproduced by `webp::tests::an_empty_extended_header_does_not_get_its_neighbour_renamed`, run against the unfixed guard to confirm it fails and against the fixed one to confirm it passes. Fixed by checking the payload length field before touching the byte.

### 4. A 64-bit box size wrapped the ISOBMFF walk (`heif.rs:120`)

Found by SWE-2. Pre-existing — reachable today through HEIC, not introduced here — but inherited by the new format, which is why it was fixed in this branch rather than left.

`boxes()` took a 64-bit box size from the file and tested `i + size > range.end` without checking the addition. A size near the top of the address space wraps, lands *below* `range.end`, passes as a box that fits, and then moves `i` backwards, so the walk never reaches the end and `out` grows until the machine gives up.

Confirmed by inspection rather than by a reproducing probe, because reproducing it means running the unbounded loop. `heif::tests::a_box_size_near_the_top_of_the_address_space_stops_the_walk` asserts the fixed behaviour — it returns an answer, which a wrapped walk would not. Fixed with `checked_add`.

## Design inconsistency, fixed

SWE-2 observed that `avis` — the AVIF image-sequence brand — routed into the convert tier, where Image I/O would decode a sequence to its primary frame and write a single JPEG. That is precisely the silent frame loss the animated-WebP path exists to refuse, arriving through the other format. A sequence is now refused for re-encoding and can still be stripped.

The same is true of `msf1` in the HEIF brand list. That one is pre-existing, predates this branch, and is left alone.

## Known limitation, not fixed

A WebP has no orientation field of its own; a turn can only be recorded inside its `EXIF` chunk, and neither `image-webp` nor this module reads one. A WebP whose orientation lives there converts to a JPEG in stored order rather than intended order. SWE-2 was right that the original comment claimed more than is true — the comment now states the limitation instead. Reading it means parsing a TIFF header out of the chunk, which is its own piece of work.

## Exonerated — do not re-suspect these

- **The RIFF walk's bounds arithmetic.** Both reading lanes confirmed independently: every index guarded, `at` provably stays `<= len`, no overflow on 64-bit because `size` is a `u32` and slice length is bounded by `isize::MAX`, and the loop can only close by landing exactly on the end.
- **`is_animated` reading chunks rather than the `VP8X` flag bit.** Correct for real files, and a malformed file that fails the walk is reported as a still, then refused by the decoder — no silent bad path.
- **Clearing the `VP8X` flags at `out[20]`.** The spec requires `VP8X` first in the RIFF body when present, so the offset is exact, and `out.len() > 20` guards a zero-length or cut-short header.
- **`ALPH` in the keep list.** It is the alpha plane, picture rather than a record of one.
- **Excluding AVIF from `is_heif`.** Every caller goes through `is_isobmff_image`.
- **The `wiped == 0` path.** Correctly reports a file that carried nothing removable without rewriting it.

## What the lanes got wrong

- Gemini's fix for defect 2 would have broken animated strip (above). It also declared the `VP8X` flag clearing "confirmed safe and correct" — it was neither, as defect 3 shows, and Gemini's own reasoning ("`out.len() > 20` prevents out-of-bounds indexing if `VP8X` has a zero-length payload") mistook a bounds check for a correctness check.
- DeepSeek produced 11.5 KB about code it could not read. Its tool access is jailed to the skill directory, so `council-ccx.sh` never got the sources to it. Worth fixing in the runner before the next round; nothing it said is evidence about this diff.
- **A process error of mine, not a lane's:** the Devin reply was still streaming when I first read it at 1.2 KB, and I recorded it as truncated. It finished at 10.8 KB, and defects 3 and 4 were in the part I had not waited for. Two of the four defects in this round would have shipped because a file was read once while it was still being written. Read a lane's output only after its process has exited.

## Seat quality this round

SWE-2 (Devin) > Gemini (agyx) >> DeepSeek (no file access) > GPT-5.6 Terra (lane failure). SWE-2 found four defects to Gemini's two, was alone on the two that mattered most, and was the only lane to distinguish what it had confirmed from what it suspected. Gemini was faster and its line references were exact.
