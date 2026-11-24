#!/usr/bin/env python3
"""E33 - raw-volume $MFT / $LogFile extractor (Windows, administrator).

`D:\\$MFT` and `D:\\$LogFile` cannot be opened by name (STATUS_ACCESS_DENIED
even for administrators while the volume is mounted), so the E33 rig reads
the volume device instead:

  1. open \\\\.\\X: with GENERIC_READ (requires administrator);
  2. read the NTFS boot sector -> cluster size, MFT LCN, record size;
  3. read MFT record 0 -> $MFT data runs -> MFT record reader;
  4. read the requested MFT records (fixture dirs, created files) and write
     them packed with a number map;
  5. read MFT record 2 -> $LogFile data runs -> dump the $LogFile image.

The extraction is consistency-limited (the volume is live); E33 only uses it
to establish which records exist and their page placement — timing evidence
comes from ETW.

Usage:
    python e33_extract.py --volume D: --tag arm-A --out-dir DIR \
        --records 5,1234,5678
"""
import argparse
import ctypes
import json
import os
import struct
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e33_mft  # noqa: E402

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p]
kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                              ctypes.c_void_p]
kernel32.SetFilePointerEx.argtypes = [ctypes.c_void_p, ctypes.c_longlong,
                                      ctypes.POINTER(ctypes.c_longlong),
                                      wintypes.DWORD]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class RawVolume:
    def __init__(self, letter: str):
        path = "\\\\.\\" + letter  # letter already carries the ':' (e.g. 'S:')
        self.handle = kernel32.CreateFileW(path, GENERIC_READ,
                                           FILE_SHARE_READ | FILE_SHARE_WRITE,
                                           None, OPEN_EXISTING, 0, None)
        if self.handle in (None, INVALID_HANDLE_VALUE):
            raise OSError("CreateFileW(%s) failed: %s"
                          % (path, ctypes.get_last_error()))
        self.sector = 512

    def close(self):
        if self.handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def read_at(self, offset: int, length: int) -> bytes:
        # Sector-align the request; serve the requested slice.
        start = offset - (offset % self.sector)
        end = offset + length
        end += (-end) % self.sector
        raw_len = end - start
        ptr = (ctypes.c_longlong * 1)(0)
        ok = kernel32.SetFilePointerEx(self.handle, ctypes.c_longlong(start),
                                       ptr, 0)
        if not ok:
            raise OSError("SetFilePointerEx(%d) failed: %s"
                          % (start, ctypes.get_last_error()))
        buf = ctypes.create_string_buffer(raw_len)
        got = wintypes.DWORD(0)
        if not kernel32.ReadFile(self.handle, buf, raw_len,
                                 ctypes.byref(got), None):
            raise OSError("ReadFile(%d,%d) failed: %s"
                          % (start, raw_len, ctypes.get_last_error()))
        data = buf.raw[:got.value]
        skip = offset - start
        return data[skip:skip + length]


class VolumeNtfs:
    """Boot-sector + MFT access on a raw volume handle."""

    def __init__(self, vol: RawVolume):
        self.vol = vol
        boot = vol.read_at(0, 512)
        if boot[3:11] != b"NTFS    ":
            raise ValueError("not an NTFS volume (OEM=%r)" % boot[3:11])
        self.bytes_per_sector = struct.unpack_from("<H", boot, 0x0B)[0]
        self.sectors_per_cluster = boot[0x0D]
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster
        self.mft_lcn = struct.unpack_from("<q", boot, 0x30)[0]
        self.rec_enc = boot[0x40] if len(boot) > 0x40 else 0xF6
        if self.rec_enc & 0x80:
            self.record_size = 1 << (256 - self.rec_enc)
        else:
            self.record_size = self.cluster_size << self.rec_enc
        if self.record_size < 256 or self.record_size > 65536:
            self.record_size = 1024
        self.boot = {
            "bytes_per_sector": self.bytes_per_sector,
            "sectors_per_cluster": self.sectors_per_cluster,
            "cluster_size": self.cluster_size,
            "mft_lcn": self.mft_lcn,
            "record_size": self.record_size,
            "total_sectors": struct.unpack_from("<q", boot, 0x28)[0],
        }
        self._mft_runs = None
        self._mft_size = None

    # -- $MFT ------------------------------------------------------------
    def mft_runs(self):
        if self._mft_runs is not None:
            return self._mft_runs
        rec0 = self.vol.read_at(self.mft_lcn * self.cluster_size,
                                self.record_size)
        m = e33_mft.MftImage(rec0 + b"\x00" * (self.record_size * 0),
                             self.cluster_size)
        fixed = e33_mft.apply_fixups(bytearray(rec0), self.record_size)
        attrs = m.attributes(fixed)
        runs = []
        size = 0
        for a in attrs:
            if a["type_code"] == e33_mft.ATTR_DATA and a["nonresident"]:
                runs = a.get("runs", [])
                size = a.get("alloc_size", 0)
                break
        if not runs:
            raise ValueError("$MFT data runs not found in record 0")
        self._mft_runs = runs
        self._mft_size = size
        return runs

    def mft_clusters(self):
        self.mft_runs()
        return self._mft_size // self.cluster_size

    def mft_record_offset(self, number: int) -> int:
        """Volume offset of an MFT record."""
        runs = self.mft_runs()
        per_cluster = self.cluster_size // self.record_size
        vcn = number // per_cluster
        idx = 0
        for i, (start_vcn, _lcn) in enumerate(runs):
            nxt = runs[i + 1][0] if i + 1 < len(runs) else None
            end_vcn = nxt if nxt is not None else vcn + 1
            if start_vcn <= vcn < end_vcn:
                idx = i
                break
        else:
            raise IndexError("MFT record %d beyond the run list" % number)
        start_vcn, lcn = runs[idx]
        return ((lcn + (vcn - start_vcn)) * self.cluster_size
                + (number % per_cluster) * self.record_size)

    def read_record(self, number: int) -> bytes:
        return self.vol.read_at(self.mft_record_offset(number),
                                self.record_size)

    # -- $LogFile --------------------------------------------------------
    def logfile_layout(self):
        rec2 = self.read_record(2)
        m = e33_mft.MftImage(rec2, self.cluster_size)
        fixed = e33_mft.apply_fixups(bytearray(rec2), self.record_size)
        for a in m.attributes(fixed):
            if a["type_code"] == e33_mft.ATTR_DATA and a["nonresident"]:
                return a.get("runs", []), a.get("data_size", 0)
        raise ValueError("$LogFile $DATA runs not found in record 2")

    def read_stream(self, runs, size: int, cap: int) -> bytes:
        """Read a nonresident stream via its run list."""
        out = bytearray()
        remaining = min(size, cap)
        for i, (start_vcn, lcn) in enumerate(runs):
            nxt = runs[i + 1][0] if i + 1 < len(runs) else None
            clusters = (nxt - start_vcn) if nxt is not None else \
                (remaining + self.cluster_size - 1) // self.cluster_size
            take = min(clusters * self.cluster_size, remaining)
            if take <= 0:
                break
            out.extend(self.vol.read_at(lcn * self.cluster_size, take))
            remaining -= take
            if remaining <= 0:
                break
        return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 raw-volume extractor")
    ap.add_argument("--volume", default="D:", help="volume letter, e.g. D:")
    ap.add_argument("--tag", default="arm", help="artifact tag (arm-A)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--records", default="",
                    help="comma list of MFT record numbers to pack")
    ap.add_argument("--logfile-cap", type=int, default=128 << 20,
                    help="max $LogFile bytes to read (default 128 MiB)")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    vol = RawVolume(args.volume)
    summary = {"tag": args.tag, "volume": args.volume}
    try:
        ntfs = VolumeNtfs(vol)
        summary["boot"] = ntfs.boot
        summary["mft_runs"] = len(ntfs.mft_runs())
        summary["mft_clusters"] = ntfs.mft_clusters()

        numbers = []
        for chunk in args.records.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                n = int(chunk)
                if n >= 0 and n not in numbers:
                    numbers.append(n)
        # Always keep the system records the decoder needs ($MFT, $LogFile).
        for forced in (0, 2):
            if forced not in numbers:
                numbers.insert(0, forced)

        packed = bytearray()
        ok_numbers = []
        volume_offsets = {}
        for n in numbers:
            try:
                rec = ntfs.read_record(n)
                volume_offsets[str(n)] = ntfs.mft_record_offset(n)
            except (OSError, IndexError) as exc:
                summary.setdefault("record_errors", []).append(
                    {"record": n, "error": str(exc)})
                continue
            packed.extend(rec)
            ok_numbers.append(n)
        with open(os.path.join(args.out_dir, args.tag + "-MFT-packed.bin"),
                  "wb") as fh:
            fh.write(bytes(packed))
        with open(os.path.join(args.out_dir, args.tag + "-MFT-packed.json"),
                  "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"record_size": ntfs.record_size,
                       "cluster_size": ntfs.cluster_size,
                       "numbers": ok_numbers,
                       "record_volume_offsets": volume_offsets,
                       "boot": ntfs.boot,
                       "requested": numbers}, fh, indent=1)
        summary["records_packed"] = len(ok_numbers)

        runs, size = ntfs.logfile_layout()
        summary["logfile_runs"] = len(runs)
        summary["logfile_run_list"] = [[v, l] for v, l in runs]
        summary["logfile_data_size"] = size
        summary["cluster_size"] = ntfs.cluster_size
        log_bytes = ntfs.read_stream(runs, size, args.logfile_cap)
        summary["logfile_bytes_read"] = len(log_bytes)
        with open(os.path.join(args.out_dir, args.tag + "-LogFile.bin"),
                  "wb") as fh:
            fh.write(log_bytes)
    finally:
        vol.close()

    with open(os.path.join(args.out_dir, args.tag + "-extract.json"),
              "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
