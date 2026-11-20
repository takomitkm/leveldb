/* E29 WinPE wrapper probe. Runs one rightkit single-operation PE from the
 * embedded bundle, records E29GO/E29DONE markers in the same E17 rig format.
 * The actual rightkit PE is produced by GitHub Actions and bundled beside
 * this probe; at build time this source is compiled to e29pe.exe.
 *
 * Arms:
 *   20 rightkit-create-dir
 *   21 rightkit-rename
 *   22 rightkit-delete
 *   23 rightkit-write
 *   98 park
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

static BOOL ReadArm(int* arm, int* reps) {
  typedef UINT (WINAPI *pGFT)(DWORD, DWORD, PVOID, UINT);
  pGFT gft = (pGFT)GetProcAddress(GetModuleHandleA("kernel32.dll"), "GetSystemFirmwareTable");
  if (gft) {
    UINT need = gft('RSMB', 0, nullptr, 0);
    static BYTE buf[1 << 20];
    if (need && need <= (1u << 20) && gft('RSMB', 0, buf, need) == need) {
      for (UINT i = 0; i + 7 <= need; ++i) {
        if (memcmp(buf + i, "E29ARM=", 7) == 0) {
          *arm = atoi((const char*)buf + i + 7);
          const char* r = strstr((const char*)buf + i, "REPS=");
          *reps = r ? atoi(r + 5) : 3;
          if (*reps < 1) *reps = 1;
          if (*reps > 16) *reps = 16;
          return TRUE;
        }
      }
    }
  }
  return FALSE;
}

static void Marker(const char* name, const char* content) {
  char path[96];
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

static int RunRightkit(int arm, int rep, const char* dir, char* note) {
  const char* exe = nullptr;
  switch (arm) {
    case 20: exe = "rightkit-create-dir.exe"; break;
    case 21: exe = "rightkit-rename.exe"; break;
    case 22: exe = "rightkit-delete.exe"; break;
    case 23: exe = "rightkit-write.exe"; break;
    default: return 99;
  }
  char cmdline[512];
  char target[160];
  snprintf(target, sizeof target, "%s\\rk_%d", dir, rep);
  char exepath[512];
  GetModuleFileNameA(nullptr, exepath, sizeof exepath);
  char* slash = strrchr(exepath, '\');
  if (slash) *(slash + 1) = 0; else strcpy(exepath, "X:\\");
  snprintf(cmdline, sizeof cmdline, "\"%s%s\" \"%s\"", exepath, exe, target);
  STARTUPINFOA si; ZeroMemory(&si, sizeof si); si.cb = sizeof si;
  PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof pi);
  BOOL ok = CreateProcessA(nullptr, cmdline, nullptr, nullptr, FALSE,
                           CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
  if (!ok) { snprintf(note, 128, "createproc_err=%lu", GetLastError()); return 1; }
  WaitForSingleObject(pi.hProcess, INFINITE);
  DWORD code = 0;
  GetExitCodeProcess(pi.hProcess, &code);
  CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
  snprintf(note, 128, "rc=%lu", code);
  return (int)code;
}

int main(void) {
  int arm = -1, reps = 3;
  for (int t = 0; t < 12 && !FindVolume(); ++t) Sleep(5000);
  if (!g_vol) Park();
  if (!ReadArm(&arm, &reps)) Park();

  char dir[32];
  snprintf(dir, sizeof dir, "%c:\\e14", g_vol);
  CreateDirectoryA(dir, nullptr);

  char m[256];
  int errs[16] = {0};
  char notes[16][128] = {{0}};
  for (int r = 0; r < reps; ++r) {
    snprintf(m, sizeof m, "E29GO arm=%d rep=%d", arm, r);
    Marker("go.txt", m);
    errs[r] = RunRightkit(arm, r, dir, notes[r]);
  }
  snprintf(m, sizeof m, "E29DONE arm=%d reps=%d errs=%d,%d,%d n0=%.100s n1=%.60s n2=%.60s",
           arm, reps, errs[0], errs[1], errs[2], notes[0], notes[1], notes[2]);
  Marker("done.txt", m);
  Park();
  return 0;
}
