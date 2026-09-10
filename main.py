"""
astrbot_plugin_meme_echo - 表情包复读机

当用户发送表情包（图片）时，按配置的概率复读相同的表情包。
- 主动发送（不回复用户，不带 @ / 引用）
- 支持群聊白名单
- @Bot 时按识图处理，不复读
"""

import random

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, At
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig


class MemeEchoPlugin(Star):
    """表情包复读插件（主动发送版）"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        prob = self.config.get("reread_probability", 0.3)
        enable = self.config.get("enable", True)
        whitelist = self.config.get("group_whitelist", []) or []
        scope = "全部群聊" if not whitelist else f"{len(whitelist)} 个白名单群"
        logger.info(
            f"[MemeEcho] 插件已加载（主动发送模式） | 启用: {enable} | 复读概率: {prob:.0%} | 生效范围: {scope}"
        )

    async def terminate(self):
        logger.info("[MemeEcho] 插件已卸载")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        # 1. 插件开关
        if not self.config.get("enable", True):
            return

        # 2. 过滤 Bot 自身消息
        if event.get_sender_id() == event.get_self_id():
            return

        # 3. 群聊白名单
        if not self._is_group_allowed(event):
            return

        # 4. 提取图片
        image_component = self._extract_image(event)
        if image_component is None:
            return

        # 5. @Bot 时跳过
        if self._is_bot_mentioned(event):
            logger.debug("[MemeEcho] 检测到 @Bot，跳过复读")
            return

        # 6. 概率判断
        if random.random() >= self._get_safe_probability():
            return

        # 7. 解析图片源
        image_source = self._resolve_image_source(image_component)
        if image_source is None:
            logger.warning("[MemeEcho] 无法解析图片源，跳过")
            return

        # 8. 主动发送（关键改动）
        try:
            chain = self._build_image_chain(image_source)
            await self.context.send_message(event.unified_msg_origin, chain)
            logger.debug(f"[MemeEcho] 已主动发送表情包: {image_source[:80]}")
        except Exception as e:
            logger.error(f"[MemeEcho] 主动发送失败: {e}")

    # ------------------------------------------------------------------
    # 消息链构造
    # ------------------------------------------------------------------

    @staticmethod
    def _build_image_chain(source: str) -> MessageChain:
        """
        根据图片源构造 MessageChain（纯图片，不带 At / Plain / Reply）。
        """
        src = source.strip()

        if src.startswith("base64://"):
            img = Image.fromBase64(src[len("base64://"):])
        elif src.startswith("data:image"):
            # data:image/png;base64,xxxxx
            b64 = src.split(",", 1)[-1] if "," in src else src
            img = Image.fromBase64(b64)
        elif src.startswith("http://") or src.startswith("https://"):
            img = Image.fromURL(src)
        else:
            # 本地文件路径
            img = Image.fromFileSystem(src)

        return MessageChain([img])

    # ------------------------------------------------------------------
    # 群聊白名单
    # ------------------------------------------------------------------

    @staticmethod
    def _get_group_id(event: AstrMessageEvent) -> str | None:
        try:
            gid = event.get_group_id()
            if gid:
                return str(gid)
        except Exception:
            pass
        gid = getattr(event.message_obj, "group_id", None)
        return str(gid) if gid else None

    def _is_group_allowed(self, event: AstrMessageEvent) -> bool:
        whitelist = self.config.get("group_whitelist", []) or []
        if not whitelist:
            return self._get_group_id(event) is not None

        group_id = self._get_group_id(event)
        if group_id is None:
            return False

        allowed = {str(g).strip() for g in whitelist if str(g).strip()}
        return group_id in allowed

    # ------------------------------------------------------------------
    # @Bot 检测
    # ------------------------------------------------------------------

    @staticmethod
    def _is_bot_mentioned(event: AstrMessageEvent) -> bool:
        try:
            self_id = str(event.get_self_id())
            chain = event.message_obj.message
        except AttributeError:
            return False
        if not self_id:
            return False

        for comp in chain:
            if not isinstance(comp, At):
                continue
            target = (
                getattr(comp, "qq", None)
                or getattr(comp, "user_id", None)
                or getattr(comp, "target", None)
            )
            if target is not None and str(target) == self_id:
                return True
        return False

    # ------------------------------------------------------------------
    # 原有工具方法
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