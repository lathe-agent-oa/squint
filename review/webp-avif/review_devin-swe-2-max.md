I'll read all four files first.The `heif.rs` read stopped at line 515 of 544. Reading the remainder.I've read all four files. Here's the review.

## 1. The RIFF walk itself is memory-safe; the code around it is not

`walk` (`webp.rs:171-214`) is clean on 64-bit: every index is guarded (`at + 8 > len` at :189, `start + size + pad > len` at :200), `at` provably stays `<= len`, the pad byte is stepped correctly, and the loop only closes by landing exactly on `bytes.len()`. No unbounded allocation — `kept`, `out`, and the `icc` copy are all bounded by input size. The "malformed accepted as valid" failure is real but lives one level up:

- **`walk` accepts chunk shapes the spec forbids**, and two of those shapes break `strip_webp` (see Q5 and Q6). A zero-length `VP8X`, a `VP8X` that isn't first, and a file whose only "picture" chunk is `VP8X` all walk cleanly.

## 2. Keep-list: complete against the spec, but `has_picture` is wrong

The registered WebP chunk set is `VP8 `, `VP8L`, `VP8X`, `ALPH`, `ICCP`, `ANIM`, `ANMF`, `EXIF`, `XMP ` — `KEPT_CHUNKS` (:37) covers all seven picture/profile chunks, and `ALPH` is correctly kept (it's the alpha plane; dropping it changes the picture). `Read all four files. Here's the review.

## 1. RIFF walk safety — `webp.rs`

The walk itself is sound on the target (64-bit). Every slice is bounds-checked before it's taken: `at + 8 > bytes.len()` guards the header (webp.rs:189), `start + size + (size & 1) > bytes.len()` guards the payload and pad (webp.rs:200), and the loop terminates because `span >= 8` always (webp.rs:198). No overflow is possible on 64-bit: `size` is a `u32`, so `8 + size + (size & 1)` and `at + span` can't reach `usize::MAX` given `bytes.len() <= isize::MAX`. `Vec::with_capacity(bytes.len())` (webp.rs:110) is the only allocation sized by untrusted input, and it's proportional. The land-exactly-on-the-end rule is genuinely enforced: a trailing byte fails the `at + 8` check, a short stop fails the declared-size check.

Caveats worth stating:

- **The strict-equality RIFF size check (webp.rs:180–183) refuses files with trailing bytes after the declared RIFF end.** Some encoders append padding or junk past the RIFF size; those files will be refused by `strip_webp` while the convert path may still accept them through `image-webp` if that decoder is more lenient. Conservative refusal — but note the asymmetry: `Source::open` never checks that `walk` closed (source.rs:87–99), so a structurally broken WebP that the decoder tolerates is converted while the same file in Strip mode errors with "not laid out as expected."
- **32-bit overflow (theoretical):** `8 + size + (size & 1)` wraps when `size == u32::MAX` on a 32-bit `usize`, turning the bounds check into a slice panic. `webp.rs` isn't cfg-gated, so this matters only if the crate is ever built off aarch64. Confirmed by reading; not exploitable on the stated target.

## 2. The keep-list — `webp.rs:37`

Correct per the current spec. The standardized chunk set is `VP8 `, `VP8L`, `VP8X`, `ALPH`, `ANIM`, `ANMF`, `ICCP`, `EXIF`, `XMP ` — all seven picture/profile chunks are kept, `EXIF`/`XMP ` drop, and `ALPH` is correctly kept (it's the alpha plane for lossy `VP8 `, picture data). Keep-order is preserved, so `ALPH` stays before `VP8 ` as required. The exclusion design means a *future* picture-bearing chunk would be silently destroyed — that's the stated tradeoff, same as the other formats.

**One confirmed gap: `VP8X` alone satisfies `has_picture`** (webp.rs:23, 98–108). `PICTURE_CHUNKS` includes `VP8X`, so a file of `RIFF/WEBP + VP8X + EXIF` with no frame chunk strips to a `VP8X`-only "WebP" — `optimize()` returns it as `Ok` and it is not a decodable image. The input is malformed either way, but the code's own rule ("one of which every WebP must have") should arguably be `VP8 `/`VP8L`/`ANMF`, not the extended header that merely *describes* a picture. Confirmed by reading.

Minor: `wiped` counts payload only (webp.rs:95) while the header and pad leave too — the test asserts this is intended, so the "bytes removed" number under-reports by 8(+1) per chunk.

## 3. `is_animated` — `webp.rs:63–74`

Reliable for spec-valid files: animation requires `ANMF` chunks, and a still cannot contain `ANIM`/`ANMF`. Two edge cases:

- **Wrong answer is possible only on malformed input.** If the walk fails *before* reaching `ANMF` chunks, the file reads as a still and falls through to the decoder (source.rs:87–91). Whether that loses frames depends entirely on whether `image-webp` is more lenient than `walk` — if it decodes a file whose RIFF size field or trailing chunk is broken, squint converts the first frame of something carrying `ANMF` further in. Suspect — I can't verify `image-webp`'s tolerance from these files. The safe direction holds for anything `image-webp` refuses.
- **False positive:** a still containing a chunk literally spelled `ANMF`/`ANIM` is refused as read-only. Safe direction, but note the comment on webp.rs:66–67 is slightly wrong — a failed walk doesn't yield "no chunks," it yields the chunks seen before the break, so a corrupt file with an early `ANIM` reports `ReadOnlyFormat` rather than "corrupt." Refused either way; just a mislabeled error.

## 4. AVIF/HEIF split — `heif.rs` — contains a panic

**`compatible_brands` panics on any `ftyp` file of length 12–15 with a major brand that isn't `avif`/`avis`** (heif.rs:81–89). When `size` isn't in `16..=bytes.len()` — always true when `len < 16` — `end` becomes 16, and `bytes[16.min(end)..end]` = `bytes[16..16]` on a 12-byte slice panics with "range out of bounds." Concrete trigger:

```
b"\x00\x00\x00\x0cftypmif1"   // 12 bytes
```

`is_heif` calls `!is_avif` for every major in the HEIF list (heif.rs:36–39), so `ftyp` + `heic`/`mif1`/`miaf`/… at 12–15 bytes panics through `is_heif`; any other non-AVIF major panics through `is_avif` directly. It's reached from `Source::open` via `is_isobmff_image` (source.rs:47) — so a truncated download or a 15-byte file on the Finder context menu crashes instead of erroring. `has_ftyp` only requires 12 bytes (heif.rs:73–75), which is exactly what opens this window. This is new code — the pre-diff `is_heif` never consulted compatible brands.

This also means the "DANGEROUS RUNS: 0" claim doesn't cover it: that corpus is JPEG segment-length fuzzing, and a 12-byte `ftyp` file wouldn't have been in it.

Related, smaller items:

- `container_name` returns `"HEIC"` for any non-AVIF input including non-ISOBMFF garbage (heif.rs:65–71). Safe only because all callers gate on `is_isobmff_image` first.
- An `ftyp` with `size == 1` (64-bit extended box) yields no compatible brands (heif.rs:83–87), so a `mif1`-major AVIF written that way is labeled HEIC in `converted_from` and messages. Cosmetic mislabel, right direction otherwise.
- **Caller audit:** within the shown code every caller correctly moved to `is_isobmff_image` (wiring.diff:57–58, source.rs:47, `is_complete` at heif.rs:272). I can't see the rest of `lib.rs`/CLI — any surviving `is_heif` call that meant "decodable ISOBMFF" now silently excludes AVIF.

**Pre-existing, now reachable via AVIF:** `boxes()` doesn't guard 64-bit box sizes (heif.rs:113–117, 122). A box with `size == 1` and a huge 64-bit size makes `i + size` wrap past the `> range.end` check, pushing a bogus `Box` and then decrementing `i` — unbounded `out` growth followed by an out-of-bounds index panic. Reachable through `find` → `is_complete` → `Source::open` for any ISOBMFF file; not introduced by this diff but the new format inherits it.

## 5. `VP8X` flag clearing — `webp.rs:129–131`

Two problems, one confirmed harmful:

- **Confirmed: a `VP8X` chunk with a zero-length payload corrupts the next chunk's FourCC.** The guard checks `out.len() > 20` and `out[12..16] == b"VP8X"` but never checks the VP8X length field at `out[16..20]`. The walk accepts a zero-length `VP8X` (it's a structurally fine 8-byte chunk), so `out[20]` is the *next* chunk's first FourCC byte, and `&= !0x0C` rewrites it. Trigger: `RIFF/WEBP + VP8X(len=0) + EXIF + VP8 (len=16)` — strip removes `EXIF` (so `wiped > 0` and `optimize()` returns the result) and `out[20]` turns `'V'` (0x56) into `'R'` (0x52), producing `RP8 ` — an output with no recognizable frame chunk. The offset `out[20]` itself is right (RIFF=0–3, size=4–7, WEBP=8–11, FourCC=12–15, len=16–19, payload=20); it just needs the payload-length check.
- **Confirmed but minor: "VP8X is first" is spec-true but not enforced.** If a malformed file puts a kept chunk before `VP8X`, `out[12..16] != "VP8X"` and the flags are silently left claiming `EXIF`/`XMP` chunks that no longer exist. The `is_animated` comment argues "the chunks are the fact and the flag is a claim" — here the output ships a stale claim. Most readers tolerate a flag for a missing chunk.

## 6. Silent-failure / wrong-success paths

- **`avis` major/compatible brand routes animated AVIF into the convert tier** (heif.rs:55). `avis` is the AVIF *image sequence* brand — the animated counterpart — and it decodes through Image I/O to a still, producing a first-frame JPEG with `converted_from: "AVIF"`. That's exactly the data-loss outcome the WebP path refuses for animated files (source.rs:83–88). Same applies, pre-existing, to `msf1` in the HEIF list (heif.rs:38). Suspect as a design inconsistency rather than a code bug — flag it.
- **WebP EXIF orientation is dropped** (source.rs:100–103). The comment asserts the writer "has already had it applied" — that's not guaranteed. `cwebp -metadata all` and similar tools preserve the EXIF orientation tag un-applied, and a camera-JPEG→WebP→squint chain produces an un-rotated JPEG. Suspect — depends on how common tagged-orientation WebPs are in practice — but the claim in the comment is stronger than reality.
- `strip_webp` returning `wiped == 0` is correctly surfaced as `NoSmallerResult` (wiring.diff:40–45) — not silent. `strip_heif`'s `wiped == 0` was already handled the same way. The animated-WebP refusal is placed correctly before the format fall-through (wiring.diff:75–77).

## Bottom line

The two findings I'd block on:

1. **`heif.rs:88` — panic on 12–15-byte `ftyp` files**, reachable from `Source::open` on arbitrary Finder-menu input. One-line fix: bound the slice start/end by `bytes.len()` or return an empty iterator when `bytes.len() < 16`.
2. **`webp.rs:129` — `out[20]` writes into the next chunk's FourCC when `VP8X` has a zero-length payload**, producing an invalid file returned as `Ok`. Check `u32::from_le_bytes(out[16..20]) >= 1` before touching `out[20]`.

Then the design questions: `avis` sequence handling (3/6), the WebP orientation assumption, and `VP8X` counting as `has_picture`. Everything else — the walk's bounds discipline, the keep-list, `is_animated` on valid files, the strict size checks — reads as correct.
