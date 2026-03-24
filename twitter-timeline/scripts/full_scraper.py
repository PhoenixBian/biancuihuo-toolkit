#!/usr/bin/env python3
"""
Twitter 全量推文采集器
通过 SearchTimeline API 按月分块搜索 from:username，绕过 UserTweets 的 3200 条限制。
"""

import asyncio
import json
import sys
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote, urlparse, parse_qs

try:
    import websockets
except ImportError:
    print("需要 websockets: pip install websockets")
    sys.exit(1)

# ─── 配置 ───
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
PAGE_INTERVAL = 3  # 每页间隔秒数
CHUNK_INTERVAL = 5  # 每个月块间隔秒数
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def extract_tweets_from_search(body):
    """从 SearchTimeline 响应中提取推文"""
    tweets = []
    cursor = None
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, TypeError):
        return tweets, cursor

    instructions = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    for inst in instructions:
        if inst.get("type") == "TimelineAddEntries":
            for entry in inst.get("entries", []):
                content = entry.get("content", {})

                # cursor
                if content.get("cursorType") == "Bottom":
                    cursor = content.get("value")
                elif content.get("entryType") == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
                    cursor = content.get("value")

                # tweet
                item_content = content.get("itemContent", {})
                if item_content.get("itemType") != "TimelineTweet":
                    continue

                result = item_content.get("tweet_results", {}).get("result", {})
                if result.get("__typename") == "TweetWithVisibilityResults":
                    result = result.get("tweet", {})
                if not result or result.get("__typename") not in ("Tweet", None):
                    if result.get("__typename") == "TweetTombstone":
                        continue

                # 过滤广告
                if item_content.get("promotedMetadata"):
                    continue

                legacy = result.get("legacy", {})
                core = result.get("core", {}).get("user_results", {}).get("result", {})
                user_legacy = core.get("legacy", {})
                views = result.get("views", {})

                tweet = {
                    "tweet_id": legacy.get("id_str", result.get("rest_id", "")),
                    "text": legacy.get("full_text", ""),
                    "created_at": legacy.get("created_at", ""),
                    "lang": legacy.get("lang", ""),
                    "favorite_count": legacy.get("favorite_count", 0),
                    "retweet_count": legacy.get("retweet_count", 0),
                    "reply_count": legacy.get("reply_count", 0),
                    "quote_count": legacy.get("quote_count", 0),
                    "bookmark_count": legacy.get("bookmark_count", 0),
                    "views": int(views.get("count", 0)) if views.get("count") else 0,
                    "user_name": user_legacy.get("name", ""),
                    "user_handle": user_legacy.get("screen_name", ""),
                    "user_id": core.get("rest_id", ""),
                    "user_followers": user_legacy.get("followers_count", 0),
                    "user_following": user_legacy.get("friends_count", 0),
                    "user_verified": core.get("is_blue_verified", False),
                    "is_retweet": "retweeted_status_result" in legacy,
                    "is_quote": "quoted_status_result" in result,
                    "media_urls": [],
                    "hashtags": [h["text"] for h in legacy.get("entities", {}).get("hashtags", [])],
                }

                # 媒体
                for media in legacy.get("extended_entities", {}).get("media", []):
                    if media.get("type") == "video":
                        variants = media.get("video_info", {}).get("variants", [])
                        mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                        if mp4s:
                            tweet["media_urls"].append(max(mp4s, key=lambda v: v.get("bitrate", 0))["url"])
                    else:
                        tweet["media_urls"].append(media.get("media_url_https", ""))

                if tweet["tweet_id"]:
                    tweets.append(tweet)

    return tweets, cursor


def generate_month_chunks(start_year, start_month, end_date=None):
    """生成从 start_year-start_month 到现在的月块列表"""
    if end_date is None:
        end_date = datetime.now()

    chunks = []
    current = datetime(start_year, start_month, 1)

    while current < end_date:
        next_month = current.replace(day=28) + timedelta(days=4)
        next_month = next_month.replace(day=1)
        if next_month > end_date:
            next_month = end_date + timedelta(days=1)

        chunks.append((
            current.strftime("%Y-%m-%d"),
            next_month.strftime("%Y-%m-%d"),
        ))
        current = next_month

    return chunks


async def scrape_full(handle, start_year=2020, start_month=1):
    """采集一个用户的全量推文"""
    handle = handle.lstrip("@")

    print("\nTwitter 全量推文采集器")
    print(f"用户: @{handle}")
    print(f"起始: {start_year}-{start_month:02d}")

    # 连接 Chrome
    import urllib.request
    with urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5) as resp:
        pages = json.loads(resp.read())

    # 找 x.com 页面或用第一个
    target = None
    for p in pages:
        if "x.com" in p.get("url", ""):
            target = p
            break
    if not target:
        target = pages[0]

    ws_url = target["webSocketDebuggerUrl"]
    print(f"连接: {ws_url[:50]}...")

    collected = {}
    msg_id = 0
    pending = {}
    stop_event = asyncio.Event()
    captured_request = {"path": None, "features": None}

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:

        async def send_cmd(method, params=None):
            nonlocal msg_id
            msg_id += 1
            cid = msg_id
            cmd = {"id": cid, "method": method}
            if params:
                cmd["params"] = params
            fut = asyncio.get_event_loop().create_future()
            pending[cid] = fut
            await ws.send(json.dumps(cmd))
            return fut

        async def message_dispatcher():
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                msg = json.loads(raw)

                if "id" in msg and msg["id"] in pending:
                    f = pending.pop(msg["id"])
                    if not f.done():
                        f.set_result(msg.get("result", {}))

                method = msg.get("method", "")
                if method == "Network.requestWillBeSent":
                    req_url = msg.get("params", {}).get("request", {}).get("url", "")
                    if "SearchTimeline" in req_url and "client-web" not in req_url:
                        parsed = urlparse(req_url)
                        qs = parse_qs(parsed.query)
                        captured_request["path"] = parsed.path
                        captured_request["features"] = json.loads(qs.get("features", ["{}"])[0])

        dispatcher_task = asyncio.create_task(message_dispatcher())

        f = await send_cmd("Network.enable")
        await asyncio.wait_for(f, timeout=5)
        f = await send_cmd("Runtime.enable")
        await asyncio.wait_for(f, timeout=5)

        # 先导航一次搜索页拿到 API path 和 features
        search_url = f"https://x.com/search?q=from%3A{handle}%20since%3A2025-01-01%20until%3A2025-02-01&src=typed_query&f=live"
        f = await send_cmd("Page.navigate", {"url": search_url})
        await asyncio.wait_for(f, timeout=15)

        for _ in range(20):
            if captured_request["path"]:
                break
            await asyncio.sleep(0.5)

        if not captured_request["path"]:
            print("未能捕获 SearchTimeline 请求")
            stop_event.set()
            dispatcher_task.cancel()
            return []

        print(f"API: {captured_request['path']}")
        await asyncio.sleep(3)

        # 准备输出
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        live_path = os.path.join(OUTPUT_DIR, f"full_{handle}_{timestamp}.jsonl")
        live_file = open(live_path, "a", encoding="utf-8")

        # 按月分块采集
        chunks = generate_month_chunks(start_year, start_month)
        print(f"共 {len(chunks)} 个月块\n")

        base_path = captured_request["path"]
        features = captured_request["features"]

        for ci, (since, until) in enumerate(chunks):
            query = f"from:{handle} since:{since} until:{until}"
            cursor = None
            page_num = 0
            empty_pages = 0
            chunk_new = 0

            print(f"  [{ci+1}/{len(chunks)}] {since} → {until}", end="", flush=True)

            while True:
                page_num += 1

                variables = {
                    "rawQuery": query,
                    "count": 20,
                    "querySource": "typed_query",
                    "product": "Latest",
                }
                if cursor:
                    variables["cursor"] = cursor

                params_str = urlencode({
                    "variables": json.dumps(variables, separators=(",", ":")),
                    "features": json.dumps(features, separators=(",", ":")),
                }, quote_via=quote)

                api_url = f"https://x.com{base_path}?{params_str}"

                fetch_js = f"""
                (async () => {{
                    const resp = await fetch({json.dumps(api_url)}, {{
                        credentials: 'include',
                        headers: {{
                            'content-type': 'application/json',
                            'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                            'x-twitter-auth-type': 'OAuth2Session',
                            'x-twitter-active-user': 'yes',
                            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'
                        }}
                    }});
                    if (!resp.ok) return JSON.stringify({{error: resp.status}});
                    return await resp.text();
                }})()
                """

                f = await send_cmd("Runtime.evaluate", {
                    "expression": fetch_js,
                    "awaitPromise": True,
                    "returnByValue": True,
                })

                try:
                    result = await asyncio.wait_for(f, timeout=15)
                except asyncio.TimeoutError:
                    print(" [超时]", end="", flush=True)
                    await asyncio.sleep(10)
                    continue

                body = result.get("result", {}).get("value", "")
                if not body:
                    break

                # rate limit
                try:
                    check = json.loads(body)
                    if "error" in check and isinstance(check.get("error"), int):
                        if check["error"] == 429:
                            print(" [限流，等120s]", end="", flush=True)
                            await asyncio.sleep(120)
                            continue
                        else:
                            break
                except (json.JSONDecodeError, TypeError):
                    pass

                tweets, new_cursor = extract_tweets_from_search(body)

                new_count = 0
                for tweet in tweets:
                    tid = tweet["tweet_id"]
                    if tid not in collected:
                        collected[tid] = tweet
                        new_count += 1
                        chunk_new += 1
                        live_file.write(json.dumps(tweet, ensure_ascii=False) + "\n")

                if new_count > 0:
                    live_file.flush()

                if new_count == 0:
                    empty_pages += 1
                    if empty_pages >= 2:
                        break
                else:
                    empty_pages = 0

                if not new_cursor:
                    break

                cursor = new_cursor
                await asyncio.sleep(PAGE_INTERVAL)

            print(f" → {chunk_new} 条 (总 {len(collected)})")
            await asyncio.sleep(CHUNK_INTERVAL)

        live_file.close()

        # 保存最终 JSON
        final_path = os.path.join(OUTPUT_DIR, f"full_{handle}_{timestamp}.json")
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(list(collected.values()), f, ensure_ascii=False, indent=2)

        stop_event.set()
        dispatcher_task.cancel()

    # 统计
    all_tweets = list(collected.values())
    print(f"\n{'='*50}")
    print(f"@{handle} 全量采集完成")
    print(f"{'='*50}")
    print(f"总推文: {len(all_tweets)}")
    if all_tweets:
        original = [t for t in all_tweets if not t["is_retweet"]]
        print(f"原创: {len(original)} | 转推: {len(all_tweets) - len(original)}")
        if original:
            avg_likes = sum(t["favorite_count"] for t in original) / len(original)
            avg_views = sum(t["views"] for t in original) / len(original)
            print(f"平均赞: {avg_likes:.0f} | 平均浏览: {avg_views:.0f}")
            top = max(original, key=lambda t: t["favorite_count"])
            print(f"最高赞: {top['favorite_count']:,} — {top['text'][:80]}")
    print(f"\n文件: {final_path}")

    return all_tweets


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Twitter 全量推文采集（按月分块搜索）")
    parser.add_argument("handle", help="用户 handle，如 @dotey")
    parser.add_argument("--since-year", type=int, default=2020, help="起始年份（默认 2020）")
    parser.add_argument("--since-month", type=int, default=1, help="起始月份（默认 1）")
    args = parser.parse_args()

    asyncio.run(scrape_full(args.handle, args.since_year, args.since_month))


if __name__ == "__main__":
    main()
