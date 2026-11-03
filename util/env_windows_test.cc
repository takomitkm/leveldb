// Copyright (c) 2018 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

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

static void LogToLogger(Logger* logger, const char* format, ...) {
  std::va_list arguments;
  va_start(arguments, format);
  logger->Logv(format, arguments);
  va_end(arguments);
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
