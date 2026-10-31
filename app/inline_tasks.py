"""Private task capture from Telegram inline queries (no inline chat messages)."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineQueryResultsButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.keyboards import back_to_menu, main_menu, priorities
from app.parsing import normalize_digits, parse_deadline

logger = logging.getLogger(__name__)


class InlineTaskStates(StatesGroup):
    assignee = State()
    title = State()
    deadline = State()
    priority = State()


async def extract_task(text, ai_service, timezone, now=None):
    """Keep missing values missing; a model outage must not lose the original text."""
    now = now or datetime.now(timezone)
    fields = {"title": None, "deadline": None, "priority": None}
    parsed = None
    if ai_service.available:
        try:
            parsed = await ai_service.parse_task(text, timezone, now)
        except Exception:
            # Do not log source text or model responses from a private conversation.
            logger.warning("Inline task extraction unavailable")
    if parsed:
        fields["title"] = parsed.title
        fields["deadline"] = parsed.deadline.isoformat() if parsed.deadline else None
        fields["priority"] = parsed.priority if parsed.priority in (1, 2, 3, 4) else None
    # Deterministic dates win over model arithmetic. Only parse explicit date text.
    temporal = re.search(
        r"امروز|فردا|دیگر|دیگه|آینده|آينده|شنبه|جمعه|آخر|پایان|"
        r"فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی\b|بهمن|اسفند|"
        r"\d{1,4}[/\-]\d{1,2}|(?:\d+|یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده)\s*(?:روز|هفته|ماه)", normalize_digits(text),
    )
    if temporal:
        date_line = re.search(r"(?:ددلاین|مهلت)\s*[:：]\s*([^\n]+)", text)
        deadline = parse_deadline(date_line.group(1) if date_line else text, timezone, now)
        if deadline:
            fields["deadline"] = deadline.isoformat()
    else:
        fields["deadline"] = None
    if not fields["title"]:
        first_line = re.sub(r"^(?:عنوان|تسک)\s*[:：]\s*", "", text.splitlines()[0]).strip()
        # Long text is preserved as the note; ask for a concise title rather than truncate it.
        fields["title"] = first_line if 0 < len(first_line) <= 200 else None
    elif len(fields["title"]) > 200:
        fields["title"] = None
    if not fields["priority"]:
        label = re.search(r"اولویت\s*[:：]\s*([^\n؛]+)", text)
        value = normalize_digits(label.group(1)).strip() if label else ""
        fields["priority"] = {
            "1": 1, "2": 2, "3": 3, "4": 4, "فوری و مهم": 1,
            "مهم، غیرفوری": 2, "مهم": 2, "فوری": 3, "کم": 4,
        }.get(value)
    return fields


class InlineTaskFlow:
    """Use the running bot's auth, team chart and notification behavior."""

    def __init__(self, context):
        self.ctx = context
        self.router = Router(name="inline-task-capture")
        self.router.inline_query.register(self.query)
        self.router.message.register(
            self.start, CommandStart(deep_link=True), F.text.regexp(r"^/start(?:@\w+)?\s+it_"),
        )
        self.router.message.register(self.cancel, StateFilter(InlineTaskStates), Command("cancel"))
        self.router.message.register(self.cancel, StateFilter(InlineTaskStates), F.text == "لغو")
        self.router.message.register(
            self.navigate, StateFilter(InlineTaskStates), context.ActiveStateNavigationFilter(),
        )
        self.router.callback_query.register(self.open_callback, F.data.startswith("it:open:"))
        self.router.callback_query.register(self.keep_callback, F.data.startswith("it:keep:"))
        self.router.callback_query.register(self.members_callback, F.data.startswith("it:members:"))
        self.router.callback_query.register(self.member_callback, F.data.startswith("it:member:"))
        self.router.callback_query.register(self.priority_callback, InlineTaskStates.priority, F.data.startswith("priority:"))
        self.router.message.register(self.answer_field, StateFilter(InlineTaskStates), ~F.text.startswith("/"))

    async def query(self, query):
        if self.ctx.settings.platform_key != "telegram":
            return
        owner = await self.ctx.db.canonical_platform_user_id("telegram", query.from_user.id)
        text = query.query.strip()
        token = None
        if await self.ctx.allowed(owner) and 0 < len(text) <= 512:
            token = await self.ctx.db.create_inline_task_draft(owner, text)
        await query.answer(
            results=[], cache_time=0, is_personal=True,
            button=InlineQueryResultsButton(
                text="تکمیل تسک در ربات" if token else "راهنمای ثبت تسک در ربات",
                start_parameter=f"it_{token}" if token else "it_help",
            ),
        )

    async def permitted(self, message, actor_id):
        if self.ctx.settings.platform_key != "telegram" or message.chat.type != "private":
            return False
        if not await self.ctx.allowed(actor_id):
            await message.answer("برای ثبت تسک ابتدا دسترسی خود را در ربات فعال کنید؛ سپس دوباره فراخوانی کنید.")
            return False
        return True

    async def start(self, message, state):
        if not await self.permitted(message, message.from_user.id):
            return
        token = message.text.split(maxsplit=1)[1][3:]
        if token == "help":
            await message.answer(
                "در چت موردنظر بنویسید: <code>@نام‌ربات متن تسک</code> و «تکمیل تسک در ربات» را بزنید.\n"
                "پیامی در آن چت ارسال نمی‌شود. طرف مقابل را اینجا از چارت انتخاب می‌کنید؛ "
                "ربات تاریخچه و هویت طرف مقابل چت را نمی‌بیند.",
            )
            return
        await self.open(message, state, token, message.from_user.id)

    async def open(self, message, state, token, owner, replace=False):
        draft = await self.ctx.db.inline_task_draft(token, owner)
        if not draft:
            await message.answer("این پیش‌نویس منقضی شده یا در دسترس شما نیست؛ دوباره از چت فراخوانی کنید.")
            return
        if draft["task_id"] is not None:
            await message.answer("این پیش‌نویس قبلاً به تسک تبدیل شده است؛ تسک دوباره ثبت نشد.")
            return
        data = await state.get_data()
        active = await state.get_state()
        if active and data.get("inline_draft") == token:
            await self.advance(message, state, owner)
            return
        if active and not replace:
            builder = InlineKeyboardBuilder()
            builder.button(text="ادامه فرم قبلی", callback_data=f"it:keep:{token}")
            builder.button(text="کنارگذاشتن فرم قبلی و شروع تسک", callback_data=f"it:open:{token}")
            builder.adjust(1)
            await message.answer("یک فرم نیمه‌کاره دارید. کدام را ادامه می‌دهید؟", reply_markup=builder.as_markup())
            return
        fields = await extract_task(draft["source"], self.ctx.ai_service, self.ctx.settings.timezone)
        await state.clear()
        await state.update_data(inline_draft=token, **fields)
        await message.answer("متن تسک دریافت شد:\n" + html.escape(draft["source"]))
        await self.advance(message, state, owner)

    async def open_callback(self, callback, state):
        await callback.answer()
        if await self.permitted(callback.message, callback.from_user.id):
            await self.open(callback.message, state, callback.data.split(":", 2)[2], callback.from_user.id, replace=True)

    async def keep_callback(self, callback, state):
        await callback.answer("فرم قبلی حفظ شد؛ به همان سؤال قبلی پاسخ دهید.")
        # No FSM writes: even an old button must never erase a newer form.

    async def show_members(self, message, owner, token, page=0):
        members = await self.ctx.db.team_members(owner)
        page = max(0, min(page, max(0, (len(members) - 1) // 12)))
        builder = InlineKeyboardBuilder()
        for member_id, name in members[page * 12:(page + 1) * 12]:
            builder.button(text=(name or str(member_id))[:64], callback_data=f"it:member:{token}:{member_id}")
        if page:
            builder.button(text="قبلی", callback_data=f"it:members:{token}:{page - 1}")
        if (page + 1) * 12 < len(members):
            builder.button(text="بعدی", callback_data=f"it:members:{token}:{page + 1}")
        builder.button(text="➕ افزودن نیرو به چارت", callback_data="team:add")
        builder.button(text="لغو", callback_data="menu:main")
        builder.adjust(1)
        await message.answer(
            "این تسک مربوط به چت با چه کسی است؟\n"
            "طرف مقابل را از چارت انتخاب کنید یا @username / شناسهٔ عددی تلگرام او را بفرستید.\n"
            "اگر در چارت نیست، ابتدا او را اضافه کنید و سپس لینک همین پیش‌نویس را دوباره باز کنید.",
            reply_markup=builder.as_markup(),
        )

    async def valid_callback(self, callback, state):
        if not await self.permitted(callback.message, callback.from_user.id):
            return None
        data = await state.get_data()
        parts = callback.data.split(":")
        if len(parts) != 4 or data.get("inline_draft") != parts[2] or await state.get_state() != InlineTaskStates.assignee.state:
            await callback.message.answer("این دکمه مربوط به فرم فعلی نیست؛ لینک پیش‌نویس را دوباره باز کنید.")
            return None
        draft = await self.ctx.db.inline_task_draft(parts[2], callback.from_user.id)
        if not draft or draft["task_id"] is not None:
            await callback.message.answer("این پیش‌نویس منقضی یا قبلاً ثبت شده است.")
            return None
        return parts

    async def members_callback(self, callback, state):
        await callback.answer()
        parts = await self.valid_callback(callback, state)
        if parts and parts[3].isdigit():
            await self.show_members(callback.message, callback.from_user.id, parts[2], int(parts[3]))

    async def member_callback(self, callback, state):
        await callback.answer()
        parts = await self.valid_callback(callback, state)
        if parts and parts[3].isdigit():
            await self.choose_member(callback.message, state, callback.from_user.id, int(parts[3]))

    async def choose_member(self, message, state, owner, member_id):
        member = await self.ctx.db.team_member(owner, member_id)
        if not member:
            await message.answer("این شخص در چارت فعال شما نیست. او را از مسیر «افزودن نیرو به چارت» اضافه کنید.")
            return
        await state.update_data(assignee_user_id=member_id)
        await message.answer("مسئول پیگیری: " + html.escape(member[1] or str(member_id)))
        await self.advance(message, state, owner)

    async def advance(self, message, state, owner):
        data = await state.get_data()
        draft = await self.ctx.db.inline_task_draft(data.get("inline_draft", ""), owner)
        if not draft or draft["task_id"] is not None:
            await message.answer("پیش‌نویس منقضی یا قبلاً ثبت شده است؛ دوباره از چت فراخوانی کنید.")
            await state.clear()
            return
        member = await self.ctx.db.team_member(owner, data["assignee_user_id"]) if data.get("assignee_user_id") else None
        if not member:
            await state.update_data(assignee_user_id=None)
            await state.set_state(InlineTaskStates.assignee)
            await self.show_members(message, owner, data["inline_draft"])
            return
        if not data.get("title"):
            await state.set_state(InlineTaskStates.title)
            await message.answer("عنوان کوتاه تسک را بفرستید (حداکثر ۲۰۰ حرف).", reply_markup=back_to_menu())
            return
        if not data.get("deadline"):
            await state.set_state(InlineTaskStates.deadline)
            await message.answer("ددلاین این تسک چه زمانی است؟ مثلاً «فردا» یا «۲۰ آبان ۱۴۰۵».", reply_markup=back_to_menu())
            return
        if not data.get("priority"):
            await state.set_state(InlineTaskStates.priority)
            await message.answer("اولویت تسک را انتخاب کنید:", reply_markup=priorities("menu:main"))
            return
        try:
            task_id, created = await self.ctx.db.finalize_inline_task(
                data["inline_draft"], owner, data["title"], datetime.fromisoformat(data["deadline"]),
                data["priority"], data["assignee_user_id"],
            )
        except ValueError as exc:
            if str(exc) == "member_unavailable":
                await state.update_data(assignee_user_id=None)
                await self.advance(message, state, owner)
                return
            await state.clear()
            await message.answer("پیش‌نویس در دسترس نیست؛ دوباره از چت فراخوانی کنید.")
            return
        await state.clear()
        if created:
            await self.ctx.report_created_task(message, task_id, owner, owner)
        else:
            await message.answer("این تسک قبلاً ثبت شده است؛ ثبت تکراری انجام نشد.", reply_markup=main_menu())

    async def answer_field(self, message, state):
        owner = message.from_user.id
        if not await self.permitted(message, owner):
            return
        current = await state.get_state()
        text = (message.text or "").strip()
        if current == InlineTaskStates.assignee.state:
            value = normalize_digits(text)
            member_id = None
            if value.startswith("@"):
                account = await self.ctx.db.telegram_account_by_username(value)
                member_id = account[0] if account else None
            elif value.isdigit():
                member_id = await self.ctx.db.canonical_platform_user_id("telegram", int(value))
            if member_id is None:
                await message.answer("طرف مقابل را از چارت انتخاب کنید یا آیدی معتبر تلگرام او را بفرستید.")
                return
            await self.choose_member(message, state, owner, member_id)
            return
        if current == InlineTaskStates.title.state:
            if not 0 < len(text) <= 200:
                await message.answer("عنوان باید بین ۱ تا ۲۰۰ حرف باشد.")
                return
            await state.update_data(title=text)
        elif current == InlineTaskStates.deadline.state:
            deadline = parse_deadline(text, self.ctx.settings.timezone)
            if not deadline and self.ctx.ai_service.available:
                try:
                    deadline = await self.ctx.ai_service.parse_natural_deadline(
                        text, self.ctx.settings.timezone, datetime.now(self.ctx.settings.timezone),
                    )
                except Exception:
                    logger.warning("Inline deadline extraction unavailable")
            if not deadline:
                await message.answer("ددلاین را متوجه نشدم؛ یک تاریخ روشن مثل «فردا» یا «۲۰ آبان ۱۴۰۵» بفرستید.")
                return
            await state.update_data(deadline=deadline.isoformat())
        else:
            await message.answer("اولویت را از دکمه‌های بالا انتخاب کنید؛ برای لغو /cancel بزنید.")
            return
        await self.advance(message, state, owner)

    async def priority_callback(self, callback, state):
        await callback.answer()
        if not await self.permitted(callback.message, callback.from_user.id):
            return
        value = callback.data.rsplit(":", 1)[1]
        if value not in {"1", "2", "3", "4"}:
            return
        await state.update_data(priority=int(value))
        await self.advance(callback.message, state, callback.from_user.id)

    async def cancel(self, message, state):
        await self.ctx.cancel(message, state)

    async def navigate(self, message, state):
        await self.ctx.leave_stale_form_and_navigate(message, state)
