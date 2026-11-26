#!/usr/bin/env python3
"""E34 - raw (no-replay) namespace dump of a crashed NTFS volume.

pytsk3 reads the on-disk metadata exactly as it sits in the image (the
equivalent of `mount -o ro,norecover`).  It does NOT replay $LogFile, so a
name found here survived in the home pages; a name missing here but present
in the pre-recovery log decode means recovery depends on the replay.

The output is JSON lines, one entry per name, so the recovery classifier can
consume it directly:

    {"ev": "pre_entry", "name": "...", "size": 4096, "dir": 0}

Usage:
    python e34-namespace.py --image crashed.img --dir /e34 --offset 1048576
    python e34-namespace.py --image crashed.img --dir /e34 --offset 1048576 \
        --out namespace.jsonl
"""
import argparse
import json
import re
import sys

try:
    import pytsk3
except ImportError:
    print("pytsk3 is required: pip install pytsk3", file=sys.stderr)
    sys.exit(2)


def open_fs(image: str, offset: int):
    img = pytsk3.Img_Info(image)
    try:
        return pytsk3.FS_Info(img, offset=offset)
    except Exception:  # noqa: BLE001
        # Some builds need the offset in a different attribute.
        return pytsk3.FS_Info(img)


def candidate_paths(value: str) -> list:
    """Candidate in-volume paths to try, longest first.

    On Windows/MSYS2 a leading slash can be rewritten into a host path
    ("/e34/large" -> "C:/Users/.../Git/e34/large"), which pytsk3 cannot
    open.  Trying the trailing components finds the in-volume path
    regardless of how the shell mangled the argument.
    """
    parts = [p for p in value.replace("\\", "/").split("/")
             if p and not re.match(r"^[A-Za-z]:$", p)]
    out = []
    for i in range(len(parts)):
        cand = "/".join(parts[i:])
        if cand not in out:
            out.append(cand)
    return out or [value]


def dump(fs, target: str) -> list:
    entries = []
    directory = fs.open_dir(path=target)
    for entry in directory:
        try:
            name = entry.info.name.name
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if name in (".", ".."):
            continue
        meta = entry.info.meta
        is_dir = bool(meta and getattr(meta, "type", None) ==
                     pytsk3.TSK_FS_META_TYPE_DIR)
        entries.append({
            "ev": "pre_entry",
            "name": name,
            "size": int(meta.size) if meta else 0,
            "dir": 1 if is_dir else 0,
        })
    return entries


def dump_with_fallback(fs, value: str) -> list:
    last = None
    for cand in candidate_paths(value):
        try:
            return dump(fs, cand)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last if last else OSError("no candidate path worked")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--dir", default="/e34",
                    help="directory inside the image")
    ap.add_argument("--offset", type=int, default=0,
                    help="byte offset of the NTFS volume inside the image")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fs = open_fs(args.image, args.offset)
    try:
        entries = dump_with_fallback(fs, args.dir)
        error = None
    except Exception as exc:  # noqa: BLE001
        entries = []
        error = "%s: %s" % (type(exc).__name__, exc)

    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    payload = "\n".join(lines)
    if payload:
        payload += "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    else:
        sys.stdout.write(payload)
    print("e34-namespace: %d entries in %s (error=%s)"
          % (len(entries), args.dir, error), file=sys.stderr)
    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())
