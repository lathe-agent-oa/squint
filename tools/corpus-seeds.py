import hashlib
import os
import struct
import subprocess
import sys
import zlib

# One PNG writer, whatever is installed, so the seed bytes depend on this
# file and zlib alone rather than on whether Pillow happened to be present.
# The JPEG and HEIC are made from it by `sips`, whose output does change
# between macOS releases; corpus-run.py checks the manifest's seed hashes
# before a run so a corpus is only ever scored against the seeds it was made
# from.

# Larger than the email preset's 2048 px cap on the long edge, so the resize
# path is exercised rather than skipped; the width is odd so a row is not a
# multiple of any alignment Quartz pads to, which is what exposed the stride
# defect the corpus exists to catch.
WIDTH, HEIGHT = 2401, 1601


def png_bytes(width, height, rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = bytearray()
    row = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * row:(y + 1) * row])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def picture(width, height):
    # Pixel (x, y) is (x, y, 200) modulo 256 plus a few counts of noise from a
    # fixed linear congruential generator. The coordinates make a flipped,
    # transposed or sheared decode measurably different from the seed; the
    # noise keeps HEVC from coding the picture down to a few kilobytes.
    state = 123456789
    buf = bytearray(width * height * 3)
    i = 0
    for y in range(height):
        for x in range(width):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            noise = (state >> 16) & 0x7
            buf[i] = min(255, (x & 255) + noise)
            buf[i + 1] = min(255, (y & 255) + noise)
            buf[i + 2] = min(255, 200 + noise)
            i += 3
    return buf


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def sips(fmt, src, dst):
    r = subprocess.run(["sips", "-s", "format", fmt, src, "--out", dst], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(dst):
        print("%s seed unavailable: sips exit %d %s" % (fmt.upper(), r.returncode, r.stderr.strip()[:200]))
        sys.exit(3)


def main():
    if len(sys.argv) != 2:
        print("usage: python3 tools/corpus-seeds.py OUTDIR")
        return 2
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    png, jpg, heic = (os.path.join(out, "seed." + e) for e in ("png", "jpg", "heic"))
    open(png, "wb").write(png_bytes(WIDTH, HEIGHT, picture(WIDTH, HEIGHT)))
    if sys.platform != "darwin":
        print("JPEG and HEIC seeds need sips, which is macOS only")
        return 3
    sips("jpeg", png, jpg)
    sips("heic", png, heic)
    for path in (png, jpg, heic):
        print("%s  %s" % (sha256(path), os.path.basename(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
