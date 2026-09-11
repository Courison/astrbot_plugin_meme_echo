"""
astrbot_plugin_meme_echo - 表情包 / 消息复读机

功能：
- 表情包复读：用户发表情包（sticker）时按概率主动发相同图片
- 图片复读：用户发普通图片时按概率主动发相同图片
- 文本复读：用户发文本时按概率主动发相同文本
- @Bot 消息一律不复读
- 支持群聊白名单
- 支持关键词黑名单（消息文本命中即跳过）
- 主动发送（不带 @ / 引用）

表情包 vs 普通图片识别：
- 优先读取 OneBot 协议 Image 组件的 sub_type 字段
  - sub_type == 1 → 表情包（客户端缩放显示，无查看原图/下载）
  - sub_type == 0 或字段缺失 → 普通图片
- 多级兜底：组件属性 → 组件原始 dict → raw_message 中的 message 数组
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
        img_p = self.config.get("image_reread_probability", 0.1)
        sticker_on = self.config.get("sticker_reread_enable", True)
        sticker_p = self.config.get("sticker_reread_probability", 0.3)
        whitelist = self.config.get("group_whitelist", []) or []
        blacklist = self.config.get("keyword_blacklist", []) or []
        scope = "全部群聊" if not whitelist else f"{len(whitelist)} 个白名单群"
        logger.info(
            f"[MemeEcho] 插件已加载（主动发送模式） | "
            f"文本: {'开' if text_on else '关'} {text_p:.0%} | "
            f"图片: {'开' if img_on else '关'} {img_p:.0%} | "
            f"表情包: {'开' if sticker_on else '关'} {sticker_p:.0%} | "
            f"生效范围: {scope} | 关键词黑名单: {len(blacklist)} 条"
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

        # 4. @ 了 Bot 一律不复读（识图/对话交给其他插件）
        if self._is_bot_mentioned(event):
            logger.debug("[MemeEcho] 检测到 @Bot，跳过复读")
            return

        # 5. 关键词黑名单（先于图片/文本分支）
        text = self._extract_text(event) or ""
        if self._hit_keyword_blacklist(text):
            logger.debug(f"[MemeEcho] 命中关键词黑名单，跳过复读: {text[:50]}")
            return

        # 6. 图片分支（区分表情包 / 普通图片）
        image_component, is_sticker = self._extract_image_and_check(event)
        if image_component is not None:
            if is_sticker:
                enable_key = "sticker_reread_enable"
                prob_key = "sticker_reread_probability"
                default_prob = 0.3
                label = "表情包"
            else:
                enable_key = "image_reread_enable"
                prob_key = "image_reread_probability"
                default_prob = 0.1
                label = "普通图片"

            if not self.config.get(enable_key, True):
                logger.debug(f"[MemeEcho] {label}复读开关关闭，跳过")
                return

            if random.random() >= self._safe_prob(prob_key, default_prob):
                return

            image_source = self._resolve_image_source(image_component)
            if image_source is None:
                logger.warning("[MemeEcho] 无法解析图片源，跳过")
                return

            try:
                chain = self._build_image_chain(image_source)
                await self.context.send_message(event.unified_msg_origin, chain)
                logger.debug(f"[MemeEcho] 已主动发送{label}: {image_source[:80]}")
            except Exception as e:
                logger.error(f"[MemeEcho] 主动发送{label}失败: {e}")
            return

        # 7. 文本分支
        if not self.config.get("text_reread_enable", False):
            return
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
    # 图片提取 + 表情包识别
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image_and_check(event: AstrMessageEvent):
        """
        提取第一个 Image 组件，并判断是否为表情包。

        返回: (Image 组件 或 None, 是否表情包: bool)

        识别依据（OneBot 协议）：
        - sub_type == 1 → 表情包（客户端缩放显示，无查看原图/下载）
        - sub_type == 0 或字段缺失 → 普通图片
        """
        try:
            message_chain = event.message_obj.message
        except AttributeError:
            return None, False

        for component in message_chain:
            if not isinstance(component, Image):
                continue

            # 优先从组件本身读 sub_type
            sub_type = getattr(component, "sub_type", None)

            # 兜底：从原始消息里读
            if sub_type is None:
                sub_type = MemeEchoPlugin._read_sub_type_from_raw(event, component)

            try:
                is_sticker = (sub_type is not None and int(sub_type) == 1)
            except (TypeError, ValueError):
                is_sticker = False

            return component, is_sticker

        return None, False

    @staticmethod
    def _read_sub_type_from_raw(event: AstrMessageEvent, component: Image):
        """
        兜底：尝试从原始消息对象里读取 sub_type。

        不同 AstrBot / OneBot 实现里，图片的原始数据可能挂在：
        - component.raw / component.data / component._data
        - event.message_obj.raw_message['message'] 数组内的 image 段
        """
        # 组件自带的原始 dict
        for attr in ("raw", "data", "_data"):
            raw = getattr(component, attr, None)
            if isinstance(raw, dict):
                sub = raw.get("sub_type") or raw.get("subType")
                if sub is not None:
                    return sub

        # 事件原始消息里的 message 数组
        raw_message = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw_message, dict):
            for seg in raw_message.get("message", []) or []:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") == "image":
                    data = seg.get("data", {}) or {}
                    sub = data.get("sub_type") or data.get("subType")
                    if sub is not None:
                        return sub

        return None

    # ------------------------------------------------------------------
    # 关键词黑名单
    # ------------------------------------------------------------------

    def _hit_keyword_blacklist(self, text: str) -> bool:
        if not text:
            return False

        blacklist = self.config.get("keyword_blacklist", []) or []
        if not blacklist:
            return False

        haystack = text.lower()
        for kw in blacklist:
            k = str(kw).strip().lower()
            if k and k in haystack:
                return True
        return False

    # ------------------------------------------------------------------
    # 消息提取
    # ------------------------------------------------------------------

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