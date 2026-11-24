#!/usr/bin/env python3
"""E34 - pre-recovery forensic decode of a crashed NTFS volume image.

Runs *before* the volume is mounted or recovered.  Decodes the $LogFile image
with the E33 decoder and reports, for every expected index operation of the
trial, one of four states:

  positively identified  the record was found and attributed to the file/parent
  overwritten            the log parsed, but the record is no longer there
  absent                the log has no evidence of the record at all
  undecodable           parts of the log could not be parsed

The $LogFile is circular, so "not found" never means "never written" - the
state vocabulary exists precisely to keep that distinction visible.

The $LogFile image is obtained either from a supplied image file
(--logfile-image) or by reading the crashed volume image directly: boot sector
-> $MFT record 0 -> $MFT runs -> record 2 -> $LogFile runs.

Usage:
    python e34-preimage.py --image vol.img --out preimage.json \
        --expect e34_create_0001.bin --parent-record 815 \
        [--volume-offset 0] [--logfile-image log.bin]
"""
import argparse
import json
import os
import struct
import sys
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
E33 = os.path.normpath(os.path.join(HERE, "..", "e33"))
sys.path.insert(0, E33)

import e33_logfile_decode as dec  # noqa: E402
import e33_mft  # noqa: E402

STATE_IDENTIFIED = "positively identified"
STATE_OVERWRITTEN = "overwritten"
STATE_ABSENT = "absent"
STATE_UNDECODABLE = "undecodable"


class ImageVolume:
    """A volume image (or a raw device) read through its own $MFT."""

    def __init__(self, path: str, volume_offset: int = 0):
        self.path = path
        self.volume_offset = volume_offset
        self.fh = open(path, "rb")
        boot = self.read_at(0, 512)
        if boot[3:11] != b"NTFS    ":
            raise ValueError("not an NTFS volume (OEM id %r)" % boot[3:11])
        self.bytes_per_sector = struct.unpack_from("<H", boot, 0x0B)[0]
        self.sectors_per_cluster = boot[0x0D]
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster
        self.mft_lcn = struct.unpack_from("<q", boot, 0x30)[0]
        enc = boot[0x40]
        if enc & 0x80:
            self.record_size = 1 << (256 - enc)
        else:
            self.record_size = self.cluster_size << enc
        if self.record_size < 256 or self.record_size > 65536:
            self.record_size = 1024
        self._mft_runs: Optional[List[Tuple[int, int]]] = None

    def close(self):
        try:
            self.fh.close()
        except OSError:
            pass

    def read_at(self, offset: int, length: int) -> bytes:
        self.fh.seek(self.volume_offset + offset)
        return self.fh.read(length)

    def mft_runs(self) -> List[Tuple[int, int]]:
        if self._mft_runs is None:
            rec0 = self.read_at(self.mft_lcn * self.cluster_size,
                               self.record_size)
            fixed = e33_mft.apply_fixups(bytearray(rec0), self.record_size)
            img = e33_mft.MftImage(bytes(fixed), self.cluster_size)
            runs = []
            for a in img.attributes(fixed):
                if a["type_code"] == e33_mft.ATTR_DATA and a["nonresident"]:
                    runs = a.get("runs", [])
                    break
            if not runs:
                raise ValueError("$MFT data runs not found in record 0")
            self._mft_runs = runs
        return self._mft_runs

    def read_mft_record(self, number: int) -> bytes:
        runs = self.mft_runs()
        per_cluster = self.cluster_size // self.record_size
        vcn = number // per_cluster
        for i, (start_vcn, lcn) in enumerate(runs):
            nxt = runs[i + 1][0] if i + 1 < len(runs) else None
            end_vcn = nxt if nxt is not None else vcn + 1
            if start_vcn <= vcn < end_vcn:
                offset = ((lcn + (vcn - start_vcn)) * self.cluster_size
                          + (number % per_cluster) * self.record_size)
                return self.read_at(offset, self.record_size)
        raise IndexError("MFT record %d is beyond the run list" % number)

    def read_logfile(self) -> bytes:
        rec2 = self.read_mft_record(2)
        fixed = e33_mft.apply_fixups(bytearray(rec2), self.record_size)
        img = e33_mft.MftImage(bytes(fixed), self.cluster_size)
        for a in img.attributes(fixed):
            if a["type_code"] != e33_mft.ATTR_DATA or not a["nonresident"]:
                continue
            runs = a.get("runs", [])
            size = a.get("data_size", a.get("alloc_size", 0))
            out = bytearray()
            for i, (start_vcn, lcn) in enumerate(runs):
                nxt = runs[i + 1][0] if i + 1 < len(runs) else None
                if nxt is None:
                    clusters = max(0, (size + self.cluster_size - 1)
                                   // self.cluster_size - start_vcn)
                else:
                    clusters = nxt - start_vcn
                if clusters <= 0:
                    break
                take = clusters * self.cluster_size
                if len(out) + take > size:
                    take = max(0, size - len(out))
                if take <= 0:
                    break
                out.extend(self.read_at(lcn * self.cluster_size, take))
            return bytes(out)
        raise ValueError("$LogFile $DATA runs not found in record 2")


def classify_expected(records: List[dict], expected: List[str],
                       parent_record: Optional[int],
                       parsed_pages: int, undecodable_pages: int) -> List[dict]:
    """Match the expected file names against the decoded records."""
    out = []
    for name in expected:
        hits = []
        for r in records:
            ent = r.get("index_entry") or r.get("undo_index_entry") or {}
            if ent.get("file_name") != name:
                continue
            if parent_record is not None:
                # The index entry's home field is an MFT reference; compare the
                # record number (the low 48 bits) so a sequence-number
                # difference does not hide a real hit.
                home = ent.get("parent_record")
                if home is not None and \
                        (home & 0xFFFFFFFFFFFF) != (parent_record & 0xFFFFFFFFFFFF):
                    continue
            hits.append(r)
        if hits:
            rec = hits[0]
            ent = rec.get("index_entry") or rec.get("undo_index_entry") or {}
            out.append({
                "filename": name,
                "state": STATE_IDENTIFIED,
                "lsn": rec.get("lsn"),
                "rcrd_page": rec.get("raw_log_page"),
                "rcrd_page_byte_offset": rec.get("raw_log_page_byte_offset"),
                "redo": rec.get("redo"),
                "undo": rec.get("undo"),
                "transaction_id": rec.get("transaction_id"),
                "parent_record": ent.get("parent_record"),
                "child_record": ent.get("ref_record"),
                "matches": len(hits),
            })
            continue
        # Not found.  The circular log means this is not proof of absence.
        if undecodable_pages > 0:
            out.append({"filename": name, "state": STATE_UNDECODABLE,
                        "lsn": None, "rcrd_page": None,
                        "undecodable_pages": undecodable_pages})
        elif parsed_pages > 0:
            out.append({"filename": name, "state": STATE_OVERWRITTEN,
                        "lsn": None, "rcrd_page": None})
        else:
            out.append({"filename": name, "state": STATE_ABSENT,
                        "lsn": None, "rcrd_page": None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 pre-recovery decode")
    ap.add_argument("--image", default=None,
                    help="crashed volume image (or raw device)")
    ap.add_argument("--out", required=True, help="output JSON")
    ap.add_argument("--expect", action="append", default=[],
                    help="file name expected to be in the log (repeatable)")
    ap.add_argument("--parent-record", type=int, default=None)
    ap.add_argument("--volume-offset", type=int, default=0,
                    help="byte offset of the NTFS volume inside the image")
    ap.add_argument("--logfile-image", default=None,
                    help="pre-extracted $LogFile image (skips volume parsing)")
    ap.add_argument("--cluster-size", type=int, default=4096)
    args = ap.parse_args()

    result = {
        "image": os.path.basename(args.image) if args.image else None,
        "expected": args.expect,
        "parent_record": args.parent_record,
        "log_bytes": 0,
        "states": [],
        "records_total": 0,
        "records_index": 0,
        "parsed_pages": 0,
        "undecodable_pages": 0,
    }

    log_bytes = None
    volume = None
    try:
        if args.logfile_image:
            with open(args.logfile_image, "rb") as fh:
                log_bytes = fh.read()
        elif args.image:
            volume = ImageVolume(args.image, args.volume_offset)
            log_bytes = volume.read_logfile()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print("e34-preimage: cannot read the $LogFile: %s" % result["error"])
        return 1
    finally:
        if volume is not None:
            volume.close()

    result["log_bytes"] = len(log_bytes) if log_bytes else 0
    if not log_bytes:
        result["error"] = "empty $LogFile image"
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print("e34-preimage: empty $LogFile image")
        return 1

    parser = dec.LogFileParser(log_bytes)
    parser.cluster_size = args.cluster_size
    records = parser.walk_chains()
    result["records_total"] = len(records)
    result["records_index"] = sum(1 for r in records if r.get("index_op"))

    page_count = len(log_bytes) // parser.page_size
    parsed = 0
    undecodable = 0
    for n in range(2, page_count):
        page = parser.page_by_number(n)
        if page is None:
            continue
        if page.magic == dec.RCRD:
            parsed += 1
        else:
            undecodable += 1
    result["parsed_pages"] = parsed
    result["undecodable_pages"] = undecodable

    result["states"] = classify_expected(
        records, args.expect, args.parent_record, parsed, undecodable)

    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    counts = {}
    for s in result["states"]:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    print("e34-preimage: %d index records, states=%s"
          % (result["records_index"], json.dumps(counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
