import hashlib, json, os, re, struct, sys

# Malformation families. Each takes the seed bytes and returns a list of
# (suffix, bytes, note). Positions are fixed rather than random so a corpus
# regenerates byte-identically from the same seed.

FRACTIONS = [0.10, 0.35, 0.60, 0.85, 0.99]


def jpeg_segments(d):
    # Offsets of each marker segment header, up to the start of scan.
    out, i = [], 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        m = d[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m in (0xDA, 0xD9):
            out.append((i, m, 0))
            break
        ln = int.from_bytes(d[i + 2:i + 4], "big")
        out.append((i, m, ln))
        i += 2 + ln
    return out


def jpeg_variants(d):
    v = []
    for f in FRACTIONS:
        n = max(2, int(len(d) * f))
        v.append(("trunc-%02d" % int(f * 100), d[:n],
                  "truncated to %d%% of %d bytes" % (int(f * 100), len(d))))

    segs = [s for s in jpeg_segments(d) if s[2] > 0]
    for idx, (off, m, ln) in enumerate(segs[:6]):
        # A length wrong by one byte desyncs the walk. This is the exact shape
        # of the defect the 2026-08-19 corpus caught, where a desynced walk
        # produced a headers-only file that passed the never-grow check and was
        # written over the photograph.
        for delta, tag in ((1, "plus1"), (-1, "minus1")):
            if ln + delta < 2:
                continue
            b = bytearray(d)
            b[off + 2:off + 4] = struct.pack(">H", ln + delta)
            v.append(("seglen-%s-FF%02X-%d" % (tag, m, idx), bytes(b),
                      "APP%X length %d declared as %d" % (m & 0x0F, ln, ln + delta)))
        b = bytearray(d)
        b[off + 2:off + 4] = struct.pack(">H", 0xFFFF)
        v.append(("seglen-huge-FF%02X-%d" % (m, idx), bytes(b),
                  "segment length claims 65535, past end of file"))

    sos = [s for s in jpeg_segments(d) if s[1] == 0xDA]
    if sos:
        off = sos[0][0]
        v.append(("no-sos", d[:off] + d[off + 2:],
                  "start-of-scan marker removed"))
        for k, pos in enumerate((off + 64, off + 512, (off + len(d)) // 2)):
            if pos < len(d):
                b = bytearray(d)
                b[pos] ^= 0x40
                v.append(("bitflip-entropy-%d" % k, bytes(b),
                          "bit flipped at offset %d, inside entropy-coded data" % pos))

    if d.endswith(b"\xff\xd9"):
        v.append(("no-eoi", d[:-2], "end-of-image marker removed"))
    return v


def png_chunks(d):
    out, i = [], 8
    while i + 8 <= len(d):
        ln = int.from_bytes(d[i:i + 4], "big")
        typ = d[i + 4:i + 8]
        out.append((i, typ, ln))
        if typ == b"IEND":
            break
        i += 12 + ln
    return out


def png_variants(d):
    v = []
    for f in FRACTIONS:
        n = max(8, int(len(d) * f))
        v.append(("trunc-%02d" % int(f * 100), d[:n],
                  "truncated to %d%% of %d bytes" % (int(f * 100), len(d))))

    for idx, (off, typ, ln) in enumerate(png_chunks(d)[:6]):
        name = typ.decode("ascii", "replace")
        for delta, tag in ((1, "plus1"), (-1, "minus1")):
            if ln + delta < 0:
                continue
            b = bytearray(d)
            b[off:off + 4] = struct.pack(">I", ln + delta)
            v.append(("chunklen-%s-%s-%d" % (tag, name, idx), bytes(b),
                      "%s length %d declared as %d" % (name, ln, ln + delta)))
        b = bytearray(d)
        b[off:off + 4] = struct.pack(">I", 0x7FFFFFFF)
        v.append(("chunklen-huge-%s-%d" % (name, idx), bytes(b),
                  "%s length claims 2GiB" % name))
        crc = off + 8 + ln
        if crc + 4 <= len(d):
            b = bytearray(d)
            b[crc] ^= 0xFF
            v.append(("bad-crc-%s-%d" % (name, idx), bytes(b),
                      "%s CRC corrupted" % name))

    idat = [c for c in png_chunks(d) if c[1] == b"IDAT"]
    if idat:
        off, _, ln = idat[0]
        for k, pos in enumerate((off + 8, off + 8 + ln // 2)):
            if pos < len(d):
                b = bytearray(d)
                b[pos] ^= 0x40
                v.append(("bitflip-idat-%d" % k, bytes(b),
                          "bit flipped at offset %d, inside IDAT" % pos))
    iend = [c for c in png_chunks(d) if c[1] == b"IEND"]
    if iend:
        v.append(("no-iend", d[:iend[0][0]], "IEND chunk removed"))
    return v


def isobmff_boxes(data, start=0, end=None):
    # Top-level boxes of one container level: (type, offset, size, header length).
    # Bounds are checked before a box is recorded, so a corrupt seed cannot
    # yield a box that reaches past its parent.
    end = len(data) if end is None else end
    out, i = [], start
    while i + 8 <= end:
        size = int.from_bytes(data[i:i + 4], "big")
        typ = data[i + 4:i + 8]
        hdr = 8
        if size == 1:
            if i + 16 > end:
                break
            size = int.from_bytes(data[i + 8:i + 16], "big")
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            break
        out.append((typ, i, size, hdr))
        i += size
    return out


def meta_children(d):
    meta = [b for b in isobmff_boxes(d) if b[0] == b"meta"]
    if not meta:
        return []
    _, at, size, hdr = meta[0]
    # A FullBox: four bytes of version and flags before the children.
    return isobmff_boxes(d, at + hdr + 4, at + size)


def child(boxes, kind):
    return next((b for b in boxes if b[0] == kind), None)


def read_iloc(d, box):
    # Every item's extents with the file position of each offset and length
    # field, so a field can be rewritten in place at its own width. The layout
    # follows ISO 14496-12 and is the same walk core/src/heif.rs makes: the
    # second size byte (base_offset_size, index_size) exists in every version.
    _, at, size, hdr = box
    body, end = at + hdr, at + size
    version = d[body]
    offset_size, length_size = d[body + 4] >> 4, d[body + 4] & 0xF
    base_size, index_size = d[body + 5] >> 4, d[body + 5] & 0xF
    p = body + 6
    if version < 2:
        count, p = int.from_bytes(d[p:p + 2], "big"), p + 2
    else:
        count, p = int.from_bytes(d[p:p + 4], "big"), p + 4

    def take(width):
        nonlocal p
        if p + width > end:
            raise ValueError("iloc runs past its box")
        value = int.from_bytes(d[p:p + width], "big") if width else 0
        pos = p
        p += width
        return pos, value

    items = []
    for _ in range(count):
        _, item_id = take(2 if version < 2 else 4)
        method = 0
        if version >= 1:
            _, m = take(2)
            method = m & 0xF
        take(2)  # data reference index
        _, base = take(base_size)
        _, extent_count = take(2)
        extents = []
        for _ in range(extent_count):
            if version >= 1 and index_size > 0:
                take(index_size)
            off_pos, off = take(offset_size)
            len_pos, ln = take(length_size)
            extents.append({"off_pos": off_pos, "off": off, "off_size": offset_size,
                            "len_pos": len_pos, "len": ln, "len_size": length_size})
        items.append({"id": item_id, "method": method, "base": base, "extents": extents})
    return items


def primary_ispe(d, children):
    # The ispe box that describes the primary item, found through the
    # container's own bookkeeping: pitm names the primary item, ipma lists the
    # 1-based ipco indices associated with it. Aiming by position instead hits
    # a tile's ispe, which Image I/O ignores, and the variant tests nothing.
    pitm = child(children, b"pitm")
    iprp = child(children, b"iprp")
    if not (pitm and iprp):
        return None
    _, at, size, hdr = pitm
    primary = int.from_bytes(d[at + hdr + 4:at + hdr + 6], "big") if d[at + hdr] == 0 \
        else int.from_bytes(d[at + hdr + 4:at + hdr + 8], "big")
    _, iat, isize, ihdr = iprp
    props = isobmff_boxes(d, iat + ihdr, iat + isize)
    ipco, ipma = child(props, b"ipco"), child(props, b"ipma")
    if not (ipco and ipma):
        return None
    _, cat, csize, chdr = ipco
    container = isobmff_boxes(d, cat + chdr, cat + csize)
    _, mat, msize, mhdr = ipma
    body, end = mat + mhdr, mat + msize
    version, flags = d[body], d[body + 3]
    p = body + 4
    entry_count = int.from_bytes(d[p:p + 4], "big")
    p += 4
    for _ in range(entry_count):
        if p >= end:
            return None
        item_id = int.from_bytes(d[p:p + 2], "big") if version < 1 else int.from_bytes(d[p:p + 4], "big")
        p += 2 if version < 1 else 4
        assoc_count = d[p]
        p += 1
        indices = []
        for _ in range(assoc_count):
            if flags & 1:
                indices.append(int.from_bytes(d[p:p + 2], "big") & 0x7FFF)
                p += 2
            else:
                indices.append(d[p] & 0x7F)
                p += 1
        if item_id == primary:
            for k in indices:
                if 1 <= k <= len(container) and container[k - 1][0] == b"ispe":
                    return container[k - 1]
    return None


def heif_variants(d):
    v = []
    for f in FRACTIONS:
        n = max(8, int(len(d) * f))
        v.append(("trunc-%02d" % int(f * 100), d[:n],
                  "truncated to %d%% of %d bytes" % (int(f * 100), len(d))))

    children = meta_children(d)
    iloc = child(children, b"iloc")
    if iloc:
        # An extent rewritten so that it ends one mebibyte past the file, at
        # the field's own width. Only extents the file places at an absolute
        # offset (construction method 0) can be pointed past the end; an idat-
        # relative one is left alone rather than mutated into something the
        # note would misdescribe. A field too narrow to hold the value is
        # skipped for the same reason.
        target = len(d) + 1024 * 1024
        k = 0
        for item in read_iloc(d, iloc):
            if k >= 3 or item["method"] != 0 or not item["extents"]:
                continue
            e = item["extents"][0]
            b = bytearray(d)
            if e["off_size"]:
                value, pos, width, field = target - e["len"] - item["base"], e["off_pos"], e["off_size"], "offset"
            elif e["len_size"]:
                value, pos, width, field = target - e["off"] - item["base"], e["len_pos"], e["len_size"], "length"
            else:
                continue
            if value < 0 or value >= 1 << (8 * width):
                continue
            b[pos:pos + width] = value.to_bytes(width, "big")
            v.append(("iloc-past-end-%d" % k, bytes(b),
                      "item %d extent 0 %s set to %d, ending 1 MiB past the file" % (item["id"], field, value)))
            k += 1

    ispe = primary_ispe(d, children)
    if ispe:
        _, at, size, hdr = ispe
        w_pos, h_pos = at + hdr + 4, at + hdr + 8
        orig_w = int.from_bytes(d[w_pos:w_pos + 4], "big")
        for tag, w, h, note in (
            ("ispe-huge", 60000, 60000, "primary ispe declares 60000x60000, 3.6 gigapixels"),
            ("ispe-one", 1, None, "primary ispe width set to 1"),
            ("ispe-double", orig_w * 2, None, "primary ispe width doubled to %d" % (orig_w * 2)),
        ):
            b = bytearray(d)
            b[w_pos:w_pos + 4] = w.to_bytes(4, "big")
            if h is not None:
                b[h_pos:h_pos + 4] = h.to_bytes(4, "big")
            v.append((tag, bytes(b), note))
    return v


# The families each kind must produce. A seed that yields none of one of
# these means the generator did not understand the file, and a corpus missing
# the family it exists to exercise must not be reported as generated.
REQUIRED = {
    "jpeg": ("trunc", "seglen", "no-sos", "bitflip"),
    "png": ("trunc", "chunklen", "bad-crc", "no-iend"),
    "heic": ("trunc", "iloc-past-end", "ispe"),
}


def family_of(suffix):
    return re.sub(r"-\d+$", "", suffix)


def main(argv):
    if len(argv) < 2:
        print("usage: python3 tools/corpus-gen.py SEED [SEED ...] [-o OUTDIR]")
        print("")
        print("Writes malformed variants of each seed image plus manifest.json, and")
        print("copies the seeds themselves into OUTDIR/seeds/ as the control family.")
        print("Seeds are JPEG, PNG or HEIC files with real structure; tools/corpus-seeds.py")
        print("makes a set without any photograph.")
        return 2

    out = "corpus"
    args = []
    i = 0
    while i < len(argv[1:]):
        a = argv[1 + i]
        if a == "-o":
            if i + 2 > len(argv[1:]):
                print("-o needs a directory")
                return 2
            out = argv[2 + i]
            i += 2
        else:
            args.append(a)
            i += 1

    os.makedirs(os.path.join(out, "seeds"), exist_ok=True)
    manifest = []
    failed = False
    for seed in args:
        d = open(seed, "rb").read()
        base = os.path.basename(seed)
        stem, ext = os.path.splitext(base)
        if d[:2] == b"\xff\xd8":
            variants, kind = jpeg_variants(d), "jpeg"
        elif d[:8] == b"\x89PNG\r\n\x1a\n":
            variants, kind = png_variants(d), "png"
        elif d[4:8] == b"ftyp":
            variants, kind = heif_variants(d), "heic"
        else:
            print("skipping %s: not a JPEG, PNG or HEIC" % seed)
            failed = True
            continue

        seed_sha = hashlib.sha256(d).hexdigest()
        open(os.path.join(out, "seeds", base), "wb").write(d)
        # The seed itself, unchanged, as the positive control: an engine that
        # cannot take a healthy file through every mode has nothing to say
        # about the malformed ones.
        open(os.path.join(out, base), "wb").write(d)
        manifest.append({"file": base, "kind": kind, "family": "control", "seed": base,
                         "seed_sha256": seed_sha, "sha256": seed_sha, "bytes": len(d),
                         "note": "the seed, unchanged"})

        families = set()
        for suffix, blob, note in variants:
            # A variant identical to its seed tests nothing and would be
            # counted as a pass; that is a generator defect, reported as one.
            if blob == d:
                print("%s: variant %s is byte-identical to the seed" % (base, suffix))
                failed = True
                continue
            name = "%s-%s%s" % (stem, suffix, ext)
            open(os.path.join(out, name), "wb").write(blob)
            families.add(family_of(suffix))
            manifest.append({
                "file": name,
                "kind": kind,
                "family": family_of(suffix),
                "seed": base,
                "seed_sha256": seed_sha,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
                "note": note,
            })
        missing = [f for f in REQUIRED[kind] if not any(x.startswith(f) for x in families)]
        if missing:
            print("%s: no variants in required families %s" % (base, ", ".join(missing)))
            failed = True
        print("%s: %d variants" % (base, len(variants)))

    open(os.path.join(out, "manifest.json"), "w").write(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("%d files in %s/" % (len(manifest), out))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
