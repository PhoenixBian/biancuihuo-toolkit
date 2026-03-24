# 边淬火工具箱

我在用 Claude Code 搞内容创作和创业的过程中写的一些工具。

都是自己在用的东西，顺手开源。MIT 协议，随便拿。

## 工具列表

### [twitter-timeline/](./twitter-timeline/) — Twitter 信息流爬虫

通过 CDP 连接你已登录的 Chrome，拦截 Twitter 的 GraphQL API 响应，用 cursor 自动翻页采集。

两个脚本：
- `scraper.py` — 采集 For You / Following 信息流
- `user_scraper.py` — 采集任意博主的全量推文（支持批量）

纯 Python + websockets，不需要装别的东西。

---

持续更新中。我每天和 Claude Code 一起工作，遇到需要就写工具，写完就扔这里。
