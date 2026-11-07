#!/usr/bin/env python3
"""Reject ESP-DL references that could silently bind to ESP-SR's older kernels."""
import argparse
import subprocess


def symbols(nm, archive, mode):
    output = subprocess.check_output([nm, "-g", mode, archive], text=True)
    return {line.split()[-1] for line in output.splitlines()
            if len(line.split()) >= 2 and not line.endswith(":")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("nm", "dl", "sr"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    vision = symbols(args.nm, args.dl, "--defined-only")
    speech = symbols(args.nm, args.sr, "--defined-only")
    references = symbols(args.nm, args.dl, "-u")
    kernel = lambda name: name.startswith(("dl_", "tie728_"))
    collisions = sorted(filter(kernel, vision & speech))
    wrong_references = sorted(filter(kernel, (references & speech) - vision))
    isolated = sum(name.startswith("collector_face_dl_") for name in vision)
    if collisions or wrong_references or isolated != 48:
        raise SystemExit("Face kernel isolation FAILED: collisions=%s, speech references=%s, isolated=%d/48"
                         % (collisions, wrong_references, isolated))
    print("Face kernel isolation PASS: 48 private kernels, no ESP-SR collisions or references")


if __name__ == "__main__":
    main()
