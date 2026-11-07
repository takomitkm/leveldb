// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#include "db/builder.h"

#include <string>
#include <utility>
#include <vector>

#include "db/dbformat.h"
#include "db/filename.h"
#include "db/table_cache.h"
#include "db/version_edit.h"
#include "gtest/gtest.h"
#include "leveldb/db.h"
#include "leveldb/env.h"
#include "leveldb/options.h"
#include "leveldb/slice.h"
#include "util/testutil.h"

namespace leveldb {

namespace {

// An Iterator over a fixed list of key/value pairs, with an optional invalid
// (malformed) internal key.  Backed by owned strings so the Slices handed to
// BuildTable stay valid for the duration of the call.
class ListIterator : public Iterator {
 public:
  ListIterator() : index_(0) {}

  void Add(const std::string& key, const std::string& value) {
    keys_.push_back(key);
    values_.push_back(value);
  }

  bool Valid() const override { return index_ < keys_.size(); }
  void SeekToFirst() override { index_ = 0; }
  void SeekToLast() override {
    index_ = keys_.empty() ? 0 : keys_.size() - 1;
  }
  void Seek(const Slice& target) override {
    index_ = 0;
    while (Valid() && keys_[index_] < target.ToString()) {
      ++index_;
    }
  }
  void Next() override { ++index_; }
  void Prev() override {
    if (index_ == 0) {
      index_ = keys_.size();  // Invalid
    } else {
      --index_;
    }
  }
  Slice key() const override { return Slice(keys_[index_]); }
  Slice value() const override { return Slice(values_[index_]); }
  Status status() const override { return Status::OK(); }

 private:
  std::vector<std::string> keys_;
  std::vector<std::string> values_;
  size_t index_;
};

std::string MakeInternalKey(const char* user_key, SequenceNumber sequence,
                            ValueType type) {
  InternalKey key(user_key, sequence, type);
  return key.Encode().ToString();
}

// A physically complete key whose value type (2) is out of range: it parses
// as "at least 8 bytes" but fails ParseInternalKey.
std::string MakeInvalidKey(const char* user_key) {
  std::string key(user_key);
  uint64_t tag = (static_cast<uint64_t>(1) << 8) | 0x02;
  key.append(reinterpret_cast<const char*>(&tag), sizeof(tag));
  return key;
}

class BuilderTest : public testing::Test {
 public:
  BuilderTest() : env_(NewMemEnv(Env::Default())) {}
  ~BuilderTest() override { delete env_; }

  Status CallBuildTable(Iterator* input, FileMetaData* meta) {
    Options options;
    options.env = env_;
    TableCache table_cache(dbname_, options, /*entries=*/10);
    return BuildTable(dbname_, env_, options, &table_cache, input, meta);
  }

  bool TableFileExists(uint64_t number) {
    uint64_t size = 0;
    return env_->GetFileSize(TableFileName(dbname_, number), &size).ok();
  }

  const std::string dbname_ = "/memenv/builder_test";
  Env* env_;
};

TEST_F(BuilderTest, BuildsTableFromValidKeys) {
  ListIterator input;
  input.Add(MakeInternalKey("a", 1, kTypeValue), "value-a");
  input.Add(MakeInternalKey("b", 2, kTypeValue), "value-b");

  FileMetaData meta;
  meta.number = 7;
  Status s = CallBuildTable(&input, &meta);

  ASSERT_LEVELDB_OK(s);
  EXPECT_GT(meta.file_size, 0);
  EXPECT_TRUE(TableFileExists(meta.number));
  EXPECT_EQ(meta.smallest.user_key(), Slice("a"));
  EXPECT_EQ(meta.largest.user_key(), Slice("b"));
}

TEST_F(BuilderTest, RejectsInvalidFirstKeyWithoutCreatingFile) {
  ListIterator input;
  input.Add(MakeInvalidKey("a"), "value-a");
  input.Add(MakeInternalKey("b", 2, kTypeValue), "value-b");

  FileMetaData meta;
  meta.number = 7;
  Status s = CallBuildTable(&input, &meta);

  ASSERT_TRUE(s.IsCorruption()) << s.ToString();
  EXPECT_EQ(meta.file_size, 0);
  EXPECT_FALSE(TableFileExists(meta.number));
}

TEST_F(BuilderTest, RejectsInvalidInteriorKeyAndRemovesPartialFile) {
  ListIterator input;
  input.Add(MakeInternalKey("a", 1, kTypeValue), "value-a");
  input.Add(MakeInvalidKey("b"), "value-b");
  input.Add(MakeInternalKey("c", 3, kTypeValue), "value-c");

  FileMetaData meta;
  meta.number = 7;
  Status s = CallBuildTable(&input, &meta);

  ASSERT_TRUE(s.IsCorruption()) << s.ToString();
  EXPECT_EQ(meta.file_size, 0);
  EXPECT_FALSE(TableFileExists(meta.number));
  // The last valid key before the failure must not leak into the metadata.
  EXPECT_EQ(meta.largest.user_key(), Slice(""));
}

}  // namespace
}  // namespace leveldb
