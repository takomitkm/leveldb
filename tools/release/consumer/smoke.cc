// Public API consumer, deliberately independent of LevelDB's private headers.
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <typeinfo>
#include "leveldb/db.h"
#include "leveldb/env.h"

namespace {
void Check(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}
void CheckStatus(const leveldb::Status& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}
#ifdef _WIN32
UINT CodePage() {
#if RELEASE_UTF8_PATHS
  return CP_UTF8;
#else
  return AreFileApisANSI() ? CP_ACP : CP_OEMCP;
#endif
}
std::string Encode(const std::wstring& path) {
  const int size = WideCharToMultiByte(CodePage(), 0, path.data(),
      static_cast<int>(path.size()), nullptr, 0, nullptr, nullptr);
  Check(size > 0, "WideCharToMultiByte size failed");
  std::string encoded(size, '\0');
  Check(WideCharToMultiByte(CodePage(), 0, path.data(),
      static_cast<int>(path.size()), &encoded[0], size, nullptr, nullptr) == size,
      "WideCharToMultiByte failed");
  const int wide_size = MultiByteToWideChar(CodePage(), 0, encoded.data(), size,
                                           nullptr, 0);
  if (wide_size <= 0) return "";
  std::wstring decoded(wide_size, L'\0');
  Check(MultiByteToWideChar(CodePage(), 0, encoded.data(), size, &decoded[0],
                           wide_size) == wide_size, "MultiByteToWideChar failed");
  return decoded == path ? encoded : "";
}
std::wstring WidePath() {
  wchar_t temp[32768];
  const DWORD length = GetTempPathW(32768, temp);
  Check(length > 0 && length < 32768, "GetTempPathW failed");
#if RELEASE_UTF8_PATHS
  const std::wstring suffix = L"\u4e2d\u6587-\u00e9";
#else
  std::wstring suffix;
  for (const wchar_t* candidate : {L"\u00e9", L"\u4e2d", L"\u0416", L"\u3042"}) {
    if (!Encode(candidate).empty()) { suffix = candidate; break; }
  }
  Check(!suffix.empty(), "ACP has no representable non-ASCII test character");
#endif
  return std::wstring(temp) + L"leveldb-release-" +
         std::to_wstring(GetCurrentProcessId()) + L"-" + suffix;
}
void CheckLog(const std::wstring& path) {
  const DWORD attributes = GetFileAttributesW((path + L"\\LOG").c_str());
  Check(attributes != INVALID_FILE_ATTRIBUTES &&
        !(attributes & FILE_ATTRIBUTE_DIRECTORY),
        "Actual LOG file is absent at the intended non-ASCII Windows path");
}
#endif
}  // namespace

int main(int argc, char** argv) {
  try {
    Check(std::string(typeid(leveldb::WritableFile).name()).size() != 0,
          "LevelDB public base-class RTTI is unavailable");
#ifdef _WIN32
    const std::wstring wide = WidePath();
    const std::string path = Encode(wide);
    Check(!path.empty(), "Path cannot round-trip in the selected encoding");
#else
    Check(argc == 2, "Expected a temporary database path");
    const std::string path = argv[1];
#endif
    leveldb::Options options;
    CheckStatus(leveldb::DestroyDB(path, options));
    options.create_if_missing = true;
    leveldb::DB* raw = nullptr;
    CheckStatus(leveldb::DB::Open(options, path, &raw));
    std::unique_ptr<leveldb::DB> db(raw);
    const std::string value("binary\0value", 12);
    leveldb::WriteOptions write;
    write.sync = true;
    CheckStatus(db->Put(write, "release-key", value));
    db.reset();
#ifdef _WIN32
    CheckLog(wide);
#endif
    options.create_if_missing = false;
    CheckStatus(leveldb::DB::Open(options, path, &raw));
    db.reset(raw);
    std::string read;
    CheckStatus(db->Get(leveldb::ReadOptions(), "release-key", &read));
    Check(read == value, "Reopened database value differs");
    db.reset();
#ifdef _WIN32
    CheckLog(wide);
#endif
    CheckStatus(leveldb::DestroyDB(path, options));
#ifdef _WIN32
    Check(GetFileAttributesW(wide.c_str()) == INVALID_FILE_ATTRIBUTES,
          "Non-ASCII database directory was not cleaned up");
#else
    Check(!leveldb::Env::Default()->FileExists(path), "Database was not cleaned up");
#endif
    std::cout << "release consumer: PASS (write/reopen/read/cleanup; path and LOG)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "release consumer: FAIL: " << error.what() << '\n';
    return 1;
  }
}
