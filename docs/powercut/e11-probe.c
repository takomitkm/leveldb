#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* usage: probe <dir> <arm:sync|nosync> <trial-id>
 * Creates from/to (both fsynced, durable), renames from->to, then
 * optionally fsyncs the directory. Returns 0 on success. The caller
 * kills the VM shortly after this exits; durability is checked after
 * the next boot. */
int main(int argc, char** argv) {
  if (argc < 4) return 1;
  const char* dir = argv[1];
  const char* arm = argv[2];
  int t = atoi(argv[3]);
  char from[512], to[512];
  snprintf(from, sizeof from, "%s/t_%s_%d.from", dir, arm, t);
  snprintf(to, sizeof to, "%s/t_%s_%d.to", dir, arm, t);
  unlink(from);
  unlink(to);
  int fd = open(from, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return 2;
  if (write(fd, "renamed-content", 15) != 15) return 3;
  fsync(fd);
  close(fd);
  fd = open(to, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return 2;
  if (write(fd, "old", 3) != 3) return 3;
  fsync(fd);
  close(fd);
  if (rename(from, to) != 0) return 4;
  if (strcmp(arm, "sync") == 0) {
    int d = open(dir, O_RDONLY | O_DIRECTORY);
    if (d < 0) return 5;
    fsync(d);
    close(d);
  }
  return 0;
}
