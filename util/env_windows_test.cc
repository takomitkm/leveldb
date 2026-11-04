// Copyright (c) 2018 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

// Prevent Windows headers from defining min/max macros and instead
// use STL (mirrors the include guard in util/env_windows.cc).
#ifndef NOMINMAX
#define NOMINMAX
#endif  // ifndef NOMINMAX
#include <windows.h>

#include <string>
#include <vector>

#include "gtest/gtest.h"
#include "leveldb/env.h"
#include "port/port.h"
#include "util/env_windows_test_helper.h"
#include "util/testutil.h"

namespace leveldb {

static const int kMMapLimit = 4;

class EnvWindowsTest : public testing::Test {
 public:
  static void SetFileLimits(int mmap_limit) {
    EnvWindowsTestHelper::SetReadOnlyMMapLimit(mmap_limit);
  }

  EnvWindowsTest() : env_(Env::Default()) {}

  Env* env_;
};

TEST_F(EnvWindowsTest, TestOpenOnRead) {
  // Write some test data to a single file that will be opened |n| times.
  std::string test_dir;
  ASSERT_LEVELDB_OK(env_->GetTestDirectory(&test_dir));
  std::string test_file = test_dir + "/open_on_read.txt";

  FILE* f = std::fopen(test_file.c_str(), "w");
  ASSERT_TRUE(f != nullptr);
  const char kFileData[] = "abcdefghijklmnopqrstuvwxyz";
  fputs(kFileData, f);
  std::fclose(f);

  // Open test file some number above the sum of the two limits to force
  // leveldb::WindowsEnv to switch from mapping the file into memory
  // to basic file reading.
  const int kNumFiles = kMMapLimit + 5;
  leveldb::RandomAccessFile* files[kNumFiles] = {0};
  for (int i = 0; i < kNumFiles; i++) {
    ASSERT_LEVELDB_OK(env_->NewRandomAccessFile(test_file, &files[i]));
  }
  char scratch;
  Slice read_result;
  for (int i = 0; i < kNumFiles; i++) {
    ASSERT_LEVELDB_OK(files[i]->Read(i, 1, &read_result, &scratch));
    ASSERT_EQ(kFileData[i], read_result[0]);
  }
  for (int i = 0; i < kNumFiles; i++) {
    delete files[i];
  }
  ASSERT_LEVELDB_OK(env_->RemoveFile(test_file));
}

// RenameFile must atomically replace an existing target. The previous
// MoveFile/ReplaceFile sequence failed with ERROR_UNABLE_TO_REMOVE_REPLACED
// in that case (google/leveldb#775), and did not make the rename durable
// before returning (google/leveldb#195).
TEST_F(EnvWindowsTest, TestRenameFileReplacesExisting) {
  std::string test_dir;
  ASSERT_LEVELDB_OK(env_->GetTestDirectory(&test_dir));
  std::string from = test_dir + "/rename_from.txt";
  std::string to = test_dir + "/rename_to.txt";

  {
    WritableFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewWritableFile(from, &f));
    ASSERT_LEVELDB_OK(f->Append("new-content"));
    ASSERT_LEVELDB_OK(f->Close());
    delete f;
  }
  {
    WritableFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewWritableFile(to, &f));
    ASSERT_LEVELDB_OK(f->Append("old-content"));
    ASSERT_LEVELDB_OK(f->Close());
    delete f;
  }

  // Target exists: the rename must replace it in a single step.
  ASSERT_LEVELDB_OK(env_->RenameFile(from, to));
  ASSERT_FALSE(env_->FileExists(from));

  {
    SequentialFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewSequentialFile(to, &f));
    char buf[32];
    Slice result;
    ASSERT_LEVELDB_OK(f->Read(sizeof(buf) - 1, &result, buf));
    ASSERT_EQ("new-content", result.ToString());
    delete f;
  }

  // Renaming to a non-existent target must work too.
  std::string to2 = test_dir + "/rename_to2.txt";
  ASSERT_LEVELDB_OK(env_->RenameFile(to, to2));
  ASSERT_FALSE(env_->FileExists(to));
  ASSERT_LEVELDB_OK(env_->RemoveFile(to2));
}

// Converts a UTF-8 path to a wide-char path the same way the WindowsEnv
// implementation does, so the test probes the file system exactly through
// the encoding path the production code uses.
static std::wstring Utf8ToWidePath(const std::string& path) {
  if (path.empty()) {
    return std::wstring();
  }
  const int path_len = static_cast<int>(path.size());
  const int wide_len =
      ::MultiByteToWideChar(/*CodePage=*/CP_UTF8, /*dwFlags=*/0,
                            path.c_str(), path_len, nullptr, 0);
  if (wide_len <= 0) {
    return std::wstring();
  }
  std::wstring wide_path(static_cast<size_t>(wide_len), L'\0');
  ::MultiByteToWideChar(/*CodePage=*/CP_UTF8, /*dwFlags=*/0,
                        path.c_str(), path_len, &wide_path[0], wide_len);
  return wide_path;
}

// Opens |path| with an independent handle created directly with CreateFileW
// (never through Env), reads the whole file, and closes the handle. Going
// around Env means the read cannot be satisfied by any state the environment
// may hold in-process; it must come from the file system namespace itself.
// The handle is opened with full sharing so the probe never perturbs the
// file for later checks.
static ::testing::AssertionResult ReadViaIndependentHandle(
    const std::string& path, std::string* contents) {
  const std::wstring wide_path = Utf8ToWidePath(path);
  const DWORD kShareAll =
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
  ::HANDLE handle = ::CreateFileW(
      wide_path.c_str(), GENERIC_READ, kShareAll,
      /*lpSecurityAttributes=*/nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
      /*hTemplateFile=*/nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return ::testing::AssertionFailure()
           << "independent CreateFileW(" << path << ") failed, error "
           << ::GetLastError();
  }
  contents->clear();
  char buffer[256];
  for (;;) {
    DWORD bytes_read = 0;
    if (!::ReadFile(handle, buffer, sizeof(buffer), &bytes_read, nullptr)) {
      const DWORD read_error = ::GetLastError();
      ::CloseHandle(handle);
      return ::testing::AssertionFailure()
             << "ReadFile(" << path << ") failed, error " << read_error;
    }
    if (bytes_read == 0) {
      break;
    }
    contents->append(buffer, static_cast<size_t>(bytes_read));
  }
  ::CloseHandle(handle);
  return ::testing::AssertionSuccess();
}

// Enumerates |directory| with an independent FindFirstFileW loop, returning
// the entry names. This walks the directory index itself instead of asking
// for one file by name, so it is a different code path from GetFileAttributes
// or CreateFile. Only the test's own ASCII file names are compared, so the
// per-character static_cast below is lossless for the names it checks.
static ::testing::AssertionResult ListDirViaFindFirstFile(
    const std::string& directory, std::vector<std::string>* names) {
  const std::wstring pattern = Utf8ToWidePath(directory + "/*");
  ::WIN32_FIND_DATAW find_data;
  ::HANDLE dir_handle = ::FindFirstFileW(pattern.c_str(), &find_data);
  if (dir_handle == INVALID_HANDLE_VALUE) {
    return ::testing::AssertionFailure()
           << "FindFirstFileW(" << directory << "/*) failed, error "
           << ::GetLastError();
  }
  names->clear();
  ::DWORD last_error = ERROR_SUCCESS;
  for (;;) {
    const std::wstring wide_name(find_data.cFileName);
    std::string name;
    name.reserve(wide_name.size());
    for (size_t i = 0; i < wide_name.size(); ++i) {
      name.push_back(static_cast<char>(wide_name[i]));
    }
    names->push_back(name);
    if (!::FindNextFileW(dir_handle, &find_data)) {
      last_error = ::GetLastError();
      break;
    }
  }
  ::FindClose(dir_handle);
  if (last_error != ERROR_NO_MORE_FILES) {
    return ::testing::AssertionFailure()
           << "FindNextFileW(" << directory << "/*) failed, error "
           << last_error;
  }
  return ::testing::AssertionSuccess();
}

// RenameFile must return only after the new name is fully visible in the
// file system namespace, and must replace an existing target in one atomic
// MOVEFILE_REPLACE_EXISTING step. The MOVEFILE_WRITE_THROUGH flag further
// guarantees the rename is durable before returning, so a later
// DeleteObsoleteFiles unlink cannot be persisted ahead of it
// (google/leveldb#195). That durability contract itself is not observable
// from inside the process (a unit test cannot stage a controlled power
// loss), so this test pins down the observable side effects instead:
//   a) immediately after RenameFile returns, a fresh handle opened directly
//      with CreateFileW (not through Env) reads the new contents, and the
//      metadata-only path (GetFileAttributesW) agrees the name exists;
//   b) an existing target whose Env handle has been closed beforehand is
//      replaced, and both an Env handle and an independent handle read the
//      new contents afterwards (the google/leveldb#775 replace path);
//   c) the new name is visible to an independent FindFirstFileW enumeration
//      of the parent directory, and the old name is gone from it.
TEST_F(EnvWindowsTest, TestRenameFileDurableSemantics) {
  std::string test_dir;
  ASSERT_LEVELDB_OK(env_->GetTestDirectory(&test_dir));
  const char kFromName[] = "rename_durable_from.txt";
  const char kToName[] = "rename_durable_to.txt";
  std::string from = test_dir + "/" + kFromName;
  std::string to = test_dir + "/" + kToName;
  std::string missing = test_dir + "/rename_durable_missing.txt";
  // Ignore errors: start from a clean slate if a previous run left files.
  env_->RemoveFile(from);
  env_->RemoveFile(to);

  {
    WritableFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewWritableFile(from, &f));
    ASSERT_LEVELDB_OK(f->Append("new-content"));
    ASSERT_LEVELDB_OK(f->Close());
    delete f;
  }
  {
    WritableFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewWritableFile(to, &f));
    ASSERT_LEVELDB_OK(f->Append("old-content"));
    ASSERT_LEVELDB_OK(f->Close());
    delete f;
  }

  // (b precondition) The target is readable through Env until now. Windows
  // refuses to replace a file while any handle is open on it, so close the
  // handle first, exactly like the DB lifecycle (reader closes, then
  // compaction/LogAndApply renames over the file).
  {
    SequentialFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewSequentialFile(to, &f));
    char buf[32];
    Slice result;
    ASSERT_LEVELDB_OK(f->Read(sizeof(buf) - 1, &result, buf));
    ASSERT_EQ("old-content", result.ToString());
    delete f;
  }

  // Target exists: one atomic MOVEFILE_REPLACE_EXISTING step, durable
  // before returning (google/leveldb#195, google/leveldb#775).
  ASSERT_LEVELDB_OK(env_->RenameFile(from, to));
  ASSERT_FALSE(env_->FileExists(from));

  // (a) A fresh, independent handle must see the new contents right away,
  // and the metadata-only query must agree that the name exists.
  std::string contents;
  ASSERT_TRUE(ReadViaIndependentHandle(to, &contents));
  ASSERT_EQ("new-content", contents);
  const std::wstring wide_to = Utf8ToWidePath(to);
  ASSERT_NE(INVALID_FILE_ATTRIBUTES, ::GetFileAttributesW(wide_to.c_str()));

  // (b) After the replace, both an Env handle and a second independent
  // handle read the new contents.
  {
    SequentialFile* f = nullptr;
    ASSERT_LEVELDB_OK(env_->NewSequentialFile(to, &f));
    char buf[32];
    Slice result;
    ASSERT_LEVELDB_OK(f->Read(sizeof(buf) - 1, &result, buf));
    ASSERT_EQ("new-content", result.ToString());
    delete f;
  }
  std::string contents_again;
  ASSERT_TRUE(ReadViaIndependentHandle(to, &contents_again));
  ASSERT_EQ("new-content", contents_again);

  // (c) The new name is visible to an independent directory enumeration,
  // and the old name is gone from it.
  std::vector<std::string> names;
  ASSERT_TRUE(ListDirViaFindFirstFile(test_dir, &names));
  bool seen_to = false;
  bool seen_from = false;
  for (size_t i = 0; i < names.size(); ++i) {
    if (names[i] == kToName) {
      seen_to = true;
    }
    if (names[i] == kFromName) {
      seen_from = true;
    }
  }
  ASSERT_TRUE(seen_to);
  ASSERT_FALSE(seen_from);

  // Error-path quality: renaming a source that does not exist must report
  // failure instead of silently succeeding.
  ASSERT_FALSE(env_->RenameFile(missing, to).ok());

  ASSERT_LEVELDB_OK(env_->RemoveFile(to));
}

static void LogToLogger(Logger* logger, const char* format, ...) {
  std::va_list arguments;
  va_start(arguments, format);
  logger->Logv(format, arguments);
  va_end(arguments);
}

// Sync() on a MANIFEST file must flush the directory entry first, like the
// POSIX side's SyncDirIfManifest(): the directory is opened with
// GENERIC_WRITE | FILE_FLAG_BACKUP_SEMANTICS and flushed with
// FlushFileBuffers(). Non-manifest files take the plain path.
TEST_F(EnvWindowsTest, ManifestSyncFlushesDirectory) {
  std::string test_dir;
  ASSERT_LEVELDB_OK(env_->GetTestDirectory(&test_dir));

  std::string manifest = test_dir + "/MANIFEST-000999";
  WritableFile* f = nullptr;
  ASSERT_LEVELDB_OK(env_->NewWritableFile(manifest, &f));
  ASSERT_LEVELDB_OK(f->Append("manifest-data"));
  ASSERT_LEVELDB_OK(f->Sync());  // exercises SyncDirIfManifest()
  ASSERT_LEVELDB_OK(f->Append("more"));
  ASSERT_LEVELDB_OK(f->Sync());
  ASSERT_LEVELDB_OK(f->Close());
  delete f;
  ASSERT_LEVELDB_OK(env_->RemoveFile(manifest));

  std::string log = test_dir + "/000999.log";
  ASSERT_LEVELDB_OK(env_->NewWritableFile(log, &f));
  ASSERT_LEVELDB_OK(f->Append("log-data"));
  ASSERT_LEVELDB_OK(f->Sync());
  ASSERT_LEVELDB_OK(f->Close());
  delete f;
  ASSERT_LEVELDB_OK(env_->RemoveFile(log));
}

// The LOG file must survive being re-opened: the POSIX logger opens with
// O_APPEND, while the Windows logger used fopen("wN"), which truncated the
// log at every open and destroyed the previous run's diagnostics.
TEST_F(EnvWindowsTest, LoggerAppendsAcrossOpens) {
  std::string test_dir;
  ASSERT_LEVELDB_OK(env_->GetTestDirectory(&test_dir));
  std::string log_file = test_dir + "/append_logger_test.log";
  env_->RemoveFile(log_file);  // Start from a clean slate if one is left over.

  Logger* logger = nullptr;
  ASSERT_LEVELDB_OK(env_->NewLogger(log_file, &logger));
  LogToLogger(logger, "%s", "first open");
  delete logger;

  ASSERT_LEVELDB_OK(env_->NewLogger(log_file, &logger));
  LogToLogger(logger, "%s", "second open");
  delete logger;

  std::string contents;
  ASSERT_LEVELDB_OK(ReadFileToString(env_, log_file, &contents));
  EXPECT_NE(std::string::npos, contents.find("first open"));
  EXPECT_NE(std::string::npos, contents.find("second open"));
  ASSERT_LEVELDB_OK(env_->RemoveFile(log_file));
}

}  // namespace leveldb

int main(int argc, char** argv) {
  // All tests currently run with the same read-only file limits.
  leveldb::EnvWindowsTest::SetFileLimits(leveldb::kMMapLimit);
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
