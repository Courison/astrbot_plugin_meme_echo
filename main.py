"""
astrbot_plugin_meme_echo - 表情包 / 消息复读机

功能：
- 图片复读：用户发表情包/图片时按概率主动发相同图片
- 文本复读：用户发文本时按概率主动发相同文本
- @Bot 消息一律不复读
- 支持群聊白名单
- 主动发送（不带 @ / 引用）
"""

import random

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, At, Plain
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig


class MemeEchoPlugin(Star):
    """表情包 / 消息复读插件（主动发送版）"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        text_on = self.config.get("text_reread_enable", False)
        text_p = self.config.get("text_reread_probability", 0.1)
        img_on = self.config.get("image_reread_enable", True)
        img_p = self.config.get("image_reread_probability", 0.3)
        whitelist = self.config.get("group_whitelist", []) or []
        scope = "全部群聊" if not whitelist else f"{len(whitelist)} 个白名单群"
        logger.info(
            f"[MemeEcho] 插件已加载（主动发送模式） | "
            f"文本复读: {'开' if text_on else '关'} {text_p:.0%} | "
            f"图片复读: {'开' if img_on else '关'} {img_p:.0%} | "
            f"生效范围: {scope}"
        )

    async def terminate(self):
        logger.info("[MemeEcho] 插件已卸载")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        # 1. 总开关
        if not self.config.get("enable", True):
            return

        # 2. 过滤 Bot 自身消息
        if event.get_sender_id() == event.get_self_id():
            return

        # 3. 群白名单
        if not self._is_group_allowed(event):
            return

        # 4. 只要 @ 了 Bot，一律不复读（文本/图片都不复读）
        if self._is_bot_mentioned(event):
            logger.debug("[MemeEcho] 检测到 @Bot，跳过复读")
            return

        # 5. 图片复读分支
        image_component = self._extract_image(event)
        if image_component is not None:
            if not self.config.get("image_reread_enable", True):
                return
            if random.random() >= self._safe_prob("image_reread_probability", 0.3):
                return
            image_source = self._resolve_image_source(image_component)
            if image_source is None:
                logger.warning("[MemeEcho] 无法解析图片源，跳过")
                return
            try:
                chain = self._build_image_chain(image_source)
                await self.context.send_message(event.unified_msg_origin, chain)
                logger.debug(f"[MemeEcho] 已主动发送图片: {image_source[:80]}")
            except Exception as e:
                logger.error(f"[MemeEcho] 主动发送图片失败: {e}")
            return

        # 6. 文本复读分支（仅当消息中无图片时）
        if not self.config.get("text_reread_enable", False):
            return
        text = self._extract_text(event)
        if not text:
            return
        if random.random() >= self._safe_prob("text_reread_probability", 0.1):
            return
        try:
            chain = MessageChain([Plain(text)])
            await self.context.send_message(event.unified_msg_origin, chain)
            logger.debug(f"[MemeEcho] 已主动发送文本: {text[:80]}")
        except Exception as e:
            logger.error(f"[MemeEcho] 主动发送文本失败: {e}")

    # ------------------------------------------------------------------
    # 消息提取
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

    @staticmethod
    def _extract_text(event: AstrMessageEvent) -> str | None:
        """拼接消息链中的所有 Plain 文本，去除首尾空白。"""
        try:
            message_chain = event.message_obj.message
        except AttributeError:
            return None
        parts = []
        for comp in message_chain:
            if isinstance(comp, Plain):
                parts.append(comp.text)
        text = "".join(parts).strip()
        return text or None

    # ------------------------------------------------------------------
    # 消息链构造
    # ------------------------------------------------------------------

    @staticmethod
    def _build_image_chain(source: str) -> MessageChain:
        src = source.strip()

        if src.startswith("base64://"):
            img = Image.fromBase64(src[len("base64://"):])
        elif src.startswith("data:image"):
            b64 = src.split(",", 1)[-1] if "," in src else src
            img = Image.fromBase64(b64)
        elif src.startswith("http://") or src.startswith("https://"):
            img = Image.fromURL(src)
        else:
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
    # 工具
    # ------------------------------------------------------------------

    def _safe_prob(self, key: str, default: float) -> float:
        raw = self.config.get(key, default)
        try:
            prob = float(raw)
        except (TypeError, ValueError):
            prob = default
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