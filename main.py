"""
astrbot_plugin_meme_echo - 表情包复读机

当用户发送表情包（图片）时，按配置的概率复读相同的表情包。
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
        logger.info(f"[MemeEcho] 插件已加载 | 启用: {enable} | 复读概率: {prob:.0%}")

    async def terminate(self):
        """插件卸载时调用"""
        logger.info("[MemeEcho] 插件已卸载")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        """
        监听所有消息事件，检测表情包并概率复读。

        处理流程：
        1. 检查插件是否启用
        2. 过滤 Bot 自身消息（防止无限循环）
        3. 提取消息中的 Image 组件
        4. 按概率决定是否复读
        5. 获取图片源并发送
        """
        # 1. 插件开关
        if not self.config.get("enable", True):
            return

        # 2. 过滤 Bot 自身消息，防止自复读循环
        if event.get_sender_id() == event.get_self_id():
            return

        # 3. 从消息链中提取图片组件
        image_component = self._extract_image(event)
        if image_component is None:
            return

        # 4. 概率判断
        probability = self._get_safe_probability()
        if random.random() >= probability:
            return  # 未命中概率，不复读

        # 5. 获取图片源
        image_source = self._resolve_image_source(image_component)
        if image_source is None:
            logger.warning("[MemeEcho] 无法解析图片源，跳过复读")
            return

        # 6. 发送复读消息
        try:
            yield event.image_result(image_source)
            logger.debug(f"[MemeEcho] 已复读表情包: {image_source[:80]}")
        except Exception as e:
            logger.error(f"[MemeEcho] 复读表情包失败: {e}")

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image(event: AstrMessageEvent) -> Image | None:
        """
        从消息链中提取第一个 Image 组件。

        Args:
            event: 消息事件

        Returns:
            第一个 Image 组件，若不存在则返回 None
        """
        try:
            message_chain = event.message_obj.message
        except AttributeError:
            return None

        for component in message_chain:
            if isinstance(component, Image):
                return component
        return None

    def _get_safe_probability(self) -> float:
        """
        获取并校验复读概率，确保在 [0.0, 1.0] 范围内。

        Returns:
            安全的概率值
        """
        raw = self.config.get("reread_probability", 0.3)
        try:
            prob = float(raw)
        except (TypeError, ValueError):
            prob = 0.3
        # 强制限制在合法范围
        return min(max(prob, 0.0), 1.0)

    @staticmethod
    def _resolve_image_source(image: Image) -> str | None:
        """
        从 Image 组件中解析出可用的图片源。

        优先级：url > file > path > base64
        不同平台（aiocqhttp / qq_official / 飞书）的字段名可能不同，
        做 fallback 兼容处理。

        Args:
            image: Image 组件

        Returns:
            图片源字符串（URL、本地路径或 base64），无法解析时返回 None
        """
        # 常见的字段名，按优先级排列
        source_fields = ("url", "file", "path", "origin_url", "file_path")

        for field in source_fields:
            value = getattr(image, field, None)
            if value and isinstance(value, str) and value.strip():
                # aiocqhttp 的 file 字段可能是 file:/// 协议头，需要转换
                if field == "file" and value.startswith("file:///"):
                    # 去掉协议头，image_result 接受裸路径
                    return value[7:]  # 去掉 "file://" 的 7 个字符
                return value

        # 最后尝试 base64（部分平台使用）
        base64_data = getattr(image, "base64", None) or getattr(image, "data", None)
        if base64_data and isinstance(base64_data, str):
            # 如果已经是完整的 data URL
            if base64_data.startswith("data:image"):
                return base64_data
            # 否则构造 data URL
            return f"data:image/png;base64,{base64_data}"

        return None