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
    "version": "1.8.2",
    "description": "订阅指定番剧/剧集（搜索 TMDB 海报点选订阅），按档期时间到点推送 Telegram / 飞书 / 通用 Webhook，持久去重。",
    "author": "ssabv",
    "timeout": 120,
    "ui": {
        "width": "normal",
        "layout": "stack",
            "components": [
            {"type": "alert",
             "text": "选番：输入关键词点「搜索 TMDB」→ 结果页点海报下「➕ 订阅」。点「试推送」验证渠道。定时计划：填好配置点「💾 保存配置」，计划参数全部留空即可共享这份配置。",
             "style": {"tone": "muted"}},
            {"type": "field", "name": "search_query"},
            {"type": "grid", "columns": 3, "children": [
                {"type": "button", "label": "🔍 搜索 TMDB", "action": "run",
                 "params": {"mode": "search"},
                 "style": {"tone": "primary"}},
                {"type": "button", "label": "▶️ 检测更新", "action": "run",
                 "params": {"mode": "run"},
                 "style": {"tone": "success"}},
                {"type": "button", "label": "📢 试推送", "action": "run",
                 "params": {"mode": "test_push"},
                 "style": {"tone": "warning"}},
            ]},
            {"type": "grid", "columns": 3, "children": [
                {"type": "button", "label": "💾 保存当前配置（供定时计划共享）",
                 "action": "run", "params": {"mode": "save_config"},
                 "style": {"tone": "primary", "variant": "outline"}},
                {"type": "button", "label": "⏳ 查看待推送", "action": "run",
                 "params": {"mode": "run"},
                 "style": {"tone": "muted", "variant": "outline"}},
            ]},
        ],
    },
    "inputs": [
        {
            "name": "api_base_url",
            "label": "服务地址",
            "type": "text",
            "default": "http://127.0.0.1:12366",
            "help": "容器内可达的本服务地址；与运行接口同容器时用默认值即可。留空则读环境变量。",
        },
        {
            "name": "openapi_token",
            "label": "OpenAPI Token",
            "type": "password",
            "default": "",
            "help": "在「实用工具 → OpenAPI」创建。可填在这里，也可配置环境变量，或在插件数据目录放 openapi.json。",
        },
        {
            "name": "tmdb_api_key",
            "label": "TMDB API Key（可选）",
            "type": "password",
            "default": "",
            "help": "选填。填写后可直连 TMDB 取每集剧照（主程序未开「完整季集模式」时的兜底）；不填则尽力而为。",
        },
        {
            "name": "mode",
            "label": "操作模式",
            "type": "select",
            "default": "run",
            "options": [
                {"label": "正常检测更新", "value": "run"},
                {"label": "搜索 TMDB 选番", "value": "search"},
                {"label": "添加订阅", "value": "add"},
                {"label": "取消订阅", "value": "remove"},
                {"label": "试推送", "value": "test_push"},
                {"label": "保存当前配置", "value": "save_config"},
            ],
            "help": "一般不用手动改：用表单/结果页按钮操作即可。",
        },
        {
            "name": "search_query",
            "label": "TMDB 搜索关键词",
            "type": "text",
            "default": "",
            "placeholder": "输入番名后点「搜索 TMDB」按钮",
        },
        {
            "name": "add_tmdb_id",
            "label": "待添加 TMDB ID",
            "type": "text",
            "default": "",
        },
        {
            "name": "add_title",
            "label": "待添加标题",
            "type": "text",
            "default": "",
        },
        {
            "name": "remove_key",
            "label": "待移除订阅键",
            "type": "text",
            "default": "",
        },
        {
            "name": "days",
            "label": "检查最近几天",
            "type": "number",
            "default": 1,
            "min": 1,
            "max": 7,
            "help": "检查最近 N 天的日历更新（含今天），防止错过。已推送过的不会重复推。",
        },
        {
            "name": "kind",
            "label": "日历类型",
            "type": "text",
            "default": "",
            "placeholder": "留空取全部",
            "help": "日历接口的 kind 筛选值，留空不过滤。",
        },
        {
            "name": "minute_level",
            "label": "分钟级到点推送",
            "type": "boolean",
            "default": True,
            "help": "日历条目带播出时刻（如 22:40）时，到点才推；没到点显示等待。条目无时间则日历出现即推。",
        },
        {
            "name": "tz_offset",
            "label": "时间校正（小时）",
            "type": "number",
            "default": 0,
            "help": "若推送时机明显偏早/偏晚，用此项加减小时数校正（如 -8、+1）。一般保持 0。",
        },
        {
            "name": "push_channels",
            "label": "推送渠道（可多选）",
            "type": "multiselect",
            "default": ["tg", "feishu", "webhook"],
            "options": [
                {"label": "Telegram（主程序机器人）", "value": "tg"},
                {"label": "飞书 Webhook", "value": "feishu"},
                {"label": "通用 Webhook", "value": "webhook"},
            ],
            "help": "只勾选的渠道会收到推送；对应渠道需先填好下方地址。用「试推送」按钮验证。",
        },
        {
            "name": "feishu_webhook",
            "label": "飞书 Webhook（每行一个）",
            "type": "textarea",
            "default": "",
            "placeholder": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
            "help": "飞书群自定义机器人 Webhook 地址，支持多个。",
        },
        {
            "name": "generic_webhooks",
            "label": "通用 Webhook（每行一个）",
            "type": "textarea",
            "default": "",
            "placeholder": "https://example.com/hook",
            "help": "POST JSON：{plugin, event, title, text, updates}。支持多个，可自行适配 n8n / Bark / Server酱 等。",
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


def _resolve_api_config(params, data_dir):
    """凭据优先级：表单 > 环境变量 > data_dir/openapi.json。"""
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
    if not base_url or not token:
        raise ValueError(
            "缺少服务地址或 OpenAPI Token：请在表单填写，或配置环境变量，"
            "或在插件数据目录放 openapi.json"
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


def _send_tg(pairs, text, proxies, poster=None):
    ok = 0
    for bot_token, chat_id in pairs:
        sent = False
        if poster:
            # 先试带海报的 sendPhoto（caption 上限 1024 字符）
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                    json={"chat_id": chat_id, "photo": poster,
                          "caption": text[:1000],
                          "disable_web_page_preview": True},
                    timeout=(10, 30), proxies=proxies,
                )
                if resp.status_code == 200:
                    sent = True
                else:
                    print(f"TG：带海报发送失败 HTTP {resp.status_code}，"
                          "回退纯文本")
            except Exception as exc:  # noqa: BLE001
                print(f"TG：带海报发送失败：{exc}，回退纯文本")
        if not sent:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text,
                          "disable_web_page_preview": not poster},
                    timeout=(10, 30), proxies=proxies,
                )
                sent = resp.status_code == 200
                if not sent:
                    print(f"TG：发送失败 HTTP {resp.status_code} "
                          f"{resp.text[:150]}")
            except Exception as exc:  # noqa: BLE001
                print(f"TG：发送失败：{exc}")
        if sent:
            ok += 1
            print(f"TG：已发送到 chat {chat_id}"
                  + ("（带海报）" if poster else ""))
    return ok


def _send_feishu(urls, text, poster=None):
    if poster:
        text = text + f"\n\n海报：{poster}"
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


def _dispatch(params, base_url, token, title, text, updates, event,
              poster=None):
    """按勾选渠道推送，返回 {渠道: 成功数}。poster 取第一条更新的海报。"""
    channels = params.get("push_channels") or []
    if isinstance(channels, str):
        channels = [channels] if channels else []
    results = {}
    if "tg" in channels:
        pairs = _resolve_bot(base_url, token)
        results["tg"] = (_send_tg(pairs, text, _resolve_proxy(base_url, token),
                                  poster)
                         if pairs else 0)
    if "feishu" in channels:
        urls = _split_lines(params.get("feishu_webhook"))
        results["feishu"] = (_send_feishu(urls, text, poster) if urls else 0)
        if not urls:
            print("飞书：未配置 Webhook 地址，跳过")
    if "webhook" in channels:
        urls = _split_lines(params.get("generic_webhooks"))
        if urls:
            payload = {"plugin": "anime-update-push", "event": event,
                       "title": title, "text": text, "updates": updates,
                       "poster": poster}
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
                           "params": {"mode": "remove", "remove_key": key},
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
             "params": {"mode": "add", "add_tmdb_id": str(c["tmdb_id"]),
                        "add_title": c["title"]},
             "style": {"tone": "primary"}},
        ]
        comps.append({"type": "card", "label": c["title"], "children": card})
    comps.append({"type": "button", "label": "🔍 用表单关键词再搜一次",
                  "action": "run", "params": {"mode": "search"},
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


def _find_still(obj):
    """深度查找剧集剧照（still_path），找不到再退回海报。"""
    for want in ("still", "poster"):
        def walk(node):
            if isinstance(node, dict):
                for key, val in node.items():
                    if want in str(key).lower() and isinstance(val, str) \
                            and val.strip():
                        v = val.strip()
                        if v.startswith("http"):
                            return v
                        if v.startswith("/"):
                            return "https://image.tmdb.org/t/p/w500" + v
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
        r = walk(obj)
        if r:
            return r
    return None


def _tmdb_api(tmdb_key, path, proxies):
    """直连 TMDB 官方 API。"""
    resp = requests.get(
        "https://api.themoviedb.org/3" + path,
        params={"api_key": tmdb_key, "language": "zh-CN"},
        timeout=(10, 20), proxies=proxies)
    return resp.json()


def _tmdb_cfg(eff, base_url, token):
    """从配置取 TMDB Key 与代理 proxies 字典。"""
    key = str((eff or {}).get("tmdb_api_key") or "").strip() or None
    proxies = None
    try:
        px = _resolve_proxy(base_url, token)
        if px:
            proxies = {"http": px, "https": px}
    except Exception:  # noqa: BLE001
        pass
    return key, proxies


def _resolve_season(base_url, token, tmdb_id, episode, cache,
                    tmdb_key=None, proxies=None):
    """季号缺失/为 0 时推断真实季号，优先级：
    1) 主程序详情 seasons[]；2) TMDB 官方 API seasons[]；
    3) 主程序详情 number_of_seasons。结果缓存到本次运行。"""
    key = f"season:{tmdb_id}:{episode}"
    if key in cache:
        return cache[key]
    season = None

    def pick(seasons):
        if not seasons:
            return None
        fit = [sn for sn, ec in seasons if ec >= episode > 0]
        return max(fit) if fit else max(sn for sn, _ in seasons)

    seasons = []
    try:
        resp = _api_call(base_url, token, "GET",
                         f"/api/media/details/tmdb/tv/{tmdb_id}")
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        node = None
        for n in _walk_lists(data):
            if isinstance(n.get("seasons"), list):
                node = n
                break
        if node:
            for s in node["seasons"]:
                if not isinstance(s, dict):
                    continue
                sn = s.get("season_number")
                ec = s.get("episode_count")
                if isinstance(sn, int) and sn >= 1:
                    seasons.append((sn, ec if isinstance(ec, int) else 0))
            season = pick(seasons)
        elif tmdb_key:
            n = _tmdb_api(tmdb_key, f"/tv/{tmdb_id}", proxies) or {}
            for s in n.get("seasons") or []:
                sn = s.get("season_number")
                ec = s.get("episode_count")
                if isinstance(sn, int) and sn >= 1:
                    seasons.append((sn, ec if isinstance(ec, int) else 0))
            season = pick(seasons)
        else:
            nos = data.get("number_of_seasons") if isinstance(data, dict) \
                else None
            if isinstance(nos, int) and nos >= 1:
                season = nos
    except Exception:  # noqa: BLE001
        pass
    cache[key] = season
    return season


def _fetch_still(base_url, token, tmdb_id, season, episode, cache,
                 tmdb_key=None, proxies=None):
    """取该集剧照：主程序季集接口 → TMDB 官方 API → 退海报。"""
    key = f"{tmdb_id}:{season}:{episode}"
    if key in cache:
        return cache[key]
    if not (isinstance(season, int) and season > 0):
        season = _resolve_season(base_url, token, tmdb_id,
                                 episode if isinstance(episode, int) else 0,
                                 cache, tmdb_key, proxies)
    url = None

    def still_from(payload):
        for ep in _walk_lists(payload):
            if isinstance(ep, dict) \
                    and ep.get("episode_number") == episode:
                st = ep.get("still_path")
                if isinstance(st, str) and st.strip():
                    return ("https://image.tmdb.org/t/p/w500" + st.strip()
                            if st.startswith("/") else st.strip())
        return None

    if isinstance(season, int) and season > 0 and isinstance(episode, int):
        try:
            resp = _api_call(
                base_url, token, "GET",
                f"/api/media/details/tmdb/tv/{tmdb_id}/season/{season}")
            url = still_from(resp.json())
        except Exception:  # noqa: BLE001
            pass
        if url is None and tmdb_key:
            try:
                url = still_from(
                    _tmdb_api(tmdb_key,
                              f"/tv/{tmdb_id}/season/{season}", proxies))
            except Exception:  # noqa: BLE001
                pass
    if url is None:
        try:
            resp = _api_call(base_url, token, "GET",
                             f"/api/media/details/tmdb/tv/{tmdb_id}")
            url = _find_still(resp.json())
        except Exception:  # noqa: BLE001
            pass
    cache[key] = url
    return url


def _walk_lists(obj):
    """深度遍历，yield 所有 dict 节点。"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_lists(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_lists(item)


def _handle_test_push(params, eff, base_url, token, data_dir=None):
    """试推送 = 推一条真实档期数据（含海报），格式与正式推送完全一致。"""
    days = int(eff.get("days") or 7)
    subs = _load_sub_store(data_dir) if data_dir else {}
    sub_ids = {v.get("tmdb_id") for v in subs.values() if v.get("tmdb_id")}
    tmdb_key, tmdb_proxies = _tmdb_cfg(eff, base_url, token)
    sample_updates, poster = [], None
    if sub_ids:
        try:
            query = f"/api/media/calendar?days={days}"
            kind = (eff.get("kind") or "").strip()
            if kind:
                query += "&kind=" + requests.utils.quote(kind)
            cal = _api_call(base_url, token, "GET", query).json()
            entries = []
            _collect_calendar_entries(cal, entries)
            cache = {}
            for e in entries:
                if e.get("tmdb_id") not in sub_ids:
                    continue
                season = e.get("season")
                if not (isinstance(season, int) and season > 0):
                    season = _resolve_season(base_url, token,
                                             e.get("tmdb_id"),
                                             e["episode"], cache,
                                             tmdb_key, tmdb_proxies)
                ep = ((f"S{season:02d}" if season else "S??")
                      + f"E{e['episode']:02d}")
                sample_updates.append({"key": e.get("tmdb_id"), "title": e["title"],
                                       "label": ep, "date": e.get("date", ""),
                                       "time": e.get("time", ""),
                                       "tmdb_id": e.get("tmdb_id"),
                                       "season": season,
                                       "episode": e["episode"]})
                if len(sample_updates) >= 3:
                    break
            if sample_updates:
                s0 = sample_updates[0]
                poster = _fetch_still(base_url, token, s0["tmdb_id"],
                                      s0.get("season"), s0.get("episode"),
                                      cache, tmdb_key, tmdb_proxies)
        except Exception as exc:  # noqa: BLE001
            print(f"试推送：读取日历失败，退回纯文本测试：{exc}")
    if sample_updates:
        lines = ["📺 追剧推送预览（真实档期数据）", ""]
        for u in sample_updates:
            t = (f"（{u['time']}）" if u.get("time") else
                 (f"（{u['date']}）" if u.get("date") else ""))
            lines.append(f"✅ {u['title']} {u['label']}{t}")
        text = "\n".join(lines)
        title, event = "追剧推送预览", "test_real"
    else:
        text = "📢 追剧推送测试\n\n这是渠道测试消息（暂无订阅剧集的档期数据）。\n" \
               + time.strftime("%Y-%m-%d %H:%M:%S")
        title, event = "追剧推送测试", "test"
    results = _dispatch(eff, base_url, token, title, text, sample_updates,
                        event, poster)
    rows = [[ch, f"{n} 条成功" if n else "未发送/失败",
             "✅" if n else "⚠️"] for ch, n in results.items()] or \
        [["（未勾选任何渠道）", "-", "-"]]
    note = ("已按真实档期数据推送" + ("（含海报）" if poster else ""))
    if not sample_updates:
        note = "订阅暂无近期档期，已发送纯文本测试"
    return {"summary": {"mode": "test_push", "results": results,
                        "sample_count": len(sample_updates)},
            "ui": [{"type": "alert",
                    "text": f"试推送完成：{note}。按各端实际收信情况确认。",
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


# ---------- 主流程 ----------

def run(params, context):
    base_url, token = _resolve_api_config(params, context.get("data_dir", ""))
    data_dir = Path(context["data_dir"])

    # 配置共享：data_dir/config.json 存档，表单/定时计划里留空的参数自动用它
    cfg = _read_json(data_dir / "config.json", {})
    eff = dict(cfg)
    for k, v in params.items():
        if v is None or v == "" or v == []:
            continue
        eff[k] = v

    # 交互模式
    mode = (params.get("mode") or "run").strip()
    store = _load_sub_store(data_dir)
    if mode == "search":
        return _handle_search(base_url, token, params, store)
    if mode == "add":
        return _handle_add(base_url, token, params, store, data_dir)
    if mode == "remove":
        return _handle_remove(params, store, data_dir)
    if mode == "test_push":
        return _handle_test_push(params, eff, base_url, token, data_dir)
    if mode == "save_config":
        defaults = {"days": 1, "kind": "", "minute_level": True, "tz_offset": 0,
                    "push_channels": ["tg", "feishu", "webhook"],
                    "feishu_webhook": "", "generic_webhooks": ""}
        snap = {k: eff.get(k, d) for k, d in defaults.items()}
        _write_json(data_dir / "config.json", snap)
        print("已保存运行配置:", json.dumps(snap, ensure_ascii=False))
        return {"summary": {"mode": "save_config", "saved": snap},
                "ui": [{"type": "alert",
                        "text": "已保存当前配置。定时计划的运行参数全部留空即可共享这份配置"
                                "（订阅本来就在插件数据目录，自动共享）。",
                        "style": {"tone": "success"}},
                       {"type": "code", "text": json.dumps(
                           snap, ensure_ascii=False, indent=2)}]}

    days = int(eff.get("days") or 1)
    kind = (eff.get("kind") or "").strip()
    minute_level = bool(eff.get("minute_level", True))
    tz_offset = float(eff.get("tz_offset") or 0)

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
    _season_cache = {}
    _tmdb_key, _tmdb_proxies = _tmdb_cfg(eff, base_url, token)
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
        if not (isinstance(season, int) and season > 0):
            season = _resolve_season(base_url, token, e.get("tmdb_id"),
                                     e["episode"], _season_cache,
                                     _tmdb_key, _tmdb_proxies)
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
                        "time": e.get("time", ""),
                        "tmdb_id": e.get("tmdb_id"),
                        "season": e.get("season"),
                        "episode": e["episode"]})

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
        poster_cache = {}
        for u in updates[:3]:  # 最多取前 3 条的剧照，TG 用第一条
            u["still"] = _fetch_still(base_url, token, u.get("tmdb_id"),
                                      u.get("season"), u.get("episode"),
                                      poster_cache, _tmdb_key, _tmdb_proxies)
        poster = next((u["still"] for u in updates[:3] if u.get("still")),
                      None)
        results = _dispatch(eff, base_url, token, "追剧更新", text,
                            updates, "episode_update", poster)
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
            {"type": "grid", "columns": 3, "children": [
                {"type": "button", "label": "💾 保存当前配置（供定时计划共享）",
                 "action": "run", "params": {"mode": "save_config"},
                 "style": {"tone": "primary", "variant": "outline"}},
                {"type": "button", "label": "📢 试推送", "action": "run",
                 "params": {"mode": "test_push"},
                 "style": {"tone": "warning", "variant": "outline"}},
            ]},
        ] + _render_subs_ui(store),
    }
