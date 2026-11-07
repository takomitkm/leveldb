// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#include "db/builder.h"

#include "db/dbformat.h"
#include "db/filename.h"
#include "db/table_cache.h"
#include "db/version_edit.h"
#include "leveldb/db.h"
#include "leveldb/env.h"
#include "leveldb/iterator.h"

namespace leveldb {

Status BuildTable(const std::string& dbname, Env* env, const Options& options,
                  TableCache* table_cache, Iterator* iter, FileMetaData* meta) {
  Status s;
  meta->file_size = 0;
  iter->SeekToFirst();

  std::string fname = TableFileName(dbname, meta->number);
  if (iter->Valid()) {
    // Validate the first key before creating anything: a malformed key must
    // not become table metadata, and there is no file or builder to clean up
    // yet.
    if (!meta->smallest.DecodeFrom(iter->key())) {
      return Status::Corruption("invalid internal key in BuildTable input");
    }

    WritableFile* file;
    s = env->NewWritableFile(fname, &file);
    if (!s.ok()) {
      return s;
    }

    TableBuilder* builder = new TableBuilder(options, file);
    Slice key;
    ParsedInternalKey parsed;
    for (; iter->Valid(); iter->Next()) {
      key = iter->key();
      if (!ParseInternalKey(key, &parsed)) {
        // A malformed key inside the input must not reach the table.  Stop
        // and fall through to the shared cleanup below, which abandons the
        // builder and removes the partial file.
        s = Status::Corruption("invalid internal key in BuildTable input");
        break;
      }
      builder->Add(key, iter->value());
    }

    if (s.ok()) {
      if (!key.empty()) {
        meta->largest.DecodeFrom(key);
      }
      // Finish and check for builder errors
      s = builder->Finish();
      if (s.ok()) {
        meta->file_size = builder->FileSize();
        assert(meta->file_size > 0);
      }
    } else {
      // The destructor requires Finish() or Abandon() to have been called;
      // on the error path Abandon() is the one to use (Finish() would write
      // more data).  Closing the file is best-effort: `s` already carries
      // the error the caller needs to see.
      builder->Abandon();
      file->Close();
    }
    delete builder;

    // Finish and check for file errors
    if (s.ok()) {
      s = file->Sync();
    }
    if (s.ok()) {
      s = file->Close();
    }
    delete file;
    file = nullptr;

    if (s.ok()) {
      // Verify that the table is usable
      Iterator* it = table_cache->NewIterator(ReadOptions(), meta->number,
                                              meta->file_size);
      s = it->status();
      delete it;
    }
  }

  // Check for input iterator errors
  if (!iter->status().ok()) {
    s = iter->status();
  }

  if (s.ok() && meta->file_size > 0) {
    // Keep it
  } else {
    env->RemoveFile(fname);
  }
  return s;
}

}  // namespace leveldb
