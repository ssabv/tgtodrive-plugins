# tgto-plugins

TgtoDrive 插件市场目录仓库。用户在「实用工具 → 我的插件 → 插件市场 → 来源设置」填入 `ssabv/tgto-plugins` 即可安装本目录中的插件。

## 当前收录

| 插件 | 版本 | 说明 |
| --- | --- | --- |
| bt-rss-offline | 1.5.0 | BT 站 RSS 订阅 → 多目标网盘离线（123/115/光鸭），自动转磁力、去重、TG 通知 |

## 发布新版本

1. 更新 `plugins/<插件ID>/` 里的插件文件，并同步修改插件内清单的 `version`
2. 到 Actions 手动运行「Publish plugin」，填：
   - `plugin_id`：如 `bt-rss-offline`
   - `version`：如 `1.6.0`（须与清单一致）
   - `tag`：如 `bt-rss-offline-v1.6.0`
3. 工作流自动完成：静态检查 → 打包（单文件直发/目录 zip）→ 创建 Release → 计算 SHA-256/size → 更新 `plugins.json`

用户刷新市场即可看到新版本。
