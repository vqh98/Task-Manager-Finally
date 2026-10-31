from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.methods import TelegramMethod


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
RTL_MARK = "\u200f"

# Telegram parses tags before displaying the text. Digits inside href values
# and plain URLs must stay ASCII, otherwise links stop working.
_PROTECTED_PART = re.compile(r"(<[^>]+>|(?:https?|tg)://[^\s<]+)", re.IGNORECASE)


def localize_visible_digits(value: str) -> str:
    """Convert visible ASCII digits while preserving HTML tags and URLs."""
    return "".join(
        part if _PROTECTED_PART.fullmatch(part) else part.translate(PERSIAN_DIGITS)
        for part in _PROTECTED_PART.split(value)
    )


def rtl_line(value: str) -> str:
    """Force Telegram to use an RTL base direction for a displayed line."""
    return value if value.startswith(RTL_MARK) else RTL_MARK + value


def _localize_keyboard(markup: Any) -> None:
    if markup is None:
        return
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None)
    if not rows:
        return
    for row in rows:
        for button in row:
            text = getattr(button, "text", None)
            if isinstance(text, str):
                button.text = localize_visible_digits(text)


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_plain_text(value: str) -> str:
    """Render Telegram HTML as readable plain text for APIs without HTML support."""
    parser = _PlainTextHTMLParser()
    try:
        parser.feed(value)
        parser.close()
        return "".join(parser.parts)
    except Exception:
        return html_lib.unescape(re.sub(r"<[^>]*>", "", value))


class PersianOutputMiddleware:
    """Normalize all user-visible Bot API text in one central layer."""

    def __init__(self, *, plain_text: bool = False) -> None:
        self.plain_text = plain_text

    async def __call__(
        self,
        make_request: Callable[[Bot, TelegramMethod[Any]], Awaitable[Any]],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        for field in ("text", "caption"):
            value = getattr(method, field, None)
            if isinstance(value, str):
                if self.plain_text:
                    value = html_to_plain_text(value)
                setattr(method, field, localize_visible_digits(value))
        if self.plain_text and hasattr(method, "parse_mode"):
            setattr(method, "parse_mode", None)
        _localize_keyboard(getattr(method, "reply_markup", None))
        return await make_request(bot, method)
