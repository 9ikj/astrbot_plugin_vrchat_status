# Changelog

## [1.1.1] - 2026-07-16

### Fixed
- 修复状态卡片嵌入指标区块后变高导致截图裁切的问题
  - 卡片放大方式由 `transform: scale(2)` 改为 `zoom: 2`，使放大后的尺寸真实占据布局空间
  - 截图改用 `full_page` 完整截取整张卡片，不再受固定视口高度限制
  - body 改为 flex 居中容器并保留内边距，使卡片在图中水平居中且四周留白
  - 每张卡片设置 `flex-shrink: 0`，避免窄视口下被压扁
  - glass / material / neumorphism 主题的全屏背景由 `position: fixed` 伪元素改为 body 自身背景，避免高卡片下背景露底

## [1.1.0] - 2026-07-15

### Added
- 状态卡片新增近 24 小时平台指标区块，4 张 sparkline 迷你图直接嵌入 `/vrcstatus` 渲染结果
  - 在线人数（Concurrent Users）
  - API 延迟（API Latency）
  - API 请求数（API Requests）
  - API 错误率（API Error Rate）
- 数据源：VRChat Statuspage CloudFront CDN（每分钟一个数据点，1441 点覆盖 24 小时）
- SVG 折线 + 面积渐变绘制，下采样到 180 点降低体积
- 每张图显示当前值和 24 小时均值；请求速率与错误率强制以 0 为基线
- 指标接口并发拉取（`asyncio.gather`），全部失败或部分失败时卡片自然折叠对应区块
- 指标区块为每个主题定制配色（cyberpunk / dark / glass 系 / terminal / retro / minimalist / material / neumorphism），与卡片整体风格一致

## [1.0.9] - 2026-06-25

### Fixed
- 修复 VRChat 状态指示器为 `none` 时被误判为异常模式的问题
- 正常状态下不再使用异常轮询间隔或额外请求状态详情

## [1.0.8] - 2026-06-15

### Added
- 实现多主题系统，支持 10 种精美主题
  - cyberpunk（赛博朋克）- 青色霓虹科技感
  - neon（霓虹之夜）- 紫粉渐变复古风
  - minimalist（极简主义）- 纯白简约日系
  - glass（玻璃拟态）- 毛玻璃 iOS 风格
  - dark（深色模式）- 深灰商务 Discord 风格
  - neumorphism（新拟态）- 浮雕柔和阴影
  - terminal（终端风格）- 命令行黑客风
  - gradient（渐变卡片）- 流动渐变 Instagram 风格
  - retro（复古游戏）- 像素 8-bit 游戏风
  - material（材料设计）- Google Material 风格
- 新增 `/vrctheme` 指令支持查看和切换主题
- 每个会话可独立设置主题，未设置则使用默认主题
- 后台新增 `default_theme` 配置项，可选择全局默认主题

### Changed
- 手动查询和自动推送均使用对应会话的主题渲染
- 主题配置持久化存储，重启后保留
- 使用 fonts.loli.net 替代 fonts.googleapis.com 以提升国内访问速度

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
