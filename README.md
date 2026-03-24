# 边淬火工具箱

我在用 Claude Code 搞内容创作和创业的过程中写的一些工具。自己在用，顺手开源。

这也是一个 Claude Code Skill。克隆到你的 `.claude/skills/` 目录下，Claude 就能直接调用里面的工具。

## 安装

```bash
cd 你的项目/.claude/skills/
git clone https://github.com/PhoenixBian/biancuihuo-toolkit.git
```

装完之后在 Claude Code 里说"帮我采集 Twitter 信息流"就行了。

## 更新

```bash
cd .claude/skills/biancuihuo-toolkit && git pull
```

或者在 Claude Code 里说"更新工具箱"，它会自己拉最新版本。Skill 启动时也会自动检查有没有新版本。

## 工具列表

### [twitter-timeline/](./twitter-timeline/) — Twitter 信息流爬虫

通过 CDP 连接你已登录的 Chrome，拦截 Twitter 的 GraphQL API 响应，用 fetch + cursor 自动翻页采集。

- `scraper.py` — 采集 For You / Following 信息流，推荐池耗尽自动刷新继续采
- `user_scraper.py` — 采集任意博主的全量推文，支持批量

纯 Python + websockets，不需要装别的东西。详见 [twitter-timeline/README.md](./twitter-timeline/README.md)。

---

持续更新中。MIT 协议，随便拿。
