/*
 * e34-probe.cpp - E34 guest probe: directory-index change + candidate flush,
 *                reported over an EXTERNAL channel (never the test volume).
 *
 * Build (Windows, MSVC):
 *   cl /nologo /EHsc /W3 /O2 e34-probe.cpp /Fe34probe.exe ws2_32.lib
 * Build (Windows, MinGW):
 *   g++ -O2 -std=c++17 e34-probe.cpp -o e34probe.exe -lws2_32
 *
 * In the WinPE guest (the E14/E17 rig) there is no environment and no
 * network, so the trial parameters come from the SMBIOS system serial string
 * ("E34ARM=C;OP=create;TOPO=large;TRIAL=1"), exactly like the E17 probe
 * reads "E17ARM=<n>;REPS=<r>".  The data volume is the NTFS volume labelled
 * E13SYS.
 *
 * Two output channels, because a hosted or bare guest may have neither:
 *   * COM1 (the QEMU serial) - the external channel E34-PLAN.md section 8
 *     wants; every event is written there, unbuffered.
 *   * A WRITE_THROUGH manifest file on the data volume - the fallback; the
 *     host polls the blklogwrites journal for its marker and then cuts the
 *     power, and the manifest is recovered from the pre-recovery image.
 *
 * Configuration from the environment (used when there is no SMBIOS, e.g. in
 * the CI rehearsal):
 *   E34_DIR       target directory (on the test volume)
 *   E34_ARM       A|B|C|F|I|K
 *   E34_OP        create|rename|delete
 *   E34_NAME      unique file name for this trial
 *   E34_PAYLOAD   payload bytes to write
 *   E34_CTRL_HOST controller host (default 127.0.0.1)
 *   E34_CTRL_PORT controller port
 *   E34_VOLUME    volume letter for the root-flush control arm
 *   E34_DONE      optional host-side file the probe touches when the trial is
 *                 over (rehearsal mode only; never on the test volume)
 *
 * Every event goes over the TCP channel to the host controller, which keeps
 * its own log.  The probe never writes a manifest to the test volume.
 *
 * After the candidate flush returns, the probe reports the result and then
 * blocks, so the controller can cut the power at the configured moment.
 */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#pragma comment(lib, "ws2_32.lib")

namespace {

SOCKET g_sock = INVALID_SOCKET;

void EnvOr(const char* name, const char* dflt, std::string* out) {
  char buf[512];
  DWORD n = GetEnvironmentVariableA(name, buf, (DWORD)sizeof buf);
  if (n == 0 || n >= sizeof buf) {
    *out = dflt;
  } else {
    out->assign(buf, n);
  }
}

bool ConnectController() {
  std::string host, port;
  EnvOr("E34_CTRL_HOST", "127.0.0.1", &host);
  EnvOr("E34_CTRL_PORT", "45000", &port);

  WSADATA wsa;
  if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return false;

  struct addrinfo hints;
  memset(&hints, 0, sizeof hints);
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;

  struct addrinfo* res = nullptr;
  if (getaddrinfo(host.c_str(), port.c_str(), &hints, &res) != 0) return false;

  for (struct addrinfo* p = res; p; p = p->ai_next) {
    SOCKET s = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
    if (s == INVALID_SOCKET) continue;
    if (connect(s, p->ai_addr, (int)p->ai_addrlen) == 0) {
      g_sock = s;
      break;
    }
    closesocket(s);
  }
  freeaddrinfo(res);
  return g_sock != INVALID_SOCKET;
}

void SendAll(const char* data, size_t len) {
  size_t done = 0;
  while (done < len) {
    int n = send(g_sock, data + done, (int)(len - done), 0);
    if (n <= 0) return;
    done += (size_t)n;
  }
}

/* Minimal JSON string escaping: paths and file names from the environment
 * can contain backslashes and quotes, which must not break the event line. */
std::string JsonEscape(const std::string& s) {
  std::string out;
  for (unsigned char c : s) {
    if (c == 0x5C) {              /* backslash */
      out += "\\\\";
    } else if (c == 0x22) {       /* quote */
      out += "\\\"";
    } else if (c == 0x0A) {       /* newline */
      out += "\\n";
    } else if (c == 0x0D) {       /* carriage return */
      out += "\\r";
    } else if (c == 0x09) {       /* tab */
      out += "\\t";
    } else if (c < 0x20) {
      char b[8];
      snprintf(b, sizeof b, "\\u%04x", (unsigned)c);
      out += b;
    } else {
      out += (char)c;
    }
  }
  return out;
}

/* ---------------------------------------------------------------- SMBIOS */
/* The E17 rig passes its arm through the SMBIOS system serial string; the
 * E34 probe does the same with "E34ARM=<arm>;OP=<op>;TOPO=<topology>". */
static std::string SmbiosField(const char* key) {
  typedef UINT(WINAPI* pGFT)(DWORD, DWORD, PVOID, UINT);
  pGFT gft = (pGFT)GetProcAddress(GetModuleHandleA("kernel32.dll"),
                                  "GetSystemFirmwareTable");
  if (!gft) return std::string();
  UINT need = gft('RSMB', 0, nullptr, 0);
  if (!need || need > (1u << 20)) return std::string();
  static BYTE buf[1 << 20];
  if (gft('RSMB', 0, buf, need) != need) return std::string();
  const char* hit = nullptr;
  size_t klen = strlen(key);
  for (UINT i = 0; i + klen <= need; ++i) {
    if (memcmp(buf + i, key, klen) == 0) { hit = (const char*)(buf + i); break; }
  }
  if (!hit) return std::string();
  std::string out(hit + klen);
  size_t stop = out.find(';');
  if (stop != std::string::npos) out = out.substr(0, stop);
  return out;
}

/* --------------------------------------------------------- data volume */
/* The rig's data disk carries the NTFS volume labelled E13SYS. */
static char FindDataVolume() {
  for (char c = 'C'; c <= 'Z'; ++c) {
    char root[4] = {c, ':', '\\', 0};
    char lbl[64] = {0};
    char fs[16] = {0};
    if (GetVolumeInformationA(root, lbl, sizeof lbl, nullptr, nullptr,
                              nullptr, fs, sizeof fs)) {
      if (_stricmp(fs, "NTFS") == 0 && _stricmp(lbl, "E13SYS") == 0) return c;
    }
  }
  return 0;
}

/* The manifest lives on its own small volume (label E34LOG), never on the
 * tested volume: a manifest write there would add an index entry to the very
 * directory tree being measured.  The host polls the volume image for the
 * E34DONE marker and cuts the power. */
static char FindManifestVolume() {
  for (char c = 'C'; c <= 'Z'; ++c) {
    char root[4] = {c, ':', '\\', 0};
    char lbl[64] = {0};
    char fs[16] = {0};
    if (GetVolumeInformationA(root, lbl, sizeof lbl, nullptr, nullptr,
                              nullptr, fs, sizeof fs)) {
      if (_stricmp(lbl, "E34LOG") == 0) return c;
    }
  }
  return 0;
}

/* ------------------------------------------------------- serial channel */
static HANDLE g_serial = INVALID_HANDLE_VALUE;

static void OpenSerial() {
  g_serial = CreateFileA("COM1", GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                         0, nullptr);
  if (g_serial == INVALID_HANDLE_VALUE) return;
  /* A raw-handle open ships with baud 0; without an explicit DCB the
   * WriteFile calls fail silently.  9600/8N1 matches QEMU's 16550. */
  DCB dcb;
  memset(&dcb, 0, sizeof dcb);
  dcb.DCBlength = sizeof dcb;
  if (GetCommState(g_serial, &dcb)) {
    dcb.BaudRate = 9600;
    dcb.ByteSize = 8;
    dcb.Parity = NOPARITY;
    dcb.StopBits = ONESTOPBIT;
    dcb.fBinary = TRUE;
    dcb.fDtrControl = DTR_CONTROL_DISABLE;
    dcb.fOutxCtsFlow = FALSE;
    dcb.fOutxDsrFlow = FALSE;
    dcb.fInX = FALSE;
    dcb.fOutX = FALSE;
    dcb.fRtsControl = RTS_CONTROL_DISABLE;
    SetCommState(g_serial, &dcb);
  }
}

static void SerialWrite(const char* data, size_t len) {
  if (g_serial == INVALID_HANDLE_VALUE) return;
  DWORD w = 0;
  WriteFile(g_serial, data, (DWORD)len, &w, nullptr);
  FlushFileBuffers(g_serial);
}

/* One JSON line per event.  The controller stores it verbatim. */
std::string g_event_log;

void Emit(const char* ev, const char* fmt, ...) {
  char body[1024];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(body, sizeof body - 1, fmt, ap);
  va_end(ap);

  FILETIME ft;
  GetSystemTimePreciseAsFileTime(&ft);
  unsigned long long ticks =
      ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;

  char line[2048];
  int n = snprintf(line, sizeof line - 1,
                   "{\"ev\":\"%s\",\"t\":%llu,\"tid\":%lu,\"pid\":%lu,%s}\r\n",
                   ev, ticks, GetCurrentThreadId(), GetCurrentProcessId(), body);
  if (n <= 0) return;
  g_event_log += line;
  SerialWrite(line, (size_t)n); /* COM1 channel (the QEMU rig) */
  if (g_sock != INVALID_SOCKET) {
    SendAll(line, (size_t)n);   /* TCP channel (CI rehearsal) */
  }
}

std::string WideToUtf8(const wchar_t* w) {
  if (!w) return std::string();
  int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
  if (n <= 1) return std::string();
  std::string s((size_t)n, '\0');
  WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
  s.resize((size_t)n - 1);
  return s;
}

std::wstring Utf8ToWide(const std::string& s) {
  if (s.empty()) return std::wstring();
  int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
  if (n <= 1) return std::wstring();
  std::wstring w((size_t)n, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, &w[0], n);
  w.resize((size_t)n - 1);
  return w;
}

bool WriteAll(HANDLE h, const char* buf, DWORD len) {
  DWORD done = 0;
  while (done < len) {
    DWORD n = 0;
    if (!WriteFile(h, buf + done, len - done, &n, nullptr)) return false;
    if (n == 0) break;
    done += n;
  }
  return done == len;
}

HANDLE OpenDirectory(const std::wstring& path) {
  return CreateFileW(path.c_str(), GENERIC_READ | FILE_APPEND_DATA,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
}

/* The $I30:$INDEX_ALLOCATION stream.  E33.1 found that this needs
 * FILE_FLAG_BACKUP_SEMANTICS; without it the open fails with
 * ERROR_ACCESS_DENIED. */
HANDLE OpenIndexStream(const std::wstring& dir) {
  std::wstring path = dir + L":$I30:$INDEX_ALLOCATION";
  return CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
}

std::string FileIdentity(HANDLE h) {
  BY_HANDLE_FILE_INFORMATION fi;
  if (h == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(h, &fi)) {
    return "\"file_record\":0";
  }
  unsigned long long idx =
      ((unsigned long long)fi.nFileIndexHigh << 32) | fi.nFileIndexLow;
  char buf[128];
  snprintf(buf, sizeof buf, "\"file_record\":%llu", idx);
  return buf;
}

/* The candidate flush for this arm.  Returns the API status text. */
std::string CandidateFlush(const std::string& arm, const std::wstring& dir,
                           HANDLE file_handle) {
  char buf[256];
  DWORD err;
  if (arm == "A") {
    snprintf(buf, sizeof buf, "\"status\":\"no-flush\",\"ok\":true");
    return buf;
  }
  if (arm == "B") {
    BOOL ok = FlushFileBuffers(file_handle);
    err = ok ? 0 : GetLastError();
    snprintf(buf, sizeof buf, "\"status\":\"win32-file\",\"ok\":%s,\"err\":%lu",
             ok ? "true" : "false", err);
    return buf;
  }
  if (arm == "C") {
    HANDLE d = OpenDirectory(dir);
    if (d == INVALID_HANDLE_VALUE) {
      snprintf(buf, sizeof buf,
               "\"status\":\"win32-dir\",\"ok\":false,\"err\":%lu", GetLastError());
      return buf;
    }
    BOOL ok = FlushFileBuffers(d);
    err = ok ? 0 : GetLastError();
    CloseHandle(d);
    snprintf(buf, sizeof buf, "\"status\":\"win32-dir\",\"ok\":%s,\"err\":%lu",
             ok ? "true" : "false", err);
    return buf;
  }
  if (arm == "F" || arm == "I") {
    HANDLE s = OpenIndexStream(dir);
    if (s == INVALID_HANDLE_VALUE) {
      snprintf(buf, sizeof buf,
               "\"status\":\"i30-stream\",\"ok\":false,\"err\":%lu", GetLastError());
      return buf;
    }
    if (arm == "F") {
      BOOL ok = FlushFileBuffers(s);
      err = ok ? 0 : GetLastError();
      CloseHandle(s);
      snprintf(buf, sizeof buf, "\"status\":\"win32-i30\",\"ok\":%s,\"err\":%lu",
               ok ? "true" : "false", err);
      return buf;
    }
    /* arm I: NtFlushBuffersFileEx(flags = 8, FLUSH_AND_PURGE) */
    typedef LONG(WINAPI* NtFlushBuffersFileEx_t)(HANDLE, ULONG, PVOID, ULONG,
                                                 void*);
    static NtFlushBuffersFileEx_t fn =
        (NtFlushBuffersFileEx_t)GetProcAddress(
            GetModuleHandleA("ntdll.dll"), "NtFlushBuffersFileEx");
    if (!fn) {
      CloseHandle(s);
      snprintf(buf, sizeof buf,
               "\"status\":\"ntex-i30-8\",\"ok\":false,\"err\":0");
      return buf;
    }
    unsigned char iosb[16];
    memset(iosb, 0, sizeof iosb);
    LONG st = fn(s, 8, nullptr, 0, iosb);
    CloseHandle(s);
    snprintf(buf, sizeof buf,
             "\"status\":\"ntex-i30-8\",\"ok\":%s,\"ntstatus\":\"0x%08lX\"",
             (st >= 0) ? "true" : "false", (unsigned long)(st & 0xFFFFFFFF));
    return buf;
  }
  if (arm == "K") {
    /* Volume root: the E26-withdrawn control, kept as a control only. */
    std::string vol;
    EnvOr("E34_VOLUME", "D:", &vol);
    std::wstring root = Utf8ToWide(vol) + L"\\";
    HANDLE r = OpenDirectory(root);
    if (r == INVALID_HANDLE_VALUE) {
      snprintf(buf, sizeof buf,
               "\"status\":\"win32-root\",\"ok\":false,\"err\":%lu", GetLastError());
      return buf;
    }
    BOOL ok = FlushFileBuffers(r);
    err = ok ? 0 : GetLastError();
    CloseHandle(r);
    snprintf(buf, sizeof buf, "\"status\":\"win32-root\",\"ok\":%s,\"err\":%lu",
             ok ? "true" : "false", err);
    return buf;
  }
  snprintf(buf, sizeof buf, "\"status\":\"unknown-arm\",\"ok\":false,\"err\":0");
  return buf;
}

/* Keep the channel open so the controller knows the guest is alive. */
void WaitForCrash(int seconds) {
  for (int i = 0; i < seconds * 2; i++) {
    Sleep(500);
    char ping[64];
    int n = snprintf(ping, sizeof ping, "{\"ev\":\"wait\",\"t\":0}\r\n");
    if (n > 0) SendAll(ping, (size_t)n);
  }
}

/* Create the trial file with a known payload (and flush it), so the
 * rename/delete workloads start from a durable, initialised source. */
HANDLE MakeSourceFile(const std::wstring& path, DWORD payload,
                       const char* tag) {
  HANDLE h = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                         FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                         FILE_ATTRIBUTE_NORMAL, nullptr);
  if (h == INVALID_HANDLE_VALUE) return INVALID_HANDLE_VALUE;
  std::string blob(payload, 'E');
  if (payload) WriteAll(h, blob.data(), payload);
  (void)tag;
  FlushFileBuffers(h);
  return h;
}

}  // namespace

int main() {
  std::string dir, arm, op, name, payload_s, done_path, vol;
  EnvOr("E34_DIR", "", &dir);
  EnvOr("E34_ARM", "", &arm);
  EnvOr("E34_OP", "", &op);
  EnvOr("E34_NAME", "", &name);
  EnvOr("E34_PAYLOAD", "4096", &payload_s);
  EnvOr("E34_DONE", "", &done_path);
  EnvOr("E34_VOLUME", "", &vol);

  /* The QEMU rig passes the trial parameters through SMBIOS; the CI rehearsal
   * uses the environment.  SMBIOS wins when both are present. */
  std::string sb_arm = SmbiosField("E34ARM=");
  std::string sb_op = SmbiosField("OP=");
  std::string sb_topo = SmbiosField("TOPO=");
  std::string sb_trial = SmbiosField("TRIAL=");
  std::string sb_mode = SmbiosField("MODE=");
  if (!sb_arm.empty()) arm = sb_arm;
  if (!sb_op.empty()) op = sb_op;
  if (arm.empty()) arm = "A";
  if (op.empty()) op = "create";

  OpenSerial();

  /* The data volume is the NTFS volume labelled E13SYS (the rig's disk). */
  std::string topo = sb_topo.empty() ? std::string("large") : sb_topo;
  char dv = FindDataVolume();
  if (dir.empty()) {
    if (!dv) {
      Emit("fatal", "\"reason\":\"no E13SYS data volume\"");
      return 3;
    }
    char d[64];
    snprintf(d, sizeof d, "%c:\\e34\\%s", dv,
             topo == "small" ? "small" : "large");
    dir = d;
    /* The target directory is pre-baked in the baseline disk and is NOT
     * created here: creating it would add an index entry to the very
     * directory tree under measurement.  If it is missing, the trial is
     * invalid. */
    HANDLE probe = OpenDirectory(Utf8ToWide(dir));
    if (probe == INVALID_HANDLE_VALUE) {
      Emit("fatal", "\"reason\":\"target directory missing; trial invalid\"");
      return 3;
    }
    CloseHandle(probe);
  }
  if (name.empty()) {
    std::string trial = sb_trial.empty() ? std::string("1") : sb_trial;
    name = "e34_" + arm + "_" + op + "_" + trial + ".bin";
  }
  if (vol.empty()) {
    vol = std::string(1, dv ? dv : 'D') + ":";
  }

  /* The TCP controller is optional: the QEMU guest has no network, and the
   * COM1 and manifest channels carry the evidence there. */
  if (!ConnectController()) {
    Emit("note", "\"reason\":\"no TCP controller; using COM1 and the "
         "manifest file\"");
  }

  std::wstring wdir = Utf8ToWide(dir);
  std::wstring wname = Utf8ToWide(name);
  std::wstring wpath = wdir + L"\\" + wname;
  DWORD payload = (DWORD)strtoul(payload_s.c_str(), nullptr, 10);

  /* Recovery mode: the guest boots on the CRASHED volume, Windows performs
   * its own NTFS log replay when the volume is mounted, and the probe then
   * reports the recovered namespace over the external channel.  This is the
   * E34-PLAN section 10 procedure; the probe must not repair anything. */
  if (sb_mode == "recover") {
    Emit("recover_start", "\"dir\":\"%s\",\"expect\":\"%s\"",
         JsonEscape(dir).c_str(), JsonEscape(name).c_str());
    HANDLE d = OpenDirectory(wdir);
    if (d == INVALID_HANDLE_VALUE) {
      Emit("recover_result", "\"ok\":false,\"err\":%lu,\"present\":false",
           GetLastError());
    } else {
      WIN32_FIND_DATAW fd;
      HANDLE find = FindFirstFileW((wdir + L"\\*").c_str(), &fd);
      int count = 0;
      int present = 0;
      while (find != INVALID_HANDLE_VALUE) {
        std::wstring wn = fd.cFileName;
        if (wn != L"." && wn != L"..") {
          count++;
          if (wn == wname) present = 1;
          std::string n8 = WideToUtf8(wn.c_str());
          Emit("recover_entry", "\"name\":\"%s\",\"size\":%lu,\"dir\":%d",
               JsonEscape(n8).c_str(), (unsigned long)fd.nFileSizeLow,
               (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? 1 : 0);
        }
        if (!FindNextFileW(find, &fd)) break;
      }
      if (find != INVALID_HANDLE_VALUE) FindClose(find);
      /* Read the payload back, if the file is there. */
      unsigned long long fsize = 0;
      HANDLE fh = CreateFileW(wpath.c_str(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, 0, nullptr);
      if (fh != INVALID_HANDLE_VALUE) {
        LARGE_INTEGER li;
        if (GetFileSizeEx(fh, &li)) fsize = (unsigned long long)li.QuadPart;
        CloseHandle(fh);
      }
      Emit("recover_result",
           "\"ok\":true,\"present\":%d,\"entries\":%d,\"size\":%llu",
           present ? 1 : 0, count, fsize);
      CloseHandle(d);
    }
    /* Write the recovery manifest to the external volume and stop. */
    {
      std::string manifest = g_event_log;
      char lv = FindManifestVolume();
      if (lv) {
        std::string mpath = std::string(1, lv) + ":\\recover-" + arm + "-" +
                            op + "-" + name + ".jsonl";
        HANDLE mh = CreateFileA(mpath.c_str(), GENERIC_WRITE,
                                FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
                                nullptr);
        if (mh != INVALID_HANDLE_VALUE) {
          DWORD w = 0;
          WriteFile(mh, manifest.data(), (DWORD)manifest.size(), &w, nullptr);
          FlushFileBuffers(mh);
          CloseHandle(mh);
        }
        std::string dpath = std::string(1, lv) + ":\\E34DONE";
        HANDLE dh = CreateFileA(dpath.c_str(), GENERIC_WRITE, 0, nullptr,
                                CREATE_ALWAYS,
                                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
                                nullptr);
        if (dh != INVALID_HANDLE_VALUE) {
          const char* tag = "E34DONE";
          DWORD w = 0;
          WriteFile(dh, tag, (DWORD)strlen(tag), &w, nullptr);
          FlushFileBuffers(dh);
          CloseHandle(dh);
        }
      }
      Emit("E34DONE", "\"arm\":\"%s\",\"op\":\"%s\"", arm.c_str(),
           op.c_str());
    }
    WaitForCrash(120);
    closesocket(g_sock);
    WSACleanup();
    return 0;
  }

  Emit("trial_start",
       "\"arm\":\"%s\",\"op\":\"%s\",\"name\":\"%s\",\"dir\":\"%s\",\"payload\":%lu",
       arm.c_str(), op.c_str(), JsonEscape(name).c_str(),
       JsonEscape(dir).c_str(), payload);

  {
    HANDLE d = OpenDirectory(wdir);
    if (d != INVALID_HANDLE_VALUE) {
      BY_HANDLE_FILE_INFORMATION fi;
      if (GetFileInformationByHandle(d, &fi)) {
        unsigned long long idx =
            ((unsigned long long)fi.nFileIndexHigh << 32) | fi.nFileIndexLow;
        Emit("dir_info", "\"dir_record\":%llu,\"vol_serial\":%lu", idx,
             fi.dwVolumeSerialNumber);
      } else {
        Emit("dir_info", "\"dir_record\":0,\"err\":%lu", GetLastError());
      }
      CloseHandle(d);
    } else {
      Emit("dir_info", "\"dir_record\":0,\"err\":%lu", GetLastError());
    }
  }

  std::string op_status = "ok";
  DWORD op_err = 0;
  std::string final_name = name;

  if (op == "create") {
    Emit("op_start", "\"op\":\"create\"");
    HANDLE h = CreateFileW(wpath.c_str(), GENERIC_READ | GENERIC_WRITE,
                           FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
      op_err = GetLastError();
      op_status = "create-failed";
      Emit("op_end", "\"op\":\"create\",\"status\":\"%s\",\"err\":%lu",
           op_status.c_str(), op_err);
    } else {
      std::string blob(payload, 'E');
      if (payload) WriteAll(h, blob.data(), payload);
      /* Do NOT flush the file here: arm A is the no-candidate-flush control
       * and arm B is defined as FlushFileBuffers(target file).  An
       * unconditional file-level flush would make arm A identical to arm B. */
      std::string ident = FileIdentity(h);
      Emit("op_end", "\"op\":\"create\",\"status\":\"ok\",%s", ident.c_str());
      Emit("flush_start", "\"arm\":\"%s\"", arm.c_str());
      std::string st = CandidateFlush(arm, wdir, h);
      Emit("flush_end", "\"arm\":\"%s\",%s", arm.c_str(), st.c_str());
      CloseHandle(h);
    }
  } else if (op == "rename") {
    /* The source must exist and be durable before the rename. */
    std::wstring wsrc = wdir + L"\\" + Utf8ToWide(name);
    std::wstring wdst = wdir + L"\\" + Utf8ToWide(name + ".ren");
    final_name = name + ".ren";
    HANDLE src = MakeSourceFile(wsrc, payload, "rename-src");
    Emit("op_start", "\"op\":\"rename\",\"src\":\"%s\"",
         JsonEscape(name).c_str());
    BOOL ok = MoveFileExW(wsrc.c_str(), wdst.c_str(), 0);
    if (!ok) {
      op_err = GetLastError();
      op_status = "rename-failed";
      Emit("op_end", "\"op\":\"rename\",\"status\":\"%s\",\"err\":%lu",
           op_status.c_str(), op_err);
    } else {
      /* The handle opened before the rename still refers to the file. */
      std::string ident = FileIdentity(src);
      Emit("op_end", "\"op\":\"rename\",\"status\":\"ok\",%s", ident.c_str());
      Emit("flush_start", "\"arm\":\"%s\"", arm.c_str());
      std::string st = CandidateFlush(arm, wdir, src);
      Emit("flush_end", "\"arm\":\"%s\",%s", arm.c_str(), st.c_str());
    }
    if (src != INVALID_HANDLE_VALUE) CloseHandle(src);
  } else if (op == "delete") {
    HANDLE target = MakeSourceFile(wpath, payload, "delete-tgt");
    Emit("op_start", "\"op\":\"delete\"");
    BOOL ok = DeleteFileW(wpath.c_str());
    if (!ok) {
      op_err = GetLastError();
      op_status = "delete-failed";
      Emit("op_end", "\"op\":\"delete\",\"status\":\"%s\",\"err\":%lu",
           op_status.c_str(), op_err);
    } else {
      std::string ident = FileIdentity(target);
      Emit("op_end", "\"op\":\"delete\",\"status\":\"ok\",%s", ident.c_str());
      Emit("flush_start", "\"arm\":\"%s\"", arm.c_str());
      std::string st = CandidateFlush(arm, wdir, target);
      Emit("flush_end", "\"arm\":\"%s\",%s", arm.c_str(), st.c_str());
    }
    if (target != INVALID_HANDLE_VALUE) CloseHandle(target);
  } else {
    op_status = "unknown-op";
    Emit("op_end", "\"op\":\"%s\",\"status\":\"unknown-op\"", op.c_str());
  }

  Emit("trial_end",
       "\"arm\":\"%s\",\"op\":\"%s\",\"status\":\"%s\",\"err\":%lu,"
       "\"final_name\":\"%s\"",
       arm.c_str(), op.c_str(), op_status.c_str(), op_err,
       JsonEscape(final_name).c_str());

  /* External evidence channel (E34-PLAN section 8).  The manifest must NOT
   * be on the tested volume -- writing it there would add an index entry to
   * the very directory tree under measurement.  Write it to the dedicated
   * E34LOG volume, flush it, then touch a small E34DONE marker that the host
   * polls on that same independent volume image. */
  {
    std::string manifest = g_event_log;
    char lv = FindManifestVolume();
    if (lv) {
      char mdir[8] = {lv, ':', '\\', 0};
      CreateDirectoryA(mdir, nullptr);
      /* The JSON event log is the ledger-grade manifest. */
      std::string mpath = std::string(1, lv) + ":\\manifest-" +
                          arm + "-" + op + "-" + name + ".jsonl";
      HANDLE mh = CreateFileA(mpath.c_str(), GENERIC_WRITE,
                              FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
                              nullptr);
      if (mh != INVALID_HANDLE_VALUE) {
        DWORD w = 0;
        WriteFile(mh, manifest.data(), (DWORD)manifest.size(), &w, nullptr);
        FlushFileBuffers(mh);
        CloseHandle(mh);
      }
      /* The marker is written LAST, after the manifest is durable, so the
       * host cutting power on seeing it cannot race the manifest write. */
      std::string dpath = std::string(1, lv) + ":\\E34DONE";
      HANDLE dh = CreateFileA(dpath.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
                              nullptr);
      if (dh != INVALID_HANDLE_VALUE) {
        const char* tag = "E34DONE";
        DWORD w = 0;
        WriteFile(dh, tag, (DWORD)strlen(tag), &w, nullptr);
        FlushFileBuffers(dh);
        CloseHandle(dh);
      }
    } else {
      /* No independent volume: emit the marker over the external channels
       * only.  The host sees nothing in that case and the trial is invalid
       * (RX), rather than silently writing evidence to the tested volume. */
      Emit("fatal", "\"reason\":\"no E34LOG manifest volume; evidence chain invalid\"");
    }
    Emit("E34DONE", "\"arm\":\"%s\",\"op\":\"%s\"", arm.c_str(),
         op.c_str());
  }

  if (!done_path.empty()) {
    /* Rehearsal mode only: the controller is told the trial is over. */
    HANDLE h = CreateFileW(Utf8ToWide(done_path).c_str(), GENERIC_WRITE,
                           FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
    Emit("rehearsal_done", "\"arm\":\"%s\"", arm.c_str());
    WaitForCrash(5);
  } else {
    Emit("awaiting_crash", "\"arm\":\"%s\"", arm.c_str());
    WaitForCrash(600);
  }

  closesocket(g_sock);
  WSACleanup();
  return 0;
}
