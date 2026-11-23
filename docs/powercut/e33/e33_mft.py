#!/usr/bin/env python3
"""E33 - raw $MFT parser: locate $LogFile extents and fixture directory context.

Reads a raw $MFT image extracted from the test volume and answers the
questions the E33 rig needs:

  * which LCNs (logical cluster numbers) hold $LogFile (MFT record 2's
    nonresident $DATA run) — the extraction/counter-A mapping;
  * which MFT record number and $INDEX_ROOT/$INDEX_ALLOCATION layout a
    fixture directory has (resident vs nonresident $I30, and the LCNs of
    the $INDEX_ALLOCATION extents) — the counter-B mapping;
  * a name -> record-number index so the rig can reference the fixtures.

Usage:
    python e33_mft.py --mft MFT.bin --cluster-size 4096 --json out.json \
        [--find-name e33_parent] [--dump-record 12345]
"""
import argparse
import json
import struct
import sys
from typing import Dict, List, Optional, Tuple

MFT_RECORD_SIZE = 1024
ATTR_STD = 0x10
ATTR_LIST = 0x20
ATTR_NAME = 0x30
ATTR_DATA = 0x80
ATTR_ROOT = 0x90
ATTR_ALLOC = 0xA0
ATTR_BITMAP = 0xB0

ATTR_NAMES: Dict[int, str] = {
    0x10: "$STANDARD_INFORMATION", 0x20: "$ATTRIBUTE_LIST",
    0x30: "$FILE_NAME", 0x40: "$OBJECT_ID", 0x50: "$SECURITY_DESCRIPTOR",
    0x60: "$VOLUME_NAME", 0x70: "$VOLUME_INFORMATION", 0x80: "$DATA",
    0x90: "$INDEX_ROOT", 0xA0: "$INDEX_ALLOCATION", 0xB0: "$BITMAP",
    0xC0: "$REPARSE_POINT", 0xD0: "$EA_INFORMATION", 0xE0: "$EA",
    0x100: "$LOGGED_UTILITY_STREAM",
}


class MftError(Exception):
    pass


def apply_fixups(rec: bytearray, record_size: int) -> bytearray:
    if len(rec) < record_size:
        raise MftError("record truncated: %d" % len(rec))
    fix_off, fix_cnt = struct.unpack_from("<HH", rec, 4)
    if fix_off < 0x30 or fix_cnt < 1 or fix_off + fix_cnt * 2 > record_size:
        raise MftError("bad fixup header")
    seq = bytes(rec[fix_off:fix_off + 2])
    for i in range(1, fix_cnt):
        at = i * 512 - 2
        if at + 2 > record_size:
            break
        stored = bytes(rec[fix_off + i * 2:fix_off + i * 2 + 2])
        if bytes(rec[at:at + 2]) != seq:
            raise MftError("fixup mismatch at %d" % at)
        rec[at:at + 2] = stored
    return rec


def parse_run_list(data: bytes, off: int) -> List[Tuple[int, int]]:
    """Decode an MFT data-run list starting at data[off:]; return [(vcn, lcn)]."""
    runs: List[Tuple[int, int]] = []
    vcn = 0
    lcn = 0
    while off < len(data):
        hdr = data[off]
        if hdr == 0:
            break
        len_field = hdr & 0xF
        off_field = (hdr >> 4) & 0xF
        off += 1
        if len_field == 0 or off + len_field + off_field > len(data):
            raise MftError("bad run header")
        raw_len = data[off:off + len_field]
        run_len = int.from_bytes(raw_len, "little")
        off += len_field
        raw_off = data[off:off + off_field]
        off += off_field
        if off_field:
            run_off = int.from_bytes(raw_off, "little", signed=True)
            lcn += run_off
        runs.append((vcn, lcn))
        vcn += run_len
    return runs


def is_nonresident(attr: bytes) -> bool:
    return attr[8] != 0


def attr_header_size(attr: bytes) -> int:
    return 0x48 if is_nonresident(attr) else 0x18


class MftImage:
    """A parsed raw $MFT image, or a packed list of selected records."""

    packed_numbers: Optional[List[int]] = None

    def __init__(self, data: bytes, cluster_size: int = 4096,
                 packed_numbers: Optional[List[int]] = None):
        self.data = data
        self.cluster_size = cluster_size
        self.record_size = MFT_RECORD_SIZE
        self.packed_numbers = packed_numbers
        # Detect the record size from the first record's fixup count.
        if len(data) >= 2 * 1024:
            cnt = struct.unpack_from("<H", data, 6)[0]
            if cnt in (0, 0xFFFF):
                pass
            else:
                guess = (cnt - 1) * 512
                if guess in (1024, 2048, 4096):
                    self.record_size = guess
        self.count = len(data) // self.record_size

    @classmethod
    def from_packed(cls, data: bytes, numbers: List[int],
                    record_size: int, cluster_size: int = 4096):
        """Build an image over a packed list of raw MFT records."""
        inst = cls(data, cluster_size, packed_numbers=list(numbers))
        inst.record_size = record_size
        inst.count = len(numbers)
        return inst

    def record(self, number: int) -> Optional[bytearray]:
        if self.packed_numbers is not None:
            if number not in self.packed_numbers:
                return None
            slot = self.packed_numbers.index(number)
            start = slot * self.record_size
            raw = bytearray(self.data[start:start + self.record_size])
            if len(raw) != self.record_size:
                return None
            if bytes(raw[:4]) not in (b"FILE", b"BAAD"):
                return None
            try:
                return apply_fixups(raw, self.record_size)
            except MftError:
                return raw
        if number < 0 or number >= self.count:
            return None
        start = number * self.record_size
        raw = bytearray(self.data[start:start + self.record_size])
        if len(raw) != self.record_size:
            return None
        magic = bytes(raw[:4])
        if magic not in (b"FILE", b"BAAD"):
            return None
        try:
            return apply_fixups(raw, self.record_size)
        except MftError:
            return raw  # keep the damaged record; callers flag magic

    def record_flag_in_use(self, rec: bytearray) -> bool:
        return struct.unpack_from("<H", rec, 0x16)[0] & 0x1

    def record_seq(self, rec: bytearray) -> int:
        return struct.unpack_from("<H", rec, 0x10)[0]

    def attributes(self, rec: bytearray) -> List[dict]:
        """Enumerate attributes (following $ATTRIBUTE_LIST is NOT done;
        records carrying one are flagged)."""
        out: List[dict] = []
        try:
            used = struct.unpack_from("<I", rec, 0x18)[0]
            first = struct.unpack_from("<H", rec, 0x14)[0]
        except struct.error:
            return out
        pos = first
        has_alist = False
        while pos + 8 <= min(used, len(rec)):
            attr_type = struct.unpack_from("<I", rec, pos)[0]
            if attr_type == 0xFFFFFFFF or attr_type == 0:
                break
            a_len = struct.unpack_from("<I", rec, pos + 4)[0]
            if a_len < 8 or pos + a_len > len(rec):
                break
            attr = bytes(rec[pos:pos + a_len])
            entry = self._parse_attr(attr, attr_type, a_len, pos)
            out.append(entry)
            if attr_type == ATTR_LIST:
                has_alist = True
            pos += a_len
        if out:
            out[0]["has_attribute_list"] = has_alist
        return out

    def _parse_attr(self, attr: bytes, attr_type: int, a_len: int,
                    pos: int) -> dict:
        non_res = is_nonresident(attr)
        name_len = attr[9]
        name_off = struct.unpack_from("<H", attr, 0xA)[0]
        name = ""
        if name_len and name_off + name_len * 2 <= len(attr):
            name = attr[name_off:name_off + name_len * 2].decode(
                "utf-16-le", errors="replace")
        entry: dict = {
            "type": ATTR_NAMES.get(attr_type, "0x%08X" % attr_type),
            "type_code": attr_type,
            "name": name,
            "nonresident": non_res,
            "offset_in_record": pos,
            "length": a_len,
        }
        if not non_res:
            data_size = struct.unpack_from("<I", attr, 0x10)[0]
            data_off = struct.unpack_from("<H", attr, 0x14)[0]
            entry["data_size"] = data_size
            entry["data_offset"] = data_off
            entry["indexed"] = bool(attr[0x16] & 1)
        else:
            svcn, evcn = struct.unpack_from("<QQ", attr, 0x10)
            run_off = struct.unpack_from("<H", attr, 0x20)[0]
            alloc = struct.unpack_from("<Q", attr, 0x28)[0]
            data_size = struct.unpack_from("<Q", attr, 0x30)[0]
            entry["svcnn"] = svcn
            entry["evcn"] = evcn
            entry["alloc_size"] = alloc
            entry["data_size"] = data_size
            try:
                entry["runs"] = [(v, l) for v, l in
                                 parse_run_list(attr, run_off)]
            except MftError:
                entry["runs"] = []
            entry["lcns"] = self.runs_to_lcns(entry["runs"], svcn, evcn)
        return entry

    def runs_to_lcns(self, runs: List[Tuple[int, int]], svcn: int,
                     evcn: int) -> List[int]:
        """Expand [(start_vcn, lcn)] runs into the LCN of every cluster
        between svcn..evcn (inclusive), saturating at the run list length."""
        if not runs:
            return []
        clusters = evcn - svcn + 1
        lcns: List[int] = []
        for i, (start_vcn, lcn) in enumerate(runs):
            next_vcn = runs[i + 1][0] if i + 1 < len(runs) else None
            run_len = (next_vcn - start_vcn) if next_vcn is not None else None
            if run_len is None:
                run_len = max(0, clusters - (start_vcn - svcn))
            for k in range(run_len):
                if len(lcns) >= clusters:
                    break
                lcns.append(lcn + k)
        return lcns

    def file_name(self, rec: bytearray) -> Optional[str]:
        """Best $FILE_NAME (prefer WIN32 over DOS)."""
        best = None
        for a in self.attributes(rec):
            if a["type_code"] != ATTR_NAME:
                continue
            attr = bytes(rec[a["offset_in_record"]:a["offset_in_record"] +
                         a["length"]])
            try:
                off = struct.unpack_from("<H", attr, 0x14)[0]
                parent = struct.unpack_from("<Q", attr, off)[0]
                name_len = attr[off + 0x40]
                ntype = attr[off + 0x41]
                raw = attr[off + 0x42:off + 0x42 + name_len * 2]
                nm = raw.decode("utf-16-le", errors="replace")
            except struct.error:
                continue
            if ntype in (0x01, 0x03):
                return nm
            if best is None:
                best = nm
            _ = parent
        return best

    def index_context(self, number: int) -> Optional[dict]:
        """Resident/nonresident $I30 layout for a directory record."""
        rec = self.record(number)
        if rec is None:
            return None
        ctx: dict = {
            "record": number,
            "sequence": self.record_seq(rec),
            "in_use": self.record_flag_in_use(rec),
            "file_name": self.file_name(rec),
            "index_root": None,
            "index_alloc": None,
            "index_bitmap": None,
        }
        for a in self.attributes(rec):
            if a["type_code"] == ATTR_ROOT:
                ctx["index_root"] = {
                    "nonresident": a["nonresident"],
                    "data_size": a.get("data_size"),
                    "offset_in_record": a["offset_in_record"],
                }
            elif a["type_code"] == ATTR_ALLOC:
                ctx["index_alloc"] = {
                    "svcnn": a.get("svcnn"),
                    "evcn": a.get("evcn"),
                    "alloc_size": a.get("alloc_size"),
                    "lcns": a.get("lcns", []),
                    "index_block_lcn_bytes": [
                        lcn * self.cluster_size for lcn in a.get("lcns", [])],
                }
            elif a["type_code"] == ATTR_BITMAP:
                ctx["index_bitmap"] = {"data_size": a.get("data_size")}
        if ctx["index_alloc"] is not None:
            ctx["i30_nonresident"] = True
        elif ctx["index_root"] is not None:
            ctx["i30_nonresident"] = False
        else:
            ctx["i30_nonresident"] = None
        return ctx

    def find_by_name(self, wanted: str, limit: int = 64) -> List[int]:
        hits: List[int] = []
        if self.packed_numbers is not None:
            for n in self.packed_numbers:
                rec = self.record(n)
                if rec is None or not self.record_flag_in_use(rec):
                    continue
                if self.file_name(rec) == wanted:
                    hits.append(n)
            return hits
        for n in range(min(limit, self.count)):
            rec = self.record(n)
            if rec is None or not self.record_flag_in_use(rec):
                continue
            nm = self.file_name(rec)
            if nm == wanted:
                hits.append(n)
        if hits:
            return hits
        # Fall back to a full scan.
        for n in range(self.count):
            if n < limit:
                continue
            rec = self.record(n)
            if rec is None or not self.record_flag_in_use(rec):
                continue
            nm = self.file_name(rec)
            if nm == wanted:
                hits.append(n)
        return hits

    def logfile_lcns(self) -> Optional[List[int]]:
        """$LogFile lives in MFT record 2; return its $DATA LCNs."""
        rec = self.record(2)
        if rec is None:
            return None
        for a in self.attributes(rec):
            if a["type_code"] == ATTR_DATA and a["nonresident"]:
                return a.get("lcns", [])
        return None

    def logfile_byte_ranges(self) -> Optional[List[Tuple[int, int]]]:
        """$LogFile extents as [(byte_offset, byte_length)] on the volume."""
        lcns = self.logfile_lcns()
        if lcns is None:
            return None
        cs = self.cluster_size
        return [(lcn * cs, cs) for lcn in lcns]


def selftest() -> int:
    """Build a synthetic $MFT with records 2 ($LogFile) and 7 (a directory)."""
    cs = 4096
    rs = MFT_RECORD_SIZE
    img = bytearray(rs * 16)
    # Record 2: $LogFile with a two-run nonresident $DATA.
    rec = bytearray(rs)
    rec[0:4] = b"FILE"
    struct.pack_into("<H", rec, 0x10, 3)
    struct.pack_into("<I", rec, 0x18, 0x150)
    struct.pack_into("<I", rec, 0x1C, rs)
    # fixups
    fix_off = 0x30
    fix_cnt = 1 + rs // 512
    struct.pack_into("<H", rec, 4, fix_off)
    struct.pack_into("<H", rec, 6, fix_cnt)
    ap = fix_off + fix_cnt * 2
    struct.pack_into("<H", rec, 0x14, ap)   # first attribute offset
    struct.pack_into("<H", rec, 0x16, 0x1)  # in use
    struct.pack_into("<H", rec, fix_off, 0x1111)
    for i in range(1, fix_cnt):
        at = i * 512 - 2
        struct.pack_into("<H", rec, fix_off + i * 2,
                         struct.unpack_from("<H", rec, at)[0])
        rec[at:at + 2] = b"\x11\x11"
    # $DATA attr at 0x30+fix_cnt*2 = 0x30+18 = 0x42.
    # One run: 9 clusters starting at LCN 16 (header 0x11 = len-size 1,
    # offset-size 1; bytes: 09 10).
    runs = bytes([0x11, 0x09, 0x10]) + b"\x00"
    a_len = 0x48 + len(runs)
    struct.pack_into("<I", rec, ap, ATTR_DATA)
    struct.pack_into("<I", rec, ap + 4, a_len)
    rec[ap + 8] = 1
    struct.pack_into("<Q", rec, ap + 0x10, 0)          # svcn
    struct.pack_into("<Q", rec, ap + 0x18, 8)          # evcn (9 clusters)
    struct.pack_into("<H", rec, ap + 0x20, 0x48)       # run offset
    struct.pack_into("<Q", rec, ap + 0x28, 9 * cs)     # alloc size
    rec[ap + 0x48:ap + 0x48 + len(runs)] = runs
    struct.pack_into("<I", rec, ap + a_len, 0xFFFFFFFF)
    img[2 * rs:3 * rs] = rec

    m = MftImage(bytes(img), cs)
    lcns = m.logfile_lcns()
    assert lcns == [16, 17, 18, 19, 20, 21, 22, 23, 24], lcns
    ranges = m.logfile_byte_ranges()
    assert ranges[0] == (16 * cs, cs) and len(ranges) == 9
    ctx = m.index_context(2)
    assert ctx["file_name"] is None
    print("selftest logfile lcns=%d ranges=%d OK" % (len(lcns), len(ranges)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 raw $MFT parser")
    ap.add_argument("--mft", help="path to the raw $MFT image")
    ap.add_argument("--cluster-size", type=int, default=4096)
    ap.add_argument("--json", help="write the context JSON here")
    ap.add_argument("--find-name", action="append", default=[],
                    help="record numbers of these $FILE_NAMEs (repeatable)")
    ap.add_argument("--dump-record", type=int, default=None)
    ap.add_argument("--logfile-ranges", action="store_true",
                    help="print $LogFile byte ranges as JSON")
    ap.add_argument("--packed-json",
                    help="packed-record map from e33_extract.py (record numbers)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.mft:
        ap.error("--mft is required unless --selftest")

    with open(args.mft, "rb") as fh:
        data = fh.read()
    if args.packed_json:
        import json as _json
        with open(args.packed_json, encoding="utf-8") as fh:
            pj = _json.load(fh)
        m = MftImage.from_packed(
            data, pj.get("numbers", []),
            pj.get("record_size", 1024),
            pj.get("cluster_size", args.cluster_size))
    else:
        m = MftImage(data, args.cluster_size)
    out: dict = {
        "cluster_size": args.cluster_size,
        "record_size": m.record_size,
        "records_total": m.count,
        "logfile_lcn_count": 0,
    }
    lcns = m.logfile_lcns()
    if lcns is not None:
        out["logfile_lcn_count"] = len(lcns)
        out["logfile_lcn_first"] = lcns[:8]
        out["logfile_byte_ranges_sample"] = [
            [off, ln] for off, ln in m.logfile_byte_ranges()[:4]]
    if args.find_name:
        found = {}
        for nm in args.find_name:
            found[nm] = m.find_by_name(nm)
        out["found"] = found
        ctxs = {}
        for nm, nums in found.items():
            for n in nums:
                ctxs["%s#%d" % (nm, n)] = m.index_context(n)
        out["contexts"] = ctxs
    if args.dump_record is not None:
        out["record_%d" % args.dump_record] = m.index_context(args.dump_record)
    if args.logfile_ranges:
        rng = m.logfile_byte_ranges() or []
        out["logfile_ranges"] = [[o, l] for o, l in rng]
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
