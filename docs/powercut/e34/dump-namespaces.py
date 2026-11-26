#!/usr/bin/env python3
"""E34 - dump the raw (no-replay) namespace of every trial image, one at a
time.

The pre-recovery images are 4 GiB each, so they are pulled, analysed and
deleted one at a time: the local disk never holds more than one image.

Usage: python dump-namespaces.py --list list.txt --work-dir <dir> --out <jsonl>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAMESPACE = os.path.join(HERE, "e34-namespace.py")


def sftp_get(remote: str, local: str) -> bool:
    batch = local + ".batch"
    # The native Windows sftp.exe does not understand MSYS paths such as
    # /s/apps/...; hand it a drive-letter path instead.
    local_win = os.path.abspath(local).replace("\\", "/")
    with open(batch, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("get %s %s\n" % (remote, local_win))
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    sftp = os.environ.get("E34_SFTP") or shutil.which("sftp")
    if not sftp:
        return False
    res = subprocess.run(
        [sftp, "-o", "BatchMode=yes", "-o", "ConnectTimeout=60",
         "-b", batch, "school-guest-pc2"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    os.unlink(batch)
    return res.returncode == 0 and os.path.exists(local)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dir", default="e34/large")
    args = ap.parse_args()

    img_dir = os.path.join(args.work_dir, "img")
    os.makedirs(img_dir, exist_ok=True)
    results = []
    with open(args.list, encoding="utf-8") as fh:
        names = [line.strip() for line in fh if line.strip()]
    for name in names:
        tag = name[len("e34-pre-"):] if name.startswith("e34-pre-") else name
        if tag.endswith(".gz"):
            tag = tag[:-3]
        print("== %s" % tag, flush=True)
        local_gz = os.path.join(img_dir, tag + ".gz")
        local_img = os.path.join(img_dir, tag)
        if not sftp_get(name, local_gz):
            results.append({"tag": tag, "error": "pull failed"})
            print(json.dumps(results[-1]), flush=True)
            continue
        try:
            res = subprocess.run(["gunzip", "-f", local_gz],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
        except OSError:
            res = subprocess.CompletedProcess([], 127, "", "gunzip missing")
        if not os.path.exists(local_img):
            # gunzip is a shell tool; fall back to the gzip module so the
            # script also runs from a plain Windows Python.
            try:
                import gzip
                with gzip.open(local_gz, "rb") as src, \
                        open(local_img, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 22)
                        if not chunk:
                            break
                        dst.write(chunk)
                os.unlink(local_gz)
            except OSError as exc:
                results.append({"tag": tag, "error": "gunzip failed: %s"
                                % str(exc)[:200]})
                print(json.dumps(results[-1]), flush=True)
                continue
        if not os.path.exists(local_img):
            results.append({"tag": tag, "error": "gunzip failed: %s"
                            % res.stderr.strip()[:200]})
            print(json.dumps(results[-1]), flush=True)
            continue
        target = "/" + args.dir
        res = subprocess.run([sys.executable, NAMESPACE, "--image", local_img,
                              "--dir", target, "--offset", "1048576",
                              "--out", local_img + ".ns.jsonl"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        entries = []
        ns_path = local_img + ".ns.jsonl"
        if os.path.exists(ns_path):
            with open(ns_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        results.append({"tag": tag, "entries": entries,
                        "stderr": res.stderr.strip()[-200:]})
        print("   entries=%d %s" % (len(entries),
                                    [e.get("name") for e in entries]), flush=True)
        os.unlink(local_img)
        if os.path.exists(ns_path):
            os.unlink(ns_path)

    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("NS-DONE %d trials" % len(results))
    shutil.rmtree(img_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
