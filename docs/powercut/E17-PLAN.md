# E17 — 断电模型复用：按各家 DB 修法逐家验证（2026-11-16 准备稿）

## 0. 三代 rig 的归档位置

| 内容 | 位置 | 状态 |
|---|---|---|
| **E14/E15 Windows 断电 rig（主资产）** | 本目录（探针 `e14pe3.cpp`、分析器 `e14_analyze.py`/`e14v3_verdict.py`、runner `e14v3-run.sh`、launch `e14v3-launch.ps1`、ISO 重建 `e14v3_gbld.sh`、验尸 `e14v3-autopsy.sh`、结果报告 `E14-RESULTS.md`） | 完整，已入仓 |
| QEMU 镜像产物 | 宿主 `<TASKDIR>\`（e14v3-{base,vm,log}-a*.img、12 张 crash 镜像、探针 exe、ISO） | 大文件，留在宿主 |
| **E11 Linux 断电模型（5/5 vs 8/8 出处）** | 本目录 `e11-probe.c` + `qemu_e11.sh` + `e11_v2.sh`（结果记录见原工作区 EXEC-LOG.md：sync 5/5 SURVIVED / nosync 8/8 LOST） | 完整，已入仓 |

**⚠️ 你转述的那段话有一处归属错误（重要）**：「5/5 有目录同步存活 / 8/8 无同步丢失」**不是 Windows 测试的结果，是 Linux QEMU 断电模型的结果**（E11，ext4，`e11-probe.c`：rename→fsync(dir) vs 不 fsync，杀 QEMU 重启验尸）。这一点 2026-11-14 已经在 SQLite 论坛纠错流程里实锤过一次（本地核查稿 sqlite-post-factcheck.md：平台归属错），对外表述必须写成两个证据：Windows = blklogwrites 块层追踪（E14/E15），Linux = 断电模型（E11）。Windows 实机断电（真硬件 + ntfs.sys 重放）至今未测，README 里也是这么如实写的。

**E13（第一次 Windows 断电尝试）没有产出有效数据**——WinPE 无串口驱动 + TCG 下 CD 引导 20-35 分钟让 lazy writer 抢先刷盘，「牙齿臂也存活」是假象。真正能出数据的是 E14 的改进设计：blklogwrites 记录每条 WRITE/FLUSH、断电时刻由**离线切点**定义（worst=最后屏障前的保证态 / best=设备收到的全部写入）、SMBIOS serial 注入臂号、ntfs-3g 双挂载验尸（ro,norecover=裸盘态 / rw=重放态）。

## 1. E14 已实证的结论（新测试的基线，2026-11-14，WinPE + 完整 Win10 22H2 各 3 轮全一致）

1. 只 `FlushFileBuffers(file)`（上游 leveldb/SQLite/RocksDB 现状）：文件**内容**有屏障，**目录项不在盘上**（数据成孤儿）。
2. `MOVEFILE_WRITE_THROUGH` 同卷 rename：**无 flush 无 FUA**，与裸 rename 同形——「落盘才返回」在这个栈上不等于存储屏障。
3. **目录句柄 flush（GENERIC_WRITE + BACKUP_SEMANTICS）是唯一实证把命名空间推上盘的操作**（go.txt 只在调了 FlushDir 的臂出现在盘上）。
4. NTFS 元数据页在操作窗口内从不自动落盘；全日志 FUA=0（IDE/TCG 栈特性，真 NVMe 未测）。

## 2. 这几天的各家 DB 修法（E17 逐家复刻的对象）

各家补丁形状（逐字核对过补丁本体）：

| DB | 补丁位置 | 目录 flush 的 API 形状 | 与 E14 臂 2 的差异 |
|---|---|---|---|
| **leveldb 30b34e0**（fork + PR #1388） | `leveldb-fork` commit 30b34e0 | `CreateFileW(GENERIC_WRITE, share=R\|W\|D, OPEN_EXISTING, BACKUP_SEMANTICS)` → `FlushFileBuffers` → Close；路径来自 manifest 全路径 | 无（E14 臂 2 就是它） |
| **SQLite win32-dirsync-v2** | 本地补丁 `win32-dirsync-v2.patch`（696 行，含 `test/dirsyncprobe.c`） | 同上 + **错误上报 `SQLITE_IOERR_DIR_FSYNC`**（unix 侧是静默忽略）+ **S7 相对路径形状**（无分隔符时用 `"."`，S8 要求先过 xFullPathname） | 多了相对路径臂 + 错误面 |
| **LMDB** | 本地补丁 `lmdb-dirflush.patch`（+309 行） | 同上 + **裸盘符 `"C:"` 先 `GetFullPathNameW` 解析**（否则 err5）+ flush 后 `GetFinalPathNameByHandleW` 回读验证 + RDONLY 的 lock file 也刷 | 多了盘符解析与回读 |
| **PostgreSQL v3** | 本地补丁 `pgmaster-dirsync-v3.patch`（197 行，fd.c/open.c/file_utils.c/port.h） | `pgwin32_open_dir_for_sync()`：CreateFile(O_RDWR 形状) → **`_open_osfhandle` 转 fd** → 后端走 fd.c transient 协议 → **`pg_fsync(fd)`（即 `_commit`）** | flush 走的是 **fd 层**不是裸 HANDLE |
| **MySQL** | fork `fix/windows-dirflush` 0c860398（os0file.cc +66 行） | `os_parent_dir_fsync_win` = 同款 CreateFileW 形状；**4 个调用点**：create_directory（mkdir 后刷**父**）/ create_func / create_simple / **delete_if_exists（`MoveFileW` 改名 `.XXXX.d` + `DeleteFileW` 后刷父）**；rename 已有 WRITE_THROUGH 不动 | 多了 **delete 舞步**（命名空间变更是删除不是创建）和 **mkdir 刷父** |
| **FrankenSQLite PR #539** | 上游 open draft | `sync_directory_handle`（best-effort）+ `sync_parent_directory`（分层） | 无（同款形状，语义声明不同） |

共同点：**全部收敛到同一个 API 组合**（GENERIC_WRITE + FILE_FLAG_BACKUP_SEMANTICS + FlushFileBuffers，非管理员可用）。差异只在：路径解析（相对/盘符）、错误上报面（SQLITE_IOERR / ut_a 崩库 / warning）、调用点覆盖（create/delete/rename/mkdir）。

## 3. E17 新探针（已写好，`e17pe.cpp` + 配套 5 件）

**思路**：E14 rig 原样复用（blklogwrites、SMBIOS 臂号、离线切点、ntfs-3g 验尸、E13SYS 卷、2048B 内容寻址体），只换探针。8 臂 × 3 轮：

| 臂 | 复刻谁 | 验证目标 |
|---|---|---|
| 0 | 上游现状（对照负组） | 只刷文件，目录项应不在盘上（复现 E14 臂 1） |
| 1 | leveldb 30b34e0 / SQLite / MySQL / FrankenSQLite 共同形状 | 目录项上盘（复现 E14 臂 2 = rig 有效性正对照） |
| 2 | SQLite S7 相对路径形状 | `"."` 解析的 flush 与绝对路径等效 |
| 3 | LMDB 形状 | flush + GetFinalPathNameByHandleW 回读不影响语义 |
| 4 | PG transient-fd 形状 | 走 `_commit(fd)` 的 flush 与裸 HANDLE 等效 |
| 5 | MySQL delete 舞步 | **删除**的目录项变更也能被父目录 flush 推上盘（新覆盖面：E14 只测了 create/rename） |
| 6 | MySQL rename（WRITE_THROUGH only） | 复核 E14 臂 4（无屏障）在本代 rig 仍成立 |
| 7 | MySQL create_directory | mkdir 后刷父：**新建子目录**这个目录项本身能被推上盘（又是新覆盖面） |

每个臂的错误码（open_err/flush_err）写进 DONE marker——环境干扰（Defender、ACL）会显式出现在标记里，不会被误读成耐久性结果。臂 5/7 特意选了「内容寻址体 + 目录命名空间」分离的形状：验尸时按名字 ls + 按体内容 grep 双通道判定。

**配套**：`e17_gbld.sh`（guest 内重建 ISO，注入 e17pe.exe 为 /e13pe.exe）、`e17-phase1.sh`（编译+推送+建盘+回拉）、`e17-launch.ps1`（WMI 拉起，monitor 换 4473 端口避开 E14 的 4463）、`e17-run.sh`（单臂 runner，协议同 e14v3-run.sh）、`e17-autopsy.sh`（guest 内 ntfs-3g 双挂载验尸）、`e17_verdict.py`（切点/建 crash 镜像/判定，复用 `e14_analyze.load_records`；py_precheck/ruff/semgrep 全部 PASS）。

## 4. 与「你贴的那段对话」的差异（执行前要对齐的）

那段对话的框架（A 负对照 / B 被测 / C 正对照 + 三个断电窗口 + 块层追踪不可省）方向正确，但有几处要按实际情况修正：

1. **「5/5 / 8/8 是 Windows 断电测试」——错**。是 Linux 模型（见 0 节警告）。真正的 Windows 证据是 E14/E15 块层追踪（6 臂 × 2 栈 × 3 轮）。
2. **「C 组 = 原来已验证的实现」不需要**——E14 rig 本身就是正对照：臂 1 复现臂 0/2 的已知结论，rig 变了会立刻暴露。
3. **「以 LevelDB MANIFEST 替换为场景写 5 步」**——E17 更进一步：不测 leveldb 集成路径，直接测**各家补丁的原始 API 序列**（微探针），这样结论归因干净（失败=该 API 形状问题，不是 leveldb 集成问题）。leveldb 端到端（MANIFEST/CURRENT 恢复）是第二阶段，可以在这之后加。
4. **「扩样本」**——E14 每臂 3 轮；E17 同为 3 轮，但 8 臂 = 24 试验点，且 worst/best 双切点每轮都出镜像。要上样本量就改 REPS=5+（探针已支持 ≤16）。
5. **断电窗口**——E14 的离线切点法已经覆盖「rename 后目录 flush 前」（worst 切点）和「目录 flush 返回后」（best 切点）两个窗口，且比「按时刻断电」更严格：worst = 保证态（丢的都是没屏障的），best = 设备收到的全部。
6. **ReFS/FAT/SMB/真 NVMe 局限**——同意保留：结论只限「WinPE/Win10 22H2 + IDE/TCG + NTFS + blklogwrites 可见的层」。

## 5. 执行清单（等拍板后跑；全部在校机，本机只出代码）

按 AGENTS.md 校机边界（实验只在校机跑）+ 每次 push 前先问的规矩：

```bash
# ① 编译+推送+建盘+回拉（校机宿主 MSYS2 g++ + Debian CI guest <guest-ssh-port>）
bash e17-phase1.sh
# ② 单臂跑（0..7，每臂 ~10-25 min，串行；期间校机 qemu 只跑这一个）
bash e17-run.sh 0; ... bash e17-run.sh 7
# ③ 切点分析 + 建 crash 镜像（校机宿主 python）
python e17_verdict.py build
# ④ 验尸（Debian guest，ntfs-3g 双挂载）
gssh 'bash /home/ci/e13/e17-autopsy.sh'   # 逐镜像
# ⑤ 汇总 → E17-RESULTS.md（对齐 E14-RESULTS.md 格式：臂表 + 验尸 + caveats）
```

前置确认（跑之前）：校机 qemu 空闲（**别杀别人的 CI VM <ci-vm-pid>**）、`<TASKDIR>\` 磁盘空间 ≥ 40G（8 臂 × (base+vm+log+2 crash) ≈ 8×20G，可跑一臂清一臂）。

## 6. 预期结果（可证伪声明）

- 臂 0：目录项不上盘（复现 E14 臂 1）→ 若上盘 = rig 退化/环境变化，停下查。
- 臂 1-4：目录项上盘（复现 E14 臂 2，且证明相对路径/fd 层/回读形状等效）。
- 臂 5：删除舞步后 `a5_r*.bin` 与 `.XXXX.d` 名字都不在盘上、父目录 flush 覆盖两种变更。
- 臂 6：与裸 rename 无差别（无屏障）→ 复核 WRITE_THROUGH 结论。
- 臂 7：新建子目录 `a7_r*.d` 的目录项上盘。
- 全臂 FUA=0、NTFS 元数据窗口内不自动落盘（与 E14 一致）。

任何一条不符 = 新发现（要么 rig 问题要么 NTFS 行为差异），先查再下结论。
