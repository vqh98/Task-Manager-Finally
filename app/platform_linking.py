from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup

from app.db import Database


class CanonicalAccountMiddleware(BaseMiddleware):
    """Expose one canonical user/chat id to handlers on every messaging platform."""

    def __init__(self, db: Database, platform: str):
        self.db = db
        self.platform = platform

    async def __call__(self, handler: Callable, event: Any, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        raw_user_id = int(user.id)
        canonical = await self.db.remember_platform_account(
            self.platform, raw_user_id, user.username, user.full_name or ""
        )
        data["raw_platform_user_id"] = raw_user_id
        data["platform_key"] = self.platform
        canonical_user = user.model_copy(update={"id": canonical})

        if isinstance(event, Message):
            updates: dict[str, Any] = {"from_user": canonical_user}
            if event.chat.type == "private":
                updates["chat"] = event.chat.model_copy(update={"id": canonical})
            event = event.model_copy(update=updates)
        elif isinstance(event, CallbackQuery):
            updates = {"from_user": canonical_user}
            if isinstance(event.message, Message) and event.message.chat.type == "private":
                updates["message"] = event.message.model_copy(update={
                    "chat": event.message.chat.model_copy(update={"id": canonical})
                })
            event = event.model_copy(update=updates)
        return await handler(event, data)


class PlatformRecipientMiddleware:
    """Translate canonical private-chat ids back to this platform's native ids."""

    def __init__(self, db: Database, platform: str):
        self.db = db
        self.platform = platform

    async def __call__(
        self,
        make_request: Callable[[Bot, TelegramMethod[Any]], Awaitable[Any]],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, int):
            await self._filter_manager_panel_button(method, chat_id)
            recipient = await self.db.platform_recipient(self.platform, chat_id)
            if recipient is not None:
                setattr(method, "chat_id", recipient)
            elif self.platform != "telegram" and chat_id > 0:
                raise RuntimeError(
                    f"Canonical account {chat_id} has no linked {self.platform} recipient"
                )
        return await make_request(bot, method)

    async def _filter_manager_panel_button(
        self, method: TelegramMethod[Any], canonical_user_id: int,
    ) -> None:
        """Hide manager panels unless the recipient can view shared tasks or events."""
        markup = getattr(method, "reply_markup", None)
        if not isinstance(markup, ReplyKeyboardMarkup):
            return
        panel_labels = {"👔 پنل مدیر", "👔 پنل مدیرهای من"}
        if not any(button.text in panel_labels for row in markup.keyboard for button in row):
            return
        event_access = await self.db.shared_event_managers(canonical_user_id)
        task_access = await self.db.shared_task_managers(canonical_user_id)
        can_view_manager_panel = bool(event_access) or any(row[2] for row in task_access)
        if can_view_manager_panel:
            return
        keyboard = [
            [button for button in row if button.text not in panel_labels]
            for row in markup.keyboard
        ]
        keyboard = [row for row in keyboard if row]
        setattr(method, "reply_markup", markup.model_copy(update={"keyboard": keyboard}))
