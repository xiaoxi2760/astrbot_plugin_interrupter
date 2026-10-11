# -*- coding: utf-8 -*-
"""复读撤回打断机。

功能：
- 复读检测打断：检测群内连续相同的消息，按概率发送"打断施法"文本、
  跟风复读，或直接撤回复读的消息（需机器人管理员权限）。
- 引用撤回：引用任意消息回复指定文本（默认"撤回"），机器人会撤回被引用的消息。

仅"引用撤回"依赖 OneBot v11（aiocqhttp）平台；其余功能全平台可用。
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import (
    EventMessageType,
    PlatformAdapterType,
    command,
    event_message_type,
    platform_adapter_type,
)
from astrbot.api.message_components import At, Face, Image, Plain, Record, Reply
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_interrupter"

_DEFAULTS = {
    "trigger_count": 2,
    "break_spell_probability": 0.3,
    "break_spell_text": "打断施法！",
    "follow_repeat": True,
    "enable_recall": False,
    "recall_max_count": 3,
    "recall_trigger_text": "撤回",
    "recall_scope": "all",
    "stop_llm": True,
}


@dataclass
class _Streak:
    """一个会话内当前复读串的状态。"""

    fingerprint: str
    count: int = 1
    message_ids: list[str] = field(default_factory=list)
    fired: bool = False


class InterrupterPlugin(Star):
    """复读撤回打断机。

    指令：
      /复读 on|off        开启或关闭本群的复读检测打断功能（可用别名 fudu / repetition）
      /复读               查看本群当前状态与配置摘要

    复读打断：群里出现连续 trigger_count 条相同消息时触发一次：
      - 按概率发送"打断施法"文本；
      - 未触发文本时可选跟风复读；
      - 可选同时撤回参与复读的消息（需机器人管理员，仅 OneBot v11）。

    引用撤回：引用任意消息回复"撤回"（可配置），机器人尝试撤回被引用的消息。
    撤回失败（无权限、消息超时等）时静默忽略。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        # session(unified_msg_origin) -> 当前复读串状态
        self._streaks: dict[str, _Streak] = {}
        # 已关闭复读检测功能的群号集合（持久化到插件数据目录）
        self._disabled_groups: set[str] = set()
        self._data_file: Path | None = None

    async def initialize(self):
        """插件加载/热重载时调用：恢复本群开关状态。"""
        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
            self._data_file = data_dir / "disabled_groups.json"
            if self._data_file.exists():
                self._disabled_groups = set(
                    json.loads(self._data_file.read_text(encoding="utf-8"))
                )
        except Exception as e:
            self._data_file = None
            logger.warning(f"{PLUGIN_NAME}: 加载分群开关状态失败：{e}")

    async def terminate(self):
        await self._save_disabled_groups()

    async def _save_disabled_groups(self):
        if self._data_file is None:
            return
        try:
            self._data_file.write_text(
                json.dumps(sorted(self._disabled_groups), ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"{PLUGIN_NAME}: 保存分群开关状态失败：{e}")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str):
        value = self.config.get(key, _DEFAULTS[key])
        try:
            if key == "trigger_count":
                return max(2, int(value))
            if key == "recall_max_count":
                return max(1, int(value))
            if key == "break_spell_probability":
                return min(1.0, max(0.0, float(value)))
            if key == "follow_repeat" or key == "enable_recall" or key == "stop_llm":
                return bool(value)
        except (TypeError, ValueError):
            return _DEFAULTS[key]
        return value

    # ------------------------------------------------------------------
    # 指令：/复读 on|off
    # ------------------------------------------------------------------

    @command("复读", alias={"fudu", "repetition"})
    async def toggle_repetition(self, event: AstrMessageEvent, operation: str = ""):
        '''开关本群的复读检测打断功能

        Args:
            operation(string): on 开启 / off 关闭，留空查看状态
        '''
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅在群聊中可用")
            return

        if operation == "off":
            self._disabled_groups.add(group_id)
            await self._save_disabled_groups()
            yield event.plain_result("已在本群关闭复读检测打断功能")
        elif operation == "on":
            self._disabled_groups.discard(group_id)
            await self._save_disabled_groups()
            yield event.plain_result("已在本群开启复读检测打断功能")
        else:
            state = "开启" if group_id not in self._disabled_groups else "关闭"
            yield event.plain_result(
                f"复读检测打断功能当前状态：{state}\n"
                f"触发条数：{self._cfg('trigger_count')} | "
                f"打断概率：{self._cfg('break_spell_probability')}\n"
                f"跟风复读：{'开' if self._cfg('follow_repeat') else '关'} | "
                f"撤回复读：{'开' if self._cfg('enable_recall') else '关'}\n"
                f"用法：/复读 on 或 /复读 off"
            )

    # ------------------------------------------------------------------
    # 复读检测打断
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(chain: list) -> str:
        """把消息链规约成可比较的指纹；结构不同的消息得到不同指纹。"""
        parts = []
        for seg in chain:
            if isinstance(seg, Plain):
                parts.append(f"t:{seg.text}")
            elif isinstance(seg, Image):
                parts.append(f"i:{seg.file or seg.url}")
            elif isinstance(seg, At):
                parts.append(f"at:{seg.qq}")
            elif isinstance(seg, Face):
                parts.append(f"f:{seg.id}")
            elif isinstance(seg, Record):
                parts.append(f"r:{seg.file}")
            elif isinstance(seg, Reply):
                parts.append(f"q:{seg.id}")
            else:
                parts.append(type(seg).__name__)
        return "|".join(parts)

    @staticmethod
    def _rebuild_chain(chain: list) -> list:
        """重建用于跟风复读的消息链：去掉引用段，图片尽量用 URL 重建。"""
        new_chain = []
        for seg in chain:
            if isinstance(seg, Reply):
                continue
            if isinstance(seg, Image) and seg.url:
                try:
                    new_chain.append(Image.fromURL(seg.url))
                    continue
                except Exception:
                    pass
            new_chain.append(seg)
        return new_chain

    def _should_track(self, event: AstrMessageEvent) -> bool:
        """复读检测只统计普通群消息。"""
        if event.is_wake_up():
            return False
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return False
        chain = event.get_messages()
        if not chain:
            return False
        if any(isinstance(seg, Reply) for seg in chain):
            return False
        return True

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        '''检测群复读并按配置打断。'''
        yield
        if not self._should_track(event):
            return

        group_id = event.get_group_id()
        if group_id in self._disabled_groups:
            return

        fingerprint = self._fingerprint(event.get_messages())
        if not fingerprint:
            return

        session = event.unified_msg_origin
        if len(self._streaks) > 2000:
            # 防御性上限，避免长期运行下会话状态无限增长
            self._streaks.clear()

        streak = self._streaks.get(session)
        if streak and streak.fingerprint == fingerprint:
            streak.count += 1
            streak.message_ids.append(str(event.message_obj.message_id))
        else:
            streak = _Streak(
                fingerprint=fingerprint,
                message_ids=[str(event.message_obj.message_id)],
            )
            self._streaks[session] = streak

        if streak.fired or streak.count < self._cfg("trigger_count"):
            return

        # 同一串复读只打断一次
        streak.fired = True

        # 附加动作：撤回复读的消息（需机器人管理员，仅 OneBot v11）
        if self._cfg("enable_recall"):
            await self._recall_streak(event, streak)

        # 主动作：概率发"打断施法"文本，否则可选跟风复读
        if random.random() < self._cfg("break_spell_probability"):
            yield event.plain_result(str(self._cfg("break_spell_text")))
        elif self._cfg("follow_repeat"):
            new_chain = self._rebuild_chain(event.get_messages())
            if new_chain:
                yield event.chain_result(new_chain)

    async def _recall_streak(self, event: AstrMessageEvent, streak: _Streak):
        """撤回复读串里的消息，仅支持 OneBot v11，失败静默忽略。"""
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.debug(f"{PLUGIN_NAME}: 非 OneBot v11 平台，跳过撤回复读。")
            return
        max_count = self._cfg("recall_max_count")
        message_ids = streak.message_ids[-max_count:]
        for message_id in message_ids:
            try:
                await asyncio.wait_for(
                    bot.call_action("delete_msg", message_id=int(message_id)),
                    timeout=10,
                )
                logger.info(f"{PLUGIN_NAME}: 已撤回复读消息 {message_id}。")
            except Exception as e:
                # 没有管理员权限、消息不存在、ID 解析失败、超时等，均静默忽略
                logger.debug(
                    f"{PLUGIN_NAME}: 撤回复读消息 {message_id} 失败：{e}",
                )

    # ------------------------------------------------------------------
    # 引用撤回
    # ------------------------------------------------------------------

    @platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def on_recall(self, event: AstrMessageEvent):
        '''引用消息回复指定文本即可撤回被引用的消息。'''
        yield
        trigger = str(self.config.get("recall_trigger_text") or _DEFAULTS["recall_trigger_text"])
        if event.message_str.strip() != trigger:
            return

        reply_seg = next(
            (seg for seg in event.get_messages() if isinstance(seg, Reply)),
            None,
        )
        if reply_seg is None:
            return

        if self._cfg("recall_scope") == "self_only" and str(reply_seg.sender_id) != str(
            event.get_self_id()
        ):
            return

        # call_llm 为 True 时不会触发 AstrBot 默认的 LLM 请求
        if self._cfg("stop_llm"):
            event.should_call_llm(True)

        bot = getattr(event, "bot", None)
        if bot is None:
            logger.debug(f"{PLUGIN_NAME}: 未找到 OneBot v11 bot 实例，跳过撤回。")
            return

        message_id = int(reply_seg.id)
        try:
            await asyncio.wait_for(
                bot.call_action("delete_msg", message_id=message_id),
                timeout=10,
            )
            logger.info(f"{PLUGIN_NAME}: 已撤回消息 {message_id}。")
        except Exception as e:
            # 没有权限、消息不存在、超时等失败均静默忽略
            logger.debug(f"{PLUGIN_NAME}: 撤回消息 {message_id} 失败：{e}")
