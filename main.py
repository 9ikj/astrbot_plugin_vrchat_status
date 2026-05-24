import asyncio
from datetime import datetime, timezone, timedelta

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star


STATUS_HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: fit-content; height: fit-content; overflow: visible; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
</style>
</head>
<body>
<div style="background: #fff; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); overflow: hidden;">
  <!-- 头部 -->
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 24px 28px; color: #fff;">
    <div style="font-size: 16px; opacity: 0.85; margin-bottom: 6px;">服务器状态监测</div>
    <div style="font-size: 28px; font-weight: 700;">🎮 VRChat</div>
  </div>
  <!-- 状态 -->
  <div style="padding: 20px 24px; border-left: 5px solid {{ dot_color }}; margin: 20px; background: #f8f9fa; border-radius: 0 10px 10px 0;">
    <div style="display: flex; align-items: center; gap: 12px;">
      <span style="width: 14px; height: 14px; border-radius: 50%; background: {{ dot_color }}; display: inline-block; flex-shrink: 0;"></span>
      <span style="font-size: 22px; font-weight: 700; color: #1a1a2e;">{{ status }}</span>
    </div>
  </div>
  <!-- 详情 -->
  {% if summary %}
  <div style="padding: 0 24px; margin-bottom: 16px;">
    <div style="background: #fff3cd; color: #856404; padding: 14px 18px; border-radius: 10px; font-size: 17px;">
      ⚠️ 受影响组件: {{ summary }}
    </div>
  </div>
  {% endif %}
  {% if update_time %}
  <div style="padding: 0 24px 20px; color: #999; font-size: 16px;">
    🕐 {{ update_time }}
  </div>
  {% endif %}
</div>
<!-- 隐藏时间戳确保每次渲染不同 -->
<div style="font-size: 0; color: transparent; user-select: none;">{{ timestamp }}</div>
</body>
</html>
'''


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
        await self._fetch_status()
        async for result in self._send_status(event):
            yield result

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
                await self._send_status()

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

        # UTC 转换为 Asia/Shanghai (UTC+8)
        local_time = ""
        if self.last_update_time:
            tz_shanghai = timezone(timedelta(hours=8))
            local_time = self.last_update_time.astimezone(tz_shanghai).strftime("%Y-%m-%d %H:%M:%S")

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
            msg += f"\n更新时间: {self.last_update_time.strftime('%Y-%m-%d %H:%M:%S')}"

        return msg

    async def _send_status(self, event: AstrMessageEvent = None):
        """发送状态消息，优先使用 HTML 渲染"""
        data = self._get_status_data()
        data["timestamp"] = datetime.now().strftime("%H%M%S%f")
        try:
            options = {
                "type": "png",
                "omit_background": True,
                "full_page": False,
                "scale": "device",
                "caret": "hide",
            }
            url = await self.html_render(STATUS_HTML_TEMPLATE, data, options=options)
            if event:
                yield event.image_result(url)
            else:
                msg_chain = MessageChain().image(url)
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
