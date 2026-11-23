#!/usr/bin/env python3
"""E33 - NTFS $LogFile decoder: attribute directory-index operations to the target directory.

Parses a raw $LogFile image (and optionally $MFT for reference resolution) and
emits one JSON line per decoded NTFS client log record, plus a summary keyed on
the index operations that concern the test directory.

On-disk structures follow the NTFS LFS layout as documented/reverse-engineered
in:
  * Linux fs/ntfs3 fslog.c / ntfs.h (RESTART_HDR, RESTART_AREA, RECORD_PAGE_HDR,
    LFS_RECORD_HDR, LOG_REC_HDR, NTFS_DE, INDEX_ROOT, ATTR_FILE_NAME);
  * "Windows NT $LogFile transaction format" analyses.

Only reading happens here; nothing is written back to the image.

Selftest: run with --selftest, builds a synthetic log in memory.
"""
import argparse
import json
import struct
import sys
from typing import Dict, List, Optional, Tuple

UPDATE_SEQUENCE_STRIDE = 512
PAGE_SIZE = 4096

# Record magics.
RSTR = b"RSTR"
RCRD = b"RCRD"
BAAD = b"BAAD"
CHKD = b"CHKD"

# Log record types.
LFS_CLIENT_RECORD = 1
LFS_CLIENT_RESTART = 2

# NTFS log operation codes (Linux fs/ntfs3 enum NTFS_LOG_OPERATION).
OP_NOOP = 0x00
OP_COMPENSATION = 0x01
OP_INITIALIZE_FRS = 0x02
OP_DEALLOCATE_FRS = 0x03
OP_WRITE_EOF_SEGMENT = 0x04
OP_CREATE_ATTRIBUTE = 0x05
OP_DELETE_ATTRIBUTE = 0x06
OP_UPDATE_RESIDENT_VALUE = 0x07
OP_UPDATE_NONRESIDENT_VALUE = 0x08
OP_UPDATE_MAPPING_PAIRS = 0x09
OP_DELETE_DIRTY_CLUSTERS = 0x0A
OP_SET_NEW_ATTRIBUTE_SIZES = 0x0B
OP_ADD_INDEX_ENTRY_ROOT = 0x0C
OP_DELETE_INDEX_ENTRY_ROOT = 0x0D
OP_ADD_INDEX_ENTRY_ALLOCATION = 0x0E
OP_DELETE_INDEX_ENTRY_ALLOCATION = 0x0F
OP_WRITE_END_OF_INDEX_BUFFER = 0x10
OP_SET_INDEX_ENTRY_VCN_ROOT = 0x11
OP_SET_INDEX_ENTRY_VCN_ALLOCATION = 0x12
OP_UPDATE_FILE_NAME_ROOT = 0x13
OP_UPDATE_FILE_NAME_ALLOCATION = 0x14
OP_SET_BITS_BITMAP = 0x15
OP_CLEAR_BITS_BITMAP = 0x16
OP_HOTFIX = 0x17
OP_END_TOP_LEVEL_ACTION = 0x18
OP_PREPARE_TRANSACTION = 0x19
OP_COMMIT_TRANSACTION = 0x1A
OP_FORGET_TRANSACTION = 0x1B
OP_OPEN_NONRESIDENT_ATTRIBUTE = 0x1C
OP_OPEN_ATTRIBUTE_TABLE_DUMP = 0x1D
OP_ATTRIBUTE_NAMES_DUMP = 0x1E
OP_DIRTY_PAGE_TABLE_DUMP = 0x1F
OP_TRANSACTION_TABLE_DUMP = 0x20
OP_UPDATE_RECORD_DATA_ROOT = 0x21
OP_UPDATE_RECORD_DATA_ALLOCATION = 0x22
OP_UPDATE_RELATIVE_DATA_INDEX = 0x23
OP_UPDATE_RELATIVE_DATA_ALLOCATION = 0x24
OP_ZERO_END_OF_FILE_RECORD = 0x25

OP_NAMES: Dict[int, str] = {
    OP_NOOP: "Noop",
    OP_COMPENSATION: "CompensationLogRecord",
    OP_INITIALIZE_FRS: "InitializeFileRecordSegment",
    OP_DEALLOCATE_FRS: "DeallocateFileRecordSegment",
    OP_WRITE_EOF_SEGMENT: "WriteEndOfFileRecordSegment",
    OP_CREATE_ATTRIBUTE: "CreateAttribute",
    OP_DELETE_ATTRIBUTE: "DeleteAttribute",
    OP_UPDATE_RESIDENT_VALUE: "UpdateResidentValue",
    OP_UPDATE_NONRESIDENT_VALUE: "UpdateNonresidentValue",
    OP_UPDATE_MAPPING_PAIRS: "UpdateMappingPairs",
    OP_DELETE_DIRTY_CLUSTERS: "DeleteDirtyClusters",
    OP_SET_NEW_ATTRIBUTE_SIZES: "SetNewAttributeSizes",
    OP_ADD_INDEX_ENTRY_ROOT: "AddIndexEntryRoot",
    OP_DELETE_INDEX_ENTRY_ROOT: "DeleteIndexEntryRoot",
    OP_ADD_INDEX_ENTRY_ALLOCATION: "AddIndexEntryAllocation",
    OP_DELETE_INDEX_ENTRY_ALLOCATION: "DeleteIndexEntryAllocation",
    OP_WRITE_END_OF_INDEX_BUFFER: "WriteEndOfIndexBuffer",
    OP_SET_INDEX_ENTRY_VCN_ROOT: "SetIndexEntryVcnRoot",
    OP_SET_INDEX_ENTRY_VCN_ALLOCATION: "SetIndexEntryVcnAllocation",
    OP_UPDATE_FILE_NAME_ROOT: "UpdateFileNameRoot",
    OP_UPDATE_FILE_NAME_ALLOCATION: "UpdateFileNameAllocation",
    OP_SET_BITS_BITMAP: "SetBitsInNonresidentBitMap",
    OP_CLEAR_BITS_BITMAP: "ClearBitsInNonresidentBitMap",
    OP_HOTFIX: "HotFix",
    OP_END_TOP_LEVEL_ACTION: "EndTopLevelAction",
    OP_PREPARE_TRANSACTION: "PrepareTransaction",
    OP_COMMIT_TRANSACTION: "CommitTransaction",
    OP_FORGET_TRANSACTION: "ForgetTransaction",
    OP_OPEN_NONRESIDENT_ATTRIBUTE: "OpenNonresidentAttribute",
    OP_OPEN_ATTRIBUTE_TABLE_DUMP: "OpenAttributeTableDump",
    OP_ATTRIBUTE_NAMES_DUMP: "AttributeNamesDump",
    OP_DIRTY_PAGE_TABLE_DUMP: "DirtyPageTableDump",
    OP_TRANSACTION_TABLE_DUMP: "TransactionTableDump",
    OP_UPDATE_RECORD_DATA_ROOT: "UpdateRecordDataRoot",
    OP_UPDATE_RECORD_DATA_ALLOCATION: "UpdateRecordDataAllocation",
    OP_UPDATE_RELATIVE_DATA_INDEX: "UpdateRelativeDataInIndex",
    OP_UPDATE_RELATIVE_DATA_ALLOCATION: "UpdateRelativeDataInIndex2",
    OP_ZERO_END_OF_FILE_RECORD: "ZeroEndOfFileRecord",
}

# Operations that patch resident $MFT data (target = an FRS number).
RESIDENT_OPS = {
    OP_INITIALIZE_FRS, OP_DEALLOCATE_FRS, OP_WRITE_EOF_SEGMENT,
    OP_CREATE_ATTRIBUTE, OP_DELETE_ATTRIBUTE, OP_UPDATE_RESIDENT_VALUE,
    OP_UPDATE_MAPPING_PAIRS, OP_SET_NEW_ATTRIBUTE_SIZES,
    OP_ADD_INDEX_ENTRY_ROOT, OP_DELETE_INDEX_ENTRY_ROOT,
    OP_SET_INDEX_ENTRY_VCN_ROOT, OP_UPDATE_FILE_NAME_ROOT,
    OP_UPDATE_RECORD_DATA_ROOT, OP_UPDATE_RELATIVE_DATA_INDEX,
    OP_ZERO_END_OF_FILE_RECORD,
}

# Operations that patch nonresident data via an open-attribute-table entry.
NONRESIDENT_OPS = {
    OP_UPDATE_NONRESIDENT_VALUE, OP_ADD_INDEX_ENTRY_ALLOCATION,
    OP_DELETE_INDEX_ENTRY_ALLOCATION, OP_WRITE_END_OF_INDEX_BUFFER,
    OP_SET_INDEX_ENTRY_VCN_ALLOCATION, OP_UPDATE_FILE_NAME_ALLOCATION,
    OP_SET_BITS_BITMAP, OP_CLEAR_BITS_BITMAP,
    OP_UPDATE_RECORD_DATA_ALLOCATION, OP_UPDATE_RELATIVE_DATA_ALLOCATION,
}

# Index-relevant operations (the E33 evidence set).
INDEX_ROOT_OPS = {
    OP_ADD_INDEX_ENTRY_ROOT, OP_DELETE_INDEX_ENTRY_ROOT,
    OP_SET_INDEX_ENTRY_VCN_ROOT, OP_UPDATE_FILE_NAME_ROOT,
}
INDEX_ALLOC_OPS = {
    OP_ADD_INDEX_ENTRY_ALLOCATION, OP_DELETE_INDEX_ENTRY_ALLOCATION,
    OP_WRITE_END_OF_INDEX_BUFFER, OP_SET_INDEX_ENTRY_VCN_ALLOCATION,
    OP_UPDATE_FILE_NAME_ALLOCATION,
}
INDEX_OPS = INDEX_ROOT_OPS | INDEX_ALLOC_OPS

# Attribute type codes.
ATTR_ROOT = 0x90      # $INDEX_ROOT
ATTR_ALLOC = 0xA0     # $INDEX_ALLOCATION
ATTR_NAME = 0x30      # $FILE_NAME
ATTR_DATA = 0x80
ATTR_LIST = 0x20
ATTR_STD = 0x10


class LogFileError(Exception):
    pass


def unprotect_sectors(buf: bytearray) -> Tuple[bytearray, int]:
    """Apply the update sequence array (USA) fixups in place."""
    if len(buf) < 2 * UPDATE_SEQUENCE_STRIDE or len(buf) % UPDATE_SEQUENCE_STRIDE:
        raise LogFileError("block is not a whole number of sectors")
    usa_off, usa_cnt = struct.unpack_from("<HH", buf, 4)
    if usa_off < 8 or usa_off > UPDATE_SEQUENCE_STRIDE - 6:
        raise LogFileError("bad USA offset %d" % usa_off)
    if usa_cnt < 2 or (usa_cnt - 1) * UPDATE_SEQUENCE_STRIDE != len(buf):
        raise LogFileError("bad USA count %d for %d bytes" % (usa_cnt, len(buf)))
    if usa_off + usa_cnt * 2 > len(buf):
        raise LogFileError("USA overruns the block")
    usa_seq = bytes(buf[usa_off:usa_off + 2])
    for i in range(1, usa_cnt):
        stored = bytes(buf[usa_off + i * 2:usa_off + i * 2 + 2])
        at = i * UPDATE_SEQUENCE_STRIDE - 2
        if bytes(buf[at:at + 2]) != usa_seq:
            raise LogFileError("USA sequence mismatch at sector %d" % i)
        buf[at:at + 2] = stored
    return buf, usa_off + usa_cnt * 2


def mft_ref_split(ref: int) -> Tuple[int, int]:
    """Split a 64-bit MFT reference into (record_number, sequence)."""
    return ref & 0xFFFFFFFFFFFF, (ref >> 48) & 0xFFFF


def decode_utf16(raw: bytes) -> str:
    return raw.decode("utf-16-le", errors="replace")


def parse_index_entry(buf: bytes, base: int) -> Optional[dict]:
    """Parse an NTFS_DE (index entry) at buf[base:]. Returns None when unsafe."""
    if base < 0 or base + 0x10 > len(buf):
        return None
    e_ref, e_size, e_key, e_flags = struct.unpack_from("<QHHH", buf, base)
    out = {
        "entry_ref": e_ref,
        "ref_record": e_ref & 0xFFFFFFFFFFFF,
        "ref_sequence": (e_ref >> 48) & 0xFFFF,
        "entry_size": e_size,
        "key_size": e_key,
        "flags": e_flags,
        "file_name": None,
        "parent_ref": None,
    }
    last = e_flags & 0x2
    has_sub = e_flags & 0x1
    if not last and e_key >= 0x42:
        # The key is an ATTR_FILE_NAME (0x42 header + name).
        fn_off = base + 0x10
        if fn_off + 0x42 <= base + e_size and fn_off + 0x42 <= len(buf):
            parent = struct.unpack_from("<Q", buf, fn_off)[0]
            name_len_words = buf[fn_off + 0x40]
            name_type = buf[fn_off + 0x41]
            raw = buf[fn_off + 0x42:fn_off + 0x42 + name_len_words * 2]
            if len(raw) == name_len_words * 2:
                out["file_name"] = decode_utf16(raw)
                out["name_type"] = name_type
                out["parent_ref"] = parent
                out["parent_record"] = parent & 0xFFFFFFFFFFFF
    if has_sub and e_size >= 0x18 and base + 0x18 <= len(buf):
        out["sub_vcn"] = struct.unpack_from("<Q", buf, base + e_size - 8)[0]
    return out


class LogPage:
    """One 4096-byte RCRD page with fixups applied."""

    def __init__(self, raw: bytes, page_number: int, data_offset: int):
        if len(raw) != PAGE_SIZE:
            raise LogFileError("page size %d" % len(raw))
        magic = raw[:4]
        if magic not in (RCRD, BAAD, CHKD):
            raise LogFileError("bad page magic %r" % magic)
        self.magic = magic
        buf = bytearray(raw)
        self.buf, self.usa_end = unprotect_sectors(buf)
        self.page_number = page_number
        if data_offset < 8 or data_offset < self.usa_end or data_offset % 8:
            raise LogFileError("bad data offset %d (usa end %d)" % (data_offset, self.usa_end))
        self.data_offset = data_offset

    def last_lsn(self) -> int:
        return struct.unpack_from("<Q", self.buf, 8)[0]

    def last_end_lsn(self) -> int:
        return struct.unpack_from("<Q", self.buf, 32)[0]

    def next_record_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 24)[0]

    def page_flags(self) -> int:
        return struct.unpack_from("<I", self.buf, 16)[0]

    def header_at(self, off: int, hdr_len: int) -> Tuple[int, bytes]:
        """Return (this_lsn, full_record_bytes) for the LFS record header at off."""
        if off < self.data_offset or off + hdr_len > len(self.buf):
            raise LogFileError("record offset %d out of range" % off)
        this_lsn = struct.unpack_from("<Q", self.buf, off)[0]
        return this_lsn, bytes(self.buf[off:])

    def client_data_at(self, off: int, hdr_len: int, data_len: int) -> bytes:
        end = off + hdr_len + data_len
        if end > len(self.buf):
            raise LogFileError("client data overruns page (%d > %d)" % (end, len(self.buf)))
        return bytes(self.buf[off + hdr_len:end])


class ClientRestart:
    """NTFS client restart area (checkpoint)."""

    def __init__(self, buf: bytes):
        self.buf = buf

    @property
    def major(self) -> int:
        return struct.unpack_from("<I", self.buf, 0)[0]

    @property
    def minor(self) -> int:
        return struct.unpack_from("<I", self.buf, 4)[0]

    def oat_lsns(self) -> List[int]:
        """Checkpoint table LSNs in the documented order:
        open attribute, attribute names, dirty page, transaction."""
        return list(struct.unpack_from("<4Q", self.buf, 16))


class ClientLogRecord:
    """One NTFS client log record: LOG_REC_HDR + redo/undo data."""

    def __init__(self, buf: bytes, lsn: int, client_id: int, transaction_id: int,
                 cluster_size: int):
        if len(buf) < 0x20:
            raise LogFileError("log record too short: %d" % len(buf))
        self.buf = buf
        self.lsn = lsn
        self.client_id = client_id
        self.transaction_id = transaction_id
        self.cluster_size = cluster_size if cluster_size else 4096

    def field(self, name: str, off: int, fmt: str):
        (val,) = struct.unpack_from(fmt, self.buf, off)
        return val

    @property
    def redo_op(self) -> int:
        return struct.unpack_from("<H", self.buf, 0)[0]

    @property
    def undo_op(self) -> int:
        return struct.unpack_from("<H", self.buf, 2)[0]

    @property
    def redo_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 4)[0]

    @property
    def redo_len(self) -> int:
        return struct.unpack_from("<H", self.buf, 6)[0]

    @property
    def undo_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 8)[0]

    @property
    def undo_len(self) -> int:
        return struct.unpack_from("<H", self.buf, 10)[0]

    @property
    def target_attr(self) -> int:
        return struct.unpack_from("<H", self.buf, 12)[0]

    @property
    def lcns_follow(self) -> int:
        return struct.unpack_from("<H", self.buf, 14)[0]

    @property
    def record_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 16)[0]

    @property
    def attr_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 18)[0]

    @property
    def cluster_block_off(self) -> int:
        return struct.unpack_from("<H", self.buf, 20)[0]

    @property
    def target_block_size(self) -> int:
        return struct.unpack_from("<H", self.buf, 22)[0]

    @property
    def target_vcn(self) -> int:
        return struct.unpack_from("<q", self.buf, 24)[0]

    def lcns(self) -> List[int]:
        out = []
        for i in range(self.lcns_follow):
            (lcn,) = struct.unpack_from("<q", self.buf, 32 + i * 8)
            out.append(lcn)
        return out

    def redo_data(self) -> bytes:
        if self.redo_off == 0 or self.redo_len == 0:
            return b""
        if self.redo_off + self.redo_len > len(self.buf):
            raise LogFileError("redo data overruns record")
        return self.buf[self.redo_off:self.redo_off + self.redo_len]

    def undo_data(self) -> bytes:
        if self.undo_off == 0 or self.undo_len == 0:
            return b""
        if self.undo_off + self.undo_len > len(self.buf):
            raise LogFileError("undo data overruns record")
        return self.buf[self.undo_off:self.undo_off + self.undo_len]

    def is_index_op(self) -> bool:
        return self.redo_op in INDEX_OPS or self.undo_op in INDEX_OPS

    def resident_target_frs(self) -> Optional[int]:
        """For resident ops: which FRS (MFT record) does this patch?"""
        if self.redo_op not in RESIDENT_OPS and self.undo_op not in RESIDENT_OPS:
            return None
        block = self.target_block_size * 512 if self.target_block_size else 1024
        vcn = self.target_vcn
        if vcn < 0:
            return None
        return (vcn * self.cluster_size + self.cluster_block_off * 512) // block

    def nonresident_offset(self) -> Optional[int]:
        """Byte offset within the nonresident stream that this record patches."""
        if self.redo_op not in NONRESIDENT_OPS and self.undo_op not in NONRESIDENT_OPS:
            return None
        return (self.target_vcn * self.cluster_size
                + self.cluster_block_off * 512
                + self.attr_off + self.record_off)

    def decode_entry(self) -> Optional[dict]:
        """For index-add ops, decode the index entry carried in the redo data."""
        if self.redo_op not in (OP_ADD_INDEX_ENTRY_ROOT, OP_ADD_INDEX_ENTRY_ALLOCATION):
            return None
        try:
            data = self.redo_data()
        except LogFileError:
            return None
        if len(data) < 0x10:
            return None
        return parse_index_entry(data, 0)


class OpenAttrTable:
    """Open-attribute table accumulated from OpenNonresidentAttribute records."""

    def __init__(self, oat_version: int = 1):
        self.entries: Dict[int, Tuple[int, str]] = {}
        self.version = oat_version

    def apply_open(self, rec: ClientLogRecord):
        try:
            name_raw = rec.undo_data()
            entry_raw = rec.redo_data()
        except LogFileError:
            return
        try:
            attr_name = decode_utf16(name_raw).rstrip("\x00")
            if self.version >= 1:
                # OPEN_ATTRIBUTE_ENTRY v1: allocated(4) bytes_per_index(4)
                # attr_type(4) [gap 4] file_ref(8) lsn(8)
                file_ref = struct.unpack_from("<Q", entry_raw, 16)[0]
            else:
                # v0: allocated(4) index(4) file_ref(8) lsn(8)
                file_ref = struct.unpack_from("<Q", entry_raw, 8)[0]
        except (struct.error, IndexError):
            return
        self.entries[rec.target_attr] = (file_ref, attr_name)

    def lookup(self, index: int) -> Optional[Tuple[int, str]]:
        return self.entries.get(index)


class LogFileParser:
    """Read a $LogFile image and walk the client record chains."""

    def __init__(self, data: bytes):
        self.data = data
        self.page_size = PAGE_SIZE
        self.record_header_len = 48
        self.page_data_off = 40
        self.cluster_size = 4096
        self.oat_version = 1
        self.seq_bits = 0
        self._parse_restart()

    def _parse_restart(self):
        if len(self.data) < 2 * PAGE_SIZE:
            raise LogFileError("file too small to hold restart pages")
        page1 = self._restart_page(self.data[0:PAGE_SIZE])
        page2 = self._restart_page(self.data[PAGE_SIZE:2 * PAGE_SIZE])
        page = page1 if page1 is not None else page2
        if page is None:
            raise LogFileError("no valid restart page")
        self.major, self.minor, ra_off, ra_raw = page
        if self.major == 1 and self.minor == 1:
            self.first_page = 4  # 2 restart pages + 2 tail copies
        elif self.major >= 2:
            self.first_page = 34  # 2 restart pages + 32 fast pages
        else:
            raise LogFileError("unsupported log version %d.%d" % (self.major, self.minor))
        # RESTART_AREA fields (Linux fslog.c struct RESTART_AREA).
        self.current_lsn = struct.unpack_from("<Q", ra_raw, 0)[0]
        _clients = struct.unpack_from("<H", ra_raw, 8)[0]
        self.seq_bits = struct.unpack_from("<I", ra_raw, 16)[0]
        self.record_header_len = struct.unpack_from("<H", ra_raw, 36)[0]
        self.page_data_off = struct.unpack_from("<H", ra_raw, 38)[0]
        if len(ra_raw) >= 84:
            bpc = struct.unpack_from("<I", ra_raw, 80)[0]
            if bpc and bpc % 512 == 0:
                self.cluster_size = bpc
        if self.record_header_len < 48 or self.record_header_len % 8:
            self.record_header_len = 48

    def _restart_page(self, raw: bytes):
        try:
            buf = bytearray(raw)
            buf, _usa_end = unprotect_sectors(buf)
            magic = bytes(buf[:4])
            if magic != RSTR:
                return None
            minor = struct.unpack_from("<H", buf, 26)[0]
            major = struct.unpack_from("<H", buf, 28)[0]
            ra_off = struct.unpack_from("<H", buf, 24)[0]
            ra_raw = bytes(buf[ra_off:])
            return (major, minor, ra_off, ra_raw)
        except (LogFileError, struct.error):
            return None

    def lsn_to_offset(self, lsn: int) -> Tuple[int, int]:
        seq_bits = self.seq_bits
        if not (1 <= seq_bits <= 63):
            seq_bits = 16
        # vbo = (lsn mod 2^file_data_bits) * 8, where file_data_bits = 64 - seq_bits
        # (fslog.c lsn_to_vbo; dfir_ntfs lsn_to_offset).
        fdb = 64 - seq_bits
        vbo = (lsn & ((1 << fdb) - 1)) * 8
        return vbo // self.page_size, vbo % self.page_size

    def page_by_number(self, number: int) -> Optional[LogPage]:
        start = number * self.page_size
        if start + self.page_size > len(self.data):
            return None
        try:
            return LogPage(self.data[start:start + self.page_size], number,
                           self.page_data_off)
        except LogFileError:
            return None

    def read_client_data(self, lsn: int) -> Optional[Tuple[bytes, int, int, int]]:
        """Read one LFS record: (client_data, record_type, client_id, transaction_id)."""
        page_no, off = self.lsn_to_offset(lsn)
        if page_no < self.first_page:
            return None
        page = self.page_by_number(page_no)
        if page is None or page.magic != RCRD:
            return None
        try:
            this_lsn, raw = page.header_at(off, self.record_header_len)
        except LogFileError:
            return None
        if this_lsn != lsn:
            return None
        # LFS_RECORD_HDR: this_lsn(8) prev(8) undo_next(8) data_len(4)
        # client(4) record_type(4) transaction(4) flags(2) pad(6) = 0x30.
        data_len = struct.unpack_from("<I", raw, 24)[0]
        client_seq, client_idx = struct.unpack_from("<HH", raw, 28)
        record_type = struct.unpack_from("<I", raw, 32)[0]
        transaction_id = struct.unpack_from("<I", raw, 36)[0]
        if record_type not in (LFS_CLIENT_RECORD, LFS_CLIENT_RESTART):
            return None
        if off + self.record_header_len > len(page.buf):
            return None
        if data_len == 0 or data_len > 0x100000:
            return None
        try:
            head = page.client_data_at(off, self.record_header_len, min(
                data_len, self.page_size - off - self.record_header_len))
        except LogFileError:
            head = b""
        got = len(head)
        if got < data_len:
            # Record spills into following pages.
            data = bytearray(head)
            npage = page_no
            remaining = data_len - got
            while remaining > 0:
                npage += 1
                if npage * self.page_size >= len(self.data):
                    npage = self.first_page
                np_ = self.page_by_number(npage)
                if np_ is None:
                    return None
                chunk_off = self.page_data_off
                chunk = np_.buf[chunk_off:chunk_off + remaining]
                if not chunk:
                    return None
                data.extend(chunk)
                remaining -= len(chunk)
            client_data = bytes(data)
        else:
            client_data = head[:data_len]
        return client_data, record_type, (client_seq << 16) | client_idx, transaction_id

    def walk_chains(self) -> List[dict]:
        """Walk every client chain reachable from the restart LSNs and dump records.

        Returns a list of dicts, one per client log record, in walk order.
        """
        restart = self._client_restart_lsn()
        oat = OpenAttrTable(self.oat_version)
        records: List[dict] = []
        seen = set()
        lsn = restart
        hops = 0
        limit = 4000000
        while lsn and hops < limit:
            hops += 1
            if lsn in seen:
                break
            seen.add(lsn)
            got = self.read_client_data(lsn)
            if got is None:
                break
            client_data, record_type, client_id, transaction_id = got
            if record_type == LFS_CLIENT_RESTART:
                try:
                    cr = ClientRestart(client_data)
                    self.oat_version = cr.major
                except LogFileError:
                    pass
                lsn = self._next_in_chain(lsn, page_hint=None)
                continue
            try:
                rec = ClientLogRecord(client_data, lsn, client_id, transaction_id,
                                      self.cluster_size)
            except LogFileError:
                break
            if rec.redo_op == OP_OPEN_NONRESIDENT_ATTRIBUTE:
                oat.apply_open(rec)
            records.append(self._record_to_dict(rec, oat))
            nxt = self._next_in_chain(lsn, page_hint=None)
            if nxt is None:
                break
            lsn = nxt
        return records

    def _client_restart_lsn(self) -> int:
        """Find the client restart LSN by scanning the restart areas' client records.

        The CLIENT_REC array sits behind the restart area; each active client
        carries oldest_lsn / restart_lsn. We pick the newest restart_lsn we can
        validate (its record must exist and self-match).
        """
        candidates = set()
        for base in (0, PAGE_SIZE):
            try:
                buf = bytearray(self.data[base:base + PAGE_SIZE])
                buf, _ = unprotect_sectors(buf)
                if bytes(buf[:4]) != RSTR:
                    continue
                ra_off = struct.unpack_from("<H", buf, 24)[0]
                # RESTART_AREA: current_lsn(0) log_clients(8) free_idx(10)
                # inuse_idx(12) flags(14) seq_bits(16) ra_len(20)
                # client_array_off(22).
                _cur = struct.unpack_from("<Q", buf, ra_off)[0]
                in_use = struct.unpack_from("<H", buf, ra_off + 12)[0]
                client_array_off = struct.unpack_from("<H", buf, ra_off + 22)[0]
                client_seq = struct.unpack_from("<H", buf, ra_off + 8)[0]
                # CLIENT_REC: oldest(0x00) restart(0x08) prev(0x10) next(0x12)
                # seq(0x14) pad(6) name_bytes(0x1C) name(128) = 0xa0.
                rec_base = ra_off + client_array_off
                idx = in_use
                while idx != 0xFFFF and rec_base + 0xA0 <= len(buf):
                    off = rec_base + idx * 0xA0
                    oldest, restart_lsn = struct.unpack_from("<QQ", buf, off)
                    cseq = struct.unpack_from("<H", buf, off + 20)[0]
                    if restart_lsn:
                        candidates.add((restart_lsn, cseq, client_seq))
                    if oldest:
                        candidates.add((oldest, cseq, client_seq))
                    nxt = struct.unpack_from("<H", buf, off + 18)[0]
                    if nxt == idx or nxt == 0xFFFF:
                        break
                    idx = nxt
            except (struct.error, LogFileError):
                continue
        # Prefer the newest valid restart LSN (walk from each, keep the longest).
        best = None
        for lsn, _cseq, _pseq in sorted(candidates, reverse=True):
            if self.read_client_data(lsn) is not None:
                best = lsn
                break
        if best is None and candidates:
            best = max(c[0] for c in candidates)
        return best or 0

    def _next_in_chain(self, lsn: int, page_hint) -> Optional[int]:
        """Compute the next record LSN: this + aligned(header + data)."""
        got = self.read_client_data(lsn)
        if got is None:
            return None
        page_no, off = self.lsn_to_offset(lsn)
        page = self.page_by_number(page_no)
        if page is None:
            return None
        data_len = got[0] and struct.unpack_from("<I", got[0][:64] + got[0], 0)  # unused
        _client_data, record_type, _cid, _tid = got
        try:
            _this_lsn, raw = page.header_at(off, self.record_header_len)
        except LogFileError:
            return None
        data_len = struct.unpack_from("<I", raw, 24)[0]
        total = self.record_header_len + ((data_len + 7) & ~7)
        cand_off = off + total
        cand_page = page_no
        if cand_off + self.record_header_len > self.page_size:
            cand_page += 1
            if cand_page * self.page_size >= len(self.data):
                cand_page = self.first_page
            cand_off = self.page_data_off
        cand = self.page_by_number(cand_page)
        if cand is None or cand.magic != RCRD:
            return None
        try:
            cand_lsn, _raw2 = cand.header_at(cand_off, self.record_header_len)
        except LogFileError:
            return None
        return cand_lsn or None

    def _record_to_dict(self, rec: ClientLogRecord, oat: OpenAttrTable) -> dict:
        d: dict = {
            "lsn": rec.lsn,
            "client_id": rec.client_id,
            "transaction_id": rec.transaction_id,
            "redo": OP_NAMES.get(rec.redo_op, "0x%04X" % rec.redo_op),
            "undo": OP_NAMES.get(rec.undo_op, "0x%04X" % rec.undo_op),
            "redo_op": rec.redo_op,
            "undo_op": rec.undo_op,
            "redo_len": rec.redo_len,
            "undo_len": rec.undo_len,
            "target_attr_idx": rec.target_attr,
            "target_vcn": rec.target_vcn,
            "record_off": rec.record_off,
            "attr_off": rec.attr_off,
            "cluster_block_off": rec.cluster_block_off,
            "target_block_size": rec.target_block_size,
            "lcns": rec.lcns(),
        }
        frs = rec.resident_target_frs()
        if frs is not None:
            d["target_frs"] = frs
            d["target_kind"] = "resident"
        off = rec.nonresident_offset()
        if off is not None:
            d["stream_offset"] = off
            d["target_kind"] = "nonresident"
            oat_hit = oat.lookup(rec.target_attr)
            if oat_hit:
                fr, name = oat_hit
                d["oat_ref"] = fr
                d["oat_record"] = fr & 0xFFFFFFFFFFFF
                d["oat_attr_name"] = name
        if rec.is_index_op():
            d["index_op"] = True
            entry = rec.decode_entry()
            if entry is not None:
                d["index_entry"] = entry
            # For allocation ops the patched INDX buffer is NOT in redo data
            # (redo patch is a splice into the buffer); decode undo for delete
            # ops when it carries the removed entry.
            if rec.redo_op in (OP_DELETE_INDEX_ENTRY_ALLOCATION,
                               OP_DELETE_INDEX_ENTRY_ROOT):
                try:
                    undo = rec.undo_data()
                except LogFileError:
                    undo = b""
                if len(undo) >= 0x10:
                    ent = parse_index_entry(undo, 0)
                    if ent is not None:
                        d["undo_index_entry"] = ent
        return d


def summarize(records: List[dict]) -> dict:
    """Aggregate the E33 evidence: count ops by class and collect index records."""
    idx = [r for r in records if r.get("index_op")]
    by_op: Dict[str, int] = {}
    for r in records:
        by_op[r["redo"]] = by_op.get(r["redo"], 0) + 1
    idx_by_op: Dict[str, int] = {}
    for r in idx:
        idx_by_op[r["redo"]] = idx_by_op.get(r["redo"], 0) + 1
    names: Dict[str, List[str]] = {}
    for r in idx:
        for key in ("index_entry", "undo_index_entry"):
            ent = r.get(key)
            if ent and ent.get("file_name"):
                names.setdefault(ent["file_name"], [])
                if r["redo"] not in names[ent["file_name"]]:
                    names[ent["file_name"]].append(r["redo"])
    return {
        "records_total": len(records),
        "records_index": len(idx),
        "ops_total": by_op,
        "ops_index": idx_by_op,
        "index_names": names,
        "index_records": idx,
    }


def selftest() -> int:
    """Synthetic-log round trip: build a small log with one index record."""
    page_size = PAGE_SIZE
    log = bytearray(PAGE_SIZE * 6)
    # Restart page 1.
    rp = bytearray(page_size)
    rp[0:4] = RSTR
    struct.pack_into("<H", rp, 4, 0x30)   # fixup offset
    struct.pack_into("<H", rp, 6, 9)      # fixup count (4096/512 + 1)
    ra_base = 0x48                       # past the USA array (0x30 + 9*2)
    struct.pack_into("<Q", rp, 8, 0)      # chkdsk lsn
    struct.pack_into("<I", rp, 16, page_size)  # system page size
    struct.pack_into("<I", rp, 20, page_size)  # log page size
    struct.pack_into("<H", rp, 24, ra_base)  # restart area offset
    struct.pack_into("<H", rp, 26, 1)     # minor 1.1
    struct.pack_into("<H", rp, 28, 1)     # major 1.1
    ra = rp[ra_base:ra_base + 0x40]
    # RESTART_AREA: current_lsn(8) clients(2) free(2) inuse(2) flags(2)
    # seq_bits(4) ra_len(2) client_arr_off(2) file_size(8) last_len(4)
    # rec_hdr_len(2) data_off(2)
    client_arr_off = 0x28
    struct.pack_into("<Q", ra, 0, 0x800)           # current lsn
    struct.pack_into("<H", ra, 8, 1)               # log clients
    struct.pack_into("<H", ra, 10, 0xFFFF)         # free list
    struct.pack_into("<H", ra, 12, 0)              # in-use index
    struct.pack_into("<H", ra, 14, 1)              # flags: single page io
    struct.pack_into("<I", ra, 16, 0x10)           # seq number bits (page>>3)
    struct.pack_into("<H", ra, 20, 0x30)           # restart area length
    struct.pack_into("<H", ra, 22, client_arr_off)
    struct.pack_into("<H", ra, 36, 48)             # record header length
    struct.pack_into("<H", ra, 38, 64)             # log page data offset
    rp[ra_base:ra_base + 0x40] = ra
    # CLIENT_REC for client 0 with restart LSN 0x800.
    cr = rp[ra_base + client_arr_off:ra_base + client_arr_off + 0xA0]
    struct.pack_into("<Q", cr, 0, 0x808)           # oldest lsn
    struct.pack_into("<Q", cr, 8, 0x808)           # restart lsn
    struct.pack_into("<H", cr, 16, 0xFFFF)         # prev client
    struct.pack_into("<H", cr, 18, 0xFFFF)         # next client
    struct.pack_into("<H", cr, 20, 1)              # seq
    struct.pack_into("<I", cr, 28, 8)              # name bytes
    rp[ra_base + client_arr_off:ra_base + client_arr_off + 0xA0] = cr

    # One RCRD page at page 2 with a single client record at data offset 40.
    pg = bytearray(page_size)
    pg[0:4] = RCRD
    struct.pack_into("<H", pg, 4, 0x28)
    struct.pack_into("<H", pg, 6, 9)
    struct.pack_into("<Q", pg, 8, 0x800)      # last lsn
    struct.pack_into("<I", pg, 16, 1)         # flags: page has record end
    struct.pack_into("<H", pg, 24, 40 + 48 + 0x40)  # next record offset
    struct.pack_into("<Q", pg, 32, 0x800)     # last end lsn

    # LFS record header at offset 40.
    lsn = 0x808
    redo_op = OP_ADD_INDEX_ENTRY_ROOT
    # Redo payload: NTFS_DE with a $FILE_NAME key.
    fname = struct.pack("<Q", 0x50000000005)  # parent ref (record 5)
    fname += b"\x00" * 0x38                    # dup info
    nm = "e33_demo.bin"
    fname += bytes([len(nm), 0x01])            # name_len words, type 1
    fname += nm.encode("utf-16-le")
    # NTFS_DE: ref(8) size(2) key_size(2) flags(2) res(2), then the
    # $FILE_NAME key (0x42-byte header + name).
    de = struct.pack("<Q", 0x60000000006)      # file ref (record 6)
    de += struct.pack("<HHHH", 0x10 + len(fname), len(fname), 0, 0)
    de += fname
    # LOG_REC_HDR: redo_op(2) undo_op(2) redo_off(2) redo_len(2) undo_off(2)
    # undo_len(2) target_attr(2) lcns(2) record_off(2) attr_off(2)
    # cluster_off(2) reserved(2) target_vcn(8)
    lrh = struct.pack(
        "<HHHHHHHHHHHHq",
        redo_op, OP_DELETE_INDEX_ENTRY_ROOT, 0x20, len(de), 0x20, len(de),
        3, 0, 0x10, 0x18, 0, 0, 0)
    client = lrh + de
    # Pad client to 8-byte alignment (adds at most 7 bytes).
    if len(client) % 8:
        client += b"\x00" * (8 - len(client) % 8)
    off = 64
    struct.pack_into("<Q", pg, off, lsn)
    struct.pack_into("<Q", pg, off + 8, 0)         # prev lsn
    struct.pack_into("<Q", pg, off + 16, 0)        # undo next lsn
    struct.pack_into("<I", pg, off + 24, len(client))  # client data length
    struct.pack_into("<HH", pg, off + 28, 1, 0)    # client id seq/index
    struct.pack_into("<I", pg, off + 32, LFS_CLIENT_RECORD)
    struct.pack_into("<I", pg, off + 36, 7)        # transaction id
    pg[off + 48:off + 48 + len(client)] = client

    # USA fixups: each 512-byte sector's last 2 bytes replaced; store originals.
    usa_off = struct.unpack_from("<H", pg, 4)[0]
    usa_cnt = struct.unpack_from("<H", pg, 6)[0]
    seq_word = b"\x33\x33"
    struct.pack_into("<H", pg, usa_off + 4, 0x3333)
    for i in range(1, usa_cnt):
        at = i * 512 - 2
        struct.pack_into("<H", pg, usa_off + i * 2,
                         struct.unpack_from("<H", pg, at)[0])
        pg[at:at + 2] = seq_word
    pg[usa_off:usa_off + 2] = seq_word

    log[0:page_size] = rp
    log[4 * page_size:5 * page_size] = pg

    parser = LogFileParser(bytes(log))
    parser.oat_version = 1
    records = parser.walk_chains()
    ok = False
    for r in records:
        if r["redo"] == "AddIndexEntryRoot":
            ent = r.get("index_entry") or {}
            if ent.get("file_name") == "e33_demo.bin":
                ok = True
    summary = summarize(records)
    print("selftest records=%d index=%d names=%s ok=%s" % (
        summary["records_total"], summary["records_index"],
        summary["index_names"], ok))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 $LogFile decoder")
    ap.add_argument("logfile", nargs="?", help="path to a raw $LogFile image")
    ap.add_argument("--mft", help="path to a raw $MFT image (context, reserved)")
    ap.add_argument("--cluster-size", type=int, default=None,
                    help="override cluster size (default: from restart area)")
    ap.add_argument("--json", action="store_true",
                    help="emit decoded records as JSON lines")
    ap.add_argument("--summary", action="store_true",
                    help="emit the aggregated summary JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic round trip and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.logfile:
        ap.error("a log file is required unless --selftest")

    with open(args.logfile, "rb") as fh:
        data = fh.read()
    parser = LogFileParser(data)
    if args.cluster_size:
        parser.cluster_size = args.cluster_size
    records = parser.walk_chains()
    summary = summarize(records)
    if args.json:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
    if args.summary:
        slim = {k: v for k, v in summary.items() if k != "index_records"}
        print(json.dumps(slim, ensure_ascii=False, indent=1))
    if not args.json and not args.summary:
        print("records=%d index=%d" % (summary["records_total"],
                                       summary["records_index"]))
        for r in records:
            line = "LSN 0x%016X %s -> %s" % (r["lsn"], r["redo"], r["undo"])
            if r.get("target_frs") is not None:
                line += "  frs=%d" % r["target_frs"]
            if r.get("oat_attr_name"):
                line += "  oat=%s#%d" % (r["oat_attr_name"], r.get("oat_record", -1))
            ent = r.get("index_entry") or {}
            if ent.get("file_name"):
                line += "  name=%r" % ent["file_name"]
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
