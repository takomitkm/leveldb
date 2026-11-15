/* E14 v3 — one arm per boot; config via SMBIOS, markers via WRITE_THROUGH files.
 *
 * History of fixes baked in here:
 *  v1: 512-byte markers at volume offset 0 destroyed the NTFS VBR (oem became
 *      b'MARK END') and each marker's FlushFileBuffers was device-wide, so it
 *      committed the previous trial's dirty metadata.
 *  v2: config from a raw 4 GiB sector was unverifiable (reads invisible in the
 *      block log) and the self-bugcheck (NtRaiseHardError OptionShutdownSystem)
 *      silently did nothing in WinPE — VM stayed alive past the 12 min timeout
 *      with the log frozen at one boot's worth of records.
 *  v3: arm id arrives via QEMU -smbios type=1,serial="E14ARM=<n>;REPS=<r>"
 *      (GetSystemFirmwareTable('RSMB') works from user mode; we scan the raw
 *      buffer for the ASCII payload — no structure parsing). Fallback: a
 *      pre-baked arm.txt with arm=98 (an arm nobody runs) so a silent SMBIOS
 *      failure is VISIBLE in the markers instead of being mislabeled.
 *      Markers are ordinary files written with FILE_FLAG_WRITE_THROUGH and NO
 *      explicit FlushFileBuffers: their data reaches the device promptly and
 *      shows up in the blklogwrites log, where the host polls for "E14DONE".
 *      No raw-disk writes, no VBR touching, no bugcheck, one arm per boot.
 *
 * Arms:
 *   0 TEETH      create+write+close, nothing flushed
 *   1 FLUSH_FILE create+write+FlushFileBuffers(file)
 *   2 FLUSH_DIR  arm1 + GENERIC_WRITE FlushFileBuffers(parent dir)
 *   3 RENAME     MoveFileExA(src,dst, REPLACE_EXISTING)
 *   4 RENAME_WT  MoveFileExA(src,dst, REPLACE|WRITE_THROUGH)
 *   5 CREATE_WT  CreateFile(FILE_FLAG_WRITE_THROUGH)+write+close
 *  98 PARK       config-fallback signature: run nothing, still mark DONE
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char g_vol = 0;

static void Park(void) { for (;;) Sleep(60000); }

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

/* SMBIOS scan: find "E14ARM=" anywhere in the raw RSMB buffer. */
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
    if (memcmp(buf + i, "E14ARM=", 7) == 0) { hit = (const char*)(buf + i); break; }
  }
  if (!hit) return FALSE;
  *arm = atoi(hit + 7);
  const char* r = strstr(hit, "REPS=");
  *reps = r ? atoi(r + 5) : 3;
  if (*reps < 1) *reps = 1;
  if (*reps > 16) *reps = 16;
  return *arm >= 0;
}

/* Fallback config: <vol>\e14\arm.txt, master-baked as "arm=98 reps=3". */
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

/* WRITE_THROUGH file marker: data reaches the device promptly (visible in the
 * block log within milliseconds) but we never issue a device-wide flush. */
static void Marker(const char* name, const char* content) {
  char path[64];
  snprintf(path, sizeof path, "%c:\\e14\\%s", g_vol, name);
  /* Pad to 2048 bytes: forces the content NON-RESIDENT (data run) so the
   * marker's data write appears exactly ONCE in the block log (NTFS journals
   * metadata only; resident content would be duplicated inside MFT-page and
   * journal writes, breaking content-addressed cut selection). */
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

static BOOL FlushDir(const char* dir) {
  HANDLE d = CreateFileA(dir, GENERIC_WRITE,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (d == INVALID_HANDLE_VALUE) return FALSE;
  BOOL ok = FlushFileBuffers(d);
  CloseHandle(d);
  return ok;
}

/* Durable pre-state for rename arms: src certainly on disk before the
 * measured operation, so the only dirty metadata at trial time is the rename. */
static BOOL MakeDurable(const char* path, const char* body) {
  HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                         FILE_FLAG_WRITE_THROUGH, nullptr);
  if (h == INVALID_HANDLE_VALUE) return FALSE;
  /* pad to 2048: non-resident -> exactly one data write in the log */
  static char buf[2048];
  memset(buf, 'P', sizeof buf);
  size_t cl = strlen(body);
  memcpy(buf, body, cl);
  buf[cl] = '\n';
  DWORD w = 0;
  BOOL ok = WriteFile(h, buf, sizeof buf, &w, nullptr);
  FlushFileBuffers(h);
  CloseHandle(h);
  return ok;
}

static int RunArm(int arm, int rep, const char* dir) {
  /* Returns 0 on success, else the failing Win32 error code. Environmental
   * interference (Defender, indexing) would otherwise be indistinguishable
   * from the durability phenomenon being measured. */
  char path[80], src[80], dst[80], body[80], root[8];
  snprintf(root, sizeof root, "%c:\\", g_vol);

  if (arm == 0 || arm == 1 || arm == 2 || arm == 5) {
    snprintf(path, sizeof path, "%s\\a%d_r%d.bin", dir, arm, rep);
    snprintf(body, sizeof body, "e14-a%d-r%d", arm, rep);
    DWORD flags = (arm == 5) ? FILE_FLAG_WRITE_THROUGH : 0;
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, flags, nullptr);
    if (h == INVALID_HANDLE_VALUE) return (int)GetLastError();
    /* pad to 2048: non-resident -> unique data write in the log */
    static char buf[2048];
    memset(buf, 'P', sizeof buf);
    size_t cl = strlen(body);
    memcpy(buf, body, cl);
    buf[cl] = '\n';
    DWORD w = 0;
    if (!WriteFile(h, buf, sizeof buf, &w, nullptr) || w != sizeof buf) {
      DWORD e = GetLastError(); CloseHandle(h); return (int)(e ? e : 100);
    }
    if ((arm == 1 || arm == 2) && !FlushFileBuffers(h)) {
      DWORD e = GetLastError(); CloseHandle(h); return (int)(e ? e : 101);
    }
    if (!CloseHandle(h)) return (int)GetLastError();
    if (arm == 2 && !FlushDir(dir)) return (int)(GetLastError() ? GetLastError() : 102);
    return 0;
  } else if (arm == 3 || arm == 4) {
    snprintf(src, sizeof src, "%s\\rn%d_r%d_src.bin", dir, arm, rep);
    snprintf(dst, sizeof dst, "%s\\rn%d_r%d_dst.bin", dir, arm, rep);
    snprintf(body, sizeof body, "e14-rn%d-r%d", arm, rep);
    DeleteFileA(dst);
    if (!MakeDurable(src, body)) return (int)(GetLastError() ? GetLastError() : 103);
    FlushDir(dir);
    FlushDir(root);
    DWORD flags = MOVEFILE_REPLACE_EXISTING;
    if (arm == 4) flags |= MOVEFILE_WRITE_THROUGH;
    if (!MoveFileExA(src, dst, flags)) return (int)GetLastError();
    return 0;
  }
  return 0;
}

int main(void) {
  int arm = -1, reps = 3;
  char how = '?';
  /* ONSTART may fire before MountMgr assigns the data volume a letter:
   * retry for up to ~1 minute. */
  for (int t = 0; t < 12 && !FindVolume(); ++t) Sleep(5000);
  if (!g_vol) Park();
  if (ReadArmFromSmbios(&arm, &reps)) how = 'S';
  else if (ReadArmFromFile(&arm, &reps)) how = 'F';
  if (arm < 0) Park();

  char dir[32];
  snprintf(dir, sizeof dir, "%c:\\e14", g_vol);
  CreateDirectoryA(dir, nullptr);   /* already exists from master bake */

  char m[160];
  int errs[16] = {0};
  for (int r = 0; r < reps; ++r) {
    snprintf(m, sizeof m, "E14GO arm=%d rep=%d how=%c", arm, r, how);
    Marker("go.txt", m);
    errs[r] = RunArm(arm, r, dir);
  }
  snprintf(m, sizeof m, "E14DONE arm=%d reps=%d how=%c errs=%d,%d,%d",
           arm, reps, how, errs[0], errs[1], errs[2]);
  Marker("done.txt", m);
  Park();
  return 0;
}