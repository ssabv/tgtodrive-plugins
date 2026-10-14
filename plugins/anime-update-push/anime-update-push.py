# -*- coding: utf-8 -*-
"""追剧更新推送：订阅指定番剧/剧集，到点推送（TG / 飞书 / 通用 Webhook）。

用法：
  1. 实用工具 → 我的插件 → 导入本 .py 并确认信任。
  2. 表单点「🔍 搜索 TMDB」搜番，结果页点「➕ 订阅」；「📢 试推送」验证渠道。
  3. 定时执行建计划：cron 建议 */5 * * * *（分钟级），timeout 建议 120。

推送逻辑：
  - 追剧日历提供「哪天 + 几点」更新哪一集（条目带 HH:MM 时间）；
  - 分钟级开启时：播出时间（日期+时刻+时区校正）一到就推；没到点显示「等待播出」；
  - 条目没带时间或分钟级关闭：日历出现即推。
  - 已推送按「ID+季+集+日期」持久去重，绝不重复推。

渠道（可多选，只推勾选的）：
  - TG：复用主程序绑定的机器人；
  - 飞书：自定义机器人 Webhook（每行一个）；
  - 通用：POST JSON {plugin, event, title, text, updates}（每行一个）。
"""

PLUGIN_MANIFEST = {
    "schema_version": 1,
    "id": "anime-update-push",
    "name": "追剧更新推送",
    "version": "1.6.1",
    "description": "订阅指定番剧/剧集（搜索 TMDB 海报点选订阅），按档期时间到点推送 Telegram / 飞书 / 通用 Webhook，持久去重。零配置：凭据与运行设置全存插件数据目录，定时计划参数全部留空。",
    "author": "ssabv",
    "timeout": 120,
    "ui": {
        "width": "normal",
        "layout": "stack",
            "components": [
            {"type": "alert",
             "text": "首次使用：复制你的 OpenAPI Token（tgto_ 开头），贴进下方输入框点「🔑 保存凭据」，一次即可。之后：输入番名点「🔍 搜索 TMDB」→ 结果页「➕ 订阅」；配置点「⚙️ 查看配置」。定时计划参数全部留空。",
             "style": {"tone": "muted"}},
            {"type": "field", "name": "cmd",
             "help": "首次：贴 Token（tgto_ 开头）点「保存凭据」；日常：输番名点搜索、贴配置 JSON 点「应用配置」；定时计划：此框必须留空。"},
            {"type": "grid", "columns": 2, "children": [
                {"type": "button", "label": "🔍 搜索 TMDB", "action": "run",
                 "style": {"tone": "primary"}},
                {"type": "button", "label": "▶️ 检测更新", "action": "run",
                 "params": {"cmd": "{\"mode\": \"run\"}"},
                 "style": {"tone": "success"}},
            ]},
            {"type": "grid", "columns": 4, "children": [
                {"type": "button", "label": "📢 试推送", "action": "run",
                 "params": {"cmd": "{\"mode\": \"test_push\"}"},
                 "style": {"tone": "warning"}},
                {"type": "button", "label": "⚙️ 查看配置", "action": "run",
                 "params": {"cmd": "{\"mode\": \"show_config\"}"},
                 "style": {"tone": "muted", "variant": "outline"}},
                {"type": "button", "label": "💾 应用配置", "action": "run",
                 "style": {"tone": "primary", "variant": "outline"}},
                {"type": "button", "label": "🔑 保存凭据", "action": "run",
                 "style": {"tone": "danger", "variant": "outline"}},
            ]},
        ],
    },
    "inputs": [
        {
            "name": "cmd",
            "label": "内部指令（定时计划留空）",
            "type": "textarea",
            "default": "",
            "help": "插件页：输入番名点「搜索 TMDB」，或贴配置 JSON 点「应用配置」。定时计划：此框必须留空。",
        },
    ],
}

import datetime
import json
import os
import re
import time
from pathlib import Path

import requests

TG_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
TMDB_ID_RE = re.compile(r"^\d{1,10}$")
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
TIME_VAL_RE = re.compile(r"^\d{1,2}:\d{2}$")
TIME_KEY_RE = re.compile(r"time|时刻|播出", re.IGNORECASE)


# ---------- 基础 ----------

def _api_headers(token):
    return {"Authorization": "Bearer " + token}


def _api_call(base_url, token, method, path, **kwargs):
    kwargs.setdefault("timeout", (10, 60))
    kwargs.setdefault("allow_redirects", False)
    resp = requests.request(
        method, base_url + path, headers=_api_headers(token), **kwargs
    )
    if resp.status_code == 401:
        raise RuntimeError("OpenAPI Token 无效或过期（HTTP 401）")
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"接口 {method} {path} 失败：HTTP {resp.status_code} {resp.text[:400]}"
        )
    return resp


DEFAULT_BASE_URL = "http://127.0.0.1:12366"


def _resolve_api_config(params, data_dir):
    """凭据优先级：环境变量 > data_dir/openapi.json（表单不再承载凭据）。"""
    base_url = (params.get("api_base_url") or "").strip()
    token = (params.get("openapi_token") or "").strip()
    if not base_url:
        base_url = os.environ.get("TGTO_OPENAPI_BASE_URL", "").strip()
    if not token:
        token = os.environ.get("TGTO_OPENAPI_TOKEN", "").strip()
    if (not base_url or not token) and data_dir:
        try:
            cfg = json.loads(
                (Path(data_dir) / "openapi.json").read_text(encoding="utf-8")
            )
            base_url = base_url or str(cfg.get("base_url") or "").strip()
            token = token or str(cfg.get("token") or "").strip()
        except (OSError, ValueError):
            pass
    if not base_url:
        base_url = DEFAULT_BASE_URL
    if not token:
        raise ValueError(
            "缺少 OpenAPI Token：请先在插件页点「🔑 保存凭据」按钮"
            "（或在插件数据目录放 openapi.json / 配置环境变量 TGTO_OPENAPI_TOKEN）"
        )
    return base_url.rstrip("/"), token


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _split_lines(raw):
    return [l.strip() for l in (raw or "").splitlines() if l.strip()]


# ---------- 代理 / TG（复用主程序机器人） ----------

def _find_proxy_url(obj):
    candidates = []

    def walk(node, in_proxy_key):
        if isinstance(node, dict):
            for key, val in node.items():
                kl = str(key).lower()
                has_proxy = in_proxy_key or "proxy" in kl
                if isinstance(val, str):
                    v = val.strip()
                    if (
                        has_proxy and v
                        and v.lower() not in ("true", "false", "1", "0", "on", "off", "yes", "no")
                        and re.match(r"^(https?://[^\s]+|[\w.+-]+:\d{2,5})$", v)
                    ):
                        candidates.append(v)
                elif isinstance(val, (dict, list)):
                    walk(val, has_proxy)
        elif isinstance(node, list):
            for entry in node:
                walk(entry, in_proxy_key)

    walk(obj, False)
    if not candidates:
        return None
    url = candidates[0]
    if not url.lower().startswith(("http://", "https://", "socks")):
        url = "http://" + url
    return url


def _resolve_proxy(base_url, token):
    try:
        resp = _api_call(base_url, token, "GET", "/api/proxy_settings")
        url = _find_proxy_url(resp.json())
        if url:
            print(f"TG 代理：复用主程序代理 {url}")
            return {"http": url, "https": url}
    except Exception as exc:  # noqa: BLE001
        print(f"TG 代理：读取失败（直连）：{exc}")
    return None


def _collect_bot_pairs(obj):
    pairs, tokens, chats = [], [], []

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                kl = str(key).lower()
                if isinstance(val, str) and TG_TOKEN_RE.match(val.strip()):
                    tokens.append(val.strip())
                if (
                    isinstance(val, (str, int))
                    and str(val).strip().lstrip("-").isdigit()
                    and any(s in kl for s in ("chat", "admin", "target", "group"))
                ):
                    chats.append(str(val).strip())
                if isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    walk(obj)
    if tokens:
        for chat in dict.fromkeys(chats) or [""]:
            for token in dict.fromkeys(tokens):
                pairs.append((token, chat))
    return list(dict.fromkeys(pairs))


def _resolve_bot(base_url, token):
    for path in ("/api/proxy_settings", "/api/notify-bots"):
        try:
            resp = _api_call(base_url, token, "GET", path)
            pairs = _collect_bot_pairs(resp.json())
            if pairs:
                print(f"TG 通知：已从 {path} 发现 {len(pairs)} 个机器人目标")
                return pairs
        except Exception as exc:  # noqa: BLE001
            print(f"TG 通知：读取 {path} 失败：{exc}")
    print("TG 通知：未发现机器人配置")
    return []


def _send_tg(pairs, text, proxies):
    ok = 0
    for bot_token, chat_id in pairs:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text,
                      "disable_web_page_preview": True},
                timeout=(10, 30), proxies=proxies,
            )
            if resp.status_code == 200:
                ok += 1
                print(f"TG：已发送到 chat {chat_id}")
            else:
                print(f"TG：发送失败 HTTP {resp.status_code} {resp.text[:150]}")
        except Exception as exc:  # noqa: BLE001
            print(f"TG：发送失败：{exc}")
    return ok


def _send_feishu(urls, text):
    ok = 0
    for url in urls:
        try:
            resp = requests.post(
                url, json={"msg_type": "text", "content": {"text": text}},
                timeout=(10, 30),
            )
            body_ok = False
            try:
                body = resp.json()
                body_ok = (body.get("StatusCode") == 0
                           or body.get("code") == 0)
            except ValueError:
                pass
            if resp.status_code == 200 and body_ok:
                ok += 1
                print(f"飞书：已发送 {url[-20:]}")
            else:
                print(f"飞书：失败 HTTP {resp.status_code} {resp.text[:150]}")
        except Exception as exc:  # noqa: BLE001
            print(f"飞书：发送失败：{exc}")
    return ok


def _send_webhooks(urls, text, payload):
    ok = 0
    for url in urls:
        try:
            resp = requests.post(url, json=payload, timeout=(10, 30))
            if 200 <= resp.status_code < 300:
                ok += 1
                print(f"Webhook：已发送 {url[-20:]}")
            else:
                print(f"Webhook：失败 HTTP {resp.status_code} {resp.text[:150]}")
        except Exception as exc:  # noqa: BLE001
            print(f"Webhook：发送失败：{exc}")
    return ok


def _as_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return _split_lines(v)


def _dispatch(params, base_url, token, title, text, updates, event):
    """按配置渠道推送，返回 {渠道: 成功数}。"""
    channels = _as_list(params.get("push_channels")) or \
        ["tg", "feishu", "webhook"]
    results = {}
    if "tg" in channels:
        pairs = _resolve_bot(base_url, token)
        results["tg"] = (_send_tg(pairs, text, _resolve_proxy(base_url, token))
                         if pairs else 0)
    if "feishu" in channels:
        urls = _as_list(params.get("feishu_webhook"))
        results["feishu"] = _send_feishu(urls, text) if urls else 0
        if not urls:
            print("飞书：未配置 Webhook 地址，跳过")
    if "webhook" in channels:
        urls = _as_list(params.get("generic_webhooks"))
        if urls:
            payload = {"plugin": "anime-update-push", "event": event,
                       "title": title, "text": text, "updates": updates}
            results["webhook"] = _send_webhooks(urls, text, payload)
        else:
            print("Webhook：未配置地址，跳过")
            results["webhook"] = 0
    return results


# ---------- 订阅界面（搜索 / 添加 / 移除） ----------

def _load_sub_store(data_dir):
    store = _read_json(Path(data_dir) / "subs_ui.json", {})
    return store if isinstance(store, dict) else {}


def _save_sub_store(data_dir, store):
    _write_json(Path(data_dir) / "subs_ui.json", store)


def _find_poster(obj):
    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if "poster" in str(key).lower() and isinstance(val, str) and val.strip():
                    v = val.strip()
                    if v.startswith("http"):
                        return v
                    if v.startswith("/"):
                        return "https://image.tmdb.org/t/p/w300" + v
                if isinstance(val, (dict, list)):
                    r = walk(val)
                    if r:
                        return r
        elif isinstance(node, list):
            for item in node:
                r = walk(item)
                if r:
                    return r
        return None

    return walk(obj)


def _render_subs_ui(store):
    """当前订阅卡片 + 每张卡内移除按钮。"""
    if not store:
        return [{"type": "text",
                 "text": "当前订阅：空。输入关键词点「搜索 TMDB」添加。"}]
    comps = [{"type": "heading", "text": f"当前订阅（{len(store)}）"}]
    cards = []
    for key, sub in sorted(store.items(), key=lambda kv: kv[1].get("title", "")):
        slot = ""
        if sub.get("air_time"):
            wd = sub.get("air_weekday") or ""
            slot = f" · {wd} {sub['air_time']}" if wd else f" · {sub['air_time']}"
        cards.append({"type": "card", "label": sub.get("title", key),
                      "children": [
                          {"type": "text",
                           "text": f"TMDB {sub.get('tmdb_id')}{slot}",
                           "style": {"tone": "muted", "size": "small"}},
                          {"type": "button", "label": "🗑 移除订阅",
                           "action": "run",
                           "params": {"cmd": json.dumps(
                               {"mode": "remove", "remove_key": key},
                               ensure_ascii=False)},
                           "style": {"tone": "danger", "variant": "outline"}},
                      ]})
    for i in range(0, len(cards), 3):
        comps.append({"type": "grid", "columns": 3,
                      "children": cards[i:i + 3]})
    return comps


def _handle_search(base_url, token, params, store):
    q = (params.get("search_query") or "").strip()
    if not q:
        return {"summary": {"mode": "search", "error": "关键词为空"},
                "ui": [{"type": "alert",
                        "text": "先在表单「TMDB 搜索关键词」里输入番名，再点搜索按钮。",
                        "style": {"tone": "warning"}}] + _render_subs_ui(store)}
    try:
        resp = _api_call(base_url, token, "GET",
                         "/api/media/search?q=" + requests.utils.quote(q)
                         + "&media_type=tv")
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"搜索失败：{exc}")

    candidates = []

    def walk(node):
        if isinstance(node, dict):
            tid = node.get("tmdb_id") or node.get("tmdbId") or node.get("id")
            title = node.get("title") or node.get("name") or ""
            if isinstance(tid, int) and str(title).strip():
                candidates.append({"tmdb_id": tid, "title": str(title).strip()})
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    seen, results = set(), []
    for c in candidates:
        if c["tmdb_id"] in seen:
            continue
        seen.add(c["tmdb_id"])
        results.append(c)
    results = results[:8]

    comps = [{"type": "heading",
              "text": f"「{q}」搜索结果（{len(results)}）"}]
    if not results:
        comps.append({"type": "alert", "text": "没有搜到结果，换个关键词试试。",
                      "style": {"tone": "warning"}})
    for c in results:
        poster = None
        try:
            detail = _api_call(
                base_url, token, "GET",
                f"/api/media/details/tmdb/tv/{c['tmdb_id']}")
            poster = _find_poster(detail.json())
        except Exception:  # noqa: BLE001
            pass
        card = [
            ({"type": "image", "src": poster, "alt": c["title"],
              "caption": c["title"]} if poster else
             {"type": "text", "text": "🎬 " + c["title"],
              "style": {"align": "center"}}),
            {"type": "button", "label": "➕ 订阅", "action": "run",
             "params": {"cmd": json.dumps(
                 {"mode": "add", "add_tmdb_id": str(c["tmdb_id"]),
                  "add_title": c["title"]}, ensure_ascii=False)},
             "style": {"tone": "primary"}},
        ]
        comps.append({"type": "card", "label": c["title"], "children": card})
    comps.append({"type": "button", "label": "🔍 用输入框关键词再搜一次",
                  "action": "run",
                  "style": {"tone": "primary"}})
    return {"summary": {"mode": "search", "query": q, "results": len(results)},
            "ui": comps + _render_subs_ui(store)}


def _handle_add(base_url, token, params, store, data_dir):
    tid = (params.get("add_tmdb_id") or "").strip()
    title = (params.get("add_title") or "").strip() or f"TMDB {tid}"
    if not tid.isdigit():
        return {"summary": {"mode": "add", "error": "缺少 TMDB ID"},
                "ui": [{"type": "alert", "text": "缺少有效的 TMDB ID。",
                        "style": {"tone": "danger"}}] + _render_subs_ui(store)}
    key = f"tmdb:{tid}"
    store[key] = {"tmdb_id": int(tid), "title": title, "aliases": []}
    _save_sub_store(data_dir, store)
    return {"summary": {"mode": "add", "added": title, "total": len(store)},
            "ui": [{"type": "alert", "text": f"已订阅：{title}（TMDB {tid}）",
                    "style": {"tone": "success"}}] + _render_subs_ui(store)}


def _handle_remove(params, store, data_dir):
    key = (params.get("remove_key") or "").strip()
    removed = store.pop(key, None)
    _save_sub_store(data_dir, store)
    return {"summary": {"mode": "remove", "removed": bool(removed),
                        "total": len(store)},
            "ui": [{"type": "alert",
                    "text": f"已取消订阅：{removed.get('title', key)}"
                    if removed else "订阅项不存在（可能已移除）。",
                    "style": {"tone": "muted"}}] + _render_subs_ui(store)}


def _handle_test_push(params, cfg, base_url, token):
    text = "📢 追剧推送测试\n\n这是一条测试消息，收到即渠道配置正确。\n" \
           + time.strftime("%Y-%m-%d %H:%M:%S")
    results = _dispatch(cfg, base_url, token, "追剧推送测试", text, [],
                        "test")
    rows = [[ch, f"{n} 条成功" if n else "未发送/失败",
             "✅" if n else "⚠️"] for ch, n in results.items()] or \
        [["（未勾选任何渠道）", "-", "-"]]
    return {"summary": {"mode": "test_push", "results": results},
            "ui": [{"type": "alert",
                    "text": "试推送完成，按各端实际收信情况确认。",
                    "style": {"tone": "success" if any(results.values())
                              else "warning"}},
                   {"type": "table", "label": "渠道结果",
                    "headers": ["渠道", "结果", "状态"], "rows": rows}]}


# ---------- 日历解析（自适应，含分钟级时刻） ----------

def _collect_calendar_entries(obj, entries, ctx=None, depth=0):
    """深度遍历日历 JSON，收集剧集条目（含可选的播出时刻 HH:MM）。"""
    if depth > 8:
        return
    ctx = ctx or {}
    if isinstance(obj, dict):
        lower = {str(k).lower(): v for k, v in obj.items()}
        # 日期来源 A：日期字符串直接作为键、值为列表
        for key, val in obj.items():
            if DATE_KEY_RE.match(str(key)) and isinstance(val, list):
                ctx = dict(ctx, date=str(key)[:10])
        # 日期来源 B：date / air_date 等字段值为日期字符串（当天列表结构）
        for k in ("date", "air_date", "update_date", "day",
                  "calendar_date", "release_date"):
            v = lower.get(k)
            if isinstance(v, str) and DATE_KEY_RE.match(v.strip()):
                ctx = dict(ctx, date=v.strip()[:10])
                break
        # 日期来源 C：calendar_episode.air_date 嵌套
        ce = lower.get("calendar_episode")
        if isinstance(ce, dict):
            v = ce.get("air_date")
            if isinstance(v, str) and DATE_KEY_RE.match(v.strip()) and "date" not in ctx:
                ctx = dict(ctx, date=v.strip()[:10])
        tid = None
        for k in ("tmdb_id", "tmdbid", "tmdb"):
            if k in lower and isinstance(lower[k], (int, str)):
                try:
                    tid = int(lower[k])
                    break
                except (TypeError, ValueError):
                    pass
        if tid is None and "id" in lower and isinstance(lower["id"], int) \
                and any(s in lower for s in ("title", "name", "series_name")):
            tid = lower["id"]
        title = None
        for k in ("title", "series_name", "name"):
            if k in lower and isinstance(lower[k], str) and lower[k].strip():
                title = lower[k].strip()
                break
        episode = None
        for k in ("episode", "episode_number", "ep", "episode_num"):
            if k in lower and isinstance(lower[k], (int, float)):
                episode = int(lower[k])
                break
        season = None
        for k in ("season", "season_number"):
            if k in lower and isinstance(lower[k], (int, float)):
                season = int(lower[k])
                break
        air_time = None
        dt_local = None
        # 精确时刻：ISO 时间字段（如 first_aired "2026-10-14T04:00:00Z"）→ 转本地时区
        for k, v in lower.items():
            if isinstance(v, str) and ("aired" in k or "datetime" in k):
                mm = re.match(
                    r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?$",
                    v.strip())
                if mm:
                    try:
                        dt = datetime.datetime.fromisoformat(
                            v.strip().replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=datetime.timezone.utc)
                        dt_local = dt.astimezone()
                        air_time = dt_local.strftime("%H:%M")
                        ctx = dict(ctx, date=dt_local.strftime("%Y-%m-%d"))
                        break
                    except ValueError:
                        pass
        if air_time is None:
            for k, v in lower.items():
                if TIME_KEY_RE.search(k) and isinstance(v, str) \
                        and TIME_VAL_RE.match(v.strip()):
                    air_time = v.strip()
                    break

        new_ctx = dict(ctx)
        # 剧名优先级：外层（剧）> 内层（集名）；外层已有时不覆盖
        if title and "title" not in ctx:
            new_ctx["title"] = title
        if tid is not None:
            new_ctx["tmdb_id"] = tid
        if season is not None:
            new_ctx["season"] = season
        if air_time:
            new_ctx["air_time"] = air_time
        if dt_local is not None:
            new_ctx["datetime_local"] = dt_local

        if episode is not None and ("title" in new_ctx or "tmdb_id" in new_ctx):
            final_time = air_time or new_ctx.get("air_time")
            final_dt = dt_local if dt_local is not None \
                else new_ctx.get("datetime_local")
            entries.append({
                "tmdb_id": new_ctx.get("tmdb_id"),
                "title": new_ctx.get("title") or f"TMDB {new_ctx.get('tmdb_id')}",
                "season": new_ctx.get("season"),
                "episode": episode,
                "date": new_ctx.get("date", ctx.get("date", "")),
                "time": final_time,
                "datetime_local": final_dt.strftime("%Y-%m-%d %H:%M")
                if final_dt else "",
            })
        for key, val in obj.items():
            kl = str(key).lower()
            if kl in ("episodes", "items", "list", "results") and isinstance(val, list):
                for item in val:
                    _collect_calendar_entries(item, entries, new_ctx, depth + 1)
            elif isinstance(val, (dict, list)):
                _collect_calendar_entries(val, entries, new_ctx, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_calendar_entries(item, entries, ctx, depth + 1)


CONFIG_DEFAULTS = {
    "days": 1, "kind": "", "minute_level": True, "tz_offset": 0,
    "push_channels": ["tg", "feishu", "webhook"],
    "feishu_webhook": [], "generic_webhooks": [],
}


def _normalize_json_text(s):
    """容错归一化：全角/智能引号与标点 → ASCII，去掉尾逗号。"""
    s = (s.replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u201e", '"').replace("\uff02", '"')
          .replace("\u2018", "'").replace("\u2019", "'")
          .replace("\uff0c", ",")   # ，
          .replace("\uff1a", ":")   # ：
          .replace("\u3001", ",")   # 、
          )
    s = re.sub(r",\s*([}\]])", r"\1", s)  # 尾逗号
    return s


def _merge_config(cfg, raw):
    """解析并校验用户提交的配置 JSON，合并返回新配置。"""
    try:
        patch = json.loads(_normalize_json_text(raw))
        if not isinstance(patch, dict):
            raise ValueError("必须是 JSON 对象")
    except ValueError as exc:
        raise ValueError(
            f"配置 JSON 解析失败：{exc}（常见原因：输入法把引号/逗号"
            "转成了中文全角符号，可重新用英文标点粘贴）")
    new = dict(cfg)
    for k, v in patch.items():
        if k == "days":
            if not isinstance(v, int) or not 1 <= v <= 7:
                raise ValueError("days 必须是 1-7 的整数")
        elif k == "minute_level":
            if not isinstance(v, bool):
                raise ValueError("minute_level 必须是 true/false")
        elif k in ("tz_offset",):
            if not isinstance(v, (int, float)):
                raise ValueError("tz_offset 必须是数字")
        elif k == "kind":
            if not isinstance(v, str):
                raise ValueError("kind 必须是字符串")
        elif k == "push_channels":
            bad = [c for c in _as_list(v) if c not in ("tg", "feishu", "webhook")]
            if bad:
                raise ValueError(f"push_channels 含未知渠道：{bad}")
        elif k in ("feishu_webhook", "generic_webhooks"):
            if _as_list(v) and not all(
                str(u).startswith("http") for u in _as_list(v)
            ):
                raise ValueError(f"{k} 里的地址必须以 http 开头")
        else:
            raise ValueError(f"未知配置字段：{k}")
        new[k] = v
    return new


def _handle_show_config(cfg):
    current = {**CONFIG_DEFAULTS, **cfg}
    presets = [
        ("只推 TG", '["tg"]'), ("只推飞书", '["feishu"]'),
        ("只推 Webhook", '["webhook"]'), ("全渠道", '["tg","feishu","webhook"]'),
    ]
    comps = [{"type": "heading", "text": "当前配置"},
             {"type": "code", "text": json.dumps(current, ensure_ascii=False, indent=2)},
             {"type": "alert",
              "text": "改配置两种方式：① 点下面预设按钮一键切换渠道；② 把上面 JSON 复制到上方输入框，改好后点「💾 应用配置」。",
              "style": {"tone": "muted"}},
             {"type": "grid", "columns": 4, "children": [
                 {"type": "button", "label": label, "action": "run",
                  "params": {"cmd": json.dumps(
                      {"mode": "apply_config", "config_json":
                       '{"push_channels": ' + channels}, ensure_ascii=False)},
                  "style": {"tone": "primary", "variant": "outline"}}
                 for label, channels in presets]}]
    return {"summary": {"mode": "show_config", "config": current},
            "ui": comps}


def _handle_apply_config(params, cfg, data_dir):
    raw = (params.get("config_json") or "").strip()
    if not raw:
        return {"summary": {"mode": "apply_config", "error": "配置 JSON 为空"},
                "ui": [{"type": "alert",
                        "text": "上方输入框是空的：把当前配置贴进去改好后再点应用，或直接用预设按钮。",
                        "style": {"tone": "warning"}}]
                + _handle_show_config(cfg)["ui"]}
    try:
        new_cfg = _merge_config(cfg, raw)
    except ValueError as exc:
        return {"summary": {"mode": "apply_config", "error": str(exc)},
                "ui": [{"type": "alert", "text": f"配置未保存：{exc}",
                        "style": {"tone": "danger"}}]
                + _handle_show_config(cfg)["ui"]}
    _write_json(Path(data_dir) / "config.json", new_cfg)
    print("配置已更新:", json.dumps(new_cfg, ensure_ascii=False))
    return {"summary": {"mode": "apply_config", "config": new_cfg},
            "ui": [{"type": "alert", "text": "✅ 配置已应用，定时任务下次触发即生效（无需改动定时计划）。",
                    "style": {"tone": "success"}}]
            + _handle_show_config(new_cfg)["ui"]}


def _handle_save_token(interactive, data_dir):
    token = (interactive.get("token") or "").strip()
    base = (interactive.get("base_url") or "").strip() or DEFAULT_BASE_URL
    if not token:
        raise ValueError(
            "指令里没有 Token：请把 OpenAPI Token（tgto_ 开头）贴进输入框"
            "后再点「🔑 保存凭据」")
    _write_json(Path(data_dir) / "openapi.json",
                {"base_url": base.rstrip("/"), "token": token})
    return {"summary": {"mode": "save_token", "saved": True},
            "ui": [{"type": "alert",
                    "text": f"✅ 凭据已保存到插件数据目录（服务地址 {base}），"
                            "定时计划与运行无需再填任何参数。",
                    "style": {"tone": "success"}}]}


# ---------- 主流程 ----------

def run(params, context):
    data_dir = Path(context["data_dir"])

    # 解析「指令」输入：纯文本=搜索关键词；带 mode 的 JSON=交互指令；
    # 不带 mode 的 JSON=配置补丁；空=正常检测更新
    raw = (params.get("cmd") or "").strip()
    mode, interactive = "run", {}
    if raw:
        if raw.startswith("tgto_"):
            mode, interactive = "save_token", {"token": raw}
        elif raw.startswith("{"):
            try:
                parsed = json.loads(_normalize_json_text(raw))
            except ValueError as exc:
                raise ValueError(
                    f"指令 JSON 解析失败：{exc}（常见原因：输入法把引号/逗号"
                    "转成了中文全角符号，可重新用英文标点粘贴）")
            if not isinstance(parsed, dict):
                raise ValueError("指令 JSON 必须是对象")
            if "mode" in parsed:
                mode = str(parsed.pop("mode")).strip() or "run"
                interactive = parsed
            else:
                mode, interactive = "apply_config", {"config_json": raw}
        else:
            mode, interactive = "search", {"search_query": raw}

    if mode == "save_token":
        return _handle_save_token(interactive, data_dir)

    base_url, token = _resolve_api_config(interactive, str(data_dir))

    # 配置：全部来自 data_dir/config.json（表单参数不再承载运行配置，
    # 定时计划里填什么都不会影响运行）
    cfg = {**CONFIG_DEFAULTS, **_read_json(data_dir / "config.json", {})}

    store = _load_sub_store(data_dir)
    if mode == "search":
        return _handle_search(base_url, token, interactive, store)
    if mode == "add":
        return _handle_add(base_url, token, interactive, store, data_dir)
    if mode == "remove":
        return _handle_remove(interactive, store, data_dir)
    if mode == "test_push":
        return _handle_test_push(interactive, cfg, base_url, token)
    if mode == "show_config":
        return _handle_show_config(cfg)
    if mode == "apply_config":
        return _handle_apply_config(interactive, cfg, data_dir)

    days = int(cfg.get("days") or 1)
    kind = (cfg.get("kind") or "").strip()
    minute_level = bool(cfg.get("minute_level", True))
    tz_offset = float(cfg.get("tz_offset") or 0)

    subs = {k: v for k, v in store.items() if v.get("tmdb_id")}
    if not subs:
        raise ValueError(
            "没有订阅：请先在表单点「搜索 TMDB」添加订阅")
    pushed = _read_json(data_dir / "pushed.json", {})
    if not isinstance(pushed, dict):
        pushed = {}

    # 1. 拉日历
    query = f"/api/media/calendar?days={days}"
    if kind:
        query += "&kind=" + requests.utils.quote(kind)
    resp = _api_call(base_url, token, "GET", query)
    try:
        cal = resp.json()
    except ValueError:
        raise RuntimeError("日历接口返回非 JSON：" + resp.text[:200])
    meta = _read_json(data_dir / "meta.json", {})
    if not meta.get("calendar_raw_logged"):
        print("日历原始返回（仅首次打印，截断）：")
        print(json.dumps(cal, ensure_ascii=False)[:3000])
        meta["calendar_raw_logged"] = True
        _write_json(data_dir / "meta.json", meta)

    # 2. 解析条目
    entries = []
    _collect_calendar_entries(cal, entries)
    print(f"日历解析出 {len(entries)} 条剧集条目（最近 {days} 天），"
          f"其中带时刻 {sum(1 for e in entries if e.get('time'))} 条")

    # 3. 匹配订阅 + 分钟级到点判断 + 去重
    now = datetime.datetime.now()
    updates, pending, matched = [], [], set()
    for e in entries:
        hit = None
        for name, sub in subs.items():
            if e.get("tmdb_id") == sub.get("tmdb_id"):
                hit = name
                break
        if not hit:
            continue
        matched.add(hit)
        season = e.get("season")
        ep_label = (f"S{season:02d}" if season else "S??") + f"E{e['episode']:02d}"
        key = f"{e.get('tmdb_id') or hit}|{ep_label}|{e.get('date', '')}"
        if key in pushed:
            continue
        expected = None
        if minute_level:
            if e.get("datetime_local"):
                try:
                    expected = datetime.datetime.strptime(
                        e["datetime_local"], "%Y-%m-%d %H:%M"
                    ) + datetime.timedelta(hours=tz_offset)
                except ValueError:
                    expected = None
            elif e.get("time") and e.get("date"):
                try:
                    expected = datetime.datetime.strptime(
                        f"{e['date']} {e['time']}", "%Y-%m-%d %H:%M"
                    ) + datetime.timedelta(hours=tz_offset)
                except ValueError:
                    expected = None
        if expected is not None and now < expected:
            pending.append({"key": key, "name": hit, "title": e["title"],
                            "label": ep_label,
                            "at": expected.strftime("%m-%d %H:%M")})
            continue
        pushed[key] = {"t": e["title"],
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "date": e.get("date", "")}
        updates.append({"key": key, "name": hit, "title": e["title"],
                        "label": ep_label, "date": e.get("date", ""),
                        "time": e.get("time", "")})

    # 4. 推送
    if updates:
        lines = ["📺 追剧更新", ""]
        for u in updates[:15]:
            t = (f"（{u['time']}）" if u.get("time") else
                 (f"（{u['date']}）" if u.get("date") else ""))
            lines.append(f"✅ {u['title']} {u['label']}{t}")
        if len(updates) > 15:
            lines.append(f"…另有 {len(updates) - 15} 条")
        text = "\n".join(lines)
        results = _dispatch(cfg, base_url, token, "追剧更新", text,
                            updates, "episode_update")
        print("推送结果:", results)

    if len(pushed) > 3000:
        pushed = dict(sorted(pushed.items(),
                             key=lambda kv: kv[1].get("ts", ""),
                             reverse=True)[:2500])
    _write_json(data_dir / "pushed.json", pushed)

    print(f"订阅 {len(subs)} 项，命中 {len(matched)}，新推送 {len(updates)}，"
          f"等待播出 {len(pending)}")

    rows = [[u["title"][:80], u["label"], u["time"] or u["date"] or "-", "已推送"]
            for u in updates]
    rows += [[p["title"][:80], p["label"], p["at"], "⏳ 等待播出"]
             for p in pending]
    if not rows:
        rows = [["（无新更新）", "-", "-", "-"]]

    summary = {
        "subscriptions": len(subs),
        "calendar_entries": len(entries),
        "new_updates": len(updates),
        "pending": len(pending),
        "pushed_total": len(pushed),
    }
    return {
        "summary": summary,
        "updates": updates,
        "pending": pending,
        "ui": [
            {"type": "heading", "text": "追剧更新检测结果"},
            {"type": "alert",
             "text": f"订阅 {len(subs)} 项，新推送 {len(updates)} 条，"
                     f"等待播出 {len(pending)} 条。",
             "style": {"tone": "success" if updates else "muted"}},
            {"type": "table", "label": "更新列表",
             "headers": ["标题", "集数", "时间", "状态"], "rows": rows},
            {"type": "divider"},
            {"type": "button", "label": "🔍 搜索 TMDB 添加订阅",
             "action": "run", "params": {"mode": "search"},
             "style": {"tone": "primary"}},
            {"type": "grid", "columns": 2, "children": [
                {"type": "button", "label": "📢 试推送", "action": "run",
                 "params": {"cmd": "{\"mode\": \"test_push\"}"},
                 "style": {"tone": "warning", "variant": "outline"}},
            ]},
        ] + _render_subs_ui(store),
    }
