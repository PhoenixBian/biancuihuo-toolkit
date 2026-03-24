# Twitter 信息流爬虫

通过 CDP（Chrome DevTools Protocol）连接你已登录 Twitter 的 Chrome 浏览器，拦截 HomeTimeline / UserTweets 的 GraphQL 响应，提取认证信息后用 fetch + cursor 自动翻页采集。

请求在页面上下文里发出，携带真实 session cookies，和你正常刷推特一模一样。

## 能采什么

推文文本、用户名、@handle、粉丝数、点赞、转发、回复、浏览量、书签、引用、创建时间、语言、媒体 URL、hashtags、是否转推/引用。

## 前置条件

- Python 3.8+
- `websockets`（`pip install websockets`）
- Chrome 开启远程调试并登录 Twitter：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Chrome-Profiles/twitter" &
```

## 用法

### 采集信息流

```bash
# For You，采 500 条
python3 scripts/scraper.py -n 500

# Following
python3 scripts/scraper.py -n 500 --following
```

推荐池耗尽会自动刷新页面继续采。遇到 rate limit 自动等待。

### 采集博主推文（近期，最多 3200 条）

```bash
# 单个博主
python3 scripts/user_scraper.py @elonmusk

# 批量
python3 scripts/user_scraper.py @user1 @user2 @user3
```

### 采集博主全量推文（突破 3200 限制）

通过 SearchTimeline API 按月分块搜索 `from:username`，绕过 UserTweets 的 3200 条上限。

```bash
# 从 2020 年 1 月开始采全量
python3 scripts/full_scraper.py @dotey

# 指定起始时间
python3 scripts/full_scraper.py @dotey --since-year 2023 --since-month 6
```

自动按月分块，每月独立翻页，全局去重。

### 输出

结果在 `output/`，`.jsonl`（实时写入，防崩溃丢数据）和 `.json`（最终汇总）。

采集完自动打印统计。

## 风控

信息流 3 秒间隔没问题。博主采集更宽松。别一次采几万条就行。
