# StateVerge / NYC 外接 SSD 与 AirDrop 自动化

## 系统用途

本套脚本用于：

- 定期检查外接 SSD（卷名 `StateVerge`）是否已正确挂载，并在未挂载时输出**只读**诊断信息（不会修复、格式化或初始化磁盘）。
- 将 `~/Downloads` 中**最近 7 天**的视频、图片、音频等文件，在 SSD 可用时**安全复制并校验**后归档到 `NYC_AUTO/raw/airdrop/`，校验通过后才删除 Downloads 中的原文件。
- 提供安全弹出流程，降低因未弹出而导致的识别/脏读风险。
- 可选通过 `launchd` 定时执行 SSD 检测与 AirDrop 导入。

若你的盘当前显示为 `NO NAME` 等名称，请在磁盘工具中将卷**重命名为 `StateVerge`**，以便脚本路径 `/Volumes/StateVerge` 生效。脚本**不会**自动改名或格式化任何卷。

## 目录结构（项目内）

| 路径 | 说明 |
|------|------|
| `scripts/system/` | SSD 检测、弹出、状态、`launchd` 安装、shell 别名安装 |
| `scripts/media/` | AirDrop / Downloads 导入脚本 |
| `logs/system/` | 本地运行日志 |
| `docs/system/` | 本文档 |

## 外接盘上的目录（挂载后由脚本 `mkdir -p` 确保存在）

- `NYC_AUTO/raw/airdrop/video/`
- `NYC_AUTO/raw/airdrop/image/`
- `NYC_AUTO/raw/airdrop/audio/`
- `NYC_AUTO/raw/airdrop/other/`
- `NYC_AUTO/logs/`（含 `airdrop_import_manifest.csv` 与按需复制的 `ssd_check_*.log`）
- `NYC_AUTO/quarantine/`

## 各脚本说明与运行方式

### `scripts/system/check_external_ssd.sh`

检测 `/Volumes/StateVerge` 是否挂载；未挂载则执行 `diskutil list external`、`diskutil list`、`system_profiler SPUSBDataType`。日志：`logs/system/ssd_check_YYYY-MM-DD.log`；若 SSD 已挂载，另复制一份到 `NYC_AUTO/logs/`。

```bash
bash scripts/system/check_external_ssd.sh
```

### `scripts/system/eject_stateverge_ssd.sh`

弹出前执行 `sync`，再对卷执行 `diskutil unmountDisk`（解析 Device Identifier）。失败时尝试 `lsof +D /Volumes/StateVerge` 并提示关闭 Finder、Terminal、Cursor、FFmpeg 等。**不会**默认结束其他进程。需要强制卸载卷时可加 `--force`（仅 `diskutil` 强制卸载，仍不 `kill` 进程）。

```bash
bash scripts/system/eject_stateverge_ssd.sh
bash scripts/system/eject_stateverge_ssd.sh --force
```

### `scripts/media/import_airdrop_to_ssd.py`

扫描 `~/Downloads` 最近 7 天、指定扩展名文件；仅在 SSD 挂载时按类型与日期归档。视频子目录含 `morning` / `day` / `golden_hour` / `night` 时段文件夹。单文件失败不影响后续文件。

```bash
python3 scripts/media/import_airdrop_to_ssd.py
```

### `scripts/system/stateverge_system_status.sh`

只读输出时间、系统版本、项目路径、`StateVerge` 挂载与空间、`NYC_AUTO` 是否存在、最近 AirDrop 导入与 SSD 检测日志片段。未挂载时提示不要执行导入。完整输出写入 `logs/system/status_YYYY-MM-DD_HHMMSS.log`。

```bash
bash scripts/system/stateverge_system_status.sh
```

### Terminal 快捷命令（`sv-*`）

在任意目录可用下列别名（需 zsh；安装后生效）：

| 命令 | 等价于 |
|------|--------|
| `sv-status` | `bash ~/StateVerge/scripts/system/stateverge_system_status.sh` |
| `sv-eject` | `bash ~/StateVerge/scripts/system/eject_stateverge_ssd.sh` |
| `sv-import` | `python3 ~/StateVerge/scripts/media/import_airdrop_to_ssd.py` |

安装/更新别名（写入 `~/.zshrc`，**替换**已有同名 `alias sv-status` / `sv-eject` / `sv-import`，并去掉旧版安装脚本留下的标记块，避免重复）：

```bash
bash ~/StateVerge/scripts/system/install_cli_shortcuts.sh
source ~/.zshrc
```

### `scripts/system/install_stateverge_automation_launchd.sh`

安装/卸载/查看两个 LaunchAgent（不影响其他已存在的 StateVerge 任务，仅操作本脚本写入的两个 plist）。

- `com.stateverge.ssdcheck`：每天 09:00、18:00、23:00 运行 `check_external_ssd.sh`
- `com.stateverge.airdropimport`：每 15 分钟运行 `import_airdrop_to_ssd.py`

标准输出/错误合并写入：

- `logs/system/launchd_ssdcheck.out.log`
- `logs/system/launchd_airdropimport.out.log`

```bash
bash scripts/system/install_stateverge_automation_launchd.sh --install
bash scripts/system/install_stateverge_automation_launchd.sh --status
bash scripts/system/install_stateverge_automation_launchd.sh --uninstall
```

## 常用命令汇总

```bash
bash scripts/system/check_external_ssd.sh
bash scripts/system/eject_stateverge_ssd.sh
python3 scripts/media/import_airdrop_to_ssd.py
bash scripts/system/stateverge_system_status.sh
bash scripts/system/install_stateverge_automation_launchd.sh --install
bash scripts/system/install_stateverge_automation_launchd.sh --status
bash scripts/system/install_stateverge_automation_launchd.sh --uninstall
```

（在项目根目录 `~/StateVerge` 下执行，或设置 `STATEVERGE_ROOT`。）

快捷方式（安装后）：`sv-status`、`sv-eject`、`sv-import`。参见上文「Terminal 快捷命令」。

## 重要警告

1. **不要直接拔 SSD**：先安全弹出或使用系统“推出”。
2. **弹出失败不要硬拔**：按脚本提示关闭占用进程后重试；必要时谨慎使用 `--force` 卸载（仍非拔线）。
3. **不要对盘进行初始化 / 抹掉**：本仓库脚本**不包含**任何格式化或抹盘操作；若在系统对话框中误点，可能造成数据丢失。
4. 若 `diskutil list external` 看不到盘：优先检查**线缆、硬盘盒、接口、另一台电脑**；不要急于格式化。

## 数据恢复建议

- 若系统能看到物理盘但无法挂载：**先不要格式化**。
- 保存 `diskutil list external` 与控制台相关输出，便于后续排查。
- 重要数据优先考虑**整盘镜像**或专业恢复工具/服务。

## 行为与安全边界

- **不会**自动格式化、初始化、抹掉任何磁盘。
- **不会**删除外接盘上已有素材；仅在校验成功的复制完成后删除 **Downloads 内**对应源文件。
- 所有脚本均写日志；导入过程另有 CSV manifest 记录每笔结果。
