#!/usr/bin/env python3
"""E34 - inject a probe into a WinPE ISO without rebuilding it.

The E14/E17 rig ships its probe as a plain file on the WinPE ISO (the
Joliet namespace holds an entry whose name is the WinPE path `X:\\e13pe.exe`,
and winpeshl.ini starts it).  Rebuilding the ISO needs genisoimage and
editing the WIM needs wimlib; both are unavailable on the rig host.

This tool does the cheap thing instead: it parses the ISO's Joliet directory
tree, finds the probe entry, and overwrites its content **in place** with the
new binary, padded to the original size so that every ISO9660/Joliet
directory record (extent, length) stays valid.

Usage:
    python e34-iso-inject.py --iso pe-e17.iso --list
    python e34-iso-inject.py --iso pe-e17.iso --find e13pe.exe
    python e34-iso-inject.py --iso pe-e17.iso --find e13pe.exe \
        --replace e34probe.exe --out pe-e34.iso
"""
import argparse
import os
import shutil
import struct
import sys
from typing import List, Tuple

SECTOR = 2048
JOLIET_ESC = b"%/@"
JOLIET_NAME = "joliet"


class Iso9660:
    """Minimal ISO9660 + Joliet reader (enough to find and patch a file)."""

    def __init__(self, path: str):
        self.path = path
        self.fh = open(path, "rb")
        self.primary_root = self._root_dir(self._find_primary())
        self.joliet_root = self._root_dir(self._find_joliet())

    # ---------------------------------------------------------- descriptors
    def _read(self, offset: int, length: int) -> bytes:
        self.fh.seek(offset)
        return self.fh.read(length)

    def _pvd(self) -> Tuple[int, int, int]:
        """Return (root dir extent, root dir size, joliet escape offset)."""
        self.fh.seek(16 * SECTOR)
        for _ in range(32):
            data = self._read(16 * SECTOR, SECTOR)
            if data[1:6] != b"CD001":
                break
            if data[0] == 1:                      # primary volume descriptor
                root = struct.unpack_from("<I", data, 156 + 2)[0]
                size = struct.unpack_from("<I", data, 156 + 10)[0]
                joliet = 0
                for esc_pos in (88, 120, 152):
                    if data[esc_pos:esc_pos + 5] == JOLIET_ESC:
                        joliet = esc_pos
                return root, size, joliet
            self.fh.seek(16 * SECTOR + SECTOR)
        raise ValueError("no ISO9660 primary volume descriptor found")

    def _find_primary(self):
        root, size, _ = self._pvd()
        return root, size, False

    def _find_joliet(self):
        try:
            root, size, joliet = self._pvd()
        except ValueError:
            return None
        if not joliet:
            return None
        # The supplementary descriptor sits right after the primary one.
        self.fh.seek(16 * SECTOR + SECTOR)
        data = self._read(16 * SECTOR + SECTOR, SECTOR)
        if data[1:6] != b"CD001" or data[0] != 2:
            return None
        root = struct.unpack_from("<I", data, 156 + 2)[0]
        size = struct.unpack_from("<I", data, 156 + 10)[0]
        return root, size, True

    @staticmethod
    def _root_dir(info) -> Tuple[int, int]:
        return info[0], info[1]

    # ------------------------------------------------------------ directory
    def read_dir(self, extent: int, size: int) -> List[dict]:
        raw = self._read(extent * SECTOR, size)
        out = []
        pos = 0
        while pos < len(raw):
            rec_len = raw[pos]
            if rec_len == 0:
                # skip to the next sector boundary
                pos = ((pos // SECTOR) + 1) * SECTOR
                continue
            rec = raw[pos:pos + rec_len]
            ext = struct.unpack_from("<I", rec, 2)[0]
            length = struct.unpack_from("<I", rec, 10)[0]
            flags = rec[25]
            name_len = rec[32]
            name_raw = rec[33:33 + name_len]
            if name_len == 1 and name_raw in (b"\x00", b"\x01"):
                name = "." if name_raw == b"\x00" else ".."
            elif flags & 0x80:            # UCS-2 (Joliet)
                name = name_raw.decode("utf-16-be", "replace")
            else:
                name = name_raw.decode("ascii", "replace").split(";")[0]
            out.append({
                "name": name, "extent": ext, "length": length,
                "is_dir": bool(flags & 0x02), "record_offset": pos,
            })
            pos += rec_len
        return out

    def walk(self, extent: int, size: int, prefix: str = "") -> List[dict]:
        out = []
        for e in self.read_dir(extent, size):
            if e["name"] in (".", ".."):
                continue
            full = prefix + "/" + e["name"]
            e = dict(e, path=full)
            out.append(e)
            if e["is_dir"]:
                out.extend(self.walk(e["extent"], e["length"], full))
        return out

    def find(self, needle: str) -> List[dict]:
        hits = []
        for root in (self.joliet_root, self.primary_root):
            if not root:
                continue
            for e in self.walk(root[0], root[1]):
                if needle.lower() in e["name"].lower():
                    hits.append(e)
        return hits

    def close(self):
        try:
            self.fh.close()
        except OSError:
            pass


def patch_content(iso_path: str, out_path: str, entry: dict,
                  payload: bytes) -> None:
    """Overwrite the entry's extent with payload, padded to the entry size."""
    size = entry["length"]
    if len(payload) > size:
        raise SystemExit("replacement (%d bytes) does not fit the original "
                         "entry (%d bytes)" % (len(payload), size))
    if out_path and not os.path.exists(out_path):
        shutil.copy2(iso_path, out_path)
    target = out_path or iso_path
    with open(target, "r+b") as fh:
        fh.seek(entry["extent"] * SECTOR)
        fh.write(payload)
        fh.write(b"\x00" * (size - len(payload)))
    print("injected %d bytes into %s (extent %d, size %d)"
          % (len(payload), target, entry["extent"], size))


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 ISO probe injector")
    ap.add_argument("--iso", required=True)
    ap.add_argument("--list", action="store_true",
                    help="list the Joliet tree")
    ap.add_argument("--find", default="",
                    help="find entries whose name contains this")
    ap.add_argument("--replace", default="",
                    help="file whose content replaces the found entry")
    ap.add_argument("--out", default="",
                    help="output ISO (default: patch in place)")
    args = ap.parse_args()

    iso = Iso9660(args.iso)
    try:
        if args.list:
            for e in iso.walk(iso.joliet_root[0], iso.joliet_root[1]):
                print("%-12s %-40s extent=%-8d size=%d"
                      % ("DIR" if e["is_dir"] else "FILE", e["path"],
                         e["extent"], e["length"]))
            return 0
        if not args.find:
            ap.error("--find or --list is required")
        hits = iso.find(args.find)
        if not hits:
            print("no entry matching %r" % args.find)
            return 1
        for e in hits:
            print("found %-40s extent=%-8d size=%-10d dir=%s"
                  % (e["path"], e["extent"], e["length"], e["is_dir"]))
        if args.replace:
            target = hits[0]
            with open(args.replace, "rb") as fh:
                payload = fh.read()
            patch_content(args.iso, args.out, target, payload)
    finally:
        iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
