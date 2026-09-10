"""
astrbot_plugin_meme_echo - 表情包复读机

当用户发送表情包（图片）时，按配置的概率复读相同的表情包。
支持群聊白名单。
"""

import random

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig


class MemeEchoPlugin(Star):
    """表情包复读插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        """插件加载时调用"""
        prob = self.config.get("reread_probability", 0.3)
        enable = self.config.get("enable", True)
        whitelist = self.config.get("group_whitelist", []) or []
        scope = "全部群聊" if not whitelist else f"{len(whitelist)} 个白名单群"
        logger.info(
            f"[MemeEcho] 插件已加载 | 启用: {enable} | 复读概率: {prob:.0%} | 生效范围: {scope}"
        )

    async def terminate(self):
        """插件卸载时调用"""
        logger.info("[MemeEcho] 插件已卸载")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        """
        监听所有消息事件，检测表情包并概率复读。
        """
        # 1. 插件开关
        if not self.config.get("enable", True):
            return

        # 2. 过滤 Bot 自身消息，防止自复读循环
        if event.get_sender_id() == event.get_self_id():
            return

        # 3. 群聊白名单判断（新增）
        if not self._is_group_allowed(event):
            return

        # 4. 从消息链中提取图片组件
        image_component = self._extract_image(event)
        if image_component is None:
            return

        # 5. 概率判断
        probability = self._get_safe_probability()
        if random.random() >= probability:
            return

        # 6. 获取图片源
        image_source = self._resolve_image_source(image_component)
        if image_source is None:
            logger.warning("[MemeEcho] 无法解析图片源，跳过复读")
            return

        # 7. 发送复读消息
        try:
            yield event.image_result(image_source)
            logger.debug(f"[MemeEcho] 已复读表情包: {image_source[:80]}")
        except Exception as e:
            logger.error(f"[MemeEcho] 复读表情包失败: {e}")

    # ------------------------------------------------------------------
    # 群聊白名单
    # ------------------------------------------------------------------

    @staticmethod
    def _get_group_id(event: AstrMessageEvent) -> str | None:
        """
        兼容多平台获取当前群号，私聊或无群号时返回 None。
        """
        # 优先用官方 API
        try:
            gid = event.get_group_id()
            if gid:
                return str(gid)
        except Exception:
            pass
        # 退化到消息对象上的字段
        gid = getattr(event.message_obj, "group_id", None)
        return str(gid) if gid else None

    def _is_group_allowed(self, event: AstrMessageEvent) -> bool:
        """
        判断当前会话是否在白名单内。

        规则：
        - 白名单为空 → 所有群聊放行（私聊仍然不触发，因为没有群号）
        - 白名单非空 → 仅列表中的群号放行
        """
        whitelist = self.config.get("group_whitelist", []) or []
        if not whitelist:
            # 空白名单 = 不限制群聊；但必须能取到群号，私聊不触发
            return self._get_group_id(event) is not None

        group_id = self._get_group_id(event)
        if group_id is None:
            return False

        allowed = {str(g).strip() for g in whitelist if str(g).strip()}
        return group_id in allowed

    # ------------------------------------------------------------------
    # 原有工具方法（保持不变）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image(event: AstrMessageEvent) -> Image | None:
        try:
            message_chain = event.message_obj.message
        except AttributeError:
            return None

        for component in message_chain:
            if isinstance(component, Image):
                return component
        return None

    def _get_safe_probability(self) -> float:
        raw = self.config.get("reread_probability", 0.3)
        try:
            prob = float(raw)
        except (TypeError, ValueError):
            prob = 0.3
        return min(max(prob, 0.0), 1.0)

    @staticmethod
    def _resolve_image_source(image: Image) -> str | None:
        source_fields = ("url", "file", "path", "origin_url", "file_path")

        for field in source_fields:
            value = getattr(image, field, None)
            if value and isinstance(value, str) and value.strip():
                if field == "file" and value.startswith("file:///"):
                    return value[7:]
                return value

        base64_data = getattr(image, "base64", None) or getattr(image, "data", None)
        if base64_data and isinstance(base64_data, str):
            if base64_data.startswith("data:image"):
                return base64_data
            return f"data:image/png;base64,{base64_data}"

        return None