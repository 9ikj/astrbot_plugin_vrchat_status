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

    # 状态指示器映射
    STATUS_EMOJI = {
        "none": "🟢",
        "minor": "🟡",
        "major": "🔴",
        "critical": "🔴",
    }

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

    async def _poll_loop(self):
        """轮询循环"""
        while self.is_running:
            try:
                await self._check_status_change()
            except Exception as e:
                logger.error(f"VRChat 状态检查失败: {e}")

            # 根据状态决定轮询间隔
            interval = 120 if self.last_indicator else 900  # 异常 2 分钟，正常 15 分钟
            await asyncio.sleep(interval)

    async def _check_status_change(self):
        """检查状态变化并推送通知"""
        old_status = self.last_status
        old_indicator = self.last_indicator

        await self._fetch_status()

        # 首次轮询仅记录状态，不推送
        if self._first_poll:
            self._first_poll = False
            return

        # 状态发生变化时推送通知
        if self.last_status != old_status or self.last_indicator != old_indicator:
            if self.registered_sessions:
                async for _ in self._send_status():
                    pass

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
                self.last_status = status.get("description", "")
                self.last_indicator = status.get("indicator", "")
                self.last_update_time = datetime.fromisoformat(
                    data["page"]["updated_at"].replace("Z", "+00:00")
                )

                # 如果有异常，获取详细信息
                if self.last_indicator:
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

    def _get_status_data(self) -> dict:
        """获取状态模板数据"""
        if not self.last_status:
            return {
                "status": "正常运行",
                "dot_color": "#28a745",
                "summary": "",
                "update_time": "",
            }

        dot_color = "#28a745" if self.last_indicator == "none" else "#dc3545"
        if self.last_indicator == "minor":
            dot_color = "#ffc107"

        # UTC 转换为本地时区
        local_time = ""
        if self.last_update_time:
            offset = self.config.get("timezone_offset", 8)
            tz_local = timezone(timedelta(hours=offset))
            local_time = self.last_update_time.astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "status": self.last_status,
            "dot_color": dot_color,
            "summary": self.last_summary,
            "update_time": local_time,
        }

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
            return
        data = self._get_status_data()
        data["timestamp"] = datetime.now().strftime("%H%M%S%f")
        try:
            html_template = _load_template("status.html")
            options = {
                "type": "png",
                "omit_background": True,
                "full_page": False,
                "scale": "css",
                "caret": "hide",
            }
            url = await self.html_render(html_template, data, options=options)
            if event:
                yield event.image_result(url)
            else:
                msg_chain = MessageChain().file_image(url)
                for umo in self.registered_sessions:
                    try:
                        await self.context.send_message(umo, msg_chain)
                    except Exception as e:
                        logger.error(f"发送消息到 {umo} 失败: {e}")
        except Exception as e:
            logger.warning(f"HTML 渲染失败，使用纯文本: {e}")
            text = self._format_status_message()
            if event:
                yield event.plain_result(text)
            else:
                msg_chain = MessageChain().message(text)
                for umo in self.registered_sessions:
                    try:
                        await self.context.send_message(umo, msg_chain)
                    except Exception as e2:
                        logger.error(f"发送消息到 {umo} 失败: {e2}")
