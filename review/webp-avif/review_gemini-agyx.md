Here is the review of the diff across `core/src/webp.rs`, `core/src/heif.rs`, `core/src/source.rs`, and `review/webp-avif/wiring.diff`.

---

### 1. Is the RIFF walk in `webp.rs` actually safe on hostile input?
**Confirmed safe.**

- **Bounds and Slicing**: `walk` ([`core/src/webp.rs#L171-L214`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L171-L214)) guards every access:
  - `is_webp(bytes)` requires `bytes.len() >= 12`, so `bytes.len() - 8` (L180) cannot underflow `usize`.
  - `at + 8 > bytes.len()` (L186) checks the chunk header bounds.
  - `start + size + (size & 1) > bytes.len()` (L200) checks the chunk payload and pad byte bounds before any slicing occurs (`&bytes[start..start + size]` and `&bytes[at..at + span]`).
- **Integer Overflow**: On 64-bit macOS (`usize` = `u64`), `start + size + (size & 1)` cannot overflow `usize` because slice length is bounded by `isize::MAX` and `size` is `u32`.
- **Landing Exactly on EOF**: The loop condition `while at < bytes.len()` combined with `if at + span > bytes.len() { return None; }` guarantees `at` after the loop must equal `bytes.len()`. If it overshoots or stops short, `walk` returns `None`.
- **Edge Case (12-byte header with 0 chunks)**: A bare 12-byte file (`RIFF....WEBP`) passes `walk` returning `Some(())` with 0 chunks. Callers handle this safely: `strip_webp` hits `if !has_picture { return None; }` ([L106](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L106)) and refuses it.

---

### 2. Is the strip's keep-list correct?
**`KEPT_CHUNKS` is correct, but `PICTURE_CHUNKS` has a bug.**

- `KEPT_CHUNKS` ([`core/src/webp.rs#L37`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L37)): Keeps `VP8 `, `VP8L`, `VP8X`, `ALPH`, `ANIM`, `ANMF`, and `ICCP`.
  - `ALPH` is the alpha bitstream for lossy `VP8 ` frames and MUST stay for transparency to survive.
  - `ICCP` stays to preserve wide-gamut profiles per project rules.
  - `ANIM`/`ANMF` stay to preserve animation frames in strip mode.
  - `EXIF` and `XMP ` are omitted and thus dropped.
- **BUG CONFIRMED in `PICTURE_CHUNKS`** ([`core/src/webp.rs#L23`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L23)):
  `PICTURE_CHUNKS` includes `b"VP8X"`. `VP8X` is the extended feature header, **not** an image frame bitstream.
  - **Trigger**: A WebP containing only `VP8X` and `EXIF` (and no `VP8 `, `VP8L`, or `ANMF` frame).
  - **Effect**: `has_picture` becomes `true` ([L99](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L99)), causing `strip_webp` to accept the file and write out a truncated 20-byte RIFF file containing only a `VP8X` header and no pixels.
  - **Fix**: Remove `b"VP8X"` from `PICTURE_CHUNKS` so `has_picture` strictly requires a true frame (`VP8 ` or `VP8L`).

---

### 3. Is `is_animated` reliable?
**Confirmed reliable.**

- `is_animated` ([`core/src/webp.rs#L63-L74`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L63-L74)) walks the chunk chain looking for `ANIM` or `ANMF`. Checking actual frame chunks rather than the `VP8X` bit is accurate for real files.
- **Malformed Input Behavior**: If a file is truncated or malformed, `walk` returns `None`, so `is_animated` returns `false`.
- **Downstream Impact**: In `Source::open` ([`core/src/source.rs#L87-L91`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/source.rs#L87-L91)), the file is passed to `Image::decode_capped`. The `image` crate decoder then attempts to decode the malformed WebP and cleanly returns `Err(Error::Decode(...))`. It does not fail silently or return invalid pixel data.

---

### 4. Is the AVIF/HEIF split in `heif.rs` correct?
**Architecture is clean, but `compatible_brands` contains a crash/panic bug.**

- **CRITICAL PANIC BUG CONFIRMED** in [`core/src/heif.rs#L83-L88`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/heif.rs#L83-L88):
  ```rust
  let end = if (16..=bytes.len()).contains(&size) {
      size
  } else {
      16
  };
  bytes[16.min(end)..end].chunks_exact(4)
  ```
  - **Trigger**: Any input slice between 12 and 15 bytes long starting with `ftyp` (e.g. `b"\x00\x00\x00\x0cftypmif1"`).
  - **Mechanism**: `has_ftyp(bytes)` ([L73](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/heif.rs#L73)) checks `bytes.len() >= 12` and returns `true`. `is_avif` ([L56](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/heif.rs#L56)) calls `compatible_brands(bytes)`. When `bytes.len()` is 12, `(16..=12)` is an empty range so `.contains(&size)` returns `false`, assigning `end = 16`. Slicing `bytes[16..16]` on a slice of length 12 panics out-of-bounds!
  - **Fix**: Cap `end` by `bytes.len()`: `let end = if (16..=bytes.len()).contains(&size) { size } else { 16.min(bytes.len()) };`.
- **Excluding AVIF from `is_heif`**: Works cleanly. Higher-level callers (`source.rs#L47`, `lib.rs#L565`) call `is_isobmff_image(bytes)` which evaluates `is_heif(bytes) || is_avif(bytes)`.

---

### 5. Does clearing the `VP8X` flags risk producing an invalid file?
**Confirmed safe and correct.**

- In `strip_webp` ([`core/src/webp.rs#L129-L134`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/core/src/webp.rs#L129-L134)):
  ```rust
  if out.len() > 20 && &out[12..16] == b"VP8X" {
      out[20] &= !(VP8X_EXIF_FLAG | VP8X_XMP_FLAG);
  }
  ```
- **Offset Arithmetic**: Per the WebP container spec, `VP8X` MUST be the first chunk in the RIFF body if present. The RIFF header is 12 bytes (`0..12`). `VP8X` chunk header is 8 bytes (`12..20`). The first payload byte containing the feature flags is at `out[20]`. `out[20]` is exact.
- **Safety**: `out.len() > 20` prevents out-of-bounds indexing if `VP8X` has a zero-length payload or is cut short. `&out[12..16] == b"VP8X"` ensures flags are only modified if `VP8X` is in the standard first-chunk position.

---

### 6. Error paths and silent failures audit

1. **Frameless `VP8X` header return**: As identified in Question 2, `strip_webp` accepts a metadata-only file if a `VP8X` header is present without a `VP8 `/`VP8L` payload, returning success and an un-decodable 20-byte file.
2. **Zero metadata stripped**: In [`core/src/lib.rs#L548-L552`](file:///Users/bemeadows/Projects/.lanes/squint-webp-read/review/webp-avif/wiring.diff#L45-L51), `wiped == 0` returns `Error::NoSmallerResult`, correctly reporting files where no metadata was removed without modifying the file.
