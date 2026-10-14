def _fetch_rich(base_url, token, tmdb_id, tmdb_key, tmdb_proxies, cache):
    """从 TMDB 取卡片扩展信息：导演/主演/地区/季数/集数。失败返回 {}。"""
    if not tmdb_id:
        return {}
    if tmdb_id in cache:
        return cache[tmdb_id]
    data = _tmdb_api(
        tmdb_key,
        f"/tv/{tmdb_id}" + "?append_to_response=credits",
        tmdb_proxies) or {}
    rich = {}
    try:
        cb = data.get("created_by") or []
        director = "、".join(str(x.get("name")) for x in cb[:2] if x.get("name"))
        if not director:
            crew = ((data.get("credits") or {}).get("crew") or [])
            director = "、".join(
                str(x.get("name")) for x in crew
                if x.get("job") == "Director") or "未知"
        rich["director"] = director[:60]
        cast = ((data.get("credits") or {}).get("cast") or [])
        rich["cast"] = " / ".join(
            str(x.get("name")) for x in cast[:3] if x.get("name"))
        region = ((data.get("production_countries") or [{}])[0].get(
            "iso_3166_1") or (data.get("origin_country") or [""])[0] or "")
        rich["region"] = str(region).upper()
        if data.get("number_of_seasons"):
            rich["seasons"] = data.get("number_of_seasons")
        if data.get("number_of_episodes"):
            rich["episodes"] = data.get("number_of_episodes")
    except Exception as exc:  # noqa: BLE001
        print(f"TMDB 扩展信息解析失败：{exc}")
    cache[tmdb_id] = rich
    return rich


def _html_escape(t):
    return (str(t).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _strip_tags(t):
    import re as _re
    return _re.sub(r"</?blockquote[^>]*>", "", str(t))


def _entry_text(u, det, rich=None):
    """单部番的推送文案（仿分享卡片样式，TG 用 HTML 引用块收简介）。"""
    rich = rich or {}
    det = det or {}
    genres = [g for g in (det.get("genres") or []) if isinstance(g, str)]
    kind = "动漫" if "动画" in genres else "电视剧"
    title = det.get("title") or det.get("original_title") \
        or u.get("title", "")
    year = det.get("year") or ""
    lines = [f"📺 {kind}｜{title}" + (f"（{year}）" if year else "")]
    meta = []
    score = det.get("score")
    if isinstance(score, (int, float)) and score > 0:
        meta.append(f"⭐️ 评分: {score}")
    if rich.get("region"):
        meta.append("地区: " + rich["region"])
    if genres:
        meta.append("类型: " + "/".join(genres[:4]))
    if meta:
        lines.append(" / ".join(meta))
    lines.append("🎬 导演: " + (rich.get("director") or "未知"))
    if rich.get("cast"):
        lines.append("👥 主演: " + rich["cast"])
    if rich.get("seasons"):
        eps = rich.get("episodes")
        lines.append(f"📺 共{rich['seasons']}季"
                     + (f" ({eps}集)" if eps else ""))
    when = u.get("time") or u.get("date") or ""
    lines.append(f"🎬 更新: {u.get('label', '')}"
                 + (f"（{when}）" if when else ""))
    ov = ((det or {}).get("overview") or (u.get("overview") or "")).strip()
    if ov:
        lines.append("📖 简介:")
        lines.append("<blockquote expandable>"
                     + _html_escape(ov[:600])
                     + ("…" if len(ov) > 600 else "")
                     + "</blockquote>")
    return NL.join(lines)
