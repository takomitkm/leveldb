/* E19 — Linux (ntfs-3g) on NTFS: does fsync of the parent directory push
 * the namespace change to the device?  E11's protocol re-targeted at an
 * ntfs-3g volume, inside the Debian CI guest; the host kills QEMU right
 * after the probe exits (power cut), blklogwrites records the block log,
 * autopsy = ntfs-3g ro,norecover vs rw replay.
 *
 * usage: e19lin <mount> <arm> <rep>
 *   arm 0: create + write 2048B + fsync(file)            (file flush only)
 *   arm 1: arm 0 + fsync(dirfd)                          (E11 sync protocol)
 *   arm 2: create + write + close, no flush              (teeth control)
 * A pre-state marker file is fsynced before the trial so the mount-time
 * metadata writes are bounded away from the trial writes in the log.
 */
#include <fcntl.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void pre_state(const char *mnt, int arm) {
  char p[512];
  snprintf(p, sizeof p, "%s/e19/pre_%d.bin", mnt, arm);
  int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return;
  char buf[2048];
  memset(buf, 'P', sizeof buf);
  int n = snprintf(buf, sizeof buf, "E19PRE arm=%d", arm);
  memcpy(buf, "E19PRE", n > 6 ? 6 : n);
  ssize_t w = write(fd, buf, sizeof buf);
  (void)w;
  fsync(fd);
  close(fd);
}

int main(int argc, char **argv) {
  if (argc < 4) return 1;
  const char *mnt = argv[1];
  int arm = atoi(argv[2]);
  int rep = atoi(argv[3]);
  char dir[512], path[560], body[64];
  snprintf(dir, sizeof dir, "%s/e19", mnt);
  mkdir(dir, 0755);
  pre_state(mnt, arm);

  snprintf(path, sizeof path, "%s/a%d_r%d.bin", dir, arm, rep);
  snprintf(body, sizeof body, "e19-a%d-r%d", arm, rep);
  int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return 2;
  char buf[2048];
  memset(buf, 'P', sizeof buf);
  size_t cl = strlen(body);
  memcpy(buf, body, cl);
  buf[cl] = '\n';
  if (write(fd, buf, sizeof buf) != sizeof buf) return 3;
  if (arm == 0 || arm == 1) {
    if (fsync(fd) != 0) return 4;          /* file flush: data + file metadata */
  }
  close(fd);
  if (arm == 1) {
    int d = open(dir, O_RDONLY | O_DIRECTORY);   /* ntfs-3g directory fd */
    if (d < 0) return 5;
    if (fsync(d) != 0) return 6;           /* THE question: parent-dir fsync */
    close(d);
  }
  /* arm 2: no flush at all (teeth) */
  return 0;
}
