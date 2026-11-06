// Copyright 2026 takomitkm. BSD-style license found in the LICENSE file.

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

#include "gtest/gtest.h"
#include "leveldb/db.h"
#include "leveldb/env.h"
#include "util/testutil.h"

namespace leveldb {
namespace {

UINT TestCodePage() {
#if LEVELDB_WINDOWS_UTF8_PATHS
  return CP_UTF8;
#else
  return ::AreFileApisANSI() ? CP_ACP : CP_OEMCP;
#endif
}

std::string EncodePath(const std::wstring& path) {
  const int size = WideCharToMultiByte(TestCodePage(), 0, path.data(),
                                       static_cast<int>(path.size()), nullptr,
                                       0, nullptr, nullptr);
  if (size <= 0) return "";
  std::string result(size, '\0');
  if (WideCharToMultiByte(TestCodePage(), 0, path.data(),
                          static_cast<int>(path.size()), &result[0], size,
                          nullptr, nullptr) != size) return "";
  return result;
}

bool CanRoundTrip(const std::wstring& path) {
  const std::string encoded = EncodePath(path);
  if (encoded.empty()) return false;
  const int size = MultiByteToWideChar(TestCodePage(), 0, encoded.data(),
                                       static_cast<int>(encoded.size()),
                                       nullptr, 0);
  if (size <= 0) return false;
  std::wstring decoded(size, L'\0');
  MultiByteToWideChar(TestCodePage(), 0, encoded.data(),
                      static_cast<int>(encoded.size()), &decoded[0], size);
  return decoded == path;
}

class WindowsPathTest : public testing::Test {
 protected:
  void SetUp() override {
    wchar_t temp[MAX_PATH];
    const DWORD size = GetTempPathW(MAX_PATH, temp);
    ASSERT_GT(size, 0u);
    ASSERT_LT(size, static_cast<DWORD>(MAX_PATH));
#if LEVELDB_WINDOWS_UTF8_PATHS
    const std::wstring suffix = L"\u4e2d\u6587";
#else
    std::wstring suffix;
    for (const wchar_t* candidate : {L"\u00e9", L"\u4e2d", L"\u0416",
                                     L"\u3042"}) {
      if (CanRoundTrip(candidate)) {
        suffix = candidate;
        break;
      }
    }
    ASSERT_FALSE(suffix.empty()) << "No representable non-ASCII test character";
#endif
    filename_ = L"renamed-" + suffix;
    wide_ = std::wstring(temp) + L"leveldb-path-" +
            std::to_wstring(GetCurrentProcessId()) + L"-" + suffix;
    path_ = EncodePath(wide_);
    ASSERT_TRUE(CanRoundTrip(wide_));
    ASSERT_TRUE(CreateDirectoryW(wide_.c_str(), nullptr));
  }

  void TearDown() override {
    if (wide_.empty()) return;
    WIN32_FIND_DATAW entry;
    const HANDLE find = FindFirstFileW((wide_ + L"\\*").c_str(), &entry);
    if (find != INVALID_HANDLE_VALUE) {
      do {
        if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
          DeleteFileW((wide_ + L"\\" + entry.cFileName).c_str());
        }
      } while (FindNextFileW(find, &entry));
      FindClose(find);
    }
    RemoveDirectoryW(wide_.c_str());
  }

  Env* env_ = Env::Default();
  std::wstring wide_;
  std::wstring filename_;
  std::string path_;
};

TEST_F(WindowsPathTest, FilesAndLoggerUseSelectedEncoding) {
  const std::string filename = path_ + "/source";
  WritableFile* raw_file = nullptr;
  ASSERT_LEVELDB_OK(env_->NewWritableFile(filename, &raw_file));
  std::unique_ptr<WritableFile> file(raw_file);
  ASSERT_LEVELDB_OK(file->Append("value"));
  ASSERT_LEVELDB_OK(file->Sync());
  ASSERT_LEVELDB_OK(file->Close());
  file.reset();
  EXPECT_NE(INVALID_FILE_ATTRIBUTES,
            GetFileAttributesW((wide_ + L"\\source").c_str()));

  const std::string basename = EncodePath(filename_);
  const std::string renamed = path_ + "/" + basename;
  ASSERT_LEVELDB_OK(env_->RenameFile(filename, renamed));
  std::string contents;
  ASSERT_LEVELDB_OK(ReadFileToString(env_, renamed, &contents));
  EXPECT_EQ("value", contents);
  std::vector<std::string> children;
  ASSERT_LEVELDB_OK(env_->GetChildren(path_, &children));
  EXPECT_NE(children.end(),
            std::find(children.begin(), children.end(), basename));
  EXPECT_NE(INVALID_FILE_ATTRIBUTES,
            GetFileAttributesW((wide_ + L"\\" + filename_).c_str()));

  Logger* raw_logger = nullptr;
  ASSERT_LEVELDB_OK(env_->NewLogger(path_ + "/LOG", &raw_logger));
  std::unique_ptr<Logger> logger(raw_logger);
  Log(logger.get(), "%s", "non-ASCII path");
  logger.reset();
  ASSERT_LEVELDB_OK(ReadFileToString(env_, path_ + "/LOG", &contents));
  EXPECT_NE(std::string::npos, contents.find("non-ASCII path"));
  EXPECT_NE(INVALID_FILE_ATTRIBUTES,
            GetFileAttributesW((wide_ + L"\\LOG").c_str()));
}

TEST_F(WindowsPathTest, DatabaseReopensInSelectedEncoding) {
  Options options;
  options.create_if_missing = true;
  DB* raw_db = nullptr;
  ASSERT_LEVELDB_OK(DB::Open(options, path_, &raw_db));
  std::unique_ptr<DB> db(raw_db);
  const std::string value("binary\0value", 12);
  ASSERT_LEVELDB_OK(db->Put(WriteOptions(), "key", value));
  db.reset();
  EXPECT_NE(INVALID_FILE_ATTRIBUTES,
            GetFileAttributesW((wide_ + L"\\CURRENT").c_str()));
  EXPECT_NE(INVALID_FILE_ATTRIBUTES,
            GetFileAttributesW((wide_ + L"\\LOG").c_str()));

  options.create_if_missing = false;
  ASSERT_LEVELDB_OK(DB::Open(options, path_, &raw_db));
  db.reset(raw_db);
  std::string actual;
  ASSERT_LEVELDB_OK(db->Get(ReadOptions(), "key", &actual));
  EXPECT_EQ(value, actual);
}

TEST_F(WindowsPathTest, TestDirectoryUsesSelectedEncoding) {
  const wchar_t* previous = _wgetenv(L"TEST_TMPDIR");
  const bool had_previous = previous != nullptr;
  const std::wstring saved = previous ? previous : L"";
  ASSERT_EQ(0, _wputenv_s(L"TEST_TMPDIR", wide_.c_str()));
  std::string actual;
  const Status status = env_->GetTestDirectory(&actual);
  const int restored = _wputenv_s(L"TEST_TMPDIR", had_previous ? saved.c_str() : L"");
  ASSERT_EQ(0, restored);
  ASSERT_LEVELDB_OK(status);
  EXPECT_EQ(path_, actual);
}

}  // namespace
}  // namespace leveldb

int main(int argc, char** argv) {
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
