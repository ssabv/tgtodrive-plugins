# -*- coding: utf-8 -*-
"""BT 站 RSS 订阅 → 自动提交离线下载（123 / 115 / 光鸭，支持多目标同时）。

用法：
  1. 实用工具 → 我的插件 → 导入插件，选择本 .py 文件并确认信任。
  2. 先在主程序「影视发现」相关页面配置好离线目标与保存目录
     （离线接口不带目录参数，目录沿用服务端已保存配置）。
  3. 配置凭据（三选一）：插件表单里填「服务地址 + OpenAPI Token」；或 compose
     加环境变量 TGTO_OPENAPI_BASE_URL / TGTO_OPENAPI_TOKEN 后重启容器；
     或在插件数据目录放 openapi.json（{"base_url": "...", "token": "..."}）。
  4. 打开插件，填 RSS 地址，先 dry_run 预览，确认后正式提交。
  5. 定时执行推荐 cron：*/30 * * * *（每 30 分钟），timeout 建议 300。

代理：默认复用主程序「全局设置」里的代理（GET /api/proxy_settings），
     仅用于 RSS 拉取、种子下载与 TG 通知；调用本服务 API 不走代理。可用开关关闭。
通知：提交后可经主程序绑定的 TG 机器人推送汇总（配置取自 /api/proxy_settings
     或 /api/notify-bots，自动发现 bot_token + chat_id）。
接口：POST /api/media/resources/offline（link/provider/...），
     GET /api/media/capabilities（发现可用离线目标）。
去重：data_dir/history.json，按「资源 + 目标」记录，只记录提交成功的组合，
     失败下次自动重试。
"""

PLUGIN_MANIFEST = {
    "schema_version": 1,
    "id": "bt-rss-offline",
    "name": "BT RSS 离线订阅",
    "version": "1.5.0",
    "description": "订阅 BT 站 RSS，解析磁力链接，按关键词/大小过滤后同时提交到多个网盘离线下载（123/115/光鸭），复用主程序代理，自动去重。",
    "author": "ssabv",
    "timeout": 300,
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
            "help": "在「实用工具 → OpenAPI」创建。可填在这里，也可配置环境变量 TGTO_OPENAPI_TOKEN，或在插件数据目录放 openapi.json；三种方式任选其一。注意：表单保存值在服务器上是明文 JSON。",
        },
        {
            "name": "rss_urls",
            "label": "RSS 地址（每行一个）",
            "type": "textarea",
            "required": True,
            "placeholder": "https://anibt.net/rss/anime.xml?bgmId=511177",
            "help": "支持 RSS 2.0 与 Atom，地址需公开可访问（公开 BT 站如 AniBT 可直接使用）。",
        },
        {
            "name": "offline_targets",
            "label": "离线目标（可多选）",
            "type": "multiselect",
            "default": [],
            "options": [
                {"label": "123 云盘", "value": "123"},
                {"label": "光鸭", "value": "guangya"},
                {"label": "115 网盘", "value": "115"},
            ],
            "help": "选中的每个目标都会提交同一条磁力（两个/三个同时离线）。不选则自动使用能力接口发现的第一个可用目标。首次正式提交请看日志确认目标取值。",
        },
        {
            "name": "use_proxy",
            "label": "复用主程序代理",
            "type": "boolean",
            "default": True,
            "help": "开启后自动读取主程序全局代理设置（全局设置里保存的代理），用于拉取 RSS 和下载种子；调用本服务 API 不走代理。未配置代理时自动直连。",
        },
        {
            "name": "offline_source",
            "label": "资源来源 source",
            "type": "text",
            "default": "seedhub",
            "placeholder": "留空自动发现",
            "help": "离线接口要求磁力挂在影视来源下（RESOURCE_SOURCE_UNAVAILABLE 时需填写）。留空时插件自动从影视来源接口匹配含 磁力/magnet/自定义 等关键词的来源；匹配不到请看日志里打印的来源列表手动填写。",
        },
        {
            "name": "maintenance",
            "label": "维护操作",
            "type": "select",
            "default": "run",
            "options": [
                {"label": "正常运行（拉取 RSS 并提交）", "value": "run"},
                {"label": "清空去重历史（全部重新可提交）", "value": "clear_history"},
                {"label": "清空种子 infohash 缓存", "value": "clear_torrent_cache"},
                {"label": "全部清空（历史 + 缓存）", "value": "clear_all"},
            ],
            "help": "清理操作立即生效并返回清理结果，不拉取 RSS。去重历史清空后，RSS 里现有条目会再次全部提交，谨慎使用。",
        },
        {
            "name": "tg_notify",
            "label": "TG 通知提交结果",
            "type": "boolean",
            "default": True,
            "help": "提交后通过主程序绑定的 Telegram 机器人推送汇总消息（机器人配置取自主程序全局设置/通知机器人配置，TG 请求复用主程序代理）。",
        },
        {
            "name": "include_keywords",
            "label": "包含关键词",
            "type": "text",
            "default": "",
            "placeholder": "逗号分隔，如 4K,HDR",
            "help": "标题需命中任意一个才提交；留空不过滤。",
        },
        {
            "name": "exclude_keywords",
            "label": "排除关键词",
            "type": "text",
            "default": "",
            "placeholder": "逗号分隔",
            "help": "标题命中任意一个则跳过。",
        },
        {
            "name": "min_size_gb",
            "label": "最小体积 GB",
            "type": "number",
            "default": 0,
            "help": "RSS 条目带体积信息时才生效；解析不到体积时放行。0 为不限制。",
        },
        {
            "name": "max_size_gb",
            "label": "最大体积 GB",
            "type": "number",
            "default": 0,
            "help": "0 为不限制。",
        },
        {
            "name": "max_items",
            "label": "单次最多处理条数",
            "type": "number",
            "default": 10,
            "min": 1,
            "max": 100,
            "help": "注意：多目标时实际提交数 = 条数 × 目标数。",
        },
        {
            "name": "dry_run",
            "label": "预览模式（不实际提交）",
            "type": "boolean",
            "default": True,
            "help": "首次使用先用预览确认过滤结果，再关掉正式提交。",
        },
        {
            "name": "extra_payload",
            "label": "附加请求字段（JSON，可选）",
            "type": "textarea",
            "default": "",
            "help": "合并进离线接口请求体。普通用户留空。",
        },
    ],
}

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from xml.etree import ElementTree

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:([A-Za-z0-9]+)")
HASH32_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
BASE32_RE = re.compile(r"\b[A-Z2-7]{32}\b")
TORRENT_URL_RE = re.compile(
    r"https?://[^\s<>\"']+\.(?:torrent)(?:\?[^\s<>\"']*)?", re.IGNORECASE
)
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(TB|TIB|GB|GIB|MB|MIB|KB|KIB)", re.IGNORECASE
)
URL_RE = re.compile(r"^https?://[^\s]+$")
TG_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
SIZE_MULT = {
    "KB": 1 / 1024 / 1024, "KIB": 1 / 1024 / 1024,
    "MB": 1 / 1024, "MIB": 1 / 1024,
    "GB": 1.0, "GIB": 1.0,
    "TB": 1024.0, "TIB": 1024.0,
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


# ---------- 基础工具 ----------

def _api_headers(token):
    return {"Authorization": "Bearer " + token}


def _api_call(base_url, token, method, path, **kwargs):
    url = base_url + path
    kwargs.setdefault("timeout", (10, 60))
    kwargs.setdefault("allow_redirects", False)
    resp = requests.request(
        method, url, headers=_api_headers(token), **kwargs
    )
    if resp.status_code == 401:
        raise RuntimeError("OpenAPI Token 无效或过期（HTTP 401）")
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"接口 {method} {path} 失败：HTTP {resp.status_code} {resp.text[:800]}"
        )
    return resp


def _split_keywords(raw):
    return [k.strip() for k in re.split(r"[,，\n]", raw or "") if k.strip()]


def _parse_size_gb(text):
    m = SIZE_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1)) * SIZE_MULT[m.group(2).upper()]
    except (KeyError, ValueError):
        return None


def _extract_magnet(item_text):
    """返回 (link, kind)。kind: magnet / None"""
    m = MAGNET_RE.search(item_text)
    if m:
        return m.group(0), "magnet"
    h = HASH32_RE.search(item_text)
    if h:
        return "magnet:?xt=urn:btih:" + h.group(0).lower(), "magnet"
    b32 = BASE32_RE.search(item_text)
    if b32:
        return "magnet:?xt=urn:btih:" + b32.group(0), "magnet"
    return None, None


def _torrent_to_magnet(torrent_url, proxies):
    """下载 .torrent 计算 infohash；返回 (infohash, name)，失败返回 None。"""
    try:
        import bencodepy  # TgtoDrive 容器自带；缺失时退回种子 URL
    except ImportError:
        print("bencodepy 未安装，直接使用种子 URL 提交")
        return None
    try:
        resp = requests.get(
            torrent_url, headers={"User-Agent": UA}, timeout=(10, 60),
            allow_redirects=True, proxies=proxies,
        )
        resp.raise_for_status()
        data = bencodepy.decode(resp.content)
        info = data[b"info"]
        infohash = hashlib.sha1(bencodepy.encode(info)).hexdigest()
        name = info.get(b"name", b"").decode("utf-8", "ignore")
        print(f"种子转磁力成功：{torrent_url[-40:]} → {infohash}")
        return infohash, name
    except Exception as exc:  # noqa: BLE001 下载/解析失败退回种子 URL
        print(f"种子转磁力失败（改用种子 URL 提交）：{exc}")
        return None


def _load_torrent_cache(data_dir):
    f = Path(data_dir) / "torrent_cache.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save_torrent_cache(data_dir, cache):
    f = Path(data_dir) / "torrent_cache.json"
    f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# ---------- RSS 解析 ----------

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node, names):
    for ch in node:
        if _local(ch.tag) in names and (ch.text or "").strip():
            return ch.text.strip()
    return ""


def _parse_feed(xml_text):
    """兼容 RSS 2.0 与 Atom，返回条目列表。"""
    root = ElementTree.fromstring(xml_text.encode("utf-8"))
    items = []
    for node in root.iter():
        tag = _local(node.tag)
        if tag not in ("item", "entry"):
            continue
        title = _child_text(node, {"title"})
        link = ""
        enc_url = ""
        enc_length = 0
        desc = ""
        guid = ""
        for ch in node:
            t = _local(ch.tag)
            if t == "link":
                href = ch.get("href")
                link = (href or (ch.text or "").strip()) or link
            elif t == "enclosure":
                enc_url = ch.get("url") or ""
                try:
                    enc_length = int(float(ch.get("length") or 0))
                except (TypeError, ValueError):
                    enc_length = 0
            elif t in ("description", "summary", "content", "encoded"):
                if not desc and (ch.text or "").strip():
                    desc = ch.text.strip()
            elif t == "guid":
                guid = (ch.text or "").strip()
        items.append(
            {
                "title": title,
                "link": link,
                "enclosure": enc_url,
                "enc_length": enc_length,
                "desc": desc,
                "guid": guid or link or title,
            }
        )
    return items


def _fetch_items(rss_urls, proxies):
    headers = {"User-Agent": UA}
    all_items, errors = [], []
    for url in rss_urls:
        try:
            resp = requests.get(
                url, headers=headers, timeout=(10, 60),
                allow_redirects=True, proxies=proxies,
            )
            resp.raise_for_status()
            parsed = _parse_feed(resp.text)
            print(f"RSS 拉取成功：{url[:80]}…，{len(parsed)} 条")
            all_items.extend(parsed)
        except Exception as exc:  # noqa: BLE001 单个源失败不影响其他源
            errors.append(f"{url[:80]}…：{exc}")
    return all_items, errors


# ---------- 代理 ----------

def _find_proxy_url(obj):
    """在主程序代理设置里启发式寻找代理地址。"""
    candidates = []

    def walk(node, in_proxy_key):
        if isinstance(node, dict):
            for key, val in node.items():
                kl = str(key).lower()
                has_proxy = in_proxy_key or "proxy" in kl
                if isinstance(val, str):
                    v = val.strip()
                    if has_proxy and v and not v.lower() in ("true", "false", "1", "0", "on", "off", "yes", "no"):
                        if URL_RE.match(v) or re.match(r"^[\w.+-]+:\d{2,5}$", v):
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


def _resolve_proxy(base_url, token, use_proxy):
    if not use_proxy:
        print("代理：已关闭，RSS/种子直连")
        return None
    try:
        resp = _api_call(base_url, token, "GET", "/api/proxy_settings")
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"代理：读取主程序代理设置失败（直连）：{exc}")
        return None
    url = _find_proxy_url(data)
    if url:
        print(f"代理：复用主程序代理 {url}")
        return {"http": url, "https": url}
    print("代理：主程序未发现可用代理配置（直连）")
    return None


# ---------- 离线目标 ----------

def _find_offline_providers(obj, results, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            kl = str(key).lower()
            if "offline" in kl or "离线" in kl:
                vals = val if isinstance(val, list) else (
                    val if isinstance(val, dict) else []
                )
                if isinstance(vals, dict):
                    vals = list(vals.values())
                for entry in vals:
                    for field in ("provider", "id", "value", "key"):
                        if isinstance(entry, dict) and isinstance(
                            entry.get(field), str
                        ):
                            results.add(entry[field])
                        elif isinstance(entry, str):
                            results.add(entry)
            _find_offline_providers(val, results, depth + 1)
    elif isinstance(obj, list):
        for entry in obj:
            _find_offline_providers(entry, results, depth + 1)


def _match_provider(selected, found):
    """把选中的目标名匹配到能力接口里的实际 provider 取值。"""
    best, best_rank = None, (3, 999)
    for cand in found:
        cl = cand.lower()
        if cand == selected or cl == selected.lower():
            rank = (0, len(cand))
        elif selected.lower() in cl:
            rank = (1, len(cand))
        else:
            continue
        if rank < best_rank:
            best, best_rank = cand, rank
    return best


def _resolve_targets(base_url, token, selected):
    """返回 (targets, note)。selected 为空时自动发现第一个可用目标。"""
    found = set()
    caps_note = "未读取"
    try:
        resp = _api_call(base_url, token, "GET", "/api/media/capabilities")
        _find_offline_providers(resp.json(), found)
        caps_note = ", ".join(sorted(found)) or "未发现"
    except Exception as exc:  # noqa: BLE001
        caps_note = f"读取失败：{exc}"
    print(f"能力接口离线相关取值：{caps_note}")

    if not selected:
        if found:
            chosen = sorted(found)[0]
            print(f"未选择目标，自动使用：{chosen}")
            return [chosen]
        raise RuntimeError("未选择离线目标且能力接口未发现可用目标")

    targets = []
    for sel in selected:
        matched = _match_provider(sel, found)
        targets.append(matched or sel)
        if not matched:
            print(f"目标 {sel} 未在能力接口中匹配到，按原值提交")
    return list(dict.fromkeys(targets))


# ---------- TG 通知（复用主程序机器人配置） ----------

def _collect_bot_pairs(obj):
    """在主程序配置里启发式收集 (bot_token, chat_id) 组合。"""
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

    for source in (obj,):
        walk(source)
    # 同一配置块内的 token/chat 优先；全局兜底
    if not pairs and tokens:
        for chat in dict.fromkeys(chats) or [""]:
            for token in dict.fromkeys(tokens):
                pairs.append((token, chat))
    return list(dict.fromkeys(pairs))


def _resolve_bot(base_url, token):
    """返回 [(bot_token, chat_id), ...]，读不到返回空列表。"""
    for path in ("/api/proxy_settings", "/api/notify-bots"):
        try:
            resp = _api_call(base_url, token, "GET", path)
            pairs = _collect_bot_pairs(resp.json())
            if pairs:
                print(f"TG 通知：已从 {path} 发现 {len(pairs)} 个机器人目标")
                return pairs
        except Exception as exc:  # noqa: BLE001
            print(f"TG 通知：读取 {path} 失败：{exc}")
    print("TG 通知：主程序未发现机器人配置，跳过通知")
    return []


def _send_tg(pairs, text, proxies):
    for bot_token, chat_id in pairs:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=(10, 30),
                proxies=proxies,
            )
            if resp.status_code == 200:
                print(f"TG 通知：已发送到 chat {chat_id}")
            else:
                print(
                    f"TG 通知：发送到 chat {chat_id} 失败，"
                    f"HTTP {resp.status_code} {resp.text[:150]}"
                )
        except Exception as exc:  # noqa: BLE001 通知失败不影响主流程
            print(f"TG 通知：发送失败：{exc}")


# ---------- 资源来源 ----------

SOURCE_HINTS = ("magnet", "磁力", "custom", "自定义", "direct", "offline",
                "离线", "link", "链接", "bt", "torrent", "种子", "rss")


def _collect_source_values(obj, results, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            kl = str(key).lower()
            if "source" in kl or "来源" in kl:
                vals = val if isinstance(val, list) else (
                    list(val.keys()) if isinstance(val, dict) else []
                )
                if isinstance(val, dict) and val:
                    first = next(iter(val.values()))
                    if not isinstance(first, (dict, list, str, int, float, bool)):
                        vals = []
                for entry in vals:
                    for field in ("id", "value", "key", "source", "name"):
                        if isinstance(entry, dict) and isinstance(
                            entry.get(field), str
                        ):
                            results.add(entry[field])
                        elif isinstance(entry, str):
                            results.add(entry)
            _collect_source_values(val, results, depth + 1)
    elif isinstance(obj, list):
        for entry in obj:
            _collect_source_values(entry, results, depth + 1)


def _resolve_source(base_url, token, configured):
    """返回 (source, note)。configured 优先；否则自动匹配。"""
    if configured:
        return configured, "手动指定"
    try:
        resp = _api_call(base_url, token, "GET", "/api/media/providers")
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"未指定 source 且来源接口读取失败：{exc}")
    raw = json.dumps(data, ensure_ascii=False)
    print("影视来源接口原始返回（截断）：")
    print(raw[:2500])
    found = set()
    _collect_source_values(data, found)
    print(f"发现的来源候选：{', '.join(sorted(found)) or '无'}")
    for cand in sorted(found):
        cl = cand.lower()
        if any(h in cl for h in SOURCE_HINTS):
            print(f"自动匹配资源来源：{cand}")
            return cand, "自动匹配"
    raise RuntimeError(
        "未指定 source 且未匹配到磁力类来源。请从上方日志的来源候选里选一个，"
        "填到插件的「资源来源 source」参数后重试"
    )


# ---------- 去重 ----------

def _load_history(data_dir):
    f = Path(data_dir) / "history.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data.get("items"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 2, "items": {}}


def _save_history(data_dir, history):
    f = Path(data_dir) / "history.json"
    items = history["items"]
    if len(items) > 5000:
        ordered = sorted(items.items(), key=lambda kv: kv[1].get("ts", ""), reverse=True)
        history["items"] = dict(ordered[:4000])
    f.write_text(
        json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------- 主流程 ----------

def _resolve_api_config(params, data_dir):
    """凭据优先级：表单填写 > 环境变量 > data_dir/openapi.json。"""
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
            "缺少服务地址或 OpenAPI Token：请在插件表单填写，或配置环境变量 "
            "TGTO_OPENAPI_BASE_URL / TGTO_OPENAPI_TOKEN，或在插件数据目录放 "
            'openapi.json（内容 {"base_url": "...", "token": "..."}）'
        )
    return base_url.rstrip("/"), token


def run(params, context):
    base_url, token = _resolve_api_config(params, context.get("data_dir"))
    data_dir = context["data_dir"]

    # 维护操作：清缓存 / 清历史，不执行订阅流程
    maintenance = (params.get("maintenance") or "run").strip()
    if maintenance != "run":
        report = {}
        if maintenance in ("clear_history", "clear_all"):
            f = Path(data_dir) / "history.json"
            n = 0
            try:
                n = len(json.loads(f.read_text(encoding="utf-8")).get("items", {}))
            except (OSError, ValueError):
                pass
            f.write_text(json.dumps({"version": 2, "items": {}}), encoding="utf-8")
            report["history_cleared"] = n
            print(f"已清空去重历史（{n} 条）")
        if maintenance in ("clear_torrent_cache", "clear_all"):
            f = Path(data_dir) / "torrent_cache.json"
            n = 0
            try:
                n = len(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
            f.write_text("{}", encoding="utf-8")
            report["torrent_cache_cleared"] = n
            print(f"已清空种子 infohash 缓存（{n} 条）")
        return {
            "summary": {"maintenance": maintenance, **report},
            "ui": [
                {"type": "heading", "text": "维护完成"},
                {"type": "text",
                 "text": "去重历史清空 {} 条，种子缓存清空 {} 条".format(
                     report.get("history_cleared", "未操作"),
                     report.get("torrent_cache_cleared", "未操作"))},
                {"type": "alert",
                 "text": "去重历史清空后，RSS 里现有条目下次运行会重新提交离线，"
                         "请在网盘侧注意去重。",
                 "style": {"tone": "warning"}},
            ],
        }

    rss_urls = [
        u.strip() for u in (params.get("rss_urls") or "").splitlines() if u.strip()
    ]
    if not rss_urls:
        raise ValueError("请至少填写一个 RSS 地址")

    dry_run = bool(params.get("dry_run", True))
    max_items = int(params.get("max_items") or 10)
    min_gb = float(params.get("min_size_gb") or 0)
    max_gb = float(params.get("max_size_gb") or 0)
    include_kw = _split_keywords(params.get("include_keywords"))
    exclude_kw = _split_keywords(params.get("exclude_keywords"))
    selected_targets = params.get("offline_targets") or []
    if isinstance(selected_targets, str):
        selected_targets = [selected_targets] if selected_targets else []

    extra = {}
    raw_extra = (params.get("extra_payload") or "").strip()
    if raw_extra:
        try:
            extra = json.loads(raw_extra)
            if not isinstance(extra, dict):
                raise ValueError("不是对象")
        except ValueError as exc:
            raise ValueError(f"附加请求字段必须是 JSON 对象：{exc}")

    # 1. 代理（仅用于外部网络：RSS + 种子下载）
    proxies = _resolve_proxy(base_url, token, bool(params.get("use_proxy", True)))

    # 2. 拉取 RSS
    items, fetch_errors = _fetch_items(rss_urls, proxies)
    if not items:
        raise RuntimeError(
            "所有 RSS 源均未获取到条目。"
            + ("；".join(fetch_errors) if fetch_errors else "")
        )

    # 3. 解析离线目标（选了目标就先解析映射，保证去重与提交用同一取值）
    targets = None
    if selected_targets:
        targets = _resolve_targets(base_url, token, selected_targets)
        print(f"离线目标：{', '.join(targets)}")

    # 4. 解析 + 过滤 + 去重
    history = _load_history(context["data_dir"])
    torrent_cache = _load_torrent_cache(context["data_dir"])
    cache_dirty = False
    candidates, skipped = [], []
    seen_keys = set()
    for it in items:
        title = it["title"] or "(无标题)"
        blob = " ".join(filter(None, [it["link"], it["enclosure"], it["desc"], title]))
        link, kind = _extract_magnet(blob)
        key = MAGNET_RE.search(link).group(1).lower() if link else None
        if not link:
            # 无磁力：找 .torrent 地址（enclosure 或正文），infohash 缓存优先
            if it["enclosure"].lower().split("?")[0].endswith(".torrent"):
                torrent_url = it["enclosure"]
            else:
                m = TORRENT_URL_RE.search(blob)
                torrent_url = m.group(0) if m else ""
            if torrent_url:
                cached = torrent_cache.get(torrent_url)
                if cached and cached.get("ok"):
                    key = cached["ih"]
                    dn = cached.get("dn")
                    link = "magnet:?xt=urn:btih:" + key
                    if dn:
                        link += "&dn=" + quote(dn)
                    kind = "magnet"
                elif cached and cached.get("ok") is False:
                    # 历史转换失败，不再重复下载，直接用种子 URL
                    link, kind = torrent_url, "torrent_url"
                else:
                    result = _torrent_to_magnet(torrent_url, proxies)
                    if result:
                        key, name = result
                        torrent_cache[torrent_url] = {"ok": True, "ih": key,
                                                      "dn": name}
                        cache_dirty = True
                        link = "magnet:?xt=urn:btih:" + key
                        if name:
                            link += "&dn=" + quote(name)
                        kind = "magnet"
                    else:
                        torrent_cache[torrent_url] = {"ok": False,
                                                      "ts": int(time.time())}
                        cache_dirty = True
                        link, kind = torrent_url, "torrent_url"
        if not link:
            skipped.append((title, "未找到磁力/种子链接"))
            continue
        if key is None:
            key = it["guid"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # 去重：所选目标全部提交过才跳过；部分提交过则只补剩余目标
        if targets and all(f"{key}|{t}" in history["items"] for t in targets):
            continue
        if include_kw and not any(k.lower() in title.lower() for k in include_kw):
            skipped.append((title, "未命中包含关键词"))
            continue
        if any(k.lower() in title.lower() for k in exclude_kw):
            skipped.append((title, "命中排除关键词"))
            continue
        # 体积优先取 enclosure 精确字节数，其次解析标题/描述里的文本体积
        size_gb = None
        if it.get("enc_length"):
            size_gb = it["enc_length"] / 1024 ** 3
        if size_gb is None:
            size_gb = _parse_size_gb(blob)
        if size_gb is not None:
            if min_gb and size_gb < min_gb:
                skipped.append((title, f"体积 {size_gb:.2f}GB 小于下限"))
                continue
            if max_gb and size_gb > max_gb:
                skipped.append((title, f"体积 {size_gb:.2f}GB 超过上限"))
                continue
        candidates.append(
            {"key": key, "title": title, "link": link, "size_gb": size_gb}
        )

    if cache_dirty:
        _save_torrent_cache(context["data_dir"], torrent_cache)
    print(f"共 {len(items)} 条，待处理 {len(candidates)} 条，跳过 {len(skipped)} 条，"
          f"种子缓存 {len(torrent_cache)}")

    # 5. 提交离线（每条 × 每个目标）
    source = None
    if candidates and not dry_run:
        if targets is None:
            targets = _resolve_targets(base_url, token, [])
        source, source_note = _resolve_source(
            base_url, token, (params.get("offline_source") or "").strip()
        )
        print(
            f"本次离线目标：{', '.join(targets)}，来源：{source}（{source_note}），"
            f"共 {len(candidates) * len(targets)} 次提交"
        )

    rows, submitted, failed = [], 0, 0
    for cand in candidates[:max_items]:
        full_title = " ".join(cand["title"].split())  # 去换行/多余空白，保留完整
        if dry_run:
            rows.append([full_title, str(cand["size_gb"] or "?"), "预览",
                         "未提交"])
            continue
        detail_parts = []
        for target in targets:
            hkey = f"{cand['key']}|{target}"
            if hkey in history["items"]:
                detail_parts.append(f"{target}:已提交过(跳过)")
                continue
            payload = {"link": cand["link"], "provider": target, "source": source}
            payload.update(extra)
            try:
                resp = _api_call(
                    base_url, token, "POST", "/api/media/resources/offline",
                    json=payload,
                )
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                ok = not (isinstance(body, dict) and body.get("success") is False)
                if ok:
                    history["items"][hkey] = {
                        "t": full_title,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "provider": target,
                    }
                    submitted += 1
                    detail_parts.append(f"{target}:成功")
                else:
                    failed += 1
                    msg = str(body.get("message") or body.get("msg") or body)[:60]
                    detail_parts.append(f"{target}:失败({msg})")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                detail_parts.append(f"{target}:失败({str(exc)[:150]})")
                if "403" in str(exc):
                    print(
                        f"403 诊断：provider={target}，link={cand['link'][:60]}…，"
                        f"完整响应见上一条错误；请确认该网盘在主程序里已登录/已配置离线"
                    )
            time.sleep(1)  # 轻微限速，避免密集提交
        rows.append([full_title, str(cand["size_gb"] or "?"),
                     "已提交" if any(":成功" in p for p in detail_parts) else "失败",
                     " ".join(detail_parts)])

    if not dry_run and submitted:
        _save_history(context["data_dir"], history)

    # 6. TG 通知（读主程序机器人配置，经主程序代理直发 Telegram）
    tg_notify = bool(params.get("tg_notify", True))
    if not dry_run and tg_notify and (submitted or failed):
        try:
            bot_pairs = _resolve_bot(base_url, token)
            if bot_pairs:
                lines = [
                    "📤 BT RSS 离线订阅",
                    f"目标：{', '.join(targets or [])}",
                    f"本次提交成功 {submitted}，失败 {failed}，"
                    f"历史累计 {len(history['items'])}",
                    "",
                ]
                for r in rows[:10]:
                    if r[2] in ("已提交", "失败"):
                        size = r[1] if r[1] == "?" else (
                            f"{float(r[1]):.2f}" if r[1] else "?")
                        lines.append(
                            f"{'✅' if r[2] == '已提交' else '❌'} "
                            f"{r[0]}（{size}GB）{('：' + r[3][:150]) if r[3] else ''}")
                if len(rows) > 10:
                    lines.append(f"…另有 {len(rows) - 10} 条未列出")
                _send_tg(bot_pairs, "\n".join(lines), proxies)
        except Exception as exc:  # noqa: BLE001 通知异常不影响结果
            print(f"TG 通知：流程异常（忽略）：{exc}")

    print(
        f"{'预览' if dry_run else '提交'}完成：待处理 {len(candidates)}，"
        f"本次列出 {len(rows)}，成功 {submitted}，失败 {failed}，"
        f"历史累计 {len(history['items'])}"
    )

    overflow = len(candidates) - len(rows)
    if overflow > 0:
        rows.append([f"…另有 {overflow} 条超出单次上限未列出", "", "", ""])

    components = []
    if dry_run and candidates:
        components.append(
            {"type": "alert",
             "text": "当前为预览模式，未实际提交。确认无误后点击下方按钮正式提交。",
             "style": {"tone": "warning"}}
        )
        components.append(
            {"type": "button", "label": "正式提交离线", "action": "run",
             "params": {"dry_run": False}, "style": {"tone": "primary"}}
        )
    components.append(
        {"type": "table", "label": "处理结果", "headers": ["标题", "GB", "状态", "详情"],
         "rows": [[r[0][:80], r[1], r[2], r[3][:120]] for r in rows]}
    )

    summary = {
        "rss_count": len(items),
        "candidates": len(candidates),
        "submitted": submitted,
        "failed": failed,
        "dry_run": dry_run,
        "targets": targets or selected_targets or ["自动"],
        "source": source if not dry_run else (params.get("offline_source") or "自动"),
        "use_proxy": bool(params.get("use_proxy", True)),
        "tg_notify": tg_notify,
        "history_total": len(history["items"]),
    }
    if fetch_errors:
        summary["fetch_errors"] = fetch_errors

    return {
        "summary": summary,
        "items": [
            {"title": r[0], "size_gb": r[1], "status": r[2], "detail": r[3]}
            for r in rows
        ],
        "ui": components,
    }
