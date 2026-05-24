import asyncio
from datetime import datetime

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star


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

        # 注册的会话列表（用于主动推送）
        self.registered_sessions: list[str] = []

        # 轮询任务
        self._poll_task = None

    async def initialize(self):
        """插件初始化"""
        # 从配置加载注册的会话
        self.registered_sessions = self.config.get("registered_sessions", [])

        # 启动轮询任务
        self.is_running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("VRChat Status 插件已启动")

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
        status_text = await self._fetch_and_format_status()
        yield event.plain_result(status_text)

    @filter.command("vrcsubscribe")
    async def subscribe(self, event: AstrMessageEvent):
        """订阅 VRChat 状态变化通知"""
        umo = event.unified_msg_origin
        if umo not in self.registered_sessions:
            self.registered_sessions.append(umo)
            self.config["registered_sessions"] = self.registered_sessions
            self.config.save_config()
            yield event.plain_result("已订阅 VRChat 状态变化通知")
        else:
            yield event.plain_result("当前会话已订阅")

    @filter.command("vrcunsubscribe")
    async def unsubscribe(self, event: AstrMessageEvent):
        """取消订阅 VRChat 状态变化通知"""
        umo = event.unified_msg_origin
        if umo in self.registered_sessions:
            self.registered_sessions.remove(umo)
            self.config["registered_sessions"] = self.registered_sessions
            self.config.save_config()
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

        # 状态发生变化时推送通知
        if self.last_status != old_status or self.last_indicator != old_indicator:
            if self.registered_sessions:
                status_text = self._format_status_message()
                await self._notify_subscribers(status_text)

    async def _fetch_status(self):
        """获取 VRChat 状态"""
        headers = {"User-Agent": "AstrBot-VRChatStatusPlugin"}

        try:
            async with aiohttp.ClientSession() as session:
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

    async def _fetch_and_format_status(self) -> str:
        """获取并格式化状态信息（用于手动查询）"""
        await self._fetch_status()
        return self._format_status_message()

    def _format_status_message(self) -> str:
        """格式化状态消息"""
        if not self.last_status:
            return "VRChat 服务器状态: 🟢 正常运行"

        emoji = self.STATUS_EMOJI.get(self.last_indicator, "⚪")
        msg = f"VRChat 服务器状态: {emoji} {self.last_status}"

        if self.last_summary:
            msg += f"\n受影响组件: {self.last_summary}"

        if self.last_update_time:
            msg += f"\n更新时间: {self.last_update_time.strftime('%Y-%m-%d %H:%M:%S')}"

        return msg

    async def _notify_subscribers(self, message: str):
        """通知所有订阅者"""
        msg_chain = MessageChain().message(message)
        for umo in self.registered_sessions:
            try:
                await self.context.send_message(umo, msg_chain)
            except Exception as e:
                logger.error(f"发送消息到 {umo} 失败: {e}")
