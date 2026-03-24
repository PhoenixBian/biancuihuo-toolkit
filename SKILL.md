---
name: biancuihuo-toolkit
version: 0.1.0
description: |
  边淬火工具箱。用 Claude Code 创业过程中积累的工具集合。
  路由器：根据用户需求自动调用对应工具。

  当前工具：
  - Twitter 信息流爬虫（采集 For You / Following / 博主全量推文）

  当用户说"采集推特"、"扒推文"、"Twitter爬虫"时自动路由到对应工具。
  当用户说"更新工具箱"时执行 git pull 获取最新版本。
---

# 边淬火工具箱

## Preamble（每次运行前执行）

```bash
_TOOLKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || echo "$PWD")"
# 如果在 skill 目录下运行
[ ! -f "$_TOOLKIT_DIR/VERSION" ] && _TOOLKIT_DIR="$(find ~/.claude/skills -name "biancuihuo-toolkit" -type d 2>/dev/null | head -1)"
[ -z "$_TOOLKIT_DIR" ] && _TOOLKIT_DIR="$(find .claude/skills -name "biancuihuo-toolkit" -type d 2>/dev/null | head -1)"

_UPD=$("$_TOOLKIT_DIR/bin/update-check" 2>/dev/null || true)
[ -n "$_UPD" ] && echo "$_UPD" || true
echo "TOOLKIT: $_TOOLKIT_DIR"
echo "VERSION: $(cat "$_TOOLKIT_DIR/VERSION" 2>/dev/null || echo unknown)"
```

如果输出 `UPGRADE_AVAILABLE <old> <new>`：告诉用户"工具箱有新版本（当前 {old}，最新 {new}）。要更新吗？"
用户同意 → 执行 `cd "$_TOOLKIT_DIR" && git pull`

---

## 路由

根据用户需求分发到对应工具：

| 触发词 | 工具 | 路径 |
|--------|------|------|
| 采集推特、扒推文、Twitter 爬虫、采集信息流 | Twitter 信息流爬虫 | `twitter-timeline/` |
| 采集博主、扒博主推文 | Twitter 博主爬虫 | `twitter-timeline/` |
| 更新工具箱 | git pull | — |

---

## 工具：Twitter 信息流爬虫

路径：`twitter-timeline/`

### 前置条件

Chrome 调试实例运行中（端口 9222），已登录 Twitter。

### 信息流采集

```bash
python3 "$_TOOLKIT_DIR/twitter-timeline/scripts/scraper.py" -n 500
```

参数：
- `-n 数量` — 目标采集条数
- `--following` — 切换到 Following 时间线

### 博主采集

```bash
python3 "$_TOOLKIT_DIR/twitter-timeline/scripts/user_scraper.py" @用户名
```

支持批量：`@user1 @user2 @user3`

### 输出

结果在 `twitter-timeline/output/`。`.jsonl`（实时）+ `.json`（最终）。采完自动打印统计。

---

## 添加新工具

工具箱持续更新。每个工具一个目录，目录下放 `scripts/` 和 `README.md`。

用户说"更新工具箱"时执行：

```bash
cd "$_TOOLKIT_DIR" && git pull && echo "已更新到 $(cat VERSION)"
```
