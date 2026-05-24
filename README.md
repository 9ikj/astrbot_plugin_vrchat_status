# astrbot_plugin_vrchat_status

AstrBot 插件 - 监测 VRChat 服务器状态并推送到即时通讯平台

## 功能

- 手动查询 VRChat 服务器状态
- 订阅/取消订阅状态变化通知
- 状态异常时自动推送通知（每 2 分钟检测）
- 状态正常时静默轮询（每 15 分钟检测）

## 命令

| 命令 | 说明 |
|------|------|
| `/vrcstatus` | 查询当前 VRChat 服务器状态 |
| `/vrcsubscribe` | 订阅状态变化通知 |
| `/vrcunsubscribe` | 取消订阅状态变化通知 |

## 安装

1. 在 AstrBot 管理面板中安装此插件
2. 或将本仓库克隆到 AstrBot 的插件目录

## 依赖

- `aiohttp`

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
