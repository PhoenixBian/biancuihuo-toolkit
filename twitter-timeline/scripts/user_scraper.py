#!/usr/bin/env python3
"""
Twitter User Timeline Scraper via CDP
采集指定用户的推文（最多 3200 条）。
用法: python3 user_scraper.py @dotey @elonmusk -n 500
"""

import asyncio
import json
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, quote

try:
    import websockets
except ImportError:
    print("需要 websockets 库: pip install websockets")
    raise

# 复用 scraper.py 的解析函数
from scraper import parse_tweet, OUTPUT_DIR

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
MAX_PER_USER = 3200
PAGE_INTERVAL = 3.0


async def get_ws_url(host=CDP_HOST, port=CDP_PORT):
    import urllib.request
    url = f"http://{host}:{port}/json"
    with urllib.request.urlopen(url, timeout=5) as resp:
        pages = json.loads(resp.read())
    for page in pages:
        if "x.com" in page.get("url", "") or "twitter.com" in page.get("url", ""):
            return page.get("webSocketDebuggerUrl")
    if pages:
        return pages[0].get("webSocketDebuggerUrl")
    raise RuntimeError("没有可用的 Chrome 页面")


def extract_user_tweets(body):
    """从 UserTweets GraphQL 响应中提取推文和 cursor"""
    tweets = []
    cursor_bottom = None

    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, TypeError):
        return tweets, None

    instructions = (
        data.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    for instruction in instructions:
        entries = instruction.get("entries", [])

        for entry in entries:
            entry_id = entry.get("entryId", "")
            content = entry.get("content", {})

            if "cursor-bottom" in entry_id:
                cursor_bottom = content.get("value")
                continue

            if "promotedMetadata" in content:
                continue

            item_content = content.get("itemContent", {})
            if item_content.get("itemType") == "TimelineTweet":
                result = item_content.get("tweet_results", {}).get("result", {})
                tweet = parse_tweet(result)
                if tweet:
                    tweets.append(tweet)

            # Module items (conversations)
            for sub_item in content.get("items", []):
                sub_content = sub_item.get("item", {}).get("itemContent", {})
                if sub_content.get("itemType") == "TimelineTweet":
                    result = sub_content.get("tweet_results", {}).get("result", {})
                    tweet = parse_tweet(result)
                    if tweet:
                        tweets.append(tweet)

    return tweets, cursor_bottom


async def _paginate(send_cmd, base_path, variables, features, collected, label, max_tweets):
    """通用翻页逻辑"""
    cursor = None
    page_num = 0
    empty_pages = 0

    while len(collected) < max_tweets:
        page_num += 1

        req_vars = dict(variables)
        if cursor:
            req_vars["cursor"] = cursor

        params_str = urlencode({
            "variables": json.dumps(req_vars, separators=(",", ":")),
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
            print(f"  [{label}] 页 {page_num}: 超时")
            await asyncio.sleep(10)
            continue

        body = result.get("result", {}).get("value", "")
        if not body:
            print(f"  [{label}] 页 {page_num}: 空响应")
            break

        try:
            check = json.loads(body)
            if isinstance(check.get("error"), int):
                if check["error"] == 429:
                    print(f"  [{label}] 页 {page_num}: Rate limited! 等 120s...")
                    await asyncio.sleep(120)
                    continue
                else:
                    print(f"  [{label}] 页 {page_num}: HTTP {check['error']}")
                    break
        except (json.JSONDecodeError, TypeError):
            pass

        tweets, new_cursor = extract_user_tweets(body)

        new_count = 0
        for tweet in tweets:
            tid = tweet["tweet_id"]
            if tid not in collected:
                collected[tid] = tweet
                new_count += 1

        print(f"  [{label}] 页 {page_num}: +{new_count} | 总计: {len(collected)}")

        if new_count == 0:
            empty_pages += 1
            if empty_pages >= 3:
                print(f"  [{label}] 到底了")
                break
        else:
            empty_pages = 0

        if not new_cursor:
            print(f"  [{label}] 无更多 cursor")
            break

        cursor = new_cursor
        await asyncio.sleep(PAGE_INTERVAL)


async def scrape_user(handle, ws, send_cmd, captured_requests, max_tweets=MAX_PER_USER):
    """采集单个用户的推文 + 回复"""
    handle = handle.lstrip("@")
    collected = {}
    print(f"\n{'='*50}")
    print(f"采集 @{handle} (目标 {max_tweets} 条，含回复)")
    print(f"{'='*50}")

    # --- Phase 1: 推文 ---
    captured_requests.clear()
    nav_fut = await send_cmd("Page.navigate", {"url": f"https://x.com/{handle}"})
    await asyncio.wait_for(nav_fut, timeout=15)

    for _ in range(20):
        if captured_requests.get("user_tweets_url"):
            break
        await asyncio.sleep(0.5)

    if not captured_requests.get("user_tweets_url"):
        print(f"  未能捕获 UserTweets URL，跳过 @{handle}")
        return []

    parsed = urlparse(captured_requests["user_tweets_url"])
    qs = parse_qs(parsed.query)
    variables = json.loads(qs.get("variables", ["{}"])[0])
    features = json.loads(qs.get("features", ["{}"])[0])

    user_id = variables.get("userId")
    print(f"  userId: {user_id}")
    print("  Phase 1: 推文")

    await _paginate(send_cmd, parsed.path, variables, features, collected, "推文", max_tweets)
    tweets_count = len(collected)
    print(f"  推文阶段: {tweets_count} 条")

    if len(collected) >= max_tweets:
        return list(collected.values())

    # --- Phase 2: 回复 ---
    captured_requests.clear()
    nav_fut = await send_cmd("Page.navigate", {"url": f"https://x.com/{handle}/with_replies"})
    await asyncio.wait_for(nav_fut, timeout=15)

    for _ in range(20):
        if captured_requests.get("replies_url"):
            break
        await asyncio.sleep(0.5)

    if captured_requests.get("replies_url"):
        parsed_r = urlparse(captured_requests["replies_url"])
        qs_r = parse_qs(parsed_r.query)
        variables_r = json.loads(qs_r.get("variables", ["{}"])[0])
        features_r = json.loads(qs_r.get("features", ["{}"])[0])

        print("  Phase 2: 回复")
        await _paginate(send_cmd, parsed_r.path, variables_r, features_r, collected, "回复", max_tweets)
        replies_count = len(collected) - tweets_count
        print(f"  回复阶段: +{replies_count} 条")
    else:
        print("  未能捕获 Replies URL，跳过回复")

    print(f"  总计: {len(collected)} 条")
    return list(collected.values())


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Twitter User Timeline Scraper")
    parser.add_argument("handles", nargs="+", help="用户 handle（如 @dotey）")
    parser.add_argument("-n", "--count", type=int, default=MAX_PER_USER,
                        help=f"每个用户最多采集条数（默认 {MAX_PER_USER}）")
    parser.add_argument("--host", type=str, default=CDP_HOST)
    parser.add_argument("--port", type=int, default=CDP_PORT)
    args = parser.parse_args()

    print("Twitter User Timeline Scraper")
    print(f"用户: {', '.join(args.handles)} | 每人最多 {args.count} 条\n")

    ws_url = await get_ws_url(args.host, args.port)

    msg_id = 0
    pending = {}
    stop_event = asyncio.Event()

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

        captured_requests = {}

        async def message_dispatcher():
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "id" in msg and msg["id"] in pending:
                    fut = pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg.get("result", {}))
                    continue
                # 拦截 UserTweets 请求
                method = msg.get("method", "")
                if method == "Network.requestWillBeSent":
                    req_url = msg.get("params", {}).get("request", {}).get("url", "")
                    if "/graphql/" in req_url and "/client-web/" not in req_url:
                        if "UserTweetsAndReplies" in req_url and not captured_requests.get("replies_url"):
                            captured_requests["replies_url"] = req_url
                        elif "UserTweets" in req_url and "Replies" not in req_url and not captured_requests.get("user_tweets_url"):
                            captured_requests["user_tweets_url"] = req_url

        dispatcher_task = asyncio.create_task(message_dispatcher())

        f = await send_cmd("Network.enable")
        await asyncio.wait_for(f, timeout=5)
        f = await send_cmd("Runtime.enable")
        await asyncio.wait_for(f, timeout=5)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        all_results = {}

        for handle in args.handles:
            handle = handle.lstrip("@")
            tweets = await scrape_user(handle, ws, send_cmd, captured_requests, max_tweets=args.count)

            if tweets:
                # 保存单用户文件
                user_path = str(OUTPUT_DIR / f"user_{handle}_{timestamp}.json")
                with open(user_path, "w", encoding="utf-8") as fp:
                    json.dump(tweets, fp, ensure_ascii=False, indent=2)
                print(f"\n  已保存: {user_path} ({len(tweets)} 条)")

                all_results[handle] = tweets

                # 简要统计
                avg_likes = sum(t["favorite_count"] for t in tweets) / len(tweets)
                avg_views = sum(t["views"] for t in tweets) / len(tweets)
                max_likes = max(tweets, key=lambda t: t["favorite_count"])
                print(f"  平均: {avg_likes:.0f} 赞 / {avg_views:.0f} 浏览")
                print(f"  最高赞: {max_likes['favorite_count']:,} - {max_likes['text'][:60]}...")

            # 用户间暂停
            await asyncio.sleep(5)

        stop_event.set()
        dispatcher_task.cancel()

    # 总结
    print(f"\n{'='*50}")
    print("采集完成")
    print(f"{'='*50}")
    for handle, tweets in all_results.items():
        print(f"  @{handle}: {len(tweets)} 条")
    print(f"  总计: {sum(len(t) for t in all_results.values())} 条")


if __name__ == "__main__":
    asyncio.run(main())
