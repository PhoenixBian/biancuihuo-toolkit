#!/usr/bin/env python3
"""
Twitter Timeline Scraper via CDP
通过 Chrome DevTools Protocol 拦截首次 HomeTimeline 请求，
提取 auth headers 和 cursor，然后用 fetch() 翻页采集推文。
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, quote

try:
    import websockets
except ImportError:
    print("需要 websockets 库: pip install websockets")
    raise

# --- 配置 ---
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
TARGET_COUNT = 200
PAGE_INTERVAL = 3.0         # 每页请求间隔（秒）
PAUSE_EVERY = 100           # 每 N 页暂停一次
PAUSE_DURATION = 90         # 暂停时长（秒）
OUTPUT_DIR = Path(__file__).parent / "output"


async def get_twitter_ws_url(host=CDP_HOST, port=CDP_PORT):
    """获取 Twitter 页面的 WebSocket 调试 URL"""
    import urllib.request
    url = f"http://{host}:{port}/json"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        pages = json.loads(resp.read())

    for page in pages:
        page_url = page.get("url", "")
        if "x.com" in page_url or "twitter.com" in page_url:
            ws_url = page.get("webSocketDebuggerUrl")
            if ws_url:
                print(f"找到 Twitter 页面: {page_url}")
                return ws_url

    if pages:
        print(f"未找到 Twitter 页面，使用: {pages[0].get('url')}")
        return pages[0].get("webSocketDebuggerUrl")

    raise RuntimeError("没有可用的 Chrome 页面")


def parse_tweet(result):
    """从 GraphQL result 对象解析推文数据"""
    typename = result.get("__typename", "")

    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet", {})
        typename = result.get("__typename", "")

    if typename != "Tweet":
        return None

    legacy = result.get("legacy", {})
    if not legacy:
        return None

    tweet_id = result.get("rest_id")
    if not tweet_id:
        return None

    user_result = result.get("core", {}).get("user_results", {}).get("result", {})
    user_core = user_result.get("core", {})
    user_legacy = user_result.get("legacy", {})

    media_list = legacy.get("extended_entities", {}).get("media", [])
    if not media_list:
        media_list = legacy.get("entities", {}).get("media", [])

    media_urls = []
    for m in media_list:
        if m.get("type") in ("video", "animated_gif"):
            variants = m.get("video_info", {}).get("variants", [])
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                media_urls.append(best["url"])
            else:
                media_urls.append(m.get("media_url_https", ""))
        else:
            media_urls.append(m.get("media_url_https", ""))

    hashtags = [h.get("text", "") for h in legacy.get("entities", {}).get("hashtags", [])]

    views_raw = result.get("views", {}).get("count", "0")
    try:
        views = int(views_raw)
    except (ValueError, TypeError):
        views = 0

    is_retweet = "retweeted_status_result" in legacy
    retweeted_tweet = None
    if is_retweet:
        rt_result = legacy.get("retweeted_status_result", {}).get("result", {})
        retweeted_tweet = parse_tweet(rt_result)

    is_quote = legacy.get("is_quote_status", False)
    quoted_tweet = None
    if is_quote and "quoted_status_result" in result:
        qt_result = result.get("quoted_status_result", {}).get("result", {})
        quoted_tweet = parse_tweet(qt_result)

    screen_name = user_core.get("screen_name", "")

    return {
        "tweet_id": tweet_id,
        "tweet_url": f"https://x.com/{screen_name}/status/{tweet_id}" if screen_name else "",
        "text": legacy.get("full_text", ""),
        "user_name": user_core.get("name", ""),
        "user_handle": screen_name,
        "user_url": f"https://x.com/{screen_name}" if screen_name else "",
        "user_id": user_result.get("rest_id", ""),
        "user_followers": user_legacy.get("followers_count", 0),
        "user_following": user_legacy.get("friends_count", 0),
        "user_verified": user_result.get("is_blue_verified", False),
        "user_bio": user_result.get("profile_bio", {}).get("description", ""),
        "created_at": legacy.get("created_at", ""),
        "lang": legacy.get("lang", ""),
        "favorite_count": legacy.get("favorite_count", 0),
        "retweet_count": legacy.get("retweet_count", 0),
        "reply_count": legacy.get("reply_count", 0),
        "quote_count": legacy.get("quote_count", 0),
        "bookmark_count": legacy.get("bookmark_count", 0),
        "views": views,
        "hashtags": hashtags,
        "media_urls": media_urls,
        "is_retweet": is_retweet,
        "is_quote": is_quote,
        "retweeted_tweet": retweeted_tweet,
        "quoted_tweet": quoted_tweet,
    }


def extract_tweets_from_response(body):
    """从 GraphQL 响应体中提取推文列表和 bottom cursor"""
    tweets = []
    cursor_bottom = None

    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, TypeError):
        return tweets, None

    instructions = (
        data.get("data", {})
        .get("home", {})
        .get("home_timeline_urt", {})
        .get("instructions", [])
    )

    for instruction in instructions:
        entries = instruction.get("entries", [])
        if instruction.get("type") == "TimelineReplaceEntry":
            entry = instruction.get("entry")
            if entry:
                entries = [entry]

        for entry in entries:
            entry_id = entry.get("entryId", "")
            content = entry.get("content", {})

            if content.get("__typename") == "TimelineTimelineCursor":
                if "cursor-bottom" in entry_id or content.get("cursorType") == "Bottom":
                    cursor_bottom = content.get("value")
                continue

            if "promotedMetadata" in content:
                continue

            item_content = content.get("itemContent", {})
            if item_content.get("__typename") == "TimelineTweet":
                result = item_content.get("tweet_results", {}).get("result", {})
                tweet = parse_tweet(result)
                if tweet:
                    tweets.append(tweet)
            else:
                # Module items (e.g. "Who to follow" contains sub-tweets)
                for sub_item in content.get("items", []):
                    sub_content = sub_item.get("item", {}).get("itemContent", {})
                    if sub_content.get("__typename") == "TimelineTweet":
                        if "promotedMetadata" in sub_item.get("item", {}):
                            continue
                        result = sub_content.get("tweet_results", {}).get("result", {})
                        tweet = parse_tweet(result)
                        if tweet:
                            tweets.append(tweet)

    return tweets, cursor_bottom


async def scrape_timeline(target_count=TARGET_COUNT, host=CDP_HOST, port=CDP_PORT, following=False):
    """主采集函数：拦截首次请求，然后用 fetch + cursor 翻页"""
    ws_url = await get_twitter_ws_url(host, port)
    print(f"连接 WebSocket: {ws_url}")

    collected = {}
    msg_id = 0
    pending = {}
    stop_event = asyncio.Event()

    # 捕获首次请求的信息
    captured_request = {"url": None, "headers": None, "variables": None, "features": None}

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
            """统一读取 ws 消息并分发"""
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

                method = msg.get("method", "")
                params = msg.get("params", {})

                # 捕获首次 Timeline 请求的 URL 和 headers
                if method == "Network.requestWillBeSent" and not captured_request["url"]:
                    req = params.get("request", {})
                    req_url = req.get("url", "")
                    target_api = "HomeLatestTimeline" if following else "HomeTimeline"
                    if "/graphql/" in req_url and target_api in req_url and "/client-web/" not in req_url:
                        captured_request["url"] = req_url
                        captured_request["headers"] = req.get("headers", {})
                        parsed = urlparse(req_url)
                        qs = parse_qs(parsed.query)
                        captured_request["variables"] = json.loads(qs.get("variables", ["{}"])[0])
                        captured_request["features"] = json.loads(qs.get("features", ["{}"])[0])
                        captured_request["base_path"] = parsed.path
                        print(f"  捕获 API: {parsed.path}")

        # --- 启动 ---
        dispatcher_task = asyncio.create_task(message_dispatcher())

        f = await send_cmd("Network.enable")
        await asyncio.wait_for(f, timeout=5)
        f = await send_cmd("Runtime.enable")
        await asyncio.wait_for(f, timeout=5)

        # 导航到首页，拦截首次请求
        mode = "Following" if following else "For You"
        print(f"加载 Twitter 首页 ({mode})...")
        f = await send_cmd("Page.navigate", {"url": "https://x.com/home"})
        await asyncio.wait_for(f, timeout=15)
        await asyncio.sleep(3)

        # Following 模式：点击 Following tab
        if following:
            print("  切换到 Following tab...")
            f = await send_cmd("Runtime.evaluate", {
                "expression": """
                (() => {
                    const tabs = document.querySelectorAll('[role="tab"]');
                    for (const tab of tabs) {
                        if (tab.textContent.trim() === 'Following') {
                            tab.click();
                            return 'clicked';
                        }
                    }
                    return 'not_found';
                })()
                """,
                "returnByValue": True,
            })
            result = await asyncio.wait_for(f, timeout=5)
            click_result = result.get("result", {}).get("value", "")
            print(f"  Tab 点击: {click_result}")
            await asyncio.sleep(2)

        # 等待首次请求被捕获
        for _ in range(20):
            if captured_request["url"]:
                break
            await asyncio.sleep(0.5)

        if not captured_request["url"]:
            print("未能捕获 HomeTimeline 请求")
            stop_event.set()
            dispatcher_task.cancel()
            return []

        # 等待页面加载完成
        await asyncio.sleep(3)

        # 准备输出文件（增量保存）
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        live_path = str(OUTPUT_DIR / f"timeline_{timestamp}.jsonl")
        print(f"\n开始采集，目标 {target_count} 条推文")
        print(f"实时写入: {live_path}\n")

        base_path = captured_request["base_path"]
        variables = captured_request["variables"]
        features = captured_request["features"]

        cursor = None
        page_num = 0
        empty_pages = 0
        refresh_count = 0
        max_refreshes = 50  # 最多刷新 50 轮，防无限循环
        live_file = open(live_path, "a", encoding="utf-8")

        async def fetch_page(api_url):
            """在页面上下文中 fetch API"""
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
                if (!resp.ok) return JSON.stringify({{error: resp.status, text: await resp.text()}});
                return await resp.text();
            }})()
            """
            fut = await send_cmd("Runtime.evaluate", {
                "expression": fetch_js,
                "awaitPromise": True,
                "returnByValue": True,
            })
            return await asyncio.wait_for(fut, timeout=15)

        try:
            while len(collected) < target_count:
                page_num += 1

                # 每 PAUSE_EVERY 页暂停，防 rate limit
                if page_num > 1 and (page_num - 1) % PAUSE_EVERY == 0:
                    print(f"\n  === 第 {page_num - 1} 页完成，暂停 {PAUSE_DURATION}s 防风控 ===\n")
                    await asyncio.sleep(PAUSE_DURATION)

                # 构建请求参数
                req_vars = dict(variables)
                if cursor:
                    req_vars["cursor"] = cursor

                params_str = urlencode({
                    "variables": json.dumps(req_vars, separators=(",", ":")),
                    "features": json.dumps(features, separators=(",", ":")),
                }, quote_via=quote)

                api_url = f"https://x.com{base_path}?{params_str}"

                try:
                    result = await fetch_page(api_url)
                except asyncio.TimeoutError:
                    print(f"  页 {page_num}: 请求超时，重试...")
                    await asyncio.sleep(10)
                    continue

                body = result.get("result", {}).get("value", "")
                if not body:
                    error = result.get("exceptionDetails", {})
                    print(f"  页 {page_num}: fetch 失败 - {error}")
                    break

                # 检查 rate limit
                try:
                    body_check = json.loads(body)
                    if "error" in body_check and isinstance(body_check.get("error"), int):
                        status = body_check["error"]
                        if status == 429:
                            print(f"  页 {page_num}: Rate limited! 等待 120s...")
                            await asyncio.sleep(120)
                            continue
                        else:
                            print(f"  页 {page_num}: HTTP {status}")
                            break
                except (json.JSONDecodeError, TypeError):
                    pass

                tweets, new_cursor = extract_tweets_from_response(body)

                new_count = 0
                for tweet in tweets:
                    tid = tweet["tweet_id"]
                    if tid not in collected:
                        collected[tid] = tweet
                        new_count += 1
                        live_file.write(json.dumps(tweet, ensure_ascii=False) + "\n")

                if new_count > 0:
                    live_file.flush()

                print(f"  页 {page_num}: +{new_count} 新推文 | 总计: {len(collected)}/{target_count}")

                if new_count == 0:
                    empty_pages += 1
                    if empty_pages >= 3:
                        # 推荐池耗尽，刷新页面获取新一批推荐
                        refresh_count += 1
                        if refresh_count >= max_refreshes:
                            print(f"\n  已刷新 {max_refreshes} 轮，停止采集")
                            break
                        print(f"\n  --- 第 {refresh_count} 轮刷新（已采 {len(collected)} 条），等 30s 让算法刷新推荐池 ---\n")
                        await asyncio.sleep(30)
                        # 重新 navigate 刷新页面
                        nav_fut = await send_cmd("Page.navigate", {"url": "https://x.com/home"})
                        await asyncio.wait_for(nav_fut, timeout=15)
                        await asyncio.sleep(5)
                        # 重置 cursor 和 empty 计数
                        cursor = None
                        empty_pages = 0
                        continue
                else:
                    empty_pages = 0

                if not new_cursor:
                    print("  无更多 cursor，到达末尾")
                    break

                cursor = new_cursor
                await asyncio.sleep(PAGE_INTERVAL)

        finally:
            live_file.close()
            stop_event.set()
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass

        # 写最终 JSON（方便后续分析）
        final_path = live_path.replace(".jsonl", ".json")
        tweets_list = list(collected.values())
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(tweets_list, f, ensure_ascii=False, indent=2)
        print(f"\n实时数据: {live_path} ({len(tweets_list)} 条)")
        print(f"最终 JSON: {final_path}")

    return tweets_list


def generate_stats(tweets):
    """生成统计摘要"""
    if not tweets:
        return "无数据"

    total = len(tweets)
    avg_likes = sum(t["favorite_count"] for t in tweets) / total
    avg_views = sum(t["views"] for t in tweets) / total
    avg_retweets = sum(t["retweet_count"] for t in tweets) / total
    avg_replies = sum(t["reply_count"] for t in tweets) / total
    avg_bookmarks = sum(t["bookmark_count"] for t in tweets) / total

    retweets = sum(1 for t in tweets if t["is_retweet"])
    quotes = sum(1 for t in tweets if t["is_quote"])
    with_media = sum(1 for t in tweets if t["media_urls"])

    langs = {}
    for t in tweets:
        lang = t["lang"] or "unknown"
        langs[lang] = langs.get(lang, 0) + 1
    top_langs = sorted(langs.items(), key=lambda x: -x[1])[:5]

    top10 = sorted(tweets, key=lambda t: t["favorite_count"], reverse=True)[:10]

    lines = [
        "=" * 60,
        "采集统计",
        "=" * 60,
        f"总推文数: {total}",
        f"原创: {total - retweets} | 转推: {retweets} | 引用: {quotes}",
        f"含媒体: {with_media}",
        "",
        "平均互动:",
        f"  点赞: {avg_likes:.1f} | 转发: {avg_retweets:.1f} | 回复: {avg_replies:.1f}",
        f"  书签: {avg_bookmarks:.1f} | 浏览: {avg_views:.0f}",
        "",
        f"语言分布: {', '.join(f'{l}({c})' for l, c in top_langs)}",
        "",
        "Top 10 高赞推文:",
        "-" * 60,
    ]

    for i, t in enumerate(top10, 1):
        text = t["text"].replace("\n", " ")[:80]
        lines.append(
            f"  {i}. @{t['user_handle']} ({t['user_followers']:,} followers)"
        )
        lines.append(f"     {text}...")
        lines.append(
            f"     ❤ {t['favorite_count']:,}  🔁 {t['retweet_count']:,}  "
            f"💬 {t['reply_count']:,}  👁 {t['views']:,}  🔖 {t['bookmark_count']:,}"
        )

    lines.append("=" * 60)
    return "\n".join(lines)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Twitter Timeline Scraper via CDP")
    parser.add_argument("-n", "--count", type=int, default=TARGET_COUNT,
                        help=f"目标推文数量 (默认 {TARGET_COUNT})")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="输出文件路径 (默认 output/timeline_YYYYMMDD_HHMMSS.json)")
    parser.add_argument("--host", type=str, default=CDP_HOST,
                        help=f"CDP 主机 (默认 {CDP_HOST})")
    parser.add_argument("--port", type=int, default=CDP_PORT,
                        help=f"CDP 端口 (默认 {CDP_PORT})")
    parser.add_argument("--following", action="store_true",
                        help="采集 Following 时间线（默认 For You）")
    args = parser.parse_args()

    mode = "Following" if args.following else "For You"
    print("Twitter Timeline Scraper")
    print(f"CDP: {args.host}:{args.port} | 模式: {mode}\n目标: {args.count} 条推文\n")

    tweets = await scrape_timeline(target_count=args.count, host=args.host, port=args.port, following=args.following)

    if not tweets:
        print("未采集到任何推文")
        return

    # 统计
    stats = generate_stats(tweets)
    print(f"\n{stats}")


if __name__ == "__main__":
    asyncio.run(main())
