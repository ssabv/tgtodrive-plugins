# tgto-plugins

TgtoDrive 插件市场目录仓库。用户在「实用工具 → 我的插件 → 插件市场 → 来源设置」填入 `ssabv/tgtodrive-plugins` 即可安装本目录中的插件。

## 当前收录

| 插件 | 版本 | 说明 |
| --- | --- | --- |
| bt-rss-offline | 1.5.0 | BT 站 RSS 订阅 → 多目标网盘离线（123/115/光鸭），自动转磁力、去重、TG 通知 |
| anime-update-push | 1.12.4 | 追剧更新推送：订阅番剧/剧集，按档期到点推送 Telegram / 飞书 / 通用 Webhook，持久去重。一部番一条推送，卡片式文案（评分/类型/简介），内置公共 TMDB Key 直连取每集剧照，季号自动解析，日历持久缓存防跨午夜错过 | 追剧更新推送：订阅番剧/剧集，按档期到点推送 Telegram / 飞书 / 通用 Webhook，持久去重。一部番一条推送，卡片式文案（评分/类型/简介），内置公共 TMDB Key 直连取每集剧照，季号自动解析 |

## 发布新版本

1. 更新 `plugins/<插件ID>/` 里的插件文件，并同步修改插件内清单的 `version`
2. 到 Actions 手动运行「Publish plugin」，填：
   - `plugin_id`：如 `bt-rss-offline`
   - `version`：如 `1.6.0`（须与清单一致）
   - `tag`：如 `bt-rss-offline-v1.6.0`
3. 工作流自动完成：静态检查 → 打包（单文件直发/目录 zip）→ 创建 Release → 计算 SHA-256/size → 更新 `plugins.json`（name/description/author 取自插件清单，category/requirements 沿用目录旧条目）

用户刷新市场即可看到新版本。注意 raw.githubusercontent.com 有约 5 分钟 CDN 缓存，发布后目录内容可能延迟生效。
