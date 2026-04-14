# 边淬火工具箱

我在用 Claude Code 搞内容创作和创业的过程中写的一些工具。自己在用，顺手开源。

这也是一个 **Claude Code Plugin Marketplace**。一行命令加进来，按需安装里面的工具。

## 安装

```bash
# 添加工具箱（一次性）
claude plugin marketplace add PhoenixBian/biancuihuo-toolkit

# 按需安装
claude plugin install feynman-learning@biancuihuo-toolkit
claude plugin install liars-dice@biancuihuo-toolkit
claude plugin install recall@biancuihuo-toolkit
claude plugin install biancuihuo-toolkit@biancuihuo-toolkit   # Twitter 爬虫
```

## 工具列表

| 工具 | 一句话 | 仓库 |
|------|--------|------|
| **feynman-learning** | 费曼学习引擎。AI 写讲义，然后做难相处的听众实时追问 | [→ repo](https://github.com/PhoenixBian/feynman-learning) |
| **liars-dice** | 骰子游戏训练。练概率、打 AI 对手、追踪 ELO | [→ repo](https://github.com/PhoenixBian/liars-dice) |
| **recall** | 搜索和恢复过去的 Claude Code 会话 | [→ repo](https://github.com/PhoenixBian/recall) |
| **biancuihuo-toolkit** | Twitter 信息流爬虫（CDP + Python） | 本仓库 |

## Twitter 信息流爬虫

本仓库自带的工具。通过 CDP 连接已登录的 Chrome，拦截 Twitter GraphQL API 响应，自动翻页采集。

- `scraper.py` — 采集 For You / Following 信息流
- `user_scraper.py` — 采集任意博主全量推文，支持批量

详见 [twitter-timeline/README.md](./twitter-timeline/README.md)

---

持续更新中。MIT 协议，随便拿。
