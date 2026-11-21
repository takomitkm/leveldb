/* E17 â power-cut probe: reproduce each database's Windows dir-flush shape
 *        exactly as its patch implements it, on the E14 rig (one arm per
 *        boot, SMBIOS arm id, blklogwrites log, offline cut, ntfs-3g autopsy).
 *
 * Parent rig: e14pe3.cpp / e14v3-*.sh in this directory (E14-RESULTS.md).
 * All arms share the E14 mechanics: markers padded to 2048B (non-resident,
 * content-addressable in the block log), FILE_FLAG_WRITE_THROUGH, no explicit
 * marker flush; arm id via -smbios type=1,serial="E17ARM=<n>;REPS=<r>";
 * fallback arm.txt = 98 (park). One arm per boot; host force-kills QEMU
 * after seeing E17DONE in the log; crash images cut offline.
 *
 * Arms (each = the literal API sequence one DB's patch performs):
 *   0  BASELINE_NODIRSYNC  create+write+FlushFileBuffers(file) â upstream
 *                          leveldb/SQLite/RocksDB-before-fix behavior (control)
 *   1  DIRFLUSH_CREATE     arm0 + GENERIC_WRITE dir handle flush (leveldb
 *                          30b34e0 / SQLite winSyncDirectory / MySQL
 *                          os_parent_dir_fsync_win / FrankSQLite best-effort:
 *                          share R|W|D, no path resolution)
 *   2  SQLITE_WIN32DIRSYNC same arm1 shape but reached through a RELATIVE path
 *                          (SQLite S7 shape: cwd-relative, dir resolved via
 *                          "." when path has no separator)
 *   3  LMDB_MDIRSYNC       LMDB mdb_mdirsync shape: open dir, flush, then
 *                          GetFinalPathNameByHandleW sanity read (documents
 *                          what handle actually got flushed)
 *   4  PG_TRANSIENT_FD     PG pgwin32_open_dir_for_sync shape: CreateFile via
 *                          O_RDWR-equivalent then _open_osfhandle, flush via
 *                          the fd (pg_fsync path), close via fd
 *   5  MYSQL_DELETE_DANCE  MySQL delete path on a DURABLE pre-state file:
 *                          MoveFileW name->name.XXXX.d + DeleteFileW
 *                          (os_file_delete_if_exists_func) with
 *                          os_parent_dir_fsync_win after
 *   6  RENAME_WRITE_THROUGH rename + MOVEFILE_WRITE_THROUGH only (MySQL
 *                          os_file_rename_func / E14 arm4 recheck)
 *   7  CREATE_MKDIR_FLUSH  MySQL create_directory shape: CreateDirectory then
 *                          flush the PARENT (POSIX arm L2951 parity)
 *   8  DELETE_NOFLUSH      control pair for arm 5: durable file, DeleteFileA,
 *                          no flush
 *   9  MKDIR_NOFLUSH       control pair for arm 7: CreateDirectory, no flush
 *  10  RENAME_DIRFLUSH     the E11 protocol on Windows: durable src, bare
 *                          rename, then dir flush (leveldb CURRENT / LMDB /
 *                          MySQL rename parity)
 *  11  CREATE_ROOTFLUSH     create + flushfile + FlushDir(ROOT) only
 *  12  CREATE_DIRPLUSROOT   create + flushfile + FlushDir(dir) + FlushDir(root)
 *  13  CREATE_DIRFLUSH_X2   create + flushfile + FlushDir(dir) twice
 *  14  RENAME_DIRPLUSROOT   durable src, bare rename, then dir+root flush
 *  15  DELETE_DIRPLUSROOT   durable pre-state (double flush), delete, then
 *                          dir+root flush
 *  98 PARK                 config-fallback signature: nothing runs
 *
 * Verdict per arm/rep (after crash-image autopsy):
 *   worst-cut = state guaranteed at op return (everything before the last
 *   barrier); best-cut = everything the device received. Directory-entry
 *   presence is checked by content: every trial file is padded to 2048B with
 *   a unique ASCII body ("e17-a<arm>-r<rep>"), so ntfs-3g autopsy can find
 *   the body bytes on the platter regardless of what name it sits under.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <io.h>
#include <fcntl.h>

static char g_vol = 0;

/* COM1 debug line (QEMU -serial file:...). Best-effort: if the serial device
 * is unavailable (driver not started yet), the open is retried on the next
 * call; a dead COM1 must never alter trial semantics, so every failure is
 * silent. */
static HANDLE g_com = INVALID_HANDLE_VALUE;
static void SerLog(const char* fmt, ...) {
  char line[512];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(line, sizeof line - 2, fmt, ap);
  va_end(ap);
  if (n <= 0) return;
  if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
  line[n] = '\r';
  line[n + 1] = '\n';
  if (g_com == INVALID_HANDLE_VALUE) {
    g_com = CreateFileA("\\\\.\\COM1", GENERIC_WRITE, 0, nullptr,
                        OPEN_EXISTING, 0, nullptr);
    if (g_com == INVALID_HANDLE_VALUE) return;
  }
  DWORD w = 0;
  WriteFile(g_com, line, (DWORD)(n + 2), &w, nullptr);
}

static void Park(const char* why) {
  SerLog("E29-PARK why=%s", why);
  for (;;) Sleep(60000);
}

static BOOL FindVolume(void) {
  char root[4] = {'C', ':', '\\', 0};
  for (char c = 'C'; c <= 'Z'; ++c) {
    if (c == 'X') continue;
    root[0] = c;
    char lbl[64] = "", fs[64] = "";
    if (GetVolumeInformationA(root, lbl, sizeof lbl, nullptr, nullptr, nullptr, fs, sizeof fs)) {
      if (_stricmp(fs, "NTFS") == 0 && _stricmp(lbl, "E13SYS") == 0) { g_vol = c; return TRUE; }
    }
  }
  return FALSE;
}

/* Same RSMB scan as e14pe3: find "E17ARM=" in the raw SMBIOS buffer. */
static BOOL ReadArmFromSmbios(int* arm, int* reps) {
  typedef UINT (WINAPI *pGFT)(DWORD, DWORD, PVOID, UINT);
  pGFT gft = (pGFT)GetProcAddress(GetModuleHandleA("kernel32.dll"),
                                  "GetSystemFirmwareTable");
  if (!gft) return FALSE;
  UINT need = gft('RSMB', 0, nullptr, 0);
  if (!need || need > 1u << 20) return FALSE;
  static BYTE buf[1 << 20];
  if (gft('RSMB', 0, buf, need) != need) return FALSE;
  const char* hit = nullptr;
  for (UINT i = 0; i + 7 <= need; ++i) {
    if (memcmp(buf + i, "E17ARM=", 7) == 0) { hit = (const char*)(buf + i); break; }
    if (memcmp(buf + i, "E30ARM=", 7) == 0) { hit = (const char*)(buf + i); break; }
  }
  if (!hit) return FALSE;
  *arm = atoi(hit + 7);
  const char* r = strstr(hit, "REPS=");
  *reps = r ? atoi(r + 5) : 3;
  if (*reps < 1) *reps = 1;
  if (*reps > 16) *reps = 16;
  return *arm >= 0;
}

static BOOL ReadArmFromFile(int* arm, int* reps) {
  char path[64];
  snprintf(path, sizeof path, "%c:\\e14\\arm.txt", g_vol);
  HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                         OPEN_EXISTING, 0, nullptr);
  if (h == INVALID_HANDLE_VALUE) return FALSE;
  char buf[128] = ""; DWORD rd = 0;
  ReadFile(h, buf, sizeof buf - 1, &rd, nullptr);
  CloseHandle(h);
  *arm = -1; *reps = 3;
  sscanf(buf, "arm=%d reps=%d", arm, reps);
  if (*reps < 1 || *reps > 16) *reps = 3;
  return *arm >= 0;
}

/* WRITE_THROUGH marker, 2048B padded (E14 recipe: non-resident, one data
 * write in the log, content-addressable). */
static void Marker(const char* name, const char* content) {
  char path[64];
  snprintf(path, sizeof path, "%c:\\e14\\%s", g_vol, name);
  static char buf[2048];
  memset(buf, 'P', sizeof buf);
  size_t cl = strlen(content);
  memcpy(buf, content, cl);
  buf[cl] = '\n';
  HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                         FILE_FLAG_WRITE_THROUGH, nullptr);
  if (h == INVALID_HANDLE_VALUE) return;
  DWORD w = 0;
  WriteFile(h, buf, sizeof buf, &w, nullptr);
  CloseHandle(h);
}

/* ---- per-DB flush shapes ---- */

/* The canonical shape every DB patch converged on (leveldb 30b34e0,
 * SQLite winSyncDirectory, MySQL os_parent_dir_fsync_win, FrankenSQLite
 * best-effort, restic #22177): GENERIC_WRITE + share R|W|D +
 * OPEN_EXISTING + FILE_FLAG_BACKUP_SEMANTICS -> FlushFileBuffers -> Close.
 * open_ret/flush_ret report Win32 error codes (0 = success) so an
 * environmental failure is visible in the marker instead of being
 * mistaken for a durability result. */
static BOOL FlushDirShape(const char* dir, DWORD* open_err, DWORD* flush_err) {
  *open_err = 0; *flush_err = 0;
  wchar_t wdir[512];
  MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 512);
  HANDLE d = CreateFileW(wdir, GENERIC_WRITE,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (d == INVALID_HANDLE_VALUE) { *open_err = GetLastError(); return FALSE; }
  BOOL ok = FlushFileBuffers(d);
  if (!ok) *flush_err = GetLastError();
  CloseHandle(d);
  return ok;
}

/* arm 0/1: create+write(+flush). 2048B padded body for content addressing. */
static BOOL WritePadded(const char* path, const char* body, BOOL flush) {
  HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
  if (h == INVALID_HANDLE_VALUE) return FALSE;
  static char buf[2048];
  memset(buf, 'P', sizeof buf);
  size_t cl = strlen(body);
  memcpy(buf, body, cl);
  buf[cl] = '\n';
  DWORD w = 0;
  BOOL ok = WriteFile(h, buf, sizeof buf, &w, nullptr) && w == sizeof buf;
  if (ok && flush && !FlushFileBuffers(h)) ok = FALSE;
  if (!CloseHandle(h)) ok = FALSE;
  return ok;
}

/* arm 4: PG pgwin32_open_dir_for_sync shape â CreateFileW(O_RDWR-shape) then
 * _open_osfhandle, flush via _commit(fd) (the pg_fsync route for fds). */
static BOOL FlushDirPgShape(const char* dir, DWORD* open_err, DWORD* flush_err) {
  *open_err = 0; *flush_err = 0;
  wchar_t wdir[512];
  MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 512);
  HANDLE d = CreateFileW(wdir, GENERIC_WRITE,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (d == INVALID_HANDLE_VALUE) { *open_err = GetLastError(); return FALSE; }
  int fd = _open_osfhandle((intptr_t)d, 0);
  if (fd < 0) { *open_err = (DWORD)GetLastError(); CloseHandle(d); return FALSE; }
  BOOL ok = (_commit(fd) == 0);
  if (!ok) *flush_err = (DWORD)errno;
  if (_close(fd) != 0 && ok) { ok = FALSE; *flush_err = (DWORD)errno; }
  return ok;
}

/* arm 3: LMDB shape â flush, then GetFinalPathNameByHandleW to document
 * which directory the flushed handle resolved to (LMDB does this for its
 * DIRSYNC-PROBE output; here it doubles as a handle-validity check). */
static BOOL FlushDirLmdbShape(const char* dir, DWORD* open_err, DWORD* flush_err) {
  DWORD oe = 0, fe = 0;
  BOOL ok = FlushDirShape(dir, &oe, &fe);
  if (oe || fe) { *open_err = oe; *flush_err = fe; return ok; }
  wchar_t wdir[512];
  MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 512);
  HANDLE d = CreateFileW(wdir, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                         nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (d == INVALID_HANDLE_VALUE) { *open_err = GetLastError(); return FALSE; }
  DWORD fn = GetFinalPathNameByHandleW(d, nullptr, 0, 0);
  if (fn) {
    wchar_t* fb = (wchar_t*)malloc((size_t)fn * sizeof(wchar_t));
    if (fb && GetFinalPathNameByHandleW(d, fb, fn, 0)) {
      Marker("lmdb-resolve.txt", "");  /* presence marker; body not needed */
    }
    free(fb);
  }
  CloseHandle(d);
  return TRUE;
}

/* arm 16/17 pre-state: build d16 with 200 entries (forces the $I30 index
 * out of the directory's MFT record into $INDEX_ALLOCATION pages), then make
 * the whole pre-state durable via the root flush (arm 11 recipe).  Runs
 * BEFORE the reps loop, so none of it pollutes the measured windows. */
static void MakeD16(const char* dir, const char* root) {
  char d16[80], p[96];
  DWORD oe = 0, fe = 0;
  snprintf(d16, sizeof d16, "%s\\d16", dir);
  CreateDirectoryA(d16, nullptr);
  for (int k = 0; k < 200; ++k) {
    snprintf(p, sizeof p, "%s\\p%03d.bin", d16, k);
    HANDLE h = CreateFileA(p, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) continue;
    char buf[40];
    int n = snprintf(buf, sizeof buf, "pad-%03d", k);
    DWORD w = 0;
    WriteFile(h, buf, (DWORD)n, &w, nullptr);
    CloseHandle(h);
  }
  FlushDirShape(d16, &oe, &fe);
  FlushDirShape(dir, &oe, &fe);
  FlushDirShape(root, &oe, &fe);
}

/* ---- native NT flush experiments (arms 20-24) ----
 * Win32 FlushFileBuffers is a thin wrapper over ntdll!NtFlushBuffersFile, so
 * "skipping Win32" for the base call is a no-op by construction (arm 20
 * documents this).  The unknown is ntdll!NtFlushBuffersFileEx (Win8+, not
 * wrapped by any Win32 API): it takes flush flags / an opaque context that
 * may select a volume-wide or write-through flush â plausibly where
 * MS-FSA 2.1.5.7 <80> really lives.  Both are resolved dynamically. */
typedef LONG NTSTATUS;
typedef struct _NT_IO_STATUS_BLOCK {
  union { NTSTATUS Status; PVOID Pointer; } S;
  ULONG_PTR Information;
} NT_IOSB;

typedef NTSTATUS (NTAPI *pNtFlushBuffersFile)(HANDLE, NT_IOSB*);
typedef NTSTATUS (NTAPI *pNtFlushBuffersFileEx)(HANDLE, ULONG, PVOID, ULONG, NT_IOSB*);

static pNtFlushBuffersFile g_NtFlush = nullptr;
static pNtFlushBuffersFileEx g_NtFlushEx = nullptr;

static void LoadNt(void) {
  HMODULE ntd = GetModuleHandleA("ntdll.dll");
  if (!ntd) return;
  g_NtFlush = (pNtFlushBuffersFile)GetProcAddress(ntd, "NtFlushBuffersFile");
  g_NtFlushEx = (pNtFlushBuffersFileEx)GetProcAddress(ntd, "NtFlushBuffersFileEx");
}

/* variant 0: NtFlushBuffersFile (baseline == Win32); 1: Ex FLUSH_AND_PURGE
 * (24H2) on dir; 2: NtFlushBuffersFile on the VOLUME object; 3: Ex
 * FLUSH_AND_PURGE on the volume object.  Flags/params per phnt ntioapi.h:
 *   NtFlushBuffersFileEx(HANDLE, ULONG Flags, PVOID Parameters,
 *                        ULONG ParametersSize, PIO_STATUS_BLOCK)
 *   FLUSH_FLAGS_FILE_NORMAL 0 / FILE_DATA_ONLY 1 / NO_SYNC 2 /
 *   FILE_DATA_SYNC_ONLY 4 (RS1+) / FLUSH_AND_PURGE 8 (24H2) */
static NTSTATUS NtFlushVariant(HANDLE h, int variant) {
  NT_IOSB iosb;
  memset(&iosb, 0, sizeof iosb);
  if (variant == 0 || variant == 2) {
    if (!g_NtFlush) return (NTSTATUS)0xC0000001;
    return g_NtFlush(h, &iosb);
  }
  if (!g_NtFlushEx) return (NTSTATUS)0xC0000002;
  ULONG flags = (variant == 1 || variant == 3) ? 8 : 0;  /* FLUSH_AND_PURGE */
  return g_NtFlushEx(h, flags, NULL, 0, &iosb);
}

/* ================= E30: NT operation-level FILE_WRITE_THROUGH =================
 * Ports run-e27.ps1 arms E/F/G to the journal rig:
 *   E (arm 34): NtCreateFile(FILE_CREATE, FILE_NON_DIRECTORY_FILE|FILE_WRITE_THROUGH)
 *               + write + close          -- create file with FWT handle
 *   F (arm 35): open src with FWT, NtSetInformationFile(FileRenameInformation)
 *               -- rename on FWT source handle
 *   G (arm 36): open target with FWT, SetFileInformationByHandle(FileDispositionInformation)
 *               -- delete on FWT target handle
 *   37: write tmp + FlushFileBuffers + rename via FWT handle (composite control)
 * The measured operation is INSIDE the window; source pre-state (src_$r for F/G)
 * is created BEFORE the marker so the operation itself is what the journal logs.
 */
typedef struct _NT_UNICODE_STRING {
  USHORT Length;
  USHORT MaximumLength;
  PWSTR  Buffer;
} NT_UNICODE_STRING_E30;

typedef struct _NT_OBJECT_ATTRIBUTES {
  ULONG  Length;
  HANDLE RootDirectory;
  NT_UNICODE_STRING_E30* ObjectName;
  ULONG  Attributes;
  PVOID  SecurityDescriptor;
  PVOID  SecurityQualityOfService;
} NT_OBJECT_ATTRIBUTES_E30;

typedef NTSTATUS (NTAPI *pNtCreateFile)(PHANDLE, ACCESS_MASK, NT_OBJECT_ATTRIBUTES_E30*,
    NT_IOSB*, PLARGE_INTEGER, ULONG, ULONG, ULONG, ULONG, PVOID, ULONG);
typedef NTSTATUS (NTAPI *pNtSetInformationFile)(HANDLE, NT_IOSB*, PVOID, ULONG, ULONG);

static pNtCreateFile g_NtCreate = nullptr;
static pNtSetInformationFile g_NtSetInfo = nullptr;

static void LoadNt2(void) {
  HMODULE ntd = GetModuleHandleA("ntdll.dll");
  if (!ntd) return;
  g_NtCreate = (pNtCreateFile)GetProcAddress(ntd, "NtCreateFile");
  g_NtSetInfo = (pNtSetInformationFile)GetProcAddress(ntd, "NtSetInformationFile");
}

#define FILE_WRITE_THROUGH_ 0x00000002
#define FILE_SYNCHRONOUS_IO_NONALERT_ 0x00000020
#define FILE_NON_DIRECTORY_FILE_ 0x00000040
#define FILE_DIRECTORY_FILE_ 0x00000001
#define OBJ_CASE_INSENSITIVE_ 0x00000040
#define FILE_CREATE_ 2
#define FILE_OPEN_ 1
#define FILE_RENAME_INFORMATION_ 10
#define FILE_DISPOSITION_INFORMATION_ 4
#define FILE_ATTRIBUTE_NORMAL_ 0x80
#define FILE_SHARE_ALL_ 0x00000007
#define FILE_DELETE_ 0x00010000
#define FILE_GENERIC_WRITE_ 0x40000000

/* Open the trial dir as an NT root directory handle (relative creates use it). */
static HANDLE NtOpenRoot(const char* dir) {
  wchar_t wdir[512];
  MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 512);
  HANDLE h = CreateFileW(wdir, GENERIC_READ | FILE_LIST_DIRECTORY,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  return h;
}

/* NtCreateFile relative to root, with given access/disposition/options. */
static HANDLE NtCreateRel(HANDLE root, const char* name, ACCESS_MASK access,
                          ULONG disposition, ULONG options) {
  NT_UNICODE_STRING_E30 us;
  wchar_t wname[128];
  MultiByteToWideChar(CP_UTF8, 0, name, -1, wname, 128);
  us.Length = (USHORT)(wcslen(wname) * 2);
  us.MaximumLength = us.Length + 2;
  us.Buffer = wname;
  NT_OBJECT_ATTRIBUTES_E30 oa;
  memset(&oa, 0, sizeof oa);
  oa.Length = sizeof oa;
  oa.RootDirectory = root;
  oa.ObjectName = &us;
  oa.Attributes = OBJ_CASE_INSENSITIVE_;
  NT_IOSB iosb;
  memset(&iosb, 0, sizeof iosb);
  HANDLE h = INVALID_HANDLE_VALUE;
  NTSTATUS st = g_NtCreate(&h, access, &oa, &iosb, nullptr, FILE_ATTRIBUTE_NORMAL_,
                           FILE_SHARE_ALL_, disposition, options, nullptr, 0);
  if (st < 0) return INVALID_HANDLE_VALUE;
  return h;
}

/* NtSetInformationFile rename relative to root. */
static NTSTATUS NtRenameRel(HANDLE src, HANDLE root, const char* name) {
  wchar_t wname[128];
  MultiByteToWideChar(CP_UTF8, 0, name, -1, wname, 128);
  ULONG nlen = (ULONG)(wcslen(wname) * 2);
  ULONG size = 8 + 8 + 4 + 2 + nlen;  /* ReplaceIfExists + RootDir + NameLen + Name (wchar) */
  BYTE* info = (BYTE*)malloc(size);
  if (!info) return (NTSTATUS)0xC0000001;
  memset(info, 0, size);
  info[0] = 1;  /* ReplaceIfExists */
  memcpy(info + 8, &root, sizeof root);
  memcpy(info + 16, &nlen, 4);
  memcpy(info + 20, wname, nlen);
  NT_IOSB iosb;
  memset(&iosb, 0, sizeof iosb);
  NTSTATUS st = g_NtSetInfo(src, &iosb, info, size, FILE_RENAME_INFORMATION_);
  free(info);
  return st;
}

/* SetFileInformationByHandle disposition (delete). */
static BOOL SetDisposition(HANDLE h) {
  BYTE b = 1;
  return SetFileInformationByHandle(h, (FILE_INFO_BY_HANDLE_CLASS)FILE_DISPOSITION_INFORMATION_, &b, 1);
}

/* E30 arms (34-37) — NT operation-level FWT shapes. */
static int E30Run(int arm, int rep, const char* dir, char* note) {
  char path[96], body[64];
  HANDLE root = NtOpenRoot(dir);
  if (root == INVALID_HANDLE_VALUE) { snprintf(note, 128, "root_err=%lu", GetLastError()); return 100; }
  int ret = 0;
  if (arm == 34) {  /* E27-E: NtCreateFile(FILE_CREATE, FWT) + write + close */
    snprintf(path, sizeof path, "e30_e_r%d.bin", rep);
    snprintf(body, sizeof body, "e30-e-r%d", rep);
    HANDLE h = NtCreateRel(root, path, FILE_GENERIC_WRITE_ | SYNCHRONIZE,
                           FILE_CREATE_, FILE_NON_DIRECTORY_FILE_ | FILE_WRITE_THROUGH_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (h == INVALID_HANDLE_VALUE) { snprintf(note, 128, "ntcreate_err=%lu", GetLastError()); ret = 101; goto out; }
    static char buf[2048];
    memset(buf, 'P', sizeof buf);
    memcpy(buf, body, strlen(body));
    DWORD w = 0;
    WriteFile(h, buf, sizeof buf, &w, nullptr);
    CloseHandle(h);
    snprintf(note, 128, "e30-create-ok");
  } else if (arm == 35) {  /* E27-F: pre-create src, open src FWT + NtSetInformationFile(rename) */
    char srcn[96];
    snprintf(srcn, sizeof srcn, "src_%d", rep);
    snprintf(path, sizeof path, "dst_%d", rep);
    /* pre-create src (no FWT — the pre-state must exist for rename to act on it) */
    HANDLE pre = NtCreateRel(root, srcn, FILE_GENERIC_WRITE_ | SYNCHRONIZE,
                             FILE_CREATE_, FILE_NON_DIRECTORY_FILE_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (pre == INVALID_HANDLE_VALUE) { snprintf(note, 128, "pre_src_err=%lu", GetLastError()); ret = 108; goto out; }
    static char prebuf[2048];
    memset(prebuf, 'P', sizeof prebuf);
    snprintf(body, sizeof body, "e30-src-r%d", rep);
    memcpy(prebuf, body, strlen(body));
    DWORD pw = 0;
    WriteFile(pre, prebuf, sizeof prebuf, &pw, nullptr);
    CloseHandle(pre);
    HANDLE h = NtCreateRel(root, srcn, FILE_DELETE_ | SYNCHRONIZE,
                           FILE_OPEN_, FILE_NON_DIRECTORY_FILE_ | FILE_WRITE_THROUGH_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (h == INVALID_HANDLE_VALUE) { snprintf(note, 128, "open_src_err=%lu", GetLastError()); ret = 102; goto out; }
    NTSTATUS st = NtRenameRel(h, root, path);
    CloseHandle(h);
    if (st < 0) { snprintf(note, 128, "rename_st=0x%X", (unsigned)(-st)); ret = 103; goto out; }
    snprintf(note, 128, "e30-rename-ok");
  } else if (arm == 36) {  /* E27-G: pre-create src, open src FWT + disposition delete */
    char srcn[96];
    snprintf(srcn, sizeof srcn, "src_%d", rep);
    snprintf(path, sizeof path, "src_%d", rep);
    HANDLE pre = NtCreateRel(root, srcn, FILE_GENERIC_WRITE_ | SYNCHRONIZE,
                             FILE_CREATE_, FILE_NON_DIRECTORY_FILE_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (pre == INVALID_HANDLE_VALUE) { snprintf(note, 128, "pre_src_err=%lu", GetLastError()); ret = 109; goto out; }
    static char prebuf2[2048];
    memset(prebuf2, 'P', sizeof prebuf2);
    snprintf(body, sizeof body, "e30-src-r%d", rep);
    memcpy(prebuf2, body, strlen(body));
    DWORD pw2 = 0;
    WriteFile(pre, prebuf2, sizeof prebuf2, &pw2, nullptr);
    CloseHandle(pre);
    HANDLE h = NtCreateRel(root, path, FILE_DELETE_ | SYNCHRONIZE,
                           FILE_OPEN_, FILE_NON_DIRECTORY_FILE_ | FILE_WRITE_THROUGH_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (h == INVALID_HANDLE_VALUE) { snprintf(note, 128, "open_tgt_err=%lu", GetLastError()); ret = 104; goto out; }
    BOOL ok = SetDisposition(h);
    CloseHandle(h);
    if (!ok) { snprintf(note, 128, "disp_err=%lu", GetLastError()); ret = 105; goto out; }
    snprintf(note, 128, "e30-delete-ok");
  } else if (arm == 37) {  /* composite: write tmp + flush + rename FWT */
    snprintf(path, sizeof path, "tmp_%d", rep);
    snprintf(body, sizeof body, "e30-tmp-r%d", rep);
    HANDLE h = NtCreateRel(root, path, FILE_GENERIC_WRITE_ | SYNCHRONIZE,
                           FILE_CREATE_, FILE_NON_DIRECTORY_FILE_ | FILE_WRITE_THROUGH_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (h == INVALID_HANDLE_VALUE) { snprintf(note, 128, "tmp_create_err=%lu", GetLastError()); ret = 106; goto out; }
    static char buf[2048];
    memset(buf, 'P', sizeof buf);
    memcpy(buf, body, strlen(body));
    DWORD w = 0;
    WriteFile(h, buf, sizeof buf, &w, nullptr);
    FlushFileBuffers(h);
    CloseHandle(h);
    char dstn[96];
    snprintf(dstn, sizeof dstn, "final_%d", rep);
    HANDLE s2 = NtCreateRel(root, path, FILE_DELETE_ | SYNCHRONIZE,
                            FILE_OPEN_, FILE_NON_DIRECTORY_FILE_ | FILE_WRITE_THROUGH_ | FILE_SYNCHRONOUS_IO_NONALERT_);
    if (s2 == INVALID_HANDLE_VALUE) { snprintf(note, 128, "reopen_err=%lu", GetLastError()); ret = 107; goto out; }
    NTSTATUS st = NtRenameRel(s2, root, dstn);
    CloseHandle(s2);
    if (st < 0) { snprintf(note, 128, "rename_st=0x%X", (unsigned)(-st)); ret = 108; goto out; }
    snprintf(note, 128, "e30-composite-ok");
  } else {
    ret = 99;
  }
out:
  CloseHandle(root);
  return ret;
}

static int RunArm(int arm, int rep, const char* dir, char* note) {  char path[96], src[96], dst[96], deldir[96], body[64], root[8];
  DWORD oe = 0, fe = 0;
  snprintf(root, sizeof root, "%c:\\", g_vol);

  switch (arm) {
  case 0:  /* control: flush file only â upstream behavior */
    snprintf(path, sizeof path, "%s\\a0_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a0-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    return 0;
  case 1:  /* leveldb 30b34e0 / SQLite / MySQL / FrankenSQLite shape */
    snprintf(path, sizeof path, "%s\\a1_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a1-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "dir open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 101);
    }
    return 0;
  case 2: {  /* SQLite S7 shape: relative path, cwd = the trial dir */
    snprintf(body, sizeof body, "e17-a2-r%d", rep);
    if (!SetCurrentDirectoryA(dir)) return (int)GetLastError();
    if (!WritePadded("a2_rel.bin", body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(".", &oe, &fe)) {
      snprintf(note, 128, "reldir open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      SetCurrentDirectoryA(root);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 102);
    }
    SetCurrentDirectoryA(root);
    return 0;
  }
  case 3:  /* LMDB mdb_mdirsync shape (+ GetFinalPathNameByHandleW) */
    snprintf(path, sizeof path, "%s\\a3_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a3-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirLmdbShape(dir, &oe, &fe)) {
      snprintf(note, 128, "lmdb open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 103);
    }
    return 0;
  case 4:  /* PG transient-fd shape: flush through _commit(fd) */
    snprintf(path, sizeof path, "%s\\a4_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a4-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirPgShape(dir, &oe, &fe)) {
      snprintf(note, 128, "pgfd open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 104);
    }
    return 0;
  case 5: {  /* MySQL delete dance on a DURABLE pre-state: WritePadded +
              * FlushDir puts a5_rN.bin on the platter first, then the
              * MoveFileW name->name.XXXX.d + DeleteFileW dance, then
              * os_parent_dir_fsync_win (flush AFTER unlink, POSIX parity).
              * Verdict: file GONE from platter = deletion persisted. */
    snprintf(path, sizeof path, "%s\\a5_r%d.bin", dir, rep);
    snprintf(deldir, sizeof deldir, "%s\\a5_r%d.bin.XXXX.d", dir, rep);
    snprintf(body, sizeof body, "e17-a5-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "pre open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return 150;
    }
    wchar_t wsrc[96], wdst[96];
    MultiByteToWideChar(CP_UTF8, 0, path, -1, wsrc, 96);
    MultiByteToWideChar(CP_UTF8, 0, deldir, -1, wdst, 96);
    if (!MoveFileW(wsrc, wdst)) return (int)GetLastError();
    if (!DeleteFileW(wdst)) return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "deldir open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 105);
    }
    return 0;
  }
  case 6: {  /* rename + MOVEFILE_WRITE_THROUGH (MySQL os_file_rename_func) */
    snprintf(src, sizeof src, "%s\\a6_r%d_src.bin", dir, rep);
    snprintf(dst, sizeof dst, "%s\\a6_r%d_dst.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a6-r%d", rep);
    DeleteFileA(dst);
    if (!WritePadded(src, body, TRUE)) return (int)GetLastError();
    /* make src durable + flush dir, so only the rename is dirty at trial
     * time (E14 arm3/4 recipe) */
    FlushDirShape(dir, &oe, &fe); FlushDirShape(root, &oe, &fe);
    wchar_t wsrc[96], wdst[96];
    MultiByteToWideChar(CP_UTF8, 0, src, -1, wsrc, 96);
    MultiByteToWideChar(CP_UTF8, 0, dst, -1, wdst, 96);
    if (!MoveFileExW(wsrc, wdst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
      return (int)GetLastError();
    return 0;
  }
  case 7: {  /* MySQL create_directory shape: mkdir then flush the PARENT */
    snprintf(deldir, sizeof deldir, "%s\\a7_r%d.d", dir, rep);
    wchar_t wdir[96];
    MultiByteToWideChar(CP_UTF8, 0, deldir, -1, wdir, 96);
    RemoveDirectoryW(wdir);
    if (!CreateDirectoryW(wdir, nullptr)) return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "mkdir flush open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 107);
    }
    return 0;
  }
  case 8: {  /* delete WITHOUT flush â control pair for arm 5:
              * durable pre-state, DeleteFileA, nothing else.
              * Verdict: file STILL on platter = deletion lost. */
    snprintf(path, sizeof path, "%s\\a8_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a8-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "pre open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return 151;
    }
    if (!DeleteFileA(path)) return (int)GetLastError();
    return 0;
  }
  case 9: {  /* mkdir WITHOUT flush â control pair for arm 7.
              * Verdict: a9_rN.d absent from platter. */
    snprintf(deldir, sizeof deldir, "%s\\a9_r%d.d", dir, rep);
    wchar_t wdir[96];
    MultiByteToWideChar(CP_UTF8, 0, deldir, -1, wdir, 96);
    RemoveDirectoryW(wdir);
    if (!CreateDirectoryW(wdir, nullptr)) return (int)GetLastError();
    return 0;
  }
  case 10: {  /* rename + parent dir flush â the E11 protocol on Windows
               * (leveldb CURRENT rename covered by SyncDirIfManifest,
               * LMDB/MySQL rename call sites). Durable src, bare
               * MOVEFILE_REPLACE_EXISTING, then FlushDir.
               * Verdict: dst PRESENT, src absent. */
    snprintf(src, sizeof src, "%s\\a10_r%d_src.bin", dir, rep);
    snprintf(dst, sizeof dst, "%s\\a10_r%d_dst.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a10-r%d", rep);
    DeleteFileA(dst);
    if (!WritePadded(src, body, TRUE)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe); FlushDirShape(root, &oe, &fe);
    wchar_t wsrc[96], wdst[96];
    MultiByteToWideChar(CP_UTF8, 0, src, -1, wsrc, 96);
    MultiByteToWideChar(CP_UTF8, 0, dst, -1, wdst, 96);
    if (!MoveFileExW(wsrc, wdst, MOVEFILE_REPLACE_EXISTING))
      return (int)GetLastError();
    if (!FlushDirShape(dir, &oe, &fe)) {
      snprintf(note, 128, "ren flush open_err=%lu flush_err=%lu",
               (unsigned long)oe, (unsigned long)fe);
      return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 110);
    }
    return 0;
  }
  case 11: {  /* create + flushfile + FlushDir(ROOT) only â disambiguates
               * whether the ROOT flush alone drives metadata to device. */
    snprintf(path, sizeof path, "%s\\a11_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a11-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(root, &oe, &fe)) return 111;
    return 0;
  }
  case 12: {  /* create + flushfile + FlushDir(dir) + FlushDir(root) â the
               * double flush that showed the platter effect in arms 6/10
               * pre-state. Expect: file PRESENT on platter. */
    snprintf(path, sizeof path, "%s\\a12_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a12-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe);
    if (!FlushDirShape(root, &oe, &fe)) return 112;
    return 0;
  }
  case 13: {  /* create + flushfile + FlushDir(dir) twice â root excluded.
               * Distinguishes "dir flush twice" from "dir+root". */
    snprintf(path, sizeof path, "%s\\a13_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a13-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe);
    if (!FlushDirShape(dir, &oe, &fe)) return 113;
    return 0;
  }
  case 14: {  /* rename + FlushDir(dir) + FlushDir(root) AFTER the rename â
               * the revised rename protocol. Durable src (double-flush
               * pre-state), bare rename, then dir+root flush.
               * Verdict: dst PRESENT, src absent. */
    snprintf(src, sizeof src, "%s\\a14_r%d_src.bin", dir, rep);
    snprintf(dst, sizeof dst, "%s\\a14_r%d_dst.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a14-r%d", rep);
    DeleteFileA(dst);
    if (!WritePadded(src, body, TRUE)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe); FlushDirShape(root, &oe, &fe);
    wchar_t wsrc[96], wdst[96];
    MultiByteToWideChar(CP_UTF8, 0, src, -1, wsrc, 96);
    MultiByteToWideChar(CP_UTF8, 0, dst, -1, wdst, 96);
    if (!MoveFileExW(wsrc, wdst, MOVEFILE_REPLACE_EXISTING))
      return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe);
    if (!FlushDirShape(root, &oe, &fe)) return 114;
    return 0;
  }
  case 15: {  /* delete + FlushDir(dir) + FlushDir(root) â durable pre-state
               * via DOUBLE flush (the single-flush pre-state never landed),
               * then DeleteFileA, then dir+root flush.
               * Verdict: a15_rN.bin GONE from platter. */
    snprintf(path, sizeof path, "%s\\a15_r%d.bin", dir, rep);
    snprintf(body, sizeof body, "e17-a15-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe); FlushDirShape(root, &oe, &fe);
    if (!DeleteFileA(path)) return (int)GetLastError();
    FlushDirShape(dir, &oe, &fe);
    if (!FlushDirShape(root, &oe, &fe)) return 115;
    return 0;
  }
  case 16: {  /* non-resident $I30 + PARENT-dir-only flush.  d16 was
               * pre-loaded with 200 entries and made durable (root flush)
               * before the reps loop, so the ONLY dirty namespace object in
               * the window is the new file.  Does residency change what the
               * parent-dir flush does? */
    char d16[80], path[112], body[80];
    snprintf(d16, sizeof d16, "%s\\d16", dir);
    snprintf(path, sizeof path, "%s\\a16_r%d.bin", d16, rep);
    snprintf(body, sizeof body, "e17-a16-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (!FlushDirShape(d16, &oe, &fe)) return (int)((oe ? oe : fe) ? (oe ? oe : fe) : 116);
    return 0;
  }
  case 17: {  /* non-resident $I30 + parent + root flush (corrected protocol
               * on a large directory).  Verdict: a17_rN.bin PRESENT. */
    char d16[80], path[112], body[80];
    snprintf(d16, sizeof d16, "%s\\d16", dir);
    snprintf(path, sizeof path, "%s\\a17_r%d.bin", d16, rep);
    snprintf(body, sizeof body, "e17-a17-r%d", rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    FlushDirShape(d16, &oe, &fe);
    if (!FlushDirShape(root, &oe, &fe)) return 117;
    return 0;
  }
  case 20: case 21: case 22: case 23: {
    /* native NT flush matrix (create+flushfile first, identical to arm 1
     * otherwise):
     *   20 NtFlushBuffersFile on the PARENT DIR  (baseline == Win32)
     *   21 NtFlushBuffersFileEx(FLUSH_AND_PURGE) on the PARENT DIR (24H2)
     *   22 NtFlushBuffersFile on the VOLUME OBJECT \\.\<vol>
     *   23 NtFlushBuffersFileEx(FLUSH_AND_PURGE) on the VOLUME OBJECT
     * Verdict per arm: a2X_rN.bin on platter + ntstatus in the DONE marker. */
    int variant = arm - 20;
    snprintf(path, sizeof path, "%s\\a%d_r%d.bin", dir, arm, rep);
    snprintf(body, sizeof body, "e17-a%d-r%d", arm, rep);
    if (!WritePadded(path, body, TRUE)) return (int)GetLastError();
    if (arm >= 22) {
      wchar_t volp[16];
      volp[0] = L'\\'; volp[1] = L'\\'; volp[2] = L'.'; volp[3] = L'\\';
      volp[4] = (wchar_t)g_vol; volp[5] = L':'; volp[6] = 0;
      HANDLE v = CreateFileW(volp, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE,
                             nullptr, OPEN_EXISTING, 0, nullptr);
      if (v == INVALID_HANDLE_VALUE) {
        snprintf(note, 128, "vol-open err=%lu", (unsigned long)GetLastError());
        return 0;
      }
      NTSTATUS st = NtFlushVariant(v, variant);
      CloseHandle(v);
      snprintf(note, 128, "vol ntstatus=0x%08lX variant=%d",
               (unsigned long)st, variant);
      return 0;
    }
    wchar_t wdir[512];
    MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 512);
    HANDLE d = CreateFileW(wdir, GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (d == INVALID_HANDLE_VALUE) return (int)GetLastError();
    NTSTATUS st = NtFlushVariant(d, variant);
    CloseHandle(d);
    snprintf(note, 128, "ntstatus=0x%08lX variant=%d",
             (unsigned long)st, variant);
    return 0;
  }
  case 90: {  /* INSTALLER (maintenance boot): copy SELF (the exe inside the
               * PE ramdisk, X:\e13pe.exe) to the full-Windows OS volume's
               * \e14\e14pe3.exe so the golden image's ONSTART task runs the
               * new probe.  OS volume = the NTFS volume carrying
               * \Windows\System32 (never the E13SYS data volume, never X:).
               * Marker records the written size for read-back verification. */
    char osvol = 0;
    for (char c = 'C'; c <= 'Z'; ++c) {
      if (c == 'X' || c == g_vol) continue;
      char p[8] = {'c', ':', '\\', 0};
      char chk[64];
      p[0] = c;
      snprintf(chk, sizeof chk, "%sWindows\\System32", p);
      DWORD attr = GetFileAttributesA(chk);
      if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY)) {
        char lbl[64] = "", fs[64] = "";
        if (GetVolumeInformationA(p, lbl, sizeof lbl, nullptr, nullptr, nullptr, fs, sizeof fs)
            && _stricmp(fs, "NTFS") == 0) { osvol = c; break; }
      }
    }
    if (!osvol) return 900;
    char self[MAX_PATH];
    if (!GetModuleFileNameA(nullptr, self, MAX_PATH)) return 901;
    char dstp[64], dird[8];
    snprintf(dird, sizeof dird, "%c:\\e14", osvol);
    snprintf(dstp, sizeof dstp, "%s\\e14pe3.exe", dird);
    CreateDirectoryA(dird, nullptr);
    if (!CopyFileA(self, dstp, FALSE)) return 902;
    HANDLE h = CreateFileA(dstp, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return 903;
    DWORD sz = GetFileSize(h, nullptr);
    CloseHandle(h);
    char body[96];
    snprintf(body, sizeof body, "E17INSTALLED size=%lu os=%c:",
             (unsigned long)sz, osvol);
    Marker("installed.txt", body);
    return 0;
  }
    case 30: {  /* E29: rightkit create-dir */
      char path[128], body[64];
      snprintf(path, sizeof path, "%s\\rk30_r%d", dir, rep);
      snprintf(body, sizeof body, "rk30-create-r%d", rep);
      SerLog("E29-A30 rep=%d mkdir=%s", rep, path);
      if (!CreateDirectoryA(path, NULL)) {
        snprintf(note, 128, "mkdir_err=%lu", GetLastError());
        SerLog("E29-A30 mkdir-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      SerLog("E29-A30 mkdir-ok");
      char mp[192];
      snprintf(mp, sizeof mp, "%s\\m.txt", path);
      WritePadded(mp, body, FALSE);
      snprintf(note, 128, "rk-create-ok");
      SerLog("E29-A30 done");
      return 0;
    }
    case 31: {  /* E29: rightkit rename_exclusive */
      char src_p[128], dst_p[128], body[64];
      snprintf(src_p, sizeof src_p, "%s\\rk31_src_%d", dir, rep);
      snprintf(dst_p, sizeof dst_p, "%s\\rk31_dst_%d", dir, rep);
      snprintf(body, sizeof body, "rk31-rename-r%d", rep);
      if (!WritePadded(src_p, body, FALSE)) {
        snprintf(note, 128, "src_err=%lu", GetLastError());
        SerLog("E29-A31 src-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      SerLog("E29-A31 rep=%d src-written", rep);
      wchar_t wsrc[256], wdst[256];
      MultiByteToWideChar(CP_UTF8, 0, src_p, -1, wsrc, 256);
      MultiByteToWideChar(CP_UTF8, 0, dst_p, -1, wdst, 256);
      if (!MoveFileExW(wsrc, wdst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        snprintf(note, 128, "rename_err=%lu", GetLastError());
        SerLog("E29-A31 rename-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      snprintf(note, 128, "rk-rename-ok");
      SerLog("E29-A31 done");
      return 0;
    }
    case 32: {  /* E29: rightkit delete */
      char path[128], body[64];
      snprintf(path, sizeof path, "%s\\rk32_r%d", dir, rep);
      snprintf(body, sizeof body, "rk32-delete-r%d", rep);
      if (!WritePadded(path, body, FALSE)) {
        snprintf(note, 128, "create_err=%lu", GetLastError());
        SerLog("E29-A32 create-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      SerLog("E29-A32 rep=%d prestate-written", rep);
      wchar_t wp[256];
      MultiByteToWideChar(CP_UTF8, 0, path, -1, wp, 256);
      if (!DeleteFileW(wp)) {
        snprintf(note, 128, "delete_err=%lu", GetLastError());
        SerLog("E29-A32 delete-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      snprintf(note, 128, "rk-delete-ok");
      SerLog("E29-A32 done");
      return 0;
    }
    case 33: {  /* E29: rightkit atomic write */
      char tmp_p[128], dst_p[128], body[64];
      snprintf(tmp_p, sizeof tmp_p, "%s\\rk33_tmp_%d", dir, rep);
      snprintf(dst_p, sizeof dst_p, "%s\\rk33_dst_%d", dir, rep);
      snprintf(body, sizeof body, "rk33-write-r%d", rep);
      if (!WritePadded(tmp_p, body, TRUE)) {
        snprintf(note, 128, "tmp_err=%lu", GetLastError());
        SerLog("E29-A33 tmp-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      SerLog("E29-A33 rep=%d tmp-written", rep);
      wchar_t wtmp[256], wdst[256];
      MultiByteToWideChar(CP_UTF8, 0, tmp_p, -1, wtmp, 256);
      MultiByteToWideChar(CP_UTF8, 0, dst_p, -1, wdst, 256);
      if (!MoveFileExW(wtmp, wdst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        snprintf(note, 128, "move_err=%lu", GetLastError());
        SerLog("E29-A33 move-err=%lu", GetLastError());
        return (int)GetLastError();
      }
      snprintf(note, 128, "rk-write-ok");
      SerLog("E29-A33 done");
      return 0;
    }
    case 34: case 35: case 36: case 37: {  /* E30 NT operation-level FWT */
      return E30Run(arm, rep, dir, note);
    }
  }
  return 0;
}

int main(void) {
  SerLog("E29-PROBE-START");
  int arm = -1, reps = 3;
  char how = '?';
  int tries = 0;
  for (int t = 0; t < 12 && !FindVolume(); ++t) { tries = t + 1; Sleep(5000); }
  SerLog("E29-FINDVOL tries=%d vol=%c", tries, g_vol ? g_vol : '-');
  if (!g_vol) Park("no-volume");
  if (ReadArmFromSmbios(&arm, &reps)) how = 'S';
  else if (ReadArmFromFile(&arm, &reps)) how = 'F';
  SerLog("E29-ARM arm=%d reps=%d how=%c", arm, reps, how);
  if (arm < 0) Park("no-arm");
  LoadNt();
  LoadNt2();
  SerLog("E29-NTLOADED ntflush=%d ntflushex=%d ntcreate=%d ntsetinfo=%d",
         g_NtFlush ? 1 : 0, g_NtFlushEx ? 1 : 0,
         g_NtCreate ? 1 : 0, g_NtSetInfo ? 1 : 0);

  char dir[32];
  snprintf(dir, sizeof dir, "%c:\\e14", g_vol);
  CreateDirectoryA(dir, nullptr);
  if (arm == 16 || arm == 17) {
    char rootp[8];
    snprintf(rootp, sizeof rootp, "%c:\\", g_vol);
    MakeD16(dir, rootp);
  }

  char m[256];
  int errs[16] = {0};
  char notes[16][128] = {{0}};
  for (int r = 0; r < reps; ++r) {
    SerLog("E29-REP-START arm=%d rep=%d", arm, r);
    snprintf(m, sizeof m, "E17GO arm=%d rep=%d how=%c", arm, r, how);
    Marker("go.txt", m);
    errs[r] = RunArm(arm, r, dir, notes[r]);
    SerLog("E29-REP-DONE arm=%d rep=%d err=%d note=%.80s", arm, r, errs[r], notes[r]);
  }
  snprintf(m, sizeof m,
           "E17DONE arm=%d reps=%d how=%c errs=%d,%d,%d n0=%.100s n1=%.60s n2=%.60s",
           arm, reps, how, errs[0], errs[1], errs[2],
           notes[0], notes[1], notes[2]);
  Marker("done.txt", m);
  SerLog("E29-DONE-MARKER-WRITTEN arm=%d", arm);
  Park("done");
  return 0;
}
