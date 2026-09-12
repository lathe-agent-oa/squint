import hashlib, json, os, re, subprocess, sys, tempfile

# The email preset is fast mode with a long-edge cap; a PNG stays a PNG and
# everything else comes out as JPEG.
MODES = ["fast", "quality", "strip", "email"]
EMAIL_CAP = 2048

# macOS has no timeout(1). A run that hangs must still be scored, and a whole
# corpus run once reported 1,194 "command not found" errors because the
# watchdog was assumed rather than checked. The alarm is cooperative; the
# Python-side timeout below is the backstop for a process that ignores it.
WATCHDOG = ["perl", "-e", "alarm shift; exec @ARGV"]
LIMIT = 20

# HEIF families whose input is cut short or points past the file. For these,
# refusal is the only correct answer: Image I/O draws what it can from such a
# file and reports success, which is how a truncated HEIC once became a black
# picture with exit 0. Whether the system decoder could open the input does
# not enter into it. A truncated JPEG or PNG is different: the decoders stop
# at the cut and the partial picture is a real, displayable picture, which the
# system decoder also opens, so those are judged like any other variant.
EXPECT_REFUSED = {"heic": ("trunc", "iloc-past-end")}

# Families whose pixels are not the seed's, so the output is not expected to
# resemble it: bit flips by design, and a JPEG or PNG cut short, which decodes
# to a partial picture. Everything else changes structure and leaves the
# picture alone, and an output that decodes but no longer looks like the seed
# is a defect (rows read at the wrong stride, a black frame, a flipped one).
PIXELS_CORRUPTED = {"jpeg": ("bitflip", "trunc"), "png": ("bitflip", "trunc"), "heic": ("bitflip",)}

# The lowest SSIMULACRA2 score a correct output may earn against the seed the
# variant was made from. Fast mode at quality 75 scores about 75 on the seed;
# a black frame scores far below zero; a sheared or flipped picture in the
# tens at best. Halfway leaves room for the lossy modes and none for a wrong
# picture.
MIN_SCORE = 40.0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def decodes(path, scratch):
    # Ask the system decoder, not a Python imaging library. The defect class
    # this looks for is a file macOS could open becoming one it cannot, so a
    # more permissive reader would answer the wrong question.
    #
    # A full format conversion, not `-g pixelWidth`: reading a property parses
    # the header only, and answers True for a JPEG truncated to a tenth of its
    # length. Apple's decoder is tolerant and resyncs where ours cannot, so a
    # file that fails this is severely broken rather than merely damaged.
    out = os.path.join(scratch, "decode-probe.png")
    r = subprocess.run(["sips", "-s", "format", "png", "--out", out, path],
                       capture_output=True, text=True)
    ok = r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    if os.path.exists(out):
        os.remove(out)
    return ok


def dimensions(path):
    r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                       capture_output=True, text=True)
    w = re.search(r"pixelWidth:\s*(\d+)", r.stdout)
    h = re.search(r"pixelHeight:\s*(\d+)", r.stdout)
    if r.returncode != 0 or not (w and h):
        return None
    return int(w.group(1)), int(h.group(1))


def expected_dimensions(mode, src_dims):
    # Every mode but email keeps the picture's size. Email caps the long edge
    # at EMAIL_CAP and never enlarges, so a seed larger than the cap must come
    # out at exactly the cap and a smaller one unchanged.
    if src_dims is None:
        return None
    w, h = src_dims
    if mode != "email" or max(w, h) <= EMAIL_CAP:
        return (w, h)
    if w >= h:
        return (EMAIL_CAP, round(h * EMAIL_CAP / w))
    return (round(w * EMAIL_CAP / h), EMAIL_CAP)


def resembles(binary, seed_png, out, dims, scratch):
    # Score the output against the seed it was made from, with the seed
    # decoded by the `image` crate rather than by the path under test. The
    # metric needs equal sizes, so the seed is scaled to the output's size
    # first when the mode changed it. Returns the score, or None when it could
    # not be measured.
    ref = seed_png
    if dims is not None and dims != dimensions(seed_png):
        ref = os.path.join(scratch, "ref-%dx%d.png" % dims)
        r = subprocess.run(["sips", "-z", str(dims[1]), str(dims[0]), seed_png, "--out", ref],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
    r = subprocess.run([binary, ref, "--against", out], capture_output=True, text=True)
    m = re.search(r"score\s+(-?[0-9.]+)", r.stdout)
    return float(m.group(1)) if m else None


def email_extension(src):
    # The email preset writes what the engine writes: a PNG stays a PNG, and
    # everything else comes out as JPEG.
    return ".png" if open(src, "rb").read(8) == b"\x89PNG\r\n\x1a\n" else ".jpg"


def run_one(binary, src, mode, workdir):
    ext = email_extension(src) if mode == "email" else os.path.splitext(src)[1]
    out = os.path.join(workdir, "out-%s%s" % (mode, ext))
    if mode == "email":
        args = [src, "--mode", "fast", "--max-dimension", str(EMAIL_CAP), "--out", out]
    else:
        args = [src, "--mode", mode, "--out", out]
    cmd = WATCHDOG + [str(LIMIT), binary] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=LIMIT + 10)
        code, err = r.returncode, r.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        code, err = -14, "python-side timeout; the watchdog did not fire"
    except OSError as e:
        return {"mode": mode, "error": str(e)}

    wrote = os.path.exists(out) and os.path.getsize(out) > 0
    magic = open(out, "rb").read(4) if wrote else b""
    rec = {
        "mode": mode,
        "exit": code,
        # Python reports a signalled child as the negative signal number, so
        # SIGALRM arrives as -14. The shell's 142 is only what a shell would
        # have printed, and scoring against it marks every hang as a clean run.
        "timed_out": code in (-14, 142),
        "crashed": code < 0 and code != -14,
        "wrote_output": wrote,
        "output_bytes": os.path.getsize(out) if wrote else 0,
        "output_magic_matches": (magic[:2] == b"\xff\xd8") if ext != ".png" else (magic == b"\x89PNG"),
        "output_decodes": decodes(out, workdir) if wrote else False,
        "dimensions": dimensions(out) if wrote else None,
        "stderr": err,
    }
    return rec, out


def judge(entry, rec, out, src_dims, seed_png, binary, workdir):
    # Every way a run can be wrong, as a reason string, or None.
    family, kind = entry["family"], entry["kind"]
    exit0 = rec.get("exit") == 0
    wrote = rec.get("wrote_output")
    if rec.get("timed_out"):
        return "timed out"
    if rec.get("crashed"):
        return "crashed (signal %d)" % -rec["exit"]
    if family.startswith(EXPECT_REFUSED.get(kind, ())) and wrote:
        return "wrote output from an input that must be refused"
    if exit0 and not wrote:
        return "reported success and wrote nothing"
    if not wrote:
        return None
    if not rec.get("output_decodes"):
        return "output does not decode"
    if rec["mode"] == "email" and not rec.get("output_magic_matches"):
        return "email output is not the format its name claims"
    want = expected_dimensions(rec["mode"], src_dims)
    if want is not None and rec.get("dimensions") != want:
        return "wrong size: %s, expected %s" % (rec.get("dimensions"), want)
    if rec.get("dimensions") is None:
        return "output size could not be read"
    if not family.startswith(PIXELS_CORRUPTED.get(kind, ())):
        score = resembles(binary, seed_png, out, rec["dimensions"], workdir)
        rec["score_against_seed"] = score
        if score is None:
            return "output could not be scored against the seed"
        if score < MIN_SCORE:
            return "output does not resemble the seed: score %.1f" % score
    return None


def main(argv):
    if len(argv) < 3:
        print("usage: python3 tools/corpus-run.py SQUINT_BINARY CORPUS_DIR [-o REPORT]")
        print("")
        print("Runs every corpus file through every mode. A run is DANGEROUS when the")
        print("engine reports success on an input that must be refused, writes a file")
        print("the system decoder cannot open, writes the wrong size, writes a picture")
        print("that no longer resembles the seed, hangs, crashes, or writes nothing.")
        return 2

    binary, corpus = argv[1], argv[2]
    report = argv[4] if len(argv) > 4 and argv[3] == "-o" else "corpus-report.json"

    # A binary that never executes produces a flawless run: perl's exec fails,
    # the watchdog still exits 0, nothing is written, and every file passes.
    # That is the same silent success this tool exists to catch, so refuse to
    # start rather than report a clean corpus against a path that is not there.
    if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
        print("not an executable file: %s" % binary)
        return 2

    manifest = json.load(open(os.path.join(corpus, "manifest.json")))
    if not manifest:
        print("HARNESS FAILED: the manifest is empty.")
        return 2

    # The seeds the corpus was generated from must be the ones present now,
    # or a red run cannot be reproduced and a silently changed seed rewrites
    # every variant with no signal.
    seeds = {}
    for entry in manifest:
        seed = os.path.join(corpus, "seeds", entry["seed"])
        if entry["seed"] not in seeds:
            if not os.path.isfile(seed):
                print("HARNESS FAILED: seed %s is not in %s/seeds/" % (entry["seed"], corpus))
                return 2
            if sha256(seed) != entry["seed_sha256"]:
                print("HARNESS FAILED: seed %s is not the file the manifest was built from" % entry["seed"])
                return 2
            seeds[entry["seed"]] = seed
    seed_png = next((p for p in seeds.values() if p.endswith(".png")), None)
    if seed_png is None:
        print("HARNESS FAILED: no PNG seed; outputs cannot be scored.")
        return 2

    results, dangerous = [], []
    with tempfile.TemporaryDirectory() as workdir:
        for n, entry in enumerate(manifest, 1):
            src = os.path.join(corpus, entry["file"])
            before = decodes(src, workdir)
            src_dims = dimensions(seeds[entry["seed"]])
            runs = []
            for m in MODES:
                r = run_one(binary, src, m, workdir)
                if isinstance(r, dict):
                    runs.append(r)
                    continue
                rec, out = r
                reason = judge(entry, rec, out, src_dims, seed_png, binary, workdir)
                if reason:
                    rec["dangerous"] = reason
                    dangerous.append({"file": entry["file"], "mode": m, "reason": reason})
                if os.path.exists(out):
                    os.remove(out)
                runs.append(rec)
            results.append({"file": entry["file"], "family": entry["family"],
                            "input_decodes": before, "runs": runs})
            if n % 25 == 0:
                print("  %d/%d" % (n, len(manifest)))

    open(report, "w").write(json.dumps(
        {"binary": binary, "corpus": corpus, "results": results, "dangerous": dangerous},
        indent=2, sort_keys=True) + "\n")

    total = len(manifest) * len(MODES)
    timeouts = sum(1 for r in results for x in r["runs"] if x.get("timed_out"))
    wrote = sum(1 for r in results for x in r["runs"] if x.get("wrote_output"))
    readable = sum(1 for r in results if r["input_decodes"])
    print("")
    print("%d files x %d modes = %d invocations" % (len(manifest), len(MODES), total))
    print("inputs the system decoder opens: %d/%d" % (readable, len(manifest)))
    print("runs that wrote output: %d" % wrote)
    print("timed out: %d" % timeouts)

    print("")
    print("%-22s %8s %8s %8s %10s" % ("family", "wrote", "refused", "timeout", "dangerous"))
    for fam in sorted({r["family"] for r in results}):
        runs = [x for r in results if r["family"] == fam for x in r["runs"]]
        print("%-22s %8d %8d %8d %10d" % (
            fam,
            sum(1 for x in runs if x.get("wrote_output")),
            sum(1 for x in runs if x.get("exit", 0) != 0 and not x.get("wrote_output")),
            sum(1 for x in runs if x.get("timed_out")),
            sum(1 for x in runs if x.get("dangerous"))))

    # The unmutated seeds are in the corpus as the `control` family. If they
    # do not go through cleanly, nothing else in the run means anything.
    controls = [r for r in results if r["family"] == "control"]
    if not controls:
        print("")
        print("HARNESS FAILED: no control files; the generator did not include the seeds.")
        return 2

    # No output anywhere, on a corpus whose inputs the system can open, means
    # the engine was never really invoked. Reporting that as a clean corpus is
    # worse than reporting nothing. A corpus the system opens none of is the
    # same failure from the other side.
    if readable == 0 or (wrote == 0 and readable):
        print("")
        print("HARNESS FAILED: nothing was tested (%d readable inputs, %d runs wrote output)." % (readable, wrote))
        return 2

    print("")
    print("DANGEROUS RUNS: %d" % len(dangerous))
    for d in dangerous:
        print("  %s (%s): %s" % (d["file"], d["mode"], d["reason"]))
    print("report written to %s" % report)
    return 1 if dangerous else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
