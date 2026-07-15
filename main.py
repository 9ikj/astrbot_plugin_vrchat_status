import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


def _load_template(name: str) -> str:
    template_path = Path(__file__).parent / "templates" / name
    return template_path.read_text(encoding="utf-8")


def _load_themes_config() -> dict:
    """加载主题配置"""
    themes_path = Path(__file__).parent / "themes.json"
    return json.loads(themes_path.read_text(encoding="utf-8"))


def _get_data_dir(plugin_name: str) -> Path:
    """获取插件数据目录"""
    data_dir = Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class VRChatStatusPlugin(Star):
    """VRChat 服务器状态监测插件"""

    # VRChat 状态 API
    STATUS_API = "https://status.vrchat.com/api/v2/status.json"
    SUMMARY_API = "https://status.vrchat.com/api/v2/summary.json"

    # VRChat 指标 API（Statuspage CloudFront CDN，每分钟一个点，共 1441 点覆盖 24 小时）
    METRICS_API = {
        "visits": "https://d31qqo63tn8lj0.cloudfront.net/visits.json",
        "latency": "https://d31qqo63tn8lj0.cloudfront.net/apilatency.json",
        "requests": "https://d31qqo63tn8lj0.cloudfront.net/apirequests.json",
        "errors": "https://d31qqo63tn8lj0.cloudfront.net/apierrors.json",
    }

    # 状态指示器映射
    STATUS_EMOJI = {
        "none": "🟢",
        "minor": "🟡",
        "major": "🔴",
        "critical": "🔴",
    }

    @staticmethod
    def _is_abnormal_indicator(indicator: str) -> bool:
        """Statuspage uses "none" to mean no incident."""
        return bool(indicator) and indicator != "none"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 状态存储
        self.last_status = ""
        self.last_indicator = ""
        self.last_summary = ""
        self.last_update_time = None
        self.is_running = False

        # 从文件存储加载注册的会话
        self.registered_sessions: list[str] = self._load_sessions()

        # 加载主题配置
        self.themes_config = _load_themes_config()

        # 加载会话主题设置
        self.session_themes: dict[str, str] = self._load_session_themes()

        # 启动轮询任务
        self.is_running = True
        self._first_poll = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("VRChat Status 插件已启动")

    def _load_sessions(self) -> list[str]:
        """从文件加载注册的会话"""
        sessions_file = _get_data_dir(self.name) / "sessions.json"
        if sessions_file.exists():
            try:
                return json.loads(sessions_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载会话文件失败: {e}")
        return []

    def _save_sessions(self):
        """保存注册的会话到文件"""
        sessions_file = _get_data_dir(self.name) / "sessions.json"
        try:
            sessions_file.write_text(
                json.dumps(self.registered_sessions, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存会话文件失败: {e}")

    def _load_session_themes(self) -> dict[str, str]:
        """从文件加载会话主题设置"""
        themes_file = _get_data_dir(self.name) / "session_themes.json"
        if themes_file.exists():
            try:
                return json.loads(themes_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载会话主题文件失败: {e}")
        return {}

    def _save_session_themes(self):
        """保存会话主题设置到文件"""
        themes_file = _get_data_dir(self.name) / "session_themes.json"
        try:
            themes_file.write_text(
                json.dumps(self.session_themes, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存会话主题文件失败: {e}")

    def _get_theme_for_session(self, session_id: str) -> str:
        """获取会话使用的主题"""
        return self.session_themes.get(session_id, self.config.get("default_theme", "cyberpunk"))

    def _get_theme_file(self, theme_id: str) -> str:
        """根据主题 ID 获取模板文件名"""
        for theme in self.themes_config.get("themes", []):
            if theme["id"] == theme_id:
                return theme["file"]
        return "cyberpunk.html"  # 默认主题

    async def terminate(self):
        """插件卸载/停用时调用"""
        self.is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("VRChat Status 插件已停止")

    @filter.command("vrcstatus")
    async def check_status(self, event: AstrMessageEvent):
        """手动查询 VRChat 服务器状态"""
        await self._fetch_status()
        async for result in self._send_status(event):
            yield result

    @filter.command("vrcsubscribe")
    async def subscribe(self, event: AstrMessageEvent):
        """订阅 VRChat 状态变化通知"""
        umo = event.unified_msg_origin
        if umo not in self.registered_sessions:
            self.registered_sessions.append(umo)
            self._save_sessions()
            yield event.plain_result("已订阅 VRChat 状态变化通知")
        else:
            yield event.plain_result("当前会话已订阅")

    @filter.command("vrcunsubscribe")
    async def unsubscribe(self, event: AstrMessageEvent):
        """取消订阅 VRChat 状态变化通知"""
        umo = event.unified_msg_origin
        if umo in self.registered_sessions:
            self.registered_sessions.remove(umo)
            self._save_sessions()
            yield event.plain_result("已取消订阅 VRChat 状态变化通知")
        else:
            yield event.plain_result("当前会话未订阅")

    @filter.command("vrctheme")
    async def set_theme(self, event: AstrMessageEvent):
        """设置或查看当前会话的主题"""
        args = event.message_str.split(maxsplit=1)
        umo = event.unified_msg_origin

        # 无参数：显示当前主题和可用主题列表
        if len(args) == 1:
            current_theme = self._get_theme_for_session(umo)
            theme_name = next(
                (t["name"] for t in self.themes_config["themes"] if t["id"] == current_theme),
                current_theme
            )

            available_themes = "\n".join(
                f"{i+1}. {t['name']} ({t['id']})"
                for i, t in enumerate(self.themes_config["themes"])
            )

            result = f"当前主题: {theme_name}\n\n可用主题:\n{available_themes}\n\n使用 /vrctheme <主题ID> 切换主题"
            yield event.plain_result(result)
            return

        # 有参数：设置主题
        theme_id = args[1].strip().lower()

        # 验证主题是否存在
        valid_themes = [t["id"] for t in self.themes_config["themes"]]
        if theme_id not in valid_themes:
            yield event.plain_result(f"无效的主题 ID: {theme_id}\n使用 /vrctheme 查看可用主题")
            return

        # 保存主题设置
        self.session_themes[umo] = theme_id
        self._save_session_themes()

        theme_name = next(t["name"] for t in self.themes_config["themes"] if t["id"] == theme_id)
        yield event.plain_result(f"已将主题切换为: {theme_name}")
        logger.info(f"会话 {umo} 切换主题为: {theme_id}")

    async def _poll_loop(self):
        """轮询循环"""
        while self.is_running:
            try:
                await self._check_status_change()
            except Exception as e:
                logger.error(f"VRChat 状态检查失败: {e}")

            # 根据状态决定轮询间隔（从配置读取）
            interval_abnormal = self.config.get("poll_interval_abnormal", 120)
            interval_normal = self.config.get("poll_interval_normal", 900)
            is_abnormal = self._is_abnormal_indicator(self.last_indicator)
            interval = interval_abnormal if is_abnormal else interval_normal

            logger.info(f"下次轮询间隔: {interval}秒 ({'异常' if is_abnormal else '正常'}模式)")
            await asyncio.sleep(interval)

    async def _check_status_change(self):
        """检查状态变化并推送通知"""
        old_status = self.last_status
        old_indicator = self.last_indicator

        await self._fetch_status()

        # 首次轮询仅记录状态，不推送
        if self._first_poll:
            self._first_poll = False
            logger.info(f"首次轮询完成，当前状态: {self.last_status}, 指示器: {self.last_indicator}")
            return

        # 状态发生变化时推送通知
        status_changed = self.last_status != old_status
        indicator_changed = self.last_indicator != old_indicator

        logger.info(f"状态检查: old_status={old_status}, new_status={self.last_status}, "
                   f"old_indicator={old_indicator}, new_indicator={self.last_indicator}, "
                   f"changed={status_changed or indicator_changed}")

        if status_changed or indicator_changed:
            if self.registered_sessions:
                logger.info(f"状态变化，推送到 {len(self.registered_sessions)} 个会话")
                async for _ in self._send_status():
                    pass
            else:
                logger.info("状态变化但无订阅者")

    async def _fetch_status(self):
        """获取 VRChat 状态"""
        headers = {"User-Agent": "AstrBot-VRChatStatusPlugin"}

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 获取状态
                async with session.get(self.STATUS_API, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"VRChat 状态 API 返回 {resp.status}")
                        return
                    data = await resp.json()

                status = data.get("status", {})
                self.last_status = status.get("description") or ""
                self.last_indicator = status.get("indicator") or ""
                self.last_update_time = datetime.fromisoformat(
                    data["page"]["updated_at"].replace("Z", "+00:00")
                )

                # 如果有异常，获取详细信息
                if self._is_abnormal_indicator(self.last_indicator):
                    async with session.get(self.SUMMARY_API, headers=headers) as resp:
                        if resp.status == 200:
                            summary_data = await resp.json()
                            components = summary_data.get("components", [])
                            abnormal = [
                                c["name"]
                                for c in components
                                if c.get("status") != "operational"
                            ]
                            self.last_summary = ", ".join(abnormal) if abnormal else ""
                else:
                    self.last_summary = ""

        except Exception as e:
            logger.error(f"获取 VRChat 状态失败: {e}")

    def _get_status_data(self, metrics: dict | None = None, theme_id: str = "cyberpunk") -> dict:
        """获取状态模板数据（含可选的 24 小时平台指标 HTML 片段）"""
        # 基础状态字段
        if not self.last_status:
            data = {
                "status": "正常运行",
                "dot_color": "#28a745",
                "summary": "",
                "update_time": "",
            }
        else:
            dot_color = "#28a745" if not self._is_abnormal_indicator(self.last_indicator) else "#dc3545"
            if self.last_indicator == "minor":
                dot_color = "#ffc107"

            local_time = ""
            if self.last_update_time:
                offset = self.config.get("timezone_offset", 8)
                tz_local = timezone(timedelta(hours=offset))
                local_time = self.last_update_time.astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S")

            data = {
                "status": self.last_status,
                "dot_color": dot_color,
                "summary": self.last_summary,
                "update_time": local_time,
            }

        # metrics 以自包含 HTML 片段形式注入，模板用 {{ metrics_html | safe }} 引入
        data["metrics_html"] = self._build_metrics_html(metrics or {}, theme_id)
        return data

    def _format_status_message(self) -> str:
        """格式化状态消息（纯文本）"""
        if not self.last_status:
            return "VRChat 服务器状态: 🟢 正常运行"

        emoji = self.STATUS_EMOJI.get(self.last_indicator, "⚪")
        msg = f"VRChat 服务器状态: {emoji} {self.last_status}"

        if self.last_summary:
            msg += f"\n受影响组件: {self.last_summary}"

        if self.last_update_time:
            offset = self.config.get("timezone_offset", 8)
            tz_local = timezone(timedelta(hours=offset))
            local_time = self.last_update_time.astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S")
            msg += f"\n更新时间: {local_time}"

        return msg

    async def _send_status(self, event: AstrMessageEvent = None):
        """发送状态消息，优先使用 HTML 渲染"""
        if not event and not self.registered_sessions:
            logger.info("无订阅者，跳过推送")
            return

        # 拉取 24 小时平台指标（并发 4 个接口，失败不影响主流程）
        metrics = await self._fetch_metrics()
        timestamp = datetime.now().strftime("%H%M%S%f")

        # 通用截图参数
        options = {
            "type": "png",
            "omit_background": True,
            "full_page": False,
            "scale": "css",
            "caret": "hide",
        }

        try:
            if event:
                # 手动查询：使用当前会话的主题
                theme_id = self._get_theme_for_session(event.unified_msg_origin)
                theme_file = self._get_theme_file(theme_id)
                data = self._get_status_data(metrics, theme_id)
                data["timestamp"] = timestamp
                html_template = _load_template(theme_file)
                url = await self.html_render(html_template, data, options=options)
                logger.info(f"HTML 渲染成功: {url}")
                yield event.image_result(url)
            else:
                # 推送通知：逐个会话使用各自主题（metrics_html 需按主题重建）
                for umo in self.registered_sessions:
                    try:
                        session_theme_id = self._get_theme_for_session(umo)
                        session_theme_file = self._get_theme_file(session_theme_id)
                        session_data = self._get_status_data(metrics, session_theme_id)
                        session_data["timestamp"] = timestamp
                        html_template = _load_template(session_theme_file)
                        url = await self.html_render(html_template, session_data, options=options)
                        logger.info(f"推送到 {umo}，使用主题: {session_theme_id}，渲染成功: {url}")

                        if url.startswith(("http://", "https://")):
                            msg_chain = MessageChain().url_image(url)
                        else:
                            msg_chain = MessageChain().file_image(url)
                        await self.context.send_message(umo, msg_chain)
                    except Exception as e:
                        logger.error(f"推送到 {umo} 失败: {e}")
        except Exception as e:
            logger.warning(f"HTML 渲染失败，使用纯文本: {e}")
            text = self._format_status_message()
            if event:
                yield event.plain_result(text)
            else:
                msg_chain = MessageChain().message(text)
                for umo in self.registered_sessions:
                    try:
                        logger.info(f"推送文本到 {umo}")
                        await self.context.send_message(umo, msg_chain)
                    except Exception as e2:
                        logger.error(f"发送消息到 {umo} 失败: {e2}")

    # ===================== 指标图表 =====================

    async def _fetch_metrics(self) -> dict:
        """从 Statuspage CDN 拉取 4 项平台指标（各 1441 点，24 小时窗口）

        返回 {"visits": [[ts, v], ...], "latency": [...], ...}，请求失败对应键缺失。
        """
        headers = {"User-Agent": "AstrBot-VRChatStatusPlugin"}
        timeout = aiohttp.ClientTimeout(total=10)
        results: dict[str, list] = {}

        async def _get_one(session: aiohttp.ClientSession, key: str, url: str):
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"指标 {key} API 返回 {resp.status}")
                        return
                    # Statuspage CDN 返回的 Content-Type 未必是 application/json
                    data = await resp.json(content_type=None)
                if isinstance(data, list):
                    results[key] = data
            except Exception as e:
                logger.error(f"获取指标 {key} 失败: {e}")

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await asyncio.gather(
                    *(_get_one(session, k, u) for k, u in self.METRICS_API.items())
                )
        except Exception as e:
            logger.error(f"获取 VRChat 指标失败: {e}")

        return results

    @staticmethod
    def _downsample(points: list, target: int = 180) -> list:
        """把时间序列下采样到 target 个点（分桶均值），保留趋势并降低 SVG 体积"""
        if not points:
            return []
        n = len(points)
        if n <= target:
            return list(points)
        bucket_size = n / target
        out = []
        for i in range(target):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size)
            if end <= start:
                end = start + 1
            bucket = points[start:end]
            if not bucket:
                continue
            ts = bucket[len(bucket) // 2][0]
            avg = sum(p[1] for p in bucket) / len(bucket)
            out.append([ts, avg])
        return out

    @staticmethod
    def _build_sparkline(
        points: list,
        width: int = 400,
        height: int = 90,
        color: str = "#00ffc8",
        y_zero: bool = False,
    ) -> dict:
        """把点序列转换为 SVG path，供模板直接嵌入

        返回:
          - line: SVG path 的 d 属性（折线）
          - area: SVG path 的 d 属性（面积填充，含底边闭合）
          - color: 颜色透传
          - min / max / avg / latest: 数值统计
        """
        empty = {"line": "", "area": "", "color": color,
                 "min": 0, "max": 0, "avg": 0, "latest": 0}
        if not points:
            return empty

        values = [p[1] for p in points]
        v_min = min(values)
        v_max = max(values)
        v_avg = sum(values) / len(values)
        v_latest = values[-1]

        # 纵轴范围：可选强制从 0 起（错误率、请求数适用）
        y_low = 0 if y_zero else v_min
        y_high = v_max
        if y_high - y_low < 1e-9:
            # 数据完全平坦，加一点点余量避免除零
            y_high = y_low + max(abs(y_low), 1.0) * 0.04 or 1.0

        n = len(points)
        pad_x = 4
        pad_top = 6
        pad_bottom = 6
        inner_w = width - 2 * pad_x
        inner_h = height - pad_top - pad_bottom

        coords = []
        for i, (_, v) in enumerate(points):
            x = pad_x + (inner_w * i / max(n - 1, 1))
            y = pad_top + inner_h * (1 - (v - y_low) / (y_high - y_low))
            coords.append((x, y))

        line_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        area_d = (
            f"M {coords[0][0]:.1f},{height - pad_bottom:.1f} "
            + "L " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            + f" L {coords[-1][0]:.1f},{height - pad_bottom:.1f} Z"
        )

        return {
            "line": line_d,
            "area": area_d,
            "color": color,
            "min": v_min,
            "max": v_max,
            "avg": v_avg,
            "latest": v_latest,
        }

    @staticmethod
    def _fmt_users(v: float) -> str:
        """在线人数：整数，带千分位"""
        return f"{int(round(v)):,}"

    @staticmethod
    def _fmt_ms(v: float) -> str:
        """API 延迟：秒 → 毫秒"""
        return f"{v * 1000:.0f}ms"

    @staticmethod
    def _fmt_rps(v: float) -> str:
        """API 请求速率"""
        if v >= 1000:
            return f"{v / 1000:.2f}k/s"
        return f"{v:.2f}/s"

    @staticmethod
    def _fmt_pct(v: float) -> str:
        """错误率：0-1 → 百分比"""
        return f"{v * 100:.3f}%"

    # 4 项指标的展示规格：(数据键, 中文标签, 强调色, 格式化函数, y 轴强制从 0)
    _METRIC_SPECS = (
        ("visits",   "在线人数", "#00ffc8", "_fmt_users", False),
        ("latency",  "API 延迟", "#4facfe", "_fmt_ms",    False),
        ("requests", "API 请求", "#f093fb", "_fmt_rps",   True),
        ("errors",   "错误率",   "#ff6b6b", "_fmt_pct",   True),
    )

    @staticmethod
    def _metrics_theme_styles(theme_id: str) -> dict:
        """返回主题对应的指标片段样式令牌

        每个令牌值都是可直接放进 style 属性的 CSS 声明串。
        """
        # 深色实底（cyberpunk 系）
        cyberpunk = {
            "container": (
                "margin:0 32px 20px;padding:14px 16px;"
                "background:rgba(0,255,200,0.03);"
                "border:1px solid rgba(0,255,200,0.15);"
                "border-radius:10px;"
            ),
            "header": (
                "font-family:'Orbitron',monospace;font-size:10px;font-weight:600;"
                "letter-spacing:3px;text-transform:uppercase;"
                "color:rgba(0,255,200,0.6);margin-bottom:10px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:10px;background:rgba(255,255,255,0.02);"
                "border:1px solid rgba(0,255,200,0.08);border-radius:8px;"
            ),
            "cell_title": (
                "color:rgba(255,255,255,0.55);font-size:10px;font-weight:500;"
                "text-transform:uppercase;letter-spacing:0.5px;"
            ),
            "cell_value_font": "font-family:'Orbitron',monospace;font-size:15px;font-weight:700;",
            "cell_sub": (
                "color:rgba(255,255,255,0.3);font-size:9px;"
                "font-family:'Orbitron',monospace;letter-spacing:0.5px;margin-top:2px;"
            ),
        }
        # 深色柔和（dark 模式）
        dark = {
            "container": (
                "margin-top:16px;padding:14px;background:#23272a;"
                "border:1px solid #3a3d42;border-radius:12px;"
            ),
            "header": (
                "color:#72767d;font-size:11px;font-weight:500;"
                "letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:10px;background:#36393f;"
                "border:1px solid #42464d;border-radius:8px;"
            ),
            "cell_title": "color:#b9bbbe;font-size:11px;font-weight:500;",
            "cell_value_font": "font-size:15px;font-weight:600;",
            "cell_sub": "color:#72767d;font-size:10px;margin-top:2px;",
        }
        # 半透明玻璃（neon / glass / gradient）
        glass = {
            "container": (
                "margin-top:16px;padding:14px;"
                "background:rgba(255,255,255,0.15);"
                "border:1px solid rgba(255,255,255,0.22);border-radius:14px;"
                "backdrop-filter:blur(10px);"
            ),
            "header": (
                "color:rgba(255,255,255,0.75);font-size:11px;font-weight:600;"
                "letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:10px;background:rgba(255,255,255,0.15);"
                "border:1px solid rgba(255,255,255,0.2);border-radius:10px;"
            ),
            "cell_title": (
                "color:rgba(255,255,255,0.85);font-size:11px;font-weight:600;"
                "letter-spacing:0.3px;"
            ),
            "cell_value_font": (
                "font-size:15px;font-weight:700;"
                "text-shadow:0 1px 4px rgba(0,0,0,0.2);"
            ),
            "cell_sub": "color:rgba(255,255,255,0.6);font-size:10px;margin-top:2px;",
        }
        # 终端风格
        terminal = {
            "container": (
                "margin-top:12px;padding:12px;background:#161b22;"
                "border:1px solid #30363d;border-radius:4px;"
                "font-family:'Fira Code','JetBrains Mono',monospace;"
            ),
            "header": (
                "color:#7ee787;font-size:11px;font-weight:700;"
                "font-family:'Fira Code',monospace;margin-bottom:10px;"
            ),
            "header_prefix": '<span style="color:#58a6ff;margin-right:6px;">></span>',
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:8px 10px;background:#0d1117;"
                "border:1px solid #21262d;border-radius:4px;"
            ),
            "cell_title": (
                "color:#79c0ff;font-size:11px;"
                "font-family:'Fira Code',monospace;"
            ),
            "cell_value_font": (
                "font-family:'Fira Code',monospace;font-size:14px;font-weight:700;"
            ),
            "cell_sub": (
                "color:#6e7681;font-size:10px;"
                "font-family:'Fira Code',monospace;margin-top:2px;"
            ),
        }
        # 复古像素
        retro = {
            "container": (
                "margin-top:14px;padding:12px;background:#16213e;"
                "border:3px solid #0f3460;"
            ),
            "header": (
                "font-family:'Press Start 2P',cursive;font-size:7px;"
                "color:#00d9ff;letter-spacing:1px;margin-bottom:10px;"
                "text-shadow:2px 2px 0 #0f3460;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:10px;background:#1a1a2e;border:2px solid #0f3460;"
            ),
            "cell_title": (
                "font-family:'Press Start 2P',cursive;font-size:6px;"
                "color:#00d9ff;letter-spacing:1px;margin-bottom:6px;"
            ),
            "cell_value_font": (
                "font-family:'VT323',monospace;font-size:20px;font-weight:700;line-height:1;"
            ),
            "cell_sub": (
                "font-family:'VT323',monospace;font-size:13px;"
                "color:#00d9ff;margin-top:4px;"
            ),
        }
        # 极简白
        minimalist = {
            "container": (
                "margin:20px 32px 0;padding:16px;"
                "background:#fafafa;border:1px solid #f0f0f0;border-radius:12px;"
            ),
            "header": (
                "color:#999;font-size:11px;font-weight:600;"
                "letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:8px;",
            "cell": (
                "padding:10px 12px;background:#ffffff;"
                "border:1px solid #ececec;border-radius:8px;"
            ),
            "cell_title": (
                "color:#666;font-size:11px;font-weight:500;"
                "letter-spacing:0.2px;"
            ),
            "cell_value_font": "font-size:15px;font-weight:600;letter-spacing:-0.2px;",
            "cell_sub": "color:#aaa;font-size:10px;margin-top:2px;",
        }
        # 材料设计
        material = {
            "container": (
                "margin-top:16px;padding:16px;background:#fafafa;"
                "border-radius:8px;"
                "box-shadow:0 1px 3px rgba(0,0,0,0.08);"
            ),
            "header": (
                "color:#616161;font-size:12px;font-weight:500;"
                "letter-spacing:0.5px;text-transform:uppercase;margin-bottom:12px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:10px;",
            "cell": (
                "padding:12px;background:#ffffff;border-radius:6px;"
                "box-shadow:0 1px 2px rgba(0,0,0,0.06);"
            ),
            "cell_title": "color:#616161;font-size:12px;font-weight:500;",
            "cell_value_font": "font-size:16px;font-weight:500;letter-spacing:0.15px;",
            "cell_sub": "color:#9e9e9e;font-size:11px;margin-top:2px;",
        }
        # 新拟态
        neumorphism = {
            "container": (
                "margin-top:16px;padding:14px;background:#e6e9f0;"
                "border-radius:16px;"
                "box-shadow:inset 4px 4px 8px rgba(163,177,198,0.35),"
                "inset -4px -4px 8px rgba(255,255,255,0.55);"
            ),
            "header": (
                "color:#9baacf;font-size:11px;font-weight:600;"
                "letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px;"
            ),
            "grid": "display:grid;grid-template-columns:1fr 1fr;gap:10px;",
            "cell": (
                "padding:10px 12px;background:#e6e9f0;border-radius:12px;"
                "box-shadow:3px 3px 6px rgba(163,177,198,0.4),"
                "-3px -3px 6px rgba(255,255,255,0.6);"
            ),
            "cell_title": "color:#7890b8;font-size:11px;font-weight:600;",
            "cell_value_font": "font-size:15px;font-weight:600;letter-spacing:-0.2px;",
            "cell_sub": "color:#9baacf;font-size:10px;margin-top:2px;",
        }
        mapping = {
            "cyberpunk": cyberpunk,
            "dark": dark,
            "neon": glass,
            "glass": glass,
            "gradient": glass,
            "terminal": terminal,
            "retro": retro,
            "minimalist": minimalist,
            "material": material,
            "neumorphism": neumorphism,
        }
        return mapping.get(theme_id, cyberpunk)

    def _build_metrics_html(self, metrics: dict, theme_id: str) -> str:
        """构造可嵌入任意主题的 24 小时平台指标 HTML 片段

        - 空指标 → 返回空串，让模板自然折叠
        - 每个指标一张 sparkline，2×2 排布
        - 全部使用内联样式，避免与外层模板选择器冲突
        """
        charts = []
        for key, label, color, fmt_name, y_zero in self._METRIC_SPECS:
            raw = metrics.get(key, []) or []
            points = self._downsample(raw, target=180)
            chart = self._build_sparkline(points, color=color, y_zero=y_zero)
            fmt = getattr(self, fmt_name)
            charts.append({
                "key": key,
                "label": label,
                "color": color,
                "line": chart["line"],
                "area": chart["area"],
                "latest": fmt(chart["latest"]) if points else "--",
                "avg": fmt(chart["avg"]) if points else "--",
                "has_data": bool(points),
            })

        # 全部无数据：不渲染任何内容
        if not any(c["has_data"] for c in charts):
            return ""

        s = self._metrics_theme_styles(theme_id)
        header_prefix = s.get("header_prefix", "")

        cells = []
        for c in charts:
            if c["has_data"]:
                grad_id = f"vrcm-{theme_id}-{c['key']}"
                area_svg = (
                    f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
                    f'<stop offset="0%" stop-color="{c["color"]}" stop-opacity="0.35"/>'
                    f'<stop offset="100%" stop-color="{c["color"]}" stop-opacity="0"/>'
                    f'</linearGradient></defs>'
                    f'<path d="{c["area"]}" fill="url(#{grad_id})"/>'
                )
                line_svg = (
                    f'<path d="{c["line"]}" fill="none" '
                    f'stroke="{c["color"]}" stroke-width="1.6" '
                    f'stroke-linecap="round" stroke-linejoin="round"/>'
                )
                svg = (
                    f'<svg viewBox="0 0 400 90" preserveAspectRatio="none" '
                    f'style="width:100%;height:34px;display:block;margin:6px 0 4px;">'
                    f'{area_svg}{line_svg}</svg>'
                )
                sub = f'<div style="{s["cell_sub"]}">avg {c["avg"]}</div>'
            else:
                svg = (
                    '<div style="height:34px;margin:6px 0 4px;opacity:0.35;'
                    'display:flex;align-items:center;justify-content:center;'
                    'font-size:10px;">no data</div>'
                )
                sub = f'<div style="{s["cell_sub"]}">avg --</div>'

            value_style = f'{s["cell_value_font"]}color:{c["color"]};'
            cells.append(
                f'<div style="{s["cell"]}">'
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:baseline;gap:8px;">'
                f'<span style="{s["cell_title"]}">{c["label"]}</span>'
                f'<span style="{value_style}">{c["latest"]}</span>'
                f'</div>'
                f'{svg}'
                f'{sub}'
                f'</div>'
            )

        return (
            f'<div style="{s["container"]}">'
            f'<div style="{s["header"]}">{header_prefix}24H METRICS</div>'
            f'<div style="{s["grid"]}">{"".join(cells)}</div>'
            f'</div>'
        )
