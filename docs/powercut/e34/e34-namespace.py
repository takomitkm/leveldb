#!/usr/bin/env python3
"""E34 - offline NTFS namespace listing on a crashed image (no replay).

pytsk3 reads the on-disk metadata exactly as it sits in the image (the
equivalent of `mount -o ro,norecover`).  It does NOT replay $LogFile, so a
name found here survived in the home pages; a name missing here but present in
the pre-recovery log decode means recovery would depend on replay.

Usage: python e34-namespace.py --image crashed.img --dir /e14 --offset 1048576
"""
import argparse
import json
import sys

try:
    import pytsk3
except ImportError:
    print("pytsk3 is required: pip install pytsk3")
    sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--dir", default="/e14",
                    help="directory inside the image; open '/' first if the "
                         "path is rewritten by MSYS/Git Bash")
    ap.add_argument("--offset", type=int, default=0,
                    help="byte offset of the NTFS volume inside the image")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    img = pytsk3.Img_Info(args.image)
    try:
        fs = pytsk3.FS_Info(img, offset=args.offset)
    except Exception:  # noqa: BLE001
        # Some builds need the offset in a different attribute.
        fs = pytsk3.FS_Info(img)
    out = {"image": args.image, "dir": args.dir, "offset": args.offset,
           "files": [], "error": None}
    # On Windows/MSYS2 a leading slash can be rewritten into a host path;
    # strip it so pytsk3 receives the plain image path.
    raw = args.dir.lstrip("/")
    # If a POSIX tool rewrote /e14 into a host path, keep only the last piece.
    target = raw.split("/")[-1]
    try:
        d = fs.open_dir(path=target)
        for e in d:
            try:
                name = e.info.name.name
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            if name in (".", ".."):
                continue
            out["files"].append({
                "name": name,
                "inode": int(getattr(e.info, "inode", 0) or 0),
                "meta_type": int(getattr(e.info, "meta_type", 0) or 0),
            })
    except Exception as exc:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload)
    print("e34-namespace: %d entries in %s (error=%s)"
          % (len(out["files"]), args.dir, out["error"]))
    return 0 if out["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
