# Changelog

## [1.0.7] - 2026-06-15

### Added
- 添加可配置的轮询间隔设置
  - `poll_interval_normal`: 状态正常时轮询间隔（默认 900 秒）
  - `poll_interval_abnormal`: 状态异常时轮询间隔（默认 120 秒）
- 为所有配置项添加滑块控件，方便在控制面板中调整
- 添加轮询间隔日志输出，显示下次轮询时间和当前模式

### Changed
- 轮询逻辑改为从配置读取，支持热更新无需重启

### Infrastructure
- 添加 GitHub Actions 自动发布脚本，版本更新时自动创建 Release

## [1.0.6] - 2026-06-12

### Fixed
- 修复订阅主动推送时渲染图片 URL 被当作本地文件路径发送的问题

## [1.0.5] - 2026-05-25

### Fixed
- 使用 `asyncio.create_task()` 替代已弃用的 `asyncio.get_event_loop()`
- 添加 aiohttp 10 秒请求超时，防止轮询循环阻塞
- 纯文本降级消息使用配置的时区偏移，与 HTML 卡片一致
- 推送模式下无订阅者时跳过 HTML 渲染

## [1.0.4] - 2026-05-25

### Fixed
- 修复订阅推送图片发送，使用 `file_image` 替代不存在的 `image` 方法

## [1.0.3] - 2026-05-25

### Fixed
- 将 `initialize` 逻辑移至 `__init__`，修复插件初始化不生效问题
- 修复 `_send_status` 异步生成器调用方式
- 插件启动后首次轮询不触发推送

## [1.0.2] - 2026-05-24

### Changed
- 赛博朋克风格重新设计状态卡片，使用 Orbitron + Exo 2 字体
- HTML 模板分离到 `templates/status.html`，支持独立编辑调试
- 使用 `put_kv_data` / `get_kv_data` 持久化存储替代 config 存储订阅会话
- 卡片放大 2 倍，使用 CSS `transform: scale(2)`
- `scale` 改为 `css` 模式适配高分屏

## [1.0.1] - 2026-05-24

### Changed
- 重新设计状态 HTML 模板，现代简约风格
- 优化 HTML 渲染，去除白边
- 优化 `html_render` 截图参数配置

### Fixed
- 修复截图透明和过小问题
- `update_time` 转换为 Asia/Shanghai (UTC+8) 时区显示
- 添加时间戳避免图片缓存导致重复图片

## [1.0.0] - 2026-05-24

### Added
- VRChat 服务器状态监测插件初始版本
- 支持 `/vrcstatus` 手动查询服务器状态
- 支持 `/vrcsubscribe` 和 `/vrcunsubscribe` 订阅状态变化通知
- 使用 HTML 渲染状态卡片，失败时回退到纯文本
- 自动轮询检测状态变化并推送通知（异常 2 分钟，正常 15 分钟）
