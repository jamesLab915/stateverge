# Envato 自动分拣（`src/utils/sort_envato.py`）

将 `~/Downloads` 中的素材按规则移动到本仓库的 `assets/envato/` 下，并预建以下目录：

- `music/` · `sfx/` · `transitions/` · `overlays/`
- `lower_thirds/` · `title_openers/`
- `_licenses/` · `_raw_backup/`

## 1. 安装 watchdog（`--watch` 需要）

```bash
cd /Users/ziweizhang/StateVerge
source .venv/bin/activate
pip install watchdog
```

`--once` 模式不依赖 watchdog，仅全量扫描一次后退出。

## 2. 手动跑一次（扫描一次）

在仓库根目录执行：

```bash
cd /Users/ziweizhang/StateVerge
export PYTHONPATH="$PWD"
python -m src.utils.sort_envato --once
```

## 3. 持续监听（后台）

```bash
bash /Users/ziweizhang/StateVerge/scripts/start_sort_daemon.sh
```

日志文件：`/Users/ziweizhang/StateVerge/logs/envato_sort.log`

## 4. 可选 shell 别名

在 `~/.zshrc` 中加入一行，方便一键整理：

```bash
alias clean_envato='cd /Users/ziweizhang/StateVerge && export PYTHONPATH="$PWD" && python -m src.utils.sort_envato --once'
```

执行 `source ~/.zshrc` 后，在终端运行 `clean_envato` 即可。

## 5. 命令说明

| 命令 | 说明 |
|------|------|
| `python -m src.utils.sort_envato --once` | 扫描 `~/Downloads` 一次后退出 |
| `python -m src.utils.sort_envato --watch` | 用 watchdog 持续监听 `~/Downloads`（需已安装 watchdog） |

若未安装 watchdog 就使用 `--watch`，会打印安装提示并以非零状态退出；`--once` 仍可用。
