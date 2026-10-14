# -*- coding: utf-8 -*-
"""追剧更新推送：订阅指定番剧/剧集，有更新立即推送 Telegram。

用法：
  1. 实用工具 → 我的插件 → 导入本 .py 并确认信任。
  2. 表单填「订阅列表」（每行一个番名或 TMDB ID）与凭据
     （服务地址 + OpenAPI Token，同 bt-rss-offline）。
  3. 先跑一次看日志：确认订阅解析成功、日历原始返回符合预期。
  4. 定时执行建计划：cron 建议 */30 * * * *（数据源按天更新，30 分钟粒度足够及时），
     timeout 建议 120。

说明：
  - 订阅解析结果缓存在 data_dir/subs.json，名称匹配失败会每次重试。
  - 已推送条目记录在 data_dir/pushed.json，按「ID+季+集+日期」去重，不会重复推送。
  - TG 机器人配置取自主程序（/api/proxy_settings → /api/notify-bots），TG 请求
    复用主程序代理。
"""

PLUGIN_MANIFEST = {
    "schema_version": 1,
    "id": "anime-update-push",
    "name": "追剧更新推送",
    "version": "1.1.0",
    "description": "订阅指定番剧/剧集，通过追剧日历检测更新并推送 Telegram，持久去重不重复推送。",
    "author": "ssabv",
    "timeout": 120,
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
            "name": "subscriptions",
            "label": "订阅列表（每行一个）",
            "type": "textarea",
            "required": True,
            "placeholder": "遭到流放的转生重骑士凭借游戏知识大开无双\n511177",
            "help": "每行一个番剧/剧集名或 TMDB ID（纯数字按 ID 处理）。中文名匹配不到时建议直接填 TMDB ID。",
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
            ],
            "help": "一般不用手动改：用运行结果页里的按钮操作即可。",
        },
        {
            "name": "search_query",
            "label": "TMDB 搜索关键词",
            "type": "text",
            "default": "",
            "placeholder": "输入番名后点结果页「搜索」按钮",
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
            "help": "日历接口的 kind 筛选值，留空不过滤；如果日志显示类型混杂，可按主程序实际取值填写（如 tv / anime）。",
        },
        {
            "name": "tg_notify",
            "label": "TG 推送更新",
            "type": "boolean",
            "default": True,
            "help": "有新更新时经主程序绑定的 TG 机器人推送。",
        },
    ],
}

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

TG_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
TMDB_ID_RE = re.compile(r"^\d{1,10}$")
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


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


# ---------- TG（与 bt-rss-offline 同一套） ----------

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
    print("TG 通知：未发现机器人配置，跳过推送")
    return []


def _send_tg(pairs, text, proxies):
    for bot_token, chat_id in pairs:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text,
                      "disable_web_page_preview": True},
                timeout=(10, 30), proxies=proxies,
            )
            if resp.status_code == 200:
                print(f"TG 通知：已发送到 chat {chat_id}")
            else:
                print(f"TG 通知：发送失败 HTTP {resp.status_code} {resp.text[:150]}")
        except Exception as exc:  # noqa: BLE001
            print(f"TG 通知：发送失败：{exc}")


# ---------- 订阅解析 ----------

def _resolve_subscriptions(base_url, token, lines, subs_cache):
    """把订阅行解析成 {key: {"tmdb_id": int|None, "title": str, "aliases": [str]}}"""
    result, unresolved = {}, []
    for line in lines:
        name = line.strip()
        if not name:
            continue
        cached = subs_cache.get(name)
        if cached:
            result[name] = cached
            continue
        if TMDB_ID_RE.match(name):
            entry = {"tmdb_id": int(name), "title": name, "aliases": []}
            subs_cache[name] = entry
            result[name] = entry
            continue
        # 名称 → 用主程序搜索解析 TMDB ID
        try:
            resp = _api_call(
                base_url, token, "GET",
                "/api/media/search?q=" + requests.utils.quote(name)
                + "&media_type=tv",
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"订阅解析失败：{name}：{exc}")
            unresolved.append(name)
            continue
        candidates = []

        def walk(node):
            if isinstance(node, dict):
                tid = node.get("tmdb_id") or node.get("tmdbId") or node.get("id")
                title = (node.get("title") or node.get("name") or "")
                if isinstance(tid, int) and title:
                    candidates.append({"tmdb_id": tid, "title": str(title),
                                       "aliases": []})
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        if candidates:
            # 精确同名优先，其次互相包含（防乱名误匹配到无关结果）
            nl = name.lower()
            pick = next((c for c in candidates
                         if c["title"].lower() == nl), None)
            if pick is None:
                pick = next((c for c in candidates
                             if nl in c["title"].lower()
                             or c["title"].lower() in nl), None)
            if pick:
                pick["aliases"] = [name]
                subs_cache[name] = pick
                result[name] = pick
                print(f"订阅解析成功：{name} → TMDB {pick['tmdb_id']}（{pick['title']}）")
            else:
                print(f"订阅解析失败：{name}：搜索结果与名称不匹配，请改用 TMDB ID")
                unresolved.append(name)
        else:
            print(f"订阅解析失败：{name}：搜索无结果，请改用 TMDB ID")
            unresolved.append(name)
    return result, unresolved


# ---------- 日历解析（自适应） ----------

def _collect_calendar_entries(obj, entries, ctx=None, depth=0):
    """深度遍历日历 JSON，收集疑似剧集条目。

    识别特征：dict 同时含「标题类字段」和「集数类字段」，
    或位于日期分组 / episodes 列表内。"""
    if depth > 8:
        return
    ctx = ctx or {}
    if isinstance(obj, dict):
        lower = {str(k).lower(): v for k, v in obj.items()}
        # 日期分组：key 形如 2026-10-14，value 为列表
        for key, val in obj.items():
            if DATE_KEY_RE.match(str(key)) and isinstance(val, list):
                ctx = dict(ctx, date=str(key)[:10])
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
        new_ctx = dict(ctx)
        if title:
            new_ctx["title"] = title
        if tid is not None:
            new_ctx["tmdb_id"] = tid
        if season is not None:
            new_ctx["season"] = season

        if episode is not None and ("title" in new_ctx or "tmdb_id" in new_ctx):
            entries.append({
                "tmdb_id": new_ctx.get("tmdb_id"),
                "title": new_ctx.get("title") or f"TMDB {new_ctx.get('tmdb_id')}",
                "season": new_ctx.get("season"),
                "episode": episode,
                "date": ctx.get("date", ""),
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


# ---------- 订阅界面（搜索 / 添加 / 移除） ----------

def _load_sub_store(data_dir):
    store = _read_json(Path(data_dir) / "subs_ui.json", {})
    return store if isinstance(store, dict) else {}


def _save_sub_store(data_dir, store):
    _write_json(Path(data_dir) / "subs_ui.json", store)


def _find_poster(obj):
    """递归找海报：poster 类字段，路径补 TMDB CDN 前缀。"""
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
    """当前订阅列表 + 移除按钮。"""
    if not store:
        return [{"type": "text", "text": "当前订阅：空。用上方搜索添加，或在订阅列表文本框里填名称/TMDB ID。"}]
    comps = [{"type": "heading", "text": f"当前订阅（{len(store)}）"}]
    rows = []
    for key, sub in sorted(store.items()):
        rows.append([sub.get("title", key),
                     str(sub.get("tmdb_id") or "-"), key])
    comps.append({"type": "table", "label": "订阅",
                  "headers": ["标题", "TMDB ID", "键"], "rows": rows})
    remove_buttons = [
        {"type": "button", "label": "➖ " + sub.get("title", key)[:20],
         "action": "run", "params": {"mode": "remove", "remove_key": key},
         "style": {"tone": "danger", "variant": "outline"}}
        for key, sub in sorted(store.items())
    ]
    for i in range(0, len(remove_buttons), 4):
        comps.append({"type": "grid", "columns": 4,
                      "children": remove_buttons[i:i + 4]})
    return comps


def _handle_search(base_url, token, params, store):
    q = (params.get("search_query") or "").strip()
    if not q:
        return {"summary": {"mode": "search", "error": "关键词为空"},
                "ui": [{"type": "alert", "text": "先在表单「TMDB 搜索关键词」里输入番名，再点搜索按钮。",
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
    # 去重 + 截前 8 个
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
        except Exception:  # noqa: BLE001 详情拿不到不影响订阅
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


def _handle_add(params, store, data_dir):
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


# ---------- 主流程 ----------

def run(params, context):
    base_url, token = _resolve_api_config(params, context.get("data_dir", ""))
    data_dir = Path(context["data_dir"])

    # 交互模式：搜索 / 添加 / 移除订阅（由结果页按钮触发）
    mode = (params.get("mode") or "run").strip()
    store = _load_sub_store(data_dir)
    if mode == "search":
        return _handle_search(base_url, token, params, store)
    if mode == "add":
        return _handle_add(params, store, data_dir)
    if mode == "remove":
        return _handle_remove(params, store, data_dir)

    lines = [l for l in (params.get("subscriptions") or "").splitlines()
             if l.strip()]
    use_ui_subs = not lines  # 文本框没填时用界面订阅；填了则两者合并
    if not lines and not store:
        raise ValueError(
            "没有订阅：请在表单填订阅列表，或在结果页搜索 TMDB 添加订阅")

    days = int(params.get("days") or 1)
    kind = (params.get("kind") or "").strip()
    tg_notify = bool(params.get("tg_notify", True))

    subs_cache = _read_json(data_dir / "subs.json", {})
    pushed = _read_json(data_dir / "pushed.json", {})
    if not isinstance(pushed, dict):
        pushed = {}

    # 1. 解析订阅：界面订阅（已有 TMDB ID）+ 文本框订阅 合并
    subs, unresolved = _resolve_subscriptions(base_url, token, lines, subs_cache)
    for key, sub in store.items():
        if sub.get("tmdb_id"):
            exists = any(s.get("tmdb_id") == sub["tmdb_id"]
                         for s in subs.values())
            if not exists:
                subs[key] = {"tmdb_id": sub["tmdb_id"], "title": sub["title"],
                             "aliases": []}
    if not subs:
        raise ValueError("没有可用的订阅（解析全部失败）")
    _write_json(data_dir / "subs.json", subs_cache)

    # 2. 拉日历
    query = f"/api/media/calendar?days={days}"
    if kind:
        query += "&kind=" + requests.utils.quote(kind)
    resp = _api_call(base_url, token, "GET", query)
    try:
        cal = resp.json()
    except ValueError:
        raise RuntimeError("日历接口返回非 JSON：" + resp.text[:200])
    if not subs_cache.get("_calendar_raw_logged"):
        raw = json.dumps(cal, ensure_ascii=False)
        print("日历原始返回（仅首次打印，截断）：")
        print(raw[:3000])
        subs_cache["_calendar_raw_logged"] = True
        _write_json(data_dir / "subs.json", subs_cache)

    # 3. 解析条目
    entries = []
    _collect_calendar_entries(cal, entries)
    print(f"日历解析出 {len(entries)} 条剧集条目（最近 {days} 天）")

    # 4. 匹配订阅 + 去重
    updates, matched_names = [], set()
    for e in entries:
        hit = None
        for name, sub in subs.items():
            if sub.get("tmdb_id") and e.get("tmdb_id") == sub["tmdb_id"]:
                hit = name
                break
            aliases = [name, sub.get("title", "")] + sub.get("aliases", [])
            if any(a and a.lower() in (e.get("title") or "").lower()
                   for a in aliases):
                hit = name
                break
        if not hit:
            continue
        season = e.get("season")
        ep_label = (f"S{season:02d}" if season else "S??") + f"E{e['episode']:02d}"
        key = f"{e.get('tmdb_id') or hit}|{ep_label}|{e.get('date', '')}"
        if key in pushed:
            continue
        pushed[key] = {
            "t": e.get("title"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "date": e.get("date", ""),
        }
        matched_names.add(hit)
        updates.append({"key": key, "name": hit, "title": e.get("title"),
                        "label": ep_label, "date": e.get("date", "")})

    # 5. 推送
    if updates and tg_notify:
        bot_pairs = _resolve_bot(base_url, token)
        if bot_pairs:
            lines_out = ["📺 追剧更新", ""]
            for u in updates[:15]:
                lines_out.append(f"✅ {u['title']} {u['label']}"
                                 + (f"（{u['date']}）" if u["date"] else ""))
            if len(updates) > 15:
                lines_out.append(f"…另有 {len(updates) - 15} 条")
            _send_tg(bot_pairs, "\n".join(lines_out), _resolve_proxy(base_url, token))

    if len(pushed) > 3000:
        pushed = dict(sorted(pushed.items(),
                             key=lambda kv: kv[1].get("ts", ""),
                             reverse=True)[:2500])
    _write_json(data_dir / "pushed.json", pushed)

    print(f"订阅 {len(subs)} 项，解析失败 {len(unresolved)}，"
          f"新更新 {len(updates)} 条")

    rows = [[u["title"][:80], u["label"], u["date"] or "-", "已推送"
             if tg_notify else "检测到（TG 关闭）"] for u in updates]
    if not rows:
        rows = [["（无新更新）", "-", "-", "-"]]

    summary = {
        "subscriptions": len(subs),
        "unresolved": unresolved,
        "new_updates": len(updates),
        "calendar_entries": len(entries),
        "pushed_total": len(pushed),
    }
    return {
        "summary": summary,
        "updates": updates,
        "ui": [
            {"type": "heading", "text": "追剧更新检测结果"},
            {"type": "alert",
             "text": f"订阅 {len(subs)} 项（未解析 {len(unresolved)}），"
                     f"新更新 {len(updates)} 条。"
                     + ("" if not unresolved else
                        "未解析的订阅请改填 TMDB ID。"),
             "style": {"tone": "success" if updates else "muted"}},
            {"type": "table", "label": "更新列表",
             "headers": ["标题", "集数", "日期", "状态"], "rows": rows},
            {"type": "divider"},
            {"type": "button", "label": "🔍 搜索 TMDB 添加订阅",
             "action": "run", "params": {"mode": "search"},
             "style": {"tone": "primary"}},
        ] + _render_subs_ui(store),
    }
