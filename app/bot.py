from __future__ import annotations

import html
import logging
import math
import re
from datetime import date, datetime, time, timedelta

import jdatetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.ai import AIService, clean_transcript
from app.charts import progress_chart, weekly_management_card
from app.config import Settings, load_settings
from app.db import Database
from app.keyboards import add_reminder_button, back_button, back_to_menu, main_menu, management_exit, member_menu, meeting_confirmation, meeting_range_options, meeting_recurrence_options, priorities, reminder_actions, reminders_menu, reminder_repeat_options, reminder_weekdays, task_actions
from app.models import Reminder, Task
from app.parsing import (
    ParsedMeeting, enforce_explicit_calendar_date, enforce_explicit_relative_day,
    normalize_digits, parse_datetime,
    parse_deadline, parse_forwarded_meeting, summarize_meeting_title,
)


router = Router()
settings: Settings
db: Database
ai_service: AIService


class NewTask(StatesGroup):
    title = State()
    deadline = State()
    priority = State()
    assignee = State()
    note = State()
    cc = State()
    reminder_repeat = State()


class EditTask(StatesGroup):
    progress = State()
    note = State()
    cc = State()


class ManagementCommand(StatesGroup):
    text = State()


class EditReminder(StatesGroup):
    value = State()
    repeat = State()


class NewMeeting(StatesGroup):
    details = State()
    time = State()
    title = State()
    confirm = State()
    recurrence = State()


class MoveMeeting(StatesGroup):
    new_time = State()


class MeetingSettings(StatesGroup):
    quiet_range = State()
    digest_time = State()
    morning_time = State()
    warning_days = State()
    shared_alerts = State()


class EnterAccessCode(StatesGroup):
    code = State()


class CreateAccessCode(StatesGroup):
    code = State()


class TeamManagement(StatesGroup):
    member = State()


class ProfileSetup(StatesGroup):
    manager_name = State()
    edit_manager_name = State()

class MemberReport(StatesGroup):
    progress = State()
    note = State()


async def allowed(user_id: int) -> bool:
    return await db.is_authorized(user_id, datetime.now(settings.timezone))


async def member_reply_menu(user_id: int):
    calendars = await db.shared_event_managers(user_id)
    task_managers = await db.shared_task_managers(user_id)
    names = {}
    for manager_id, name, _ in calendars:
        names[manager_id] = name or str(manager_id)
    for manager_id, name, *_ in task_managers:
        names[manager_id] = name or str(manager_id)
    return member_menu(list(names.values()))
PERSIAN_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
PERSIAN_NUMBERS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_number(value: object) -> str:
    return str(value).translate(PERSIAN_NUMBERS)


def fmt_date(value: datetime) -> str:
    local = value.astimezone(settings.timezone)
    jalali = jdatetime.datetime.fromgregorian(datetime=local)
    return f"{fa_number(jalali.day)} {PERSIAN_MONTHS[jalali.month - 1]} {fa_number(jalali.year)}"


def fmt_dt(value: datetime) -> str:
    local = value.astimezone(settings.timezone)
    return f"{fmt_date(local)}، ساعت {fa_number(local.strftime('%H:%M'))}"


def recurrence_description(recurrence: dict | None) -> str:
    if not recurrence:
        return ""
    frequency = recurrence.get("frequency")
    interval = int(recurrence.get("interval") or 1)
    if frequency == "daily":
        return "روزانه" if interval == 1 else f"هر {fa_number(interval)} روز"
    if frequency == "even_days":
        return "روزهای زوج"
    if frequency == "odd_days":
        return "روزهای فرد"
    if frequency == "monthly_jalali":
        return "ماهانه"
    if frequency == "weekly":
        names = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")
        weekdays = [names[int(day)] for day in recurrence.get("weekdays") or [] if 0 <= int(day) < 7]
        suffix = "، ".join(weekdays) or "همین روز"
        return f"هفتگی ({suffix})" if interval == 1 else f"هر {fa_number(interval)} هفته ({suffix})"
    return "تکرارشونده"


def task_text(
    task: Task, display_number: int | None = None, viewer_id: int | None = None,
) -> str:
    note = html.escape(task.last_note) if task.last_note else "—"
    assignee = html.escape(task.assignee) if task.assignee else "—"
    actor_id = task.last_changed_by or task.user_id
    if viewer_id == actor_id:
        changed_by = "شما"
    else:
        actor_name = (task.last_changed_by_name or "").strip()
        if not actor_name and actor_id == task.user_id:
            actor_name = "مدیر تسک"
        elif not actor_name and actor_id == task.assignee_user_id and task.assignee:
            actor_name = task.assignee
        changed_by = html.escape(actor_name or "کاربر")
    last_changed_at = task.updated_at or task.created_at
    remaining = math.ceil((task.deadline - datetime.now(task.deadline.tzinfo or settings.timezone)).total_seconds() / 86400)
    if remaining < 0:
        remaining_text = f"🔻 {fa_number(abs(remaining))} روز از ددلاین گذشته"
    elif remaining == 0:
        remaining_text = "⏳ ددلاین امروز است"
    else:
        remaining_text = f"⏳ {fa_number(remaining)} روز تا ددلاین"
    return (
        f"<b>{fa_number(display_number) + '. ' if display_number is not None else ''}{html.escape(task.title)}</b>\n"
        f"{task.priority.label}\n"
        f"📥 تاریخ ثبت: {fmt_date(task.created_at)}\n"
        f"⏰ ددلاین: {fmt_dt(task.deadline)}\n"
        f"{remaining_text}\n"
        f"📈 پیشرفت: {fa_number(task.progress)}٪\n"
        f"👤 مسئول پیگیری: {assignee}\n"
        f"📝 آخرین توضیح: {note}\n"
        f"🕘 آخرین تغییر: {fmt_date(last_changed_at)} توسط {changed_by}\n"
        f"📨 رونوشت گزارش: {'فعال' if task.cc_manager_id else 'خاموش'}"
    )


def reminder_text(reminder: Reminder, display_number: int | None = None) -> str:
    repeat_labels = {
        "once": "یک‌بار مصرف", "daily": "روزانه", "every_other_day": "روز درمیان",
        "even_days": "روزهای زوج", "odd_days": "روزهای فرد", "monthly": "ماهانه",
    }
    if reminder.repeat_type.startswith("weekly:"):
        names = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
        try:
            repeat = f"هفتگی (هر {names[int(reminder.repeat_type.split(':')[1])]})"
        except (ValueError, IndexError):
            repeat = "هفتگی"
    else:
        repeat = repeat_labels.get(reminder.repeat_type, reminder.repeat_type)
    until = f"\nپایان تکرار: {fmt_dt(reminder.repeat_until)}" if reminder.repeat_until else ""
    return (
        f"<b>{fa_number(display_number) + '. ' if display_number is not None else ''}"
        f"{html.escape(reminder.title)}</b>\n"
        f"⏰ زمان: {fmt_dt(reminder.due_at)}\n"
        f"🔁 نوع: {repeat}{until}"
    )


async def reject_if_needed(event: Message | CallbackQuery) -> bool:
    user_id = event.from_user.id if event.from_user else 0
    if await allowed(user_id):
        return False
    if isinstance(event, Message):
        await event.answer(
            "⛔️ هنوز اجازه استفاده از این ربات را ندارید.\n\n"
            f"شناسه تلگرام شما: <code>{user_id}</code>\n"
            "این شناسه را برای مالک ربات بفرستید تا از بخش «مدیریت دسترسی» شما را اضافه کند."
        )
    else:
        await event.answer("دسترسی غیرمجاز", show_alert=True)
    return True


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.remember_telegram_account(
        message.from_user.id, message.from_user.username, message.from_user.full_name or ""
    )
    if not await allowed(message.from_user.id):
        memberships = await db.member_managers(message.from_user.id)
        if memberships:
            await message.answer(
                "سلام! شما به‌عنوان نیروی پیگیری در یک یا چند تیم ثبت شده‌اید. "
                "از بخش زیر می‌توانید تسک‌های واگذارشده و جلسات هر مدیر را ببینید.",
                reply_markup=await member_reply_menu(message.from_user.id),
            )
            return
        builder = InlineKeyboardBuilder()
        builder.button(text="🔑 وارد کردن کد ورود", callback_data="access_code:enter")
        await message.answer(
            "سلام! استفاده از این ربات رایگان است، اما برای ورود باید کد ورودی را که مالک ربات ساخته وارد کنید.\n\n"
            f"شناسه تلگرام شما: <code>{message.from_user.id}</code>",
            reply_markup=builder.as_markup(),
        )
        return
    if not await db.user_display_name(message.from_user.id):
        await state.set_state(ProfileSetup.manager_name)
        await message.answer(
            "برای نمایش نام شما در منوی نیروها، نامی که می‌خواهید کنار دکمه جلسات دیده شود بفرستید؛ "
            "مثلاً «رضا»."
        )
        return
    await message.answer(
        "سلام! من دستیار مدیریت جلسات و تسک‌های شما هستم.\n\n"
        "یک پیام حاوی اطلاعات جلسه را برایم ارسال یا فوروارد کنید تا تاریخ و ساعتش را استخراج کنم.",
        reply_markup=main_menu(),
    )


@router.message(ProfileSetup.manager_name)
async def save_manager_display_name(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split()).strip()
    if len(name) < 2:
        await message.answer("نام را واضح‌تر بفرستید؛ مثلاً «رضا».")
        return
    if not await db.set_user_display_name(message.from_user.id, name):
        await message.answer("ثبت نام انجام نشد؛ دوباره تلاش کنید.")
        return
    await state.clear()
    await message.answer(
        f"✅ نام نمایشی شما «{html.escape(name[:80])}» ثبت شد.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "access_code:enter")
async def ask_for_access_code(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(EnterAccessCode.code)
    await callback.message.answer("کد ورود را وارد کنید:")
    await callback.answer()


@router.message(EnterAccessCode.code)
async def redeem_access_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    display_name = message.from_user.full_name or message.from_user.username or str(message.from_user.id)
    if not code or not await db.redeem_access_code(code, message.from_user.id, display_name):
        await message.answer("کد ورود معتبر نیست یا قبلاً استفاده شده است. دوباره تلاش کنید.")
        return
    await state.clear()
    await message.answer("✅ کد ورود تأیید شد و دسترسی شما فعال شد.\nبرای ورود به ربات /start را بزنید.")
    for owner_id in settings.allowed_user_ids:
        await message.bot.send_message(owner_id, f"🔑 کد ورود <code>{html.escape(code)}</code> توسط کاربر {html.escape(display_name)} (<code>{message.from_user.id}</code>) استفاده شد.")


@router.message(Command("cancel"))
@router.message(F.text == "لغو")
async def cancel(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    await state.clear()
    await message.answer("عملیات لغو شد.", reply_markup=main_menu())


@router.message(F.text == "❓ راهنما")
async def help_message(message: Message) -> None:
    if await reject_if_needed(message):
        return
    await message.answer(
        "<b>ثبت جلسه</b>\nپیام جلسه را فوروارد کنید. متن باید تاریخ و ساعت داشته باشد؛ مثل «جلسه فروش، فردا ساعت ۱۰:۳۰» یا «۱۴۰۵/۰۵/۲۴ ساعت ۱۰».\n\n"
        "<b>مدیریت تسک</b>\nاز «تسک جدید» استفاده کنید. در لیست تسک‌ها می‌توانید درصد پیشرفت، آخرین توضیح و وضعیت انجام را تغییر دهید.\n\n<b>فرمان مدیریتی</b>\nاز دکمه «🤖 فرمان مدیریتی» استفاده کنید و درخواست را با زبان طبیعی بنویسید؛ مثل «تسک‌های کمتر از ۵۰ درصد پیشرفت را بفرست».\n\n"
        "برای لغو هر فرم: /cancel",
    )


@router.message(F.text == "👥 مدیریت دسترسی")
async def access_management(message: Message) -> None:
    if await reject_if_needed(message):
        return
    if await db.user_role(message.from_user.id) != "owner":
        await message.answer("این بخش فقط برای مالک ربات قابل دسترسی است.")
        return
    managers, codes = await db.managers(), await db.access_codes()
    builder = InlineKeyboardBuilder()
    builder.button(text="🔑 ساخت کد ورود", callback_data="access_code:create")
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    for user_id, name in managers:
        builder.button(text=f"🗑 لغو دسترسی {name or user_id}", callback_data=f"access:remove:{user_id}")
    lines = ["<b>👥 مدیریت دسترسی</b>", "", "ورود به ربات رایگان و فقط با کد ورود مالک امکان‌پذیر است."]
    lines += (["", "حساب‌های مستقل دارای دسترسی:"] + [f"• {html.escape(name) if name else 'بدون نام'} — <code>{user_id}</code>" for user_id, name in managers]) if managers else ["", "هنوز حساب مستقل دیگری دسترسی نگرفته است."]
    if codes:
        lines += ["", "<b>کدهای ورود:</b>"] + [f"• <code>{html.escape(code)}</code> — {'فعال' if active else 'استفاده‌شده/غیرفعال'}" for code, active in codes]
        for code, active in codes:
            if active:
                builder.button(text=f"غیرفعال‌کردن {code}", callback_data=f"access_code:disable:{code}")
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data == "access_code:create")
async def ask_access_code(callback: CallbackQuery, state: FSMContext) -> None:
    if await db.user_role(callback.from_user.id) != "owner":
        await callback.answer("فقط مالک اجازه این کار را دارد.", show_alert=True)
        return
    await state.set_state(CreateAccessCode.code)
    await callback.message.answer("کد ورود جدید را وارد کنید؛ مثال: <code>WELCOME-2026</code>")
    await callback.answer()


@router.message(CreateAccessCode.code)
async def save_access_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code or len(code) > 30 or not code.replace("-", "").isalnum():
        await message.answer("کد فقط باید شامل حروف، عدد یا خط تیره و حداکثر ۳۰ کاراکتر باشد.")
        return
    await db.create_access_code(code)
    await state.clear()
    await message.answer(f"✅ کد ورود یک‌بارمصرف <code>{html.escape(code)}</code> ساخته شد.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("access_code:disable:"))
async def disable_access_code(callback: CallbackQuery) -> None:
    if await db.user_role(callback.from_user.id) != "owner":
        await callback.answer("فقط مالک اجازه این کار را دارد.", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    await db.deactivate_access_code(code)
    await callback.message.edit_text(f"✅ کد ورود <code>{html.escape(code)}</code> غیرفعال شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("access:remove:"))
async def remove_manager(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    if await db.user_role(callback.from_user.id) != "owner":
        await callback.answer("فقط مالک ربات اجازه این کار را دارد.", show_alert=True)
        return
    manager_id = int(callback.data.rsplit(":", 1)[1])
    removed = await db.remove_manager(manager_id)
    if removed:
        await callback.message.edit_text(f"✅ دسترسی مدیر <code>{manager_id}</code> لغو شد.")
        await callback.answer()
    else:
        await callback.answer("این مدیر پیدا نشد.", show_alert=True)


@router.message(F.text == "➕ تسک جدید")
async def new_task(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    await state.set_state(NewTask.title)
    await message.answer("عنوان تسک چیست؟", reply_markup=back_to_menu())


@router.message(F.text == "➕ یادآور جدید")
async def new_reminder(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    await state.clear()
    await state.set_state(NewTask.title)
    await state.update_data(reminder_only=True)
    await message.answer("عنوان یادآور چیست؟", reply_markup=back_to_menu())


@router.callback_query(F.data == "reminder:new")
async def new_reminder_inline(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    await state.clear()
    await state.set_state(NewTask.title)
    await state.update_data(reminder_only=True)
    await callback.message.answer("یادآور جدید: عنوان را بفرستید.", reply_markup=back_to_menu())
    await callback.answer()


def parse_bulk_reminders(text: str) -> list[tuple[str, datetime, datetime | None]]:
    normalized = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    result: list[tuple[str, datetime]] = []
    now = datetime.now(settings.timezone)
    jalali_now = jdatetime.date.fromgregorian(date=now.date())
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip(" \t-،") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        schedules = [line for line in lines if "هر ماه" in line and "باید" not in line]
        if not schedules:
            continue
        schedule = schedules[0]
        day_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:ام)?\s*هر ماه", schedule)
        if not day_match:
            continue
        day = int(day_match.group(1))
        month = jalali_now.month
        year = jalali_now.year
        if day <= jalali_now.day:
            month += 1
            if month == 13:
                month = 1
                year += 1
        try:
            gregorian = jdatetime.date(year, month, min(day, 31)).togregorian()
        except ValueError:
            continue
        amount = next((line for line in lines if "ریال" in line), "")
        title = f"{schedule} — {amount}" if amount else schedule
        ending = next((line for line in lines if "پایان" in line), "")
        repeat_until = parse_deadline(ending, settings.timezone) if ending else None
        result.append((title[:200], datetime.combine(gregorian, datetime.max.time(), tzinfo=settings.timezone), repeat_until))
    return result


@router.callback_query(F.data == "menu:main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("به منوی اصلی برگشتید.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "nav:team")
async def back_to_team(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_message = callback.message.model_copy(update={"from_user": callback.from_user})
    await team_chart(user_message)
    await callback.answer()


@router.callback_query(F.data == "nav:shared")
async def back_to_shared_calendars(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_message = callback.message.model_copy(update={"from_user": callback.from_user})
    await shared_event_calendars(user_message)
    await callback.answer()

@router.callback_query(F.data == "nav:settings")
async def back_to_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await reject_if_needed(callback):
        return
    await show_meeting_settings(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == "nav:reminders")
async def back_to_reminders(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    reminders = await db.reminders(callback.from_user.id)
    if not reminders:
        await callback.message.answer("یادآور فعالی برای نمایش ندارید.", reply_markup=reminders_menu())
    else:
        for index, reminder in enumerate(reminders, 1):
            await callback.message.answer(
                reminder_text(reminder, index),
                reply_markup=reminder_actions(reminder.id),
            )
        await callback.message.answer("برای ثبت یادآور جدید:", reply_markup=reminders_menu())
    await callback.answer()


@router.callback_query(F.data == "nav:reminder_repeat")
async def back_to_reminder_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if current == EditReminder.repeat.state:
        markup = reminder_repeat_options("reminder:edit_repeat", "nav:reminders")
    else:
        await state.set_state(NewTask.reminder_repeat)
        markup = reminder_repeat_options("reminder:repeat", "nav:reminders")
    await callback.message.answer("نوع تکرار یادآور را انتخاب کنید:", reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "nav:task_priority")
async def back_to_task_priority(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewTask.priority)
    await callback.message.answer(
        "اولویت تسک را انتخاب کنید:",
        reply_markup=priorities(),
    )
    await callback.answer()


@router.callback_query(F.data == "nav:task_note")
async def back_to_task_note(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewTask.note)
    await callback.message.answer(
        "توضیح اولیه را دوباره بنویسید. اگر توضیحی ندارید، «-» بفرستید.",
        reply_markup=back_button("nav:task_priority"),
    )
    await callback.answer()

@router.callback_query(F.data == "nav:task_deadline")
async def back_to_task_deadline(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewTask.deadline)
    await callback.message.answer(
        "ددلاین تسک را دوباره بفرستید؛ مثلاً «فردا ساعت ۱۸» یا «۱۴۰۵/۰۶/۱۰ ساعت ۹».",
        reply_markup=back_button("menu:main"),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reminder:done:"))
async def complete_reminder(callback: CallbackQuery) -> None:
    reminder_id = int(callback.data.rsplit(":", 1)[1])
    reminder = await db.complete_reminder(reminder_id, callback.from_user.id, datetime.now(settings.timezone))
    if not reminder:
        await callback.answer("یادآور پیدا نشد.", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ انجام شد." + (" نوبت بعدی ساخته شد." if reminder.repeat_type != "once" else ""),
        reply_markup=back_to_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reminder:delete:"))
async def delete_reminder(callback: CallbackQuery) -> None:
    reminder_id = int(callback.data.rsplit(":", 1)[1])
    if await db.delete_reminder(reminder_id, callback.from_user.id):
        await callback.message.edit_text("🗑 یادآور حذف شد.", reply_markup=back_to_menu())
        await callback.answer()
    else:
        await callback.answer("یادآور پیدا نشد.", show_alert=True)


@router.callback_query(F.data.startswith("reminder:edit:"))
async def edit_reminder_start(callback: CallbackQuery, state: FSMContext) -> None:
    reminder_id = int(callback.data.rsplit(":", 1)[1])
    if not await db.reminder(reminder_id, callback.from_user.id):
        await callback.answer("یادآور پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.set_state(EditReminder.value)
    await state.update_data(reminder_id=reminder_id)
    await callback.message.answer(
        "برای ویرایش، عنوان و تاریخ را با | جدا بفرستید.\nمثال: پرداخت بانک | ۲۰ مهر ۱۴۰۵",
        reply_markup=back_to_menu(),
    )
    await callback.answer()


@router.message(EditReminder.value)
async def edit_reminder_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if ai_service.available:
        parsed_ai = await ai_service.parse_reminder(raw, settings.timezone, datetime.now(settings.timezone))
        if parsed_ai:
            await state.update_data(edit_title=parsed_ai.title, edit_due_at=parsed_ai.due_at.isoformat())
            await state.set_state(EditReminder.repeat)
            await message.answer("تکرار یادآور را انتخاب کنید:", reply_markup=reminder_repeat_options("reminder:edit_repeat"))
            return
    parts = [part.strip() for part in raw.split("|", 1)]
    if len(parts) == 2 and parts[0]:
        title, date_text = parts
    else:
        # Also accept a copied reminder card or natural text, e.g.
        # «بانک رفاه — ۲۹ هر ماه — مبلغ ...».
        normalized = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        date_match = re.search(
            r"(?P<date>\d{1,2}\s*(?:هر\s*ماه|روز\s*(?:دیگه|دیگر|آینده|بعد)|"
            r"(?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند)"
            r"(?:\s*1[34]\d{2})?|فردا|پس\s*فردا|امروز|آخر\s+هفته))",
            normalized,
            re.IGNORECASE,
        )
        if not date_match:
            await message.answer("قالب درست: عنوان | تاریخ\nمثال: بانک رفاه | ۲۹ هر ماه", reply_markup=back_to_menu())
            return
        date_text = date_match.group("date")
        title = normalized[:date_match.start()].strip(" -—،؛:\n")
        title = re.sub(r"^(?:یادآور|ویرایش)\s*", "", title).strip()
        if not title:
            title = "یادآور"
    due_at = parse_deadline(date_text, settings.timezone)
    if not due_at:
        await message.answer("تاریخ را متوجه نشدم.", reply_markup=back_to_menu())
        return
    await state.update_data(edit_title=title, edit_due_at=due_at.isoformat())
    await state.set_state(EditReminder.repeat)
    await message.answer("تکرار یادآور را انتخاب کنید:", reply_markup=reminder_repeat_options("reminder:edit_repeat"))


@router.callback_query(EditReminder.repeat, F.data.startswith("reminder:edit_repeat:"))
async def edit_reminder_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    repeat_type = callback.data.rsplit(":", 1)[1]
    if repeat_type == "weekly":
        await callback.message.answer("کدام روز هفته تکرار شود؟", reply_markup=reminder_weekdays("reminder:edit_weekday"))
        await callback.answer()
        return
    data = await state.get_data()
    await db.update_reminder(
        data["reminder_id"], callback.from_user.id, data["edit_title"],
        datetime.fromisoformat(data["edit_due_at"]), repeat_type
    )
    await state.clear()
    await callback.message.answer("✅ یادآور ویرایش شد.", reply_markup=back_to_menu())
    await callback.answer()


@router.callback_query(EditReminder.repeat, F.data.startswith("reminder:edit_weekday:"))
async def edit_reminder_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    repeat_type = f"weekly:{callback.data.rsplit(':', 1)[1]}"
    await db.update_reminder(
        data["reminder_id"], callback.from_user.id, data["edit_title"],
        datetime.fromisoformat(data["edit_due_at"]), repeat_type
    )
    await state.clear()
    await callback.message.answer("✅ یادآور ویرایش شد.", reply_markup=back_to_menu())
    await callback.answer()


@router.message(NewTask.title)
async def task_title(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("لطفاً عنوان را به‌صورت متن بفرستید.")
        return
    data = await state.get_data()
    if data.get("reminder_only") and "\n" in message.text:
        created = []
        for title, deadline, repeat_until in parse_bulk_reminders(message.text):
            reminder_id = await db.add_reminder(
                message.from_user.id, title, deadline, "monthly", repeat_until
            )
            created.append(await db.reminder(reminder_id, message.from_user.id))
        if created:
            await state.clear()
            await message.answer(
                f"✅ {len(created)} یادآور ثبت شد.\n\n" +
                "\n\n".join(reminder_text(reminder) for reminder in created),
                reply_markup=main_menu(),
            )
            return
    await state.update_data(title=message.text.strip())
    await state.set_state(NewTask.deadline)
    await message.answer(
        "ددلاین را بفرستید؛ نیازی به ساعت نیست. مثال‌ها:\n"
        "• ۱۰ روز آینده\n• آخر هفته آینده\n• آخر همین ماه\n"
        "• تا آخر پاییز\n• تا آخر سال\n• ۲۰ مهر ۱۴۰۵",
        reply_markup=back_to_menu(),
    )


@router.message(NewTask.deadline)
async def task_deadline(message: Message, state: FSMContext) -> None:
    raw_deadline = message.text or ""
    deadline = None
    if ai_service.available:
        deadline = await ai_service.parse_natural_deadline(
            raw_deadline, settings.timezone, datetime.now(settings.timezone)
        )
    if deadline is None:
        deadline = parse_deadline(raw_deadline, settings.timezone)
    if not deadline:
        await message.answer(
            "ددلاین را متوجه نشدم. می‌توانید تاریخ دقیق یا یک عبارت طبیعی بنویسید؛ "
            "مثلاً «۸ روز دیگه»، «پنج‌شنبه هفته بعد»، «آخر آبان» یا «۲۰ مهر ۱۴۰۵».",
            reply_markup=back_to_menu(),
        )
        return
    await state.update_data(deadline=deadline.isoformat())
    data = await state.get_data()
    if data.get("reminder_only"):
        await state.update_data(deadline=deadline.isoformat())
        await state.set_state(NewTask.reminder_repeat)
        await message.answer("این یادآور یک‌بار مصرف است یا تکرار شود؟", reply_markup=reminder_repeat_options())
        return
    await state.set_state(NewTask.priority)
    await message.answer("اولویت تسک را در ماتریس آیزنهاور انتخاب کنید:", reply_markup=priorities())


@router.callback_query(NewTask.reminder_repeat, F.data.startswith("reminder:repeat:"))
async def new_reminder_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    repeat_type = callback.data.rsplit(":", 1)[1]
    if repeat_type == "weekly":
        await state.set_state(NewTask.reminder_repeat)
        await callback.message.answer("کدام روز هفته تکرار شود؟", reply_markup=reminder_weekdays())
        await callback.answer()
        return
    data = await state.get_data()
    reminder_id = await db.add_reminder(
        callback.from_user.id, data["title"], datetime.fromisoformat(data["deadline"]), repeat_type
    )
    await state.clear()
    reminder = await db.reminder(reminder_id, callback.from_user.id)
    await callback.message.answer("✅ یادآور ثبت شد.\n\n" + reminder_text(reminder), reply_markup=main_menu())
    await callback.answer()


@router.callback_query(NewTask.reminder_repeat, F.data.startswith("reminder:weekday:"))
async def new_reminder_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    repeat_type = f"weekly:{callback.data.rsplit(':', 1)[1]}"
    reminder_id = await db.add_reminder(
        callback.from_user.id, data["title"], datetime.fromisoformat(data["deadline"]), repeat_type
    )
    await state.clear()
    reminder = await db.reminder(reminder_id, callback.from_user.id)
    await callback.message.answer("✅ یادآور ثبت شد.\n\n" + reminder_text(reminder), reply_markup=main_menu())
    await callback.answer()


@router.callback_query(NewTask.priority, F.data.startswith("priority:"))
async def task_priority(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    priority = int(callback.data.split(":")[1])
    await state.update_data(priority=priority)
    await state.set_state(NewTask.assignee)
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    owner_id = int(data.get("shared_task_owner_id") or callback.from_user.id)
    members = await db.team_members(owner_id)
    if members:
        builder = InlineKeyboardBuilder()
        for member_id, name in members:
            builder.button(text=f"👤 {name or member_id}", callback_data=f"task:assignee:{member_id}")
        builder.button(text="✍️ ورود نام به‌صورت دستی", callback_data="task:assignee:manual")
        builder.button(text="⬅️ بازگشت", callback_data="nav:task_priority")
        builder.adjust(1)
        await callback.message.answer("مسئول پیگیری را از چارت نیروها انتخاب کنید:", reply_markup=builder.as_markup())
    else:
        await callback.message.answer(
            "هنوز نیرویی در چارت ثبت نشده است. نام مسئول را دستی بنویسید؛ "
            "بعداً می‌توانید از بخش «چارت نیروها» افراد را با آیدی تلگرام اضافه کنید."
        )
    await callback.answer()


@router.callback_query(NewTask.assignee, F.data.startswith("task:assignee:"))
async def choose_task_assignee(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.rsplit(":", 1)[1]
    if value == "manual":
        await callback.message.answer("نام شخص یا واحد مسئول را بنویسید:", reply_markup=back_button("nav:task_priority"))
        await callback.answer()
        return
    member_id = int(value)
    data = await state.get_data()
    owner_id = int(data.get("shared_task_owner_id") or callback.from_user.id)
    members = dict(await db.team_members(owner_id))
    if member_id not in members:
        await callback.answer("این نیرو دیگر در چارت شما فعال نیست.", show_alert=True)
        return
    await state.update_data(assignee=members[member_id] or str(member_id), assignee_user_id=member_id)
    await state.set_state(NewTask.note)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("توضیح اولیه را بنویسید. اگر توضیحی ندارید، «-» بفرستید.")
    await callback.answer()


@router.message(NewTask.assignee)
async def task_assignee(message: Message, state: FSMContext) -> None:
    await state.update_data(assignee=(message.text or "").strip(), assignee_user_id=None)
    await state.set_state(NewTask.note)
    await message.answer("توضیح اولیه را بنویسید. اگر توضیحی ندارید، «-» بفرستید.")


@router.message(NewTask.note)
async def task_note(message: Message, state: FSMContext) -> None:
    note = (message.text or "").strip()
    if note == "-":
        note = ""
    await state.update_data(note=note, cc_enabled=False)
    await state.set_state(NewTask.cc)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔘 رونوشت خاموش (پیش‌فرض)", callback_data="task:cc:off")
    builder.button(text="🟢 روشن‌کردن رونوشت", callback_data="task:cc:on")
    builder.button(text="⬅️ بازگشت", callback_data="nav:task_note")
    builder.adjust(1)
    await message.answer(
        "آیا گزارش‌های این تسک برای مدیر دیگری هم رونوشت شود؟\n"
        "حالت پیش‌فرض خاموش است.", reply_markup=builder.as_markup()
    )


async def finalize_task(
    message: Message, state: FSMContext, owner_id: int, actor_id: int,
    cc_manager_id: int | None = None,
) -> None:
    data = await state.get_data()
    task_id = await db.add_task(
        owner_id, data["title"], datetime.fromisoformat(data["deadline"]),
        data["priority"], data["assignee"], data.get("note", ""), data.get("assignee_user_id"), cc_manager_id,
        actor_id,
    )
    await state.clear()
    task = await db.task(task_id, owner_id)
    await message.answer(
        "✅ تسک ثبت شد.\n\n" + task_text(task, viewer_id=actor_id),
        reply_markup=main_menu(),
    )
    if task.assignee_user_id:
        try:
            await message.bot.send_message(
                task.assignee_user_id,
                "📌 <b>یک تسک جدید به شما واگذار شد</b>\n\n"
                + task_text(task, viewer_id=task.assignee_user_id)
                + "\n\nبرای مشاهده و ارسال گزارش، ربات را /start کنید.",
                reply_markup=await member_reply_menu(task.assignee_user_id),
            )
        except Exception:
            await message.answer(
                "ℹ️ تسک واگذار شد؛ این شخص هنوز ربات را استارت نکرده است. "
                "از او بخواهید وارد ربات شود و /start را بزند."
            )


@router.callback_query(NewTask.cc, F.data == "task:cc:off")
async def task_cc_off(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    await finalize_task(
        callback.message, state,
        int(data.get("shared_task_owner_id") or callback.from_user.id),
        callback.from_user.id,
    )
    await callback.answer()


@router.callback_query(NewTask.cc, F.data == "task:cc:on")
async def task_cc_on(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "آیدی مدیر دریافت‌کننده رونوشت را به شکل <code>@username</code> بفرستید.\n"
        "آن مدیر باید قبلاً یک‌بار همین ربات را Start کرده باشد."
    )
    await callback.answer()


@router.message(NewTask.cc)
async def task_cc_username(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not username.startswith("@"):
        await message.answer("آیدی را به شکل <code>@username</code> بفرستید.")
        return
    account = await db.telegram_account_by_username(username)
    if not account:
        me = await message.bot.get_me()
        await message.answer(
            f"این آیدی هنوز به ربات متصل نشده است. از او بخواهید ابتدا @{me.username} را باز کند و Start بزند؛ "
            "سپس همین آیدی را دوباره ارسال کنید."
        )
        return
    cc_manager_id, _ = account
    if cc_manager_id == message.from_user.id:
        await message.answer("برای رونوشت باید آیدی مدیر دیگری را وارد کنید.")
        return
    if not await allowed(cc_manager_id):
        await message.answer("این شخص در حال حاضر دسترسی فعال مدیریتی به ربات ندارد.")
        return
    data = await state.get_data()
    await finalize_task(
        message, state, int(data.get("shared_task_owner_id") or message.from_user.id),
        message.from_user.id, cc_manager_id,
    )


@router.message(F.text == "📋 لیست تسک‌ها")
async def list_tasks(message: Message) -> None:
    if await reject_if_needed(message):
        return
    tasks = await db.tasks(message.from_user.id)
    if not tasks:
        await message.answer("تسک بازی ندارید.")
        return
    await message.answer("تسک‌ها بر اساس اولویت و سپس نزدیک‌ترین ددلاین مرتب شده‌اند:")
    for index, task in enumerate(tasks, 1):
        await message.answer(
            task_text(task, index, message.from_user.id),
            reply_markup=task_actions(task.id),
        )


@router.message(F.text == "🧑‍🤝‍🧑 چارت نیروها")
async def team_chart(message: Message) -> None:
    if await reject_if_needed(message):
        return
    members = await db.team_members(message.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ افزودن نیرو", callback_data="team:add")
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    for member_id, name in members:
        builder.button(text=f"📊 پروفایل {name or member_id}", callback_data=f"team:profile:{member_id}")
        builder.button(text=f"📅 دسترسی ایونت {name or member_id}", callback_data=f"eventshare:menu:{member_id}")
        builder.button(text=f"✅ دسترسی تسک {name or member_id}", callback_data=f"taskshare:menu:{member_id}")
        builder.button(text=f"🗑 حذف {name or member_id}", callback_data=f"team:remove:{member_id}")
    builder.adjust(1)
    lines = ["<b>🧑‍🤝‍🧑 چارت نیروهای من</b>", ""]
    if members:
        lines += [f"• {html.escape(name) if name else 'بدون نام'} — <code>{member_id}</code>" for member_id, name in members]
    else:
        lines.append("هنوز نیرویی ثبت نشده است.")
    lines += ["", "نیرو با آیدی تلگرام اضافه می‌شود و پس از استارت ربات می‌تواند تسک‌های خودش و ایونت‌هایی را که با او به اشتراک می‌گذارید ببیند."]
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("eventshare:menu:"))
async def event_share_menu(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    member_id = int(callback.data.rsplit(":", 1)[1])
    member = await db.team_member(callback.from_user.id, member_id)
    if not member:
        await callback.answer("این نیرو در چارت شما نیست.", show_alert=True)
        return
    current = await db.event_share_permission(callback.from_user.id, member_id)
    labels = {None: "بدون دسترسی", "view": "فقط مشاهده", "edit": "مشاهده و ویرایش"}
    builder = InlineKeyboardBuilder()
    builder.button(text="🚫 بدون دسترسی", callback_data=f"eventshare:set:{member_id}:none")
    builder.button(text="👁 فقط مشاهده", callback_data=f"eventshare:set:{member_id}:view")
    builder.button(text="✏️ مشاهده و ویرایش", callback_data=f"eventshare:set:{member_id}:edit")
    builder.button(text="⬅️ بازگشت", callback_data="nav:team")
    builder.adjust(1)
    await callback.message.answer(
        f"<b>دسترسی ایونت‌های {html.escape(member[1])}</b>\n"
        f"سطح فعلی: <b>{labels[current]}</b>\n\n"
        "این دسترسی فقط تقویم ایونت‌ها را شامل می‌شود و به تسک‌ها یا تنظیمات دسترسی نمی‌دهد.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("eventshare:set:"))
async def set_event_share(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    _, _, member_raw, permission_raw = callback.data.split(":")
    if permission_raw not in {"none", "view", "edit"}:
        await callback.answer("سطح دسترسی نامعتبر است.", show_alert=True)
        return
    permission = None if permission_raw == "none" else permission_raw
    if not await db.set_event_share(callback.from_user.id, int(member_raw), permission):
        await callback.answer("این نیرو در چارت شما نیست.", show_alert=True)
        return
    labels = {None: "بدون دسترسی", "view": "فقط مشاهده", "edit": "مشاهده و ویرایش"}
    await callback.message.edit_text(f"✅ سطح دسترسی ایونت روی «{labels[permission]}» تنظیم شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("taskshare:menu:"))
async def task_share_menu(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    member_id = int(callback.data.rsplit(":", 1)[1])
    member = await db.team_member(callback.from_user.id, member_id)
    if not member:
        await callback.answer("این نیرو در چارت شما نیست.", show_alert=True)
        return
    perms = await db.task_share_permissions(callback.from_user.id, member_id) or {
        "can_view": False, "can_edit": False, "can_add": False, "can_delete": False
    }
    builder = InlineKeyboardBuilder()
    for key, label in (
        ("can_view", "👁 مشاهده تسک‌ها"),
        ("can_edit", "✏️ ویرایش تسک‌ها"),
        ("can_add", "➕ افزودن تسک"),
        ("can_delete", "🗑 حذف تسک"),
    ):
        mark = "✅" if perms[key] else "⬜"
        builder.button(text=f"{mark} {label}", callback_data=f"taskshare:toggle:{member_id}:{key}")
    builder.button(text="🚫 لغو همه دسترسی‌ها", callback_data=f"taskshare:clear:{member_id}")
    builder.button(text="⬅️ بازگشت", callback_data="nav:team")
    builder.adjust(1)
    summary = "، ".join(
        label for key, label in (
            ("can_view", "مشاهده"), ("can_edit", "ویرایش"),
            ("can_add", "افزودن"), ("can_delete", "حذف")
        ) if perms[key]
    ) or "بدون دسترسی"
    await callback.message.answer(
        f"<b>دسترسی تسک‌های مدیر برای {html.escape(member[1])}</b>\n"
        f"دسترسی‌های فعال: <b>{summary}</b>\n\nبرای تغییر هر مورد روی دکمه بزنید.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("taskshare:toggle:"))
async def toggle_task_share(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    _, _, member_raw, key = callback.data.split(":")
    if key not in {"can_view", "can_edit", "can_add", "can_delete"}:
        await callback.answer("مجوز نامعتبر است.", show_alert=True)
        return
    member_id = int(member_raw)
    current = await db.task_share_permissions(callback.from_user.id, member_id) or {
        "can_view": False, "can_edit": False, "can_add": False, "can_delete": False
    }
    await db.set_task_share(callback.from_user.id, member_id, **{key: not current[key]})
    await task_share_menu(callback)


@router.callback_query(F.data.startswith("taskshare:clear:"))
async def clear_task_share(callback: CallbackQuery) -> None:
    member_id = int(callback.data.rsplit(":", 1)[1])
    await db.set_task_share(
        callback.from_user.id, member_id,
        can_view=False, can_edit=False, can_add=False, can_delete=False,
    )
    await task_share_menu(callback)
@router.callback_query(F.data.startswith("team:profile:"))
async def team_member_profile(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    member_id = int(callback.data.rsplit(":", 1)[1])
    members = dict(await db.team_members(callback.from_user.id))
    if member_id not in members:
        await callback.answer("این نیرو در چارت شما نیست.", show_alert=True)
        return
    stats = await db.member_performance(callback.from_user.id, member_id, datetime.now(settings.timezone))
    timing = stats["average_timing_percent"]
    if timing is None:
        timing_text = "هنوز تسک تکمیل‌شده کافی برای ارزیابی زمان تحویل ندارد"
    elif timing > 0:
        timing_text = f"معمولاً حدود {fa_number(abs(timing))}٪ زودتر از موعد تحویل می‌دهد"
    elif timing < 0:
        timing_text = f"معمولاً حدود {fa_number(abs(timing))}٪ با تأخیر تحویل می‌دهد"
    else:
        timing_text = "معمولاً در موعد مقرر تحویل می‌دهد"
    average_days = stats["average_delta_days"]
    if average_days is None:
        average_days_text = "—"
    elif average_days > 0:
        average_days_text = f"{fa_number(average_days)} روز زودتر"
    elif average_days < 0:
        average_days_text = f"{fa_number(abs(average_days))} روز دیرتر"
    else:
        average_days_text = "در موعد مقرر"
    await callback.message.answer(
        f"<b>👤 پروفایل عملکرد {html.escape(members[member_id])}</b>\n"
        f"آیدی داخلی متصل: <code>{member_id}</code>\n\n"
        f"📦 مجموع تسک‌های واگذارشده: {fa_number(stats['total'])}\n"
        f"🔄 در جریان: {fa_number(stats['open'])}\n"
        f"✅ تکمیل‌شده: {fa_number(stats['done'])}\n"
        f"⚠️ عقب‌افتاده و باز: {fa_number(stats['overdue'])}\n"
        f"📈 میانگین پیشرفت تسک‌های باز: {fa_number(stats['avg_progress'])}٪\n\n"
        f"⏱ <b>الگوی تحویل:</b> {timing_text}\n"
        f"📅 میانگین اختلاف با ددلاین: {average_days_text}\n"
        f"🟢 زودتر از موعد: {fa_number(stats['early'])} | "
        f"🔵 به‌موقع: {fa_number(stats['on_time'])} | "
        f"🔴 با تأخیر: {fa_number(stats['late'])}"
    )
    await callback.answer()


@router.callback_query(F.data == "team:add")
async def ask_team_member(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    await state.set_state(TeamManagement.member)
    await callback.message.answer(
        "آیدی تلگرام نیرو را به شکل <code>@username</code> بفرستید.\n\n"
        "شخص باید قبلاً یک‌بار همین ربات را Start کرده باشد تا ربات بتواند شناسه داخلی او را به‌صورت خودکار ثبت کند."
    )
    await callback.answer()


@router.message(TeamManagement.member)
async def save_team_member(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not username.startswith("@"):
        await message.answer("آیدی را به شکل <code>@username</code> بفرستید.")
        return
    account = await db.telegram_account_by_username(username)
    if not account:
        me = await message.bot.get_me()
        await message.answer(
            f"این آیدی هنوز به ربات متصل نشده است. لینک <code>https://t.me/{me.username}</code> را برای او بفرستید؛ "
            "بعد از زدن Start، همین آیدی را دوباره اینجا ارسال کنید."
        )
        return
    member_id, name = account
    if member_id == message.from_user.id:
        await message.answer("نیازی نیست خودتان را به‌عنوان نیروی زیرمجموعه ثبت کنید.")
        return
    name = name or username
    await db.add_team_member(message.from_user.id, member_id, name)
    await state.clear()
    await message.answer(
        f"✅ {html.escape(name)} با آیدی <code>{html.escape(username)}</code> به چارت اضافه شد.\n"
        "اکنون هنگام تعریف تسک می‌توانید او را انتخاب کنید.", reply_markup=main_menu()
    )


@router.callback_query(F.data.startswith("team:remove:"))
async def remove_team_member(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    member_id = int(callback.data.rsplit(":", 1)[1])
    removed = await db.remove_team_member(callback.from_user.id, member_id)
    await callback.answer("نیرو از چارت حذف شد." if removed else "این نیرو پیدا نشد.", show_alert=not removed)
    if removed:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.message(F.text.startswith("تسک منیجر "))
async def member_task_manager(message: Message) -> None:
    requested_name = (message.text or "")[len("تسک منیجر "):].strip()
    events = {
        manager_id: (name or str(manager_id), permission)
        for manager_id, name, permission in await db.shared_event_managers(message.from_user.id)
    }
    tasks = {
        manager_id: (name or str(manager_id), can_view, can_edit, can_add, can_delete)
        for manager_id, name, can_view, can_edit, can_add, can_delete
        in await db.shared_task_managers(message.from_user.id)
    }
    selected = next(
        (manager_id for manager_id, (name, _) in events.items() if name == requested_name),
        None,
    )
    if selected is None:
        selected = next(
            (manager_id for manager_id, (name, *_) in tasks.items() if name == requested_name),
            None,
        )
    if selected is None:
        await message.answer("این تسک منیجر دیگر با شما به اشتراک گذاشته نشده است.",
                             reply_markup=await member_reply_menu(message.from_user.id))
        return
    event_permission = events.get(selected, ("", None))[1]
    task_permissions = tasks.get(selected, ("", False, False, False, False))[1:]
    builder = InlineKeyboardBuilder()
    if event_permission:
        builder.button(text="📅 ایونت‌های مدیر", callback_data=f"shared:calendar:{selected}")
    if task_permissions[0]:
        builder.button(text="✅ تسک‌های مدیر", callback_data=f"shared:tasks:{selected}")
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    builder.adjust(1)
    await message.answer(
        f"<b>تسک منیجر {html.escape(requested_name)}</b>\n"
        "بخش موردنظر را انتخاب کنید:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("shared:tasks:"))
async def show_shared_tasks(callback: CallbackQuery) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_view"]:
        await callback.answer("دسترسی مشاهدهٔ تسک‌های این مدیر را ندارید.", show_alert=True)
        return
    tasks = await db.tasks(manager_id, include_done=True, limit=100, include_unassigned=True)
    builder = InlineKeyboardBuilder()
    if perms["can_add"]:
        builder.button(text="➕ افزودن تسک برای مدیر", callback_data=f"sharedtask:add:{manager_id}")
    builder.button(text="⬅️ بازگشت", callback_data=f"shared:resources:{manager_id}")
    builder.adjust(1)
    await callback.message.answer(
        f"<b>✅ تسک‌های مدیر</b>\nسطح دسترسی: "
        f"{'ویرایش' if perms['can_edit'] else 'فقط مشاهده'}",
        reply_markup=builder.as_markup(),
    )
    for index, task in enumerate(tasks, 1):
        buttons = []
        if perms["can_edit"]:
            buttons.extend([
                ("📈 ثبت پیشرفت", f"sharedtask:progress:{manager_id}:{task.id}"),
                ("📝 توضیح", f"sharedtask:note:{manager_id}:{task.id}"),
                ("✅ انجام شد", f"sharedtask:done:{manager_id}:{task.id}"),
            ])
        if perms["can_delete"]:
            buttons.append(("🗑 حذف", f"sharedtask:delete:{manager_id}:{task.id}"))
        markup = None
        if buttons:
            kb = InlineKeyboardBuilder()
            for label, data in buttons:
                kb.button(text=label, callback_data=data)
            kb.adjust(2)
            markup = kb.as_markup()
        await callback.message.answer(
            task_text(task, index, callback.from_user.id), reply_markup=markup,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("sharedtask:add:"))
async def add_shared_task(callback: CallbackQuery, state: FSMContext) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_add"]:
        await callback.answer("اجازهٔ افزودن تسک برای این مدیر را ندارید.", show_alert=True)
        return
    await state.clear()
    await state.update_data(shared_task_owner_id=manager_id)
    await state.set_state(NewTask.title)
    await callback.message.answer("عنوان تسک جدید برای این مدیر را بفرستید:",
                                  reply_markup=back_to_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("shared:resources:"))
async def shared_resources_menu(callback: CallbackQuery) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    event_rows = await db.shared_event_managers(callback.from_user.id)
    task_rows = await db.shared_task_managers(callback.from_user.id)
    event_permission = next((p for mid, _, p in event_rows if mid == manager_id), None)
    task_row = next((row for row in task_rows if row[0] == manager_id), None)
    if not event_permission and not task_row:
        await callback.answer("این اشتراک دیگر فعال نیست.", show_alert=True)
        return
    name = next((n for mid, n, _ in event_rows if mid == manager_id), None)
    if not name and task_row:
        name = task_row[1]
    builder = InlineKeyboardBuilder()
    if event_permission:
        builder.button(text="📅 ایونت‌های مدیر", callback_data=f"shared:calendar:{manager_id}")
    if task_row and task_row[2]:
        builder.button(text="✅ تسک‌های مدیر", callback_data=f"shared:tasks:{manager_id}")
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    builder.adjust(1)
    await callback.message.edit_text(
        f"<b>تسک منیجر {html.escape(name or str(manager_id))}</b>\nبخش موردنظر را انتخاب کنید:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sharedtask:progress:"))
async def shared_task_progress(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, manager_raw, task_raw = callback.data.split(":")
    manager_id, task_id = int(manager_raw), int(task_raw)
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_edit"] or not await db.task(task_id, manager_id):
        await callback.answer("اجازهٔ ویرایش این تسک را ندارید.", show_alert=True)
        return
    await state.update_data(task_id=task_id, shared_task_owner_id=manager_id)
    await state.set_state(EditTask.progress)
    await callback.message.answer("درصد پیشرفت جدید را بین ۰ تا ۱۰۰ بفرستید:")
    await callback.answer()


@router.callback_query(F.data.startswith("sharedtask:note:"))
async def shared_task_note(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, manager_raw, task_raw = callback.data.split(":")
    manager_id, task_id = int(manager_raw), int(task_raw)
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_edit"] or not await db.task(task_id, manager_id):
        await callback.answer("اجازهٔ ویرایش این تسک را ندارید.", show_alert=True)
        return
    await state.update_data(task_id=task_id, shared_task_owner_id=manager_id)
    await state.set_state(EditTask.note)
    await callback.message.answer("توضیح جدید تسک را بفرستید:")
    await callback.answer()


@router.callback_query(F.data.startswith("sharedtask:delete:"))
async def delete_shared_task(callback: CallbackQuery) -> None:
    _, _, manager_raw, task_raw = callback.data.split(":")
    manager_id, task_id = int(manager_raw), int(task_raw)
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_delete"]:
        await callback.answer("اجازهٔ حذف تسک را ندارید.", show_alert=True)
        return
    if await db.delete_task(task_id, manager_id):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("تسک حذف شد.")
    else:
        await callback.answer("تسک پیدا نشد.", show_alert=True)


@router.callback_query(F.data.startswith("sharedtask:done:"))
async def complete_shared_task(callback: CallbackQuery) -> None:
    _, _, manager_raw, task_raw = callback.data.split(":")
    manager_id, task_id = int(manager_raw), int(task_raw)
    perms = await db.task_share_permissions(manager_id, callback.from_user.id)
    if not perms or not perms["can_edit"]:
        await callback.answer("اجازهٔ ویرایش این تسک را ندارید.", show_alert=True)
        return
    if await db.update_task(
        task_id, manager_id, "status", "done", changed_by=callback.from_user.id,
    ):
        await db.update_task(
            task_id, manager_id, "progress", 100, changed_by=callback.from_user.id,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("تسک انجام‌شده ثبت شد.")
    else:
        await callback.answer("تسک پیدا نشد.", show_alert=True)


@router.message(F.text.startswith("مدیریت ایونت‌های مدیر "))
async def named_shared_calendar(message: Message) -> None:
    requested_name = (message.text or "")[len("مدیریت ایونت‌های مدیر "):].strip()
    calendars = await db.shared_event_managers(message.from_user.id)
    selected = next(
        (
            (manager_id, permission)
            for manager_id, name, permission in calendars
            if (name or str(manager_id)) == requested_name
        ),
        None,
    )
    if not selected:
        await message.answer(
            "این تقویم دیگر با شما به اشتراک گذاشته نشده است.",
            reply_markup=await member_reply_menu(message.from_user.id),
        )
        return
    manager_id, permission = selected
    events = await db.upcoming_meetings(manager_id, datetime.now(settings.timezone), limit=100)
    if permission == "edit":
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ افزودن ایونت برای این مدیر", callback_data=f"shared:add:{manager_id}")
        await message.answer(
            f"مدیریت ایونت‌های مدیر {html.escape(requested_name)} — دسترسی مشاهده و ویرایش",
            reply_markup=builder.as_markup(),
        )
    else:
        await message.answer(f"مدیریت ایونت‌های مدیر {html.escape(requested_name)} — فقط مشاهده")
    if not events:
        await message.answer("ایونت آینده‌ای در این تقویم ثبت نشده است.")
        return
    for index, event in enumerate(events, 1):
        builder = InlineKeyboardBuilder()
        recurrence = await db.meeting_recurrence(event.id, manager_id)
        if permission == "edit":
            builder.button(text="🔄 جابه‌جایی", callback_data=f"shared:move:{manager_id}:{event.id}")
            builder.button(text="🗑 حذف", callback_data=f"shared:delete:{manager_id}:{event.id}")
            builder.adjust(2)
            if recurrence:
                builder.button(
                    text=("⏸ غیرفعال‌کردن تکرار" if recurrence["active"] else "▶️ فعال‌کردن تکرار"),
                    callback_data=f"shared:repeat:toggle:{manager_id}:{recurrence['series_id']}",
                )
                builder.button(
                    text="✏️ ویرایش تکرار",
                    callback_data=f"shared:repeat:edit:{manager_id}:{recurrence['series_id']}",
                )
                builder.button(
                    text="🗑 حذف تکرار",
                    callback_data=f"shared:repeat:delete:{manager_id}:{recurrence['series_id']}",
                )
        await message.answer(
            f"<b>{fa_number(index)}. {html.escape(event.title)}</b>\n"
            f"شروع: {fmt_dt(event.starts_at)}\nپایان: {fmt_dt(event.ends_at)}"
            + (f"\n🔁 تکرار: {html.escape(recurrence_description(recurrence))}" if recurrence else ""),
            reply_markup=builder.as_markup() if permission == "edit" else None,
        )

@router.message(F.text == "📅 ایونت‌های اشتراکی")
async def shared_event_calendars(message: Message) -> None:
    calendars = await db.shared_event_managers(message.from_user.id)
    if not calendars:
        await message.answer("هنوز هیچ مدیری ایونت‌هایش را با شما به اشتراک نگذاشته است.")
        return
    builder = InlineKeyboardBuilder()
    for manager_id, name, permission in calendars:
        level = "ویرایش" if permission == "edit" else "مشاهده"
        builder.button(
            text=f"📅 {name or manager_id} — {level}",
            callback_data=f"shared:calendar:{manager_id}",
        )
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    builder.adjust(1)
    await message.answer("تقویم مدیر موردنظر را انتخاب کنید:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("shared:calendar:"))
async def show_shared_calendar(callback: CallbackQuery) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    permission = await db.event_share_permission(manager_id, callback.from_user.id)
    if not permission:
        await callback.answer("دسترسی شما به این تقویم لغو شده است.", show_alert=True)
        return
    events = await db.upcoming_meetings(manager_id, datetime.now(settings.timezone), limit=100)
    if permission == "edit":
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ افزودن ایونت برای این مدیر", callback_data=f"shared:add:{manager_id}")
        builder.button(text="⬅️ بازگشت", callback_data="nav:shared")
        await callback.message.answer("سطح دسترسی شما: <b>مشاهده و ویرایش</b>", reply_markup=builder.as_markup())
    if not events:
        await callback.message.answer("ایونت آینده‌ای در این تقویم ثبت نشده است.")
    for index, event in enumerate(events, 1):
        builder = InlineKeyboardBuilder()
        recurrence = await db.meeting_recurrence(event.id, manager_id)
        if permission == "edit":
            builder.button(text="🔄 جابه‌جایی", callback_data=f"shared:move:{manager_id}:{event.id}")
            builder.button(text="🗑 حذف", callback_data=f"shared:delete:{manager_id}:{event.id}")
            builder.adjust(2)
            if recurrence:
                builder.button(
                    text=("⏸ غیرفعال‌کردن تکرار" if recurrence["active"] else "▶️ فعال‌کردن تکرار"),
                    callback_data=f"shared:repeat:toggle:{manager_id}:{recurrence['series_id']}",
                )
                builder.button(
                    text="✏️ ویرایش تکرار",
                    callback_data=f"shared:repeat:edit:{manager_id}:{recurrence['series_id']}",
                )
                builder.button(
                    text="🗑 حذف تکرار",
                    callback_data=f"shared:repeat:delete:{manager_id}:{recurrence['series_id']}",
                )
        await callback.message.answer(
            f"<b>{fa_number(index)}. {html.escape(event.title)}</b>\n"
            f"شروع: {fmt_dt(event.starts_at)}\nپایان: {fmt_dt(event.ends_at)}"
            + (f"\n🔁 تکرار: {html.escape(recurrence_description(recurrence))}" if recurrence else ""),
            reply_markup=builder.as_markup() if permission == "edit" else None,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("shared:add:"))
async def add_shared_event(callback: CallbackQuery, state: FSMContext) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    await state.clear()
    await state.update_data(meeting_owner_id=manager_id, meeting_source="")
    await state.set_state(NewMeeting.details)
    await callback.message.answer(
        "اطلاعات کامل ایونت را بفرستید؛ مثلاً «جلسه فروش، فردا ساعت ۱۰»."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shared:move:"))
async def move_shared_event(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, manager_raw, meeting_raw = callback.data.split(":")
    manager_id, meeting_id = int(manager_raw), int(meeting_raw)
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    meeting = await db.meeting(meeting_id, manager_id)
    if not meeting:
        await callback.answer("ایونت پیدا نشد.", show_alert=True)
        return
    await state.update_data(
        move_target=str(meeting_id), meeting_owner_id=manager_id,
        move_duration=int((meeting.ends_at - meeting.starts_at).total_seconds()),
    )
    await state.set_state(MoveMeeting.new_time)
    await callback.message.answer(
        f"زمان شروع جدید «{html.escape(meeting.title)}» را بفرستید؛ مثلاً «پس‌فردا ساعت ۱۰»."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shared:delete:"))
async def delete_shared_event(callback: CallbackQuery) -> None:
    _, _, manager_raw, meeting_raw = callback.data.split(":")
    manager_id, meeting_id = int(manager_raw), int(meeting_raw)
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    deleted = await db.delete_meeting(meeting_id, manager_id)
    if deleted:
        await callback.message.edit_text("🗑 ایونت از تقویم مدیر حذف شد.")
    await callback.answer("ایونت حذف شد." if deleted else "ایونت پیدا نشد.", show_alert=not deleted)


@router.callback_query(F.data.startswith("shared:repeat:toggle:"))
async def toggle_shared_meeting_recurrence(callback: CallbackQuery) -> None:
    _, _, _, manager_raw, series_raw = callback.data.split(":")
    manager_id, series_id = int(manager_raw), int(series_raw)
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    series = await db.meeting_recurrence_series(series_id, manager_id)
    if not series:
        await callback.answer("تکرار ایونت پیدا نشد.", show_alert=True)
        return
    await db.set_meeting_recurrence_active(series_id, manager_id, not series["active"])
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("تکرار ایونت فعال شد." if not series["active"] else "تکرار ایونت غیرفعال شد.")


@router.callback_query(F.data.startswith("shared:repeat:delete:"))
async def delete_shared_meeting_recurrence(callback: CallbackQuery) -> None:
    _, _, _, manager_raw, series_raw = callback.data.split(":")
    manager_id, series_id = int(manager_raw), int(series_raw)
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    ok = await db.delete_meeting_recurrence(series_id, manager_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("تکرار ایونت حذف شد." if ok else "تکرار ایونت پیدا نشد.", show_alert=not ok)


@router.callback_query(F.data.startswith("shared:repeat:edit:"))
async def edit_shared_meeting_recurrence(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, manager_raw, series_raw = callback.data.split(":")
    manager_id, series_id = int(manager_raw), int(series_raw)
    if await db.event_share_permission(manager_id, callback.from_user.id) != "edit":
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    if not await db.meeting_recurrence_series(series_id, manager_id):
        await callback.answer("تکرار ایونت پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.set_state(NewMeeting.recurrence)
    await state.update_data(
        recurrence_edit_series_id=series_id,
        recurrence_edit_user_id=manager_id,
        meeting_owner_id=manager_id,
    )
    await callback.message.answer(
        "تکرار جدید را بنویسید؛ مثلاً «هر هفته یکشنبه و سه‌شنبه»، «روزهای زوج» یا «روزهای فرد».",
        reply_markup=back_to_menu(),
    )
    await callback.answer()
@router.message(F.text == "📌 تسک‌های واگذارشده به من")
async def member_tasks(message: Message) -> None:
    tasks = await db.assigned_tasks(message.from_user.id)
    if not tasks:
        await message.answer("در حال حاضر تسک بازی به شما واگذار نشده است.", reply_markup=await member_reply_menu(message.from_user.id))
        return
    await message.answer("<b>📌 تسک‌های واگذارشده به شما</b>")
    for index, task in enumerate(tasks, 1):
        builder = InlineKeyboardBuilder()
        builder.button(text="📤 ارسال گزارش", callback_data=f"report:start:{task.id}")
        await message.answer(
            task_text(task, index, message.from_user.id),
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("report:start:"))
async def start_member_report(callback: CallbackQuery, state: FSMContext) -> None:
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.assigned_task(task_id, callback.from_user.id)
    if not task or task.status != "open":
        await callback.answer("این تسک در دسترس شما نیست.", show_alert=True)
        return
    await state.update_data(report_task_id=task_id, report_manager_id=task.user_id)
    await state.set_state(MemberReport.progress)
    await callback.message.answer("درصد پیشرفت فعلی را بین ۰ تا ۱۰۰ بفرستید:")
    await callback.answer()


@router.message(MemberReport.progress)
async def member_report_progress(message: Message, state: FSMContext) -> None:
    try:
        progress = int((message.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if not 0 <= progress <= 100:
            raise ValueError
    except ValueError:
        await message.answer("یک عدد بین ۰ تا ۱۰۰ بفرستید.")
        return
    await state.update_data(report_progress=progress)
    await state.set_state(MemberReport.note)
    await message.answer("گزارش یا آخرین توضیح را بنویسید:")


@router.message(MemberReport.note)
async def save_member_report(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    note = (message.text or "").strip()
    report_id = await db.add_task_report(
        data["report_task_id"], data["report_manager_id"], message.from_user.id,
        data["report_progress"], note,
    )
    await state.clear()
    if not report_id:
        await message.answer("این تسک دیگر به شما واگذار نشده است.", reply_markup=await member_reply_menu(message.from_user.id))
        return
    task = await db.task(data["report_task_id"], data["report_manager_id"])
    await message.answer("✅ گزارش شما برای مدیر ثبت و ارسال شد.", reply_markup=await member_reply_menu(message.from_user.id))
    await message.bot.send_message(
        data["report_manager_id"],
        f"📥 <b>گزارش جدید تسک</b>\nگزارش‌دهنده: <code>{message.from_user.id}</code>\n\n"
        + task_text(task, viewer_id=data["report_manager_id"]),
        reply_markup=task_actions(task.id, task.status),
    )
    if task.cc_manager_id:
        try:
            await message.bot.send_message(
                task.cc_manager_id,
                f"📨 <b>رونوشت گزارش تسک</b>\nمدیر اصلی: <code>{task.user_id}</code>\n"
                f"گزارش‌دهنده: <code>{message.from_user.id}</code>\n\n"
                + task_text(task, viewer_id=task.cc_manager_id),
            )
        except Exception:
            logging.exception("Could not send task report copy to %s", task.cc_manager_id)


@router.callback_query(F.data.startswith("task:reports:"))
async def show_task_reports(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    if not await db.task(task_id, callback.from_user.id):
        await callback.answer("تسک پیدا نشد.", show_alert=True)
        return
    reports = await db.task_reports(task_id, callback.from_user.id)
    if not reports:
        await callback.answer("هنوز گزارشی برای این تسک ثبت نشده است.", show_alert=True)
        return
    lines = ["<b>🗂 سابقه گزارش‌های تسک</b>", ""]
    for reporter_id, progress, note, created_at in reports:
        lines.append(f"• {fmt_dt(created_at.replace(tzinfo=settings.timezone))} — {fa_number(progress)}٪\n  <code>{reporter_id}</code>: {html.escape(note)}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("task:chart:"))
async def show_task_progress_chart(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.task(task_id, callback.from_user.id)
    if not task:
        await callback.answer("تسک پیدا نشد.", show_alert=True)
        return
    history = await db.task_progress_history(task_id, callback.from_user.id)
    now = datetime.now(settings.timezone)
    image = progress_chart(task, history, now)
    created_at = task.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=settings.timezone)
    deadline = task.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=settings.timezone)
    elapsed = max(0, math.floor((now - created_at).total_seconds() / 86400))
    remaining = math.ceil((deadline - now).total_seconds() / 86400)
    remaining_text = (f"{fa_number(remaining)} روز تا ددلاین باقی مانده" if remaining >= 0
                      else f"{fa_number(abs(remaining))} روز از ددلاین گذشته")
    await callback.message.answer_photo(
        BufferedInputFile(image, filename=f"task-{task.id}-progress.png"),
        caption=(f"📉 <b>نمودار پیشرفت: {html.escape(task.title)}</b>\n"
                 f"از شروع تسک: {fa_number(elapsed)} روز گذشته\n{remaining_text}\n"
                 f"آخرین پیشرفت: {fa_number(task.progress)}٪"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:done:"))
async def complete_task(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    await db.update_task(task_id, callback.from_user.id, "status", "done")
    await db.update_task(task_id, callback.from_user.id, "progress", 100)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("تسک انجام‌شده ثبت شد ✅")


@router.callback_query(F.data.startswith("task:progress:"))
async def ask_progress(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(task_id=task_id)
    await state.set_state(EditTask.progress)
    await callback.message.answer("درصد پیشرفت جدید را بین ۰ تا ۱۰۰ بفرستید:")
    await callback.answer()


@router.message(EditTask.progress)
async def save_progress(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if not 0 <= value <= 100:
            raise ValueError
    except ValueError:
        await message.answer("یک عدد بین ۰ تا ۱۰۰ بفرستید.")
        return
    data = await state.get_data()
    owner_id = int(data.get("shared_task_owner_id") or message.from_user.id)
    if data.get("shared_task_owner_id"):
        perms = await db.task_share_permissions(owner_id, message.from_user.id)
        if not perms or not perms["can_edit"]:
            await state.clear()
            await message.answer("اجازهٔ ویرایش این تسک را ندارید.")
            return
    await db.update_task(
        data["task_id"], owner_id, "progress", value, changed_by=message.from_user.id,
    )
    if value == 100:
        await db.update_task(
            data["task_id"], owner_id, "status", "done", changed_by=message.from_user.id,
        )
    await state.clear()
    await message.answer("✅ درصد پیشرفت به‌روزرسانی شد.",
                         reply_markup=await member_reply_menu(message.from_user.id)
                         if data.get("shared_task_owner_id") else main_menu())


@router.callback_query(F.data.startswith("task:note:"))
async def ask_note(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    await state.update_data(task_id=int(callback.data.rsplit(":", 1)[1]))
    await state.set_state(EditTask.note)
    await callback.message.answer("آخرین توضیح جدید را بنویسید:")
    await callback.answer()


@router.message(EditTask.note)
async def save_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    owner_id = int(data.get("shared_task_owner_id") or message.from_user.id)
    if data.get("shared_task_owner_id"):
        perms = await db.task_share_permissions(owner_id, message.from_user.id)
        if not perms or not perms["can_edit"]:
            await state.clear()
            await message.answer("اجازهٔ ویرایش این تسک را ندارید.")
            return
    await db.update_task(
        data["task_id"], owner_id, "last_note", (message.text or "").strip(),
        changed_by=message.from_user.id,
    )
    await state.clear()
    await message.answer("✅ آخرین توضیح به‌روزرسانی شد.",
                         reply_markup=await member_reply_menu(message.from_user.id)
                         if data.get("shared_task_owner_id") else main_menu())


@router.callback_query(F.data.startswith("task:cc:menu:"))
async def task_cc_menu(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.task(task_id, callback.from_user.id)
    if not task:
        await callback.answer("تسک پیدا نشد.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 فعال / تغییر مدیر", callback_data=f"task:cc:edit:on:{task_id}")
    builder.button(text="🔴 غیرفعال‌کردن", callback_data=f"task:cc:edit:off:{task_id}")
    builder.adjust(1)
    current = f"فعال — شناسه مدیر: <code>{task.cc_manager_id}</code>" if task.cc_manager_id else "خاموش"
    await callback.message.answer(
        f"<b>📨 مدیریت رونوشت تسک</b>\nوضعیت فعلی: {current}\n\n"
        "می‌توانید رونوشت را خاموش کنید یا مدیر دریافت‌کننده را فعال/تغییر دهید.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:cc:edit:off:"))
async def edit_task_cc_off(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    if not await db.update_task(task_id, callback.from_user.id, "cc_manager_id", None):
        await callback.answer("تسک پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("✅ رونوشت این تسک غیرفعال شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("task:cc:edit:on:"))
async def edit_task_cc_on(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    if not await db.task(task_id, callback.from_user.id):
        await callback.answer("تسک پیدا نشد.", show_alert=True)
        return
    await state.update_data(task_id=task_id)
    await state.set_state(EditTask.cc)
    await callback.message.answer(
        "آیدی مدیر دریافت‌کننده رونوشت را به شکل <code>@username</code> بفرستید.\n"
        "آن مدیر باید قبلاً یک‌بار همین ربات را Start کرده باشد."
    )
    await callback.answer()


@router.message(EditTask.cc)
async def edit_task_cc_username(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not username.startswith("@"):
        await message.answer("آیدی را به شکل <code>@username</code> بفرستید.")
        return
    account = await db.telegram_account_by_username(username)
    if not account:
        me = await message.bot.get_me()
        await message.answer(
            f"این آیدی هنوز به ربات متصل نشده است. از او بخواهید ابتدا @{me.username} را باز کند و Start بزند؛ "
            "سپس همین آیدی را دوباره ارسال کنید."
        )
        return
    cc_manager_id, name = account
    if cc_manager_id == message.from_user.id:
        await message.answer("برای رونوشت باید آیدی مدیر دیگری را وارد کنید.")
        return
    if not await allowed(cc_manager_id):
        await message.answer("این شخص در حال حاضر دسترسی فعال مدیریتی به ربات ندارد.")
        return
    data = await state.get_data()
    if not await db.update_task(data["task_id"], message.from_user.id, "cc_manager_id", cc_manager_id):
        await state.clear()
        await message.answer("تسک پیدا نشد.", reply_markup=main_menu())
        return
    await state.clear()
    await message.answer(
        f"✅ رونوشت این تسک برای {html.escape(name or username)} فعال شد.",
        reply_markup=main_menu(),
    )

@router.message(F.text == "📊 داشبورد")
async def dashboard(message: Message) -> None:
    if await reject_if_needed(message):
        return
    now = datetime.now(settings.timezone)
    stats = await db.dashboard_stats(message.from_user.id, now)
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    next_week = start_of_week + timedelta(days=7)
    following_week = next_week + timedelta(days=7)
    this_week_events = await db.meetings_between(message.from_user.id, start_of_week, next_week)
    next_week_events = await db.meetings_between(message.from_user.id, next_week, following_week)

    timed_events = this_week_events
    total_minutes = 0
    day_counts: dict[str, int] = {}
    for event in timed_events:
        total_minutes += max(0, int((event.ends_at - event.starts_at).total_seconds() // 60))
        day_key = event.starts_at.date().isoformat()
        day_counts[day_key] = day_counts.get(day_key, 0) + 1
    busiest = None
    if day_counts:
        busiest_key = max(day_counts, key=day_counts.get)
        busiest = datetime.fromisoformat(busiest_key).replace(tzinfo=settings.timezone)

    future_events = await db.upcoming_meetings(message.from_user.id, now, limit=1)
    nearest = future_events[0] if future_events else None

    lines = [
        "<b>📊 داشبورد مدیریتی</b>", "",
        "<b>📅 جلسات</b>",
        f"جلسات این هفته: <b>{fa_number(len(this_week_events))}</b>",
        f"زمان جلسات این هفته: <b>{fa_number(total_minutes // 60)} ساعت و {fa_number(total_minutes % 60)} دقیقه</b>",
        f"جلسات هفته آینده: <b>{fa_number(len(next_week_events))}</b>",
    ]
    if busiest:
        lines.append(f"شلوغ‌ترین روز: <b>{fmt_date(busiest)}</b> با {fa_number(day_counts[busiest.date().isoformat()])} جلسه")
    if nearest:
        lines.append(f"نزدیک‌ترین جلسه: <b>{html.escape(nearest.title)}</b> — {fmt_dt(nearest.starts_at)}")

    priorities = stats["priorities"]
    lines += [
        "", "<b>✅ وضعیت تسک‌ها</b>",
        f"باز: <b>{fa_number(stats['open'])}</b> | انجام‌شده: <b>{fa_number(stats['done'])}</b>",
        f"عقب‌افتاده: <b>{fa_number(stats['overdue'])}</b>",
        f"ددلاین در ۷ روز آینده: <b>{fa_number(stats['due_week'])}</b>",
        f"میانگین پیشرفت تسک‌های باز: <b>{fa_number(stats['avg'])}٪</b>",
        f"نرخ تکمیل کل تسک‌ها: <b>{fa_number(stats['completion_rate'])}٪</b>",
        "", "<b>🧭 ماتریس آیزنهاور</b>",
        f"🔴 فوری و مهم: {fa_number(priorities.get(1, 0))}",
        f"🟠 مهم، غیرفوری: {fa_number(priorities.get(2, 0))}",
        f"🟡 فوری، کم‌اهمیت: {fa_number(priorities.get(3, 0))}",
        f"🟢 غیرفوری و کم‌اهمیت: {fa_number(priorities.get(4, 0))}",
    ]
    if stats["attention"]:
        lines += ["", "<b>🚨 نیازمند توجه:</b>"] + [
            f"• {html.escape(title)} — {fa_number(progress)}٪ — {fmt_date(deadline)}"
            for _, title, deadline, progress in stats["attention"]
        ]
    await message.answer("\n".join(lines))


async def user_day_schedule_text(user_id: int, day, label: str) -> str:
    start = datetime.combine(day, datetime.min.time(), tzinfo=settings.timezone)
    events = await db.meetings_on(user_id, day)
    if not events:
        return f"📅 برای {label} جلسه‌ای در ربات ثبت نشده است."
    lines = [f"<b>📅 برنامه جلسات {label} ({fmt_date(start)})</b>", ""]
    total = 0
    for index, event in enumerate(events, 1):
        total += int((event.ends_at - event.starts_at).total_seconds() // 60)
        lines.append(
            f"{fa_number(index)}. <b>{html.escape(event.title)}</b>\n"
            f"   🕐 {fa_number(event.starts_at.strftime('%H:%M'))} تا {fa_number(event.ends_at.strftime('%H:%M'))}"
        )
    lines += ["", f"مجموع زمان جلسات: <b>{fa_number(total // 60)} ساعت و {fa_number(total % 60)} دقیقه</b>"]
    return "\n".join(lines)


async def tomorrow_text(user_id: int) -> str:
    tomorrow = datetime.now(settings.timezone).date() + timedelta(days=1)
    return await user_day_schedule_text(user_id, tomorrow, "فردا")


@router.message(F.text == "📅 جلسات فردا")
async def tomorrow_meetings(message: Message) -> None:
    if await reject_if_needed(message):
        return
    await message.answer(await tomorrow_text(message.from_user.id))


@router.message(F.text == "🗓 مدیریت جلسات")
async def manage_meetings(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    await message.answer(
        "بازهٔ زمانی جلسات را انتخاب کنید:",
        reply_markup=meeting_range_options(),
    )


@router.callback_query(F.data.startswith("meetings:range:"))
async def list_meetings_in_range(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    kind = callback.data.rsplit(":", 1)[1]
    now = datetime.now(settings.timezone)
    start = now
    if kind == "week_end":
        # Iran workweek ends Thursday (weekday=3).
        days = (3 - now.weekday()) % 7
        end = (now + timedelta(days=days)).replace(hour=23, minute=59, second=59, microsecond=999999)
        label = "تا آخر هفته"
    elif kind == "next_week":
        days_to_next_saturday = (5 - now.weekday()) % 7
        if days_to_next_saturday == 0:
            days_to_next_saturday = 7
        start = (now + timedelta(days=days_to_next_saturday)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        label = "هفتهٔ آینده"
    elif kind == "all":
        end = now + timedelta(days=365)
        label = "همهٔ جلسات آینده"
    else:
        await callback.answer("بازهٔ زمانی نامعتبر است.", show_alert=True)
        return
    events = await db.meetings_in_range(callback.from_user.id, start, end, limit=100)
    if not events:
        await callback.message.edit_text(f"در بازهٔ «{label}» جلسه‌ای پیدا نشد.", reply_markup=meeting_range_options())
        return
    await callback.message.edit_text(f"جلسات {label}:", reply_markup=meeting_range_options())
    for index, event in enumerate(events):
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 جابه‌جایی این جلسه", callback_data=f"meeting:move:{event.id}")
        builder.button(text="🗑 حذف این جلسه", callback_data=f"meeting:delete:{event.id}")
        recurrence = await db.meeting_recurrence(event.id, callback.from_user.id)
        if recurrence:
            builder.button(
                text=("⏸ غیرفعال‌کردن تکرار" if recurrence["active"] else "▶️ فعال‌کردن تکرار"),
                callback_data=f"meeting:repeat:toggle:{recurrence['series_id']}",
            )
            builder.button(
                text="✏️ ویرایش تکرار",
                callback_data=f"meeting:repeat:edit:{recurrence['series_id']}",
            )
            builder.button(
                text="🗑 حذف تکرار",
                callback_data=f"meeting:repeat:delete:{recurrence['series_id']}",
            )
        builder.adjust(1)
        await callback.message.answer(
            f"<b>{fa_number(index + 1)}. {html.escape(event.title)}</b>\n"
            f"شروع: {fmt_dt(event.starts_at)}\n"
            f"پایان: {fmt_dt(event.ends_at)}"
            + (f"\n🔁 تکرار: {html.escape(recurrence_description(recurrence))}" if recurrence else ""),
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting:delete:"))
async def delete_meeting(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    meeting_id = int(callback.data.rsplit(":", 1)[1])
    meeting = await db.meeting(meeting_id, callback.from_user.id)
    if not meeting:
        await callback.answer("جلسه پیدا نشد.", show_alert=True)
        return
    deleted = await db.delete_meeting(meeting_id, callback.from_user.id)
    if deleted:
        await callback.message.edit_text(
            f"🗑 جلسهٔ «{html.escape(meeting.title)}» حذف شد."
        )
    await callback.answer("جلسه حذف شد." if deleted else "جلسه پیدا نشد.", show_alert=not deleted)


@router.callback_query(F.data.startswith("meeting:repeat:toggle:"))
async def toggle_meeting_recurrence(callback: CallbackQuery) -> None:
    series_id = int(callback.data.rsplit(":", 1)[1])
    series = await db.meeting_recurrence_series(series_id, callback.from_user.id)
    if not series:
        await callback.answer("تکرار ایونت پیدا نشد.", show_alert=True)
        return
    active = not series["active"]
    await db.set_meeting_recurrence_active(series_id, callback.from_user.id, active)
    await callback.answer("تکرار ایونت فعال شد." if active else "تکرار ایونت غیرفعال شد.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("meeting:repeat:delete:"))
async def delete_meeting_recurrence(callback: CallbackQuery) -> None:
    series_id = int(callback.data.rsplit(":", 1)[1])
    if await db.delete_meeting_recurrence(series_id, callback.from_user.id):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("تکرار ایونت حذف شد.")
    else:
        await callback.answer("تکرار ایونت پیدا نشد.", show_alert=True)


@router.callback_query(F.data.startswith("meeting:repeat:edit:"))
async def edit_meeting_recurrence(callback: CallbackQuery, state: FSMContext) -> None:
    series_id = int(callback.data.rsplit(":", 1)[1])
    if not await db.meeting_recurrence_series(series_id, callback.from_user.id):
        await callback.answer("تکرار ایونت پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.set_state(NewMeeting.recurrence)
    await state.update_data(
        recurrence_edit_series_id=series_id,
        recurrence_edit_user_id=callback.from_user.id,
    )
    await callback.message.answer(
        "تکرار جدید را بنویسید؛ مثلاً «هر هفته یکشنبه و سه‌شنبه»، «روزهای زوج» یا «روزهای فرد».",
        reply_markup=back_to_menu(),
    )
    await callback.answer()


async def schedule_warnings(user_id: int, starts_at: datetime, ends_at: datetime,
                            exclude_id: int | None = None) -> list:
    return await db.overlapping_meetings(user_id, starts_at, ends_at, exclude_id)


def warning_text(local: list) -> str:
    lines = ["⚠️ <b>هشدار زمان‌بندی</b>", "", "جلسات متداخل ثبت‌شده در ربات:"]
    lines += [
        f"• #{meeting.id} {html.escape(meeting.title)} — {fa_number(meeting.starts_at.strftime('%H:%M'))} تا {fa_number(meeting.ends_at.strftime('%H:%M'))}"
        for meeting in local
    ]
    return "\n".join(lines)


def normalize_user_command(text: str) -> str:
    return " ".join(
        (text or "").replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک").split()
    ).strip().lower()


@router.message(F.text == "🤖 فرمان مدیریتی")
async def management_command_menu(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    if not ai_service.available:
        await message.answer(
            "کلید API هوش مصنوعی تنظیم نشده است. ابتدا OPENAI_API_KEY را در فایل .env تنظیم کنید."
        )
        return
    await state.set_state(ManagementCommand.text)
    await message.answer(
        "<b>🤖 فرمان مدیریتی</b>\n\n"
        "فرمان را با زبان طبیعی بنویسید. مثال:\n"
        "<code>تسک‌هایی که کمتر از ۵۰ درصد پیشرفت کردند را بفرست</code>\n\n"
        "<code>تسک‌هایی که در یک هفته اخیر هیچ تغییری نکرده‌اند را بفرست</code>\n\n"
        "برای خروج، دکمه زیر را بزنید.",
        reply_markup=management_exit(),
    )


@router.message(ManagementCommand.text, F.text)
async def execute_management_command(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    command = (message.text or "").strip()
    if not command:
        await message.answer(
            "فرمان را به‌صورت متنی بفرستید.",
            reply_markup=management_exit(),
        )
        return
    now = datetime.now(settings.timezone)
    tasks = await db.tasks(message.from_user.id, include_done=True, limit=50)
    meetings = await db.upcoming_meetings(message.from_user.id, now - timedelta(days=1), limit=50)
    context = {
        "tasks": [{"id": item.id, "title": item.title, "deadline": item.deadline.isoformat(),
                   "progress": item.progress, "status": item.status, "assignee": item.assignee} for item in tasks],
        "meetings": [{"id": item.id, "title": item.title, "starts_at": item.starts_at.isoformat(),
                      "ends_at": item.ends_at.isoformat()} for item in meetings],
    }
    waiting = await message.answer("⏳ در حال فهم فرمان و آماده‌سازی نتیجه...")
    plan = await ai_service.parse_management_command(command, now, context)
    try:
        await waiting.delete()
    except Exception:
        pass
    if not plan:
        await message.answer(
            "ارتباط با API ناموفق بود. کمی بعد دوباره تلاش کنید.",
            reply_markup=management_exit(),
        )
        return
    operation = plan.get("operation")
    if operation == "dashboard":
        await dashboard(message)
        return
    if operation == "tomorrow_meetings":
        await tomorrow_meetings(message)
        return
    if operation == "create_task":
        await state.clear()
        await start_task_from_text(message, state, str(plan.get("task_text") or command))
        return
    if operation == "create_meeting":
        await state.clear()
        await meeting_message(message, state)
        return
    if operation == "unsupported":
        await message.answer(
            "این فرمان با اطمینان کافی قابل اجرا نیست: "
            + html.escape(str(plan.get("message") or "")),
            reply_markup=management_exit(),
        )
        return
    if operation == "task_query":
        matched = await db.query_tasks(
            message.from_user.id, plan.get("filters") or {}, plan.get("sort") or "deadline_asc",
            int(plan.get("limit") or 20), now,
        )
        if not matched:
            await message.answer(
                "تسکی مطابق این فرمان پیدا نشد.",
                reply_markup=management_exit(),
            )
            return
        await message.answer(
            f"<b>نتیجه فرمان</b> — {fa_number(len(matched))} تسک پیدا شد:",
            reply_markup=management_exit(),
        )
        for index, task in enumerate(matched, 1):
            await message.answer(
                task_text(task, index, message.from_user.id),
                reply_markup=task_actions(task.id, task.status),
            )
        return

    labels = {
        "update_task_progress": "تغییر پیشرفت تسک", "update_task_note": "ثبت یادداشت تسک",
        "update_task_deadline": "تغییر ددلاین تسک", "update_task_status": "تغییر وضعیت تسک",
        "move_meeting": "جابه‌جایی ایونت",
    }
    if operation not in labels:
        await message.answer(
            "برای این فرمان اجراگر امنی وجود ندارد.",
            reply_markup=management_exit(),
        )
        return
    await state.update_data(pending_management_plan=plan)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأیید و اجرا", callback_data="management:confirm")
    builder.button(text="❌ لغو", callback_data="management:cancel")
    builder.adjust(2)
    details = [f"<b>{labels[operation]}</b>", f"هدف: {html.escape(str(plan.get('target_title') or 'مورد انتخاب‌شده'))}"]
    if operation == "update_task_progress":
        details.append(f"پیشرفت جدید: {fa_number(plan['progress'])}٪")
    elif operation == "update_task_note":
        details.append(f"یادداشت: {html.escape(plan['note'])}")
    elif operation == "update_task_deadline":
        details.append(f"ددلاین جدید: {fmt_dt(datetime.fromisoformat(plan['deadline']))}")
    elif operation == "update_task_status":
        details.append(f"وضعیت جدید: {html.escape(plan['status'])}")
    else:
        details.append(f"زمان جدید: {fmt_dt(datetime.fromisoformat(plan['starts_at']))}")
    separator = chr(10)
    await message.answer(separator.join(details) + separator + separator + "این تغییر اجرا شود؟", reply_markup=builder.as_markup())


@router.callback_query(F.data == "management:confirm")
async def confirm_management_mutation(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    plan = data.get("pending_management_plan")
    if not isinstance(plan, dict):
        await callback.answer("این فرمان منقضی شده است.", show_alert=True)
        return
    operation = plan.get("operation")
    ok = False
    if operation == "update_task_progress":
        ok = await db.update_task(plan["target_task_id"], callback.from_user.id, "progress", plan["progress"])
    elif operation == "update_task_note":
        ok = await db.update_task(plan["target_task_id"], callback.from_user.id, "last_note", plan["note"])
    elif operation == "update_task_deadline":
        ok = await db.update_task(plan["target_task_id"], callback.from_user.id, "deadline", plan["deadline"])
    elif operation == "update_task_status":
        ok = await db.update_task(plan["target_task_id"], callback.from_user.id, "status", plan["status"])
        if ok and plan["status"] == "done":
            await db.update_task(plan["target_task_id"], callback.from_user.id, "progress", 100)
    elif operation == "move_meeting":
        ok = await db.move_meeting(plan["target_meeting_id"], callback.from_user.id,
                                   datetime.fromisoformat(plan["starts_at"]), datetime.fromisoformat(plan["ends_at"]))
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ فرمان با موفقیت اجرا شد." if ok else "❌ مورد هدف پیدا نشد یا اجازه تغییر آن را ندارید.")
    await callback.answer()


@router.callback_query(F.data == "management:cancel")
async def cancel_management_mutation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("فرمان لغو شد.")
    await callback.answer()

@router.message(ManagementCommand.text)
async def management_command_text_only(message: Message) -> None:
    await message.answer(
        "لطفاً فرمان مدیریتی را به‌صورت متن ارسال کنید یا از دکمه خروج استفاده کنید.",
        reply_markup=management_exit(),
    )


def _creation_intent_hint(text: str) -> str | None:
    clean = normalize_user_command(text)
    if any(word in clean for word in ("\u062c\u0644\u0633\u0647", "\u0642\u0631\u0627\u0631", "\u0627\u06cc\u0648\u0646\u062a", "\u0631\u0648\u06cc\u062f\u0627\u062f", "\u0646\u0634\u0633\u062a")):
        return "meeting"
    if any(word in clean for word in ("\u062a\u0633\u06a9", "\u0648\u0638\u06cc\u0641\u0647", "\u062a\u062d\u0648\u06cc\u0644", "\u0627\u0646\u062c\u0627\u0645 \u0628\u062f\u0645", "\u067e\u06cc\u06af\u06cc\u0631\u06cc")):
        return "task"
    return None


async def start_task_from_text(message: Message, state: FSMContext, text: str) -> None:
    parsed = await ai_service.parse_task(text, settings.timezone, datetime.now(settings.timezone)) if ai_service.available else None
    if not parsed or not parsed.title:
        await state.set_state(NewTask.title)
        await message.answer("\u0639\u0646\u0648\u0627\u0646 \u062a\u0633\u06a9 \u0631\u0627 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.")
        return
    await state.update_data(title=parsed.title)
    if not parsed.deadline:
        await state.set_state(NewTask.deadline)
        await message.answer("\u062a\u0633\u06a9 \u062a\u0634\u062e\u06cc\u0635 \u062f\u0627\u062f\u0647 \u0634\u062f. \u062f\u062f\u0644\u0627\u06cc\u0646 \u0622\u0646 \u0631\u0627 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.")
        return
    await state.update_data(deadline=parsed.deadline.isoformat())
    if not parsed.priority:
        await state.set_state(NewTask.priority)
        await message.answer("\u062a\u0633\u06a9 \u062a\u0634\u062e\u06cc\u0635 \u062f\u0627\u062f\u0647 \u0634\u062f. \u0627\u0648\u0644\u0648\u06cc\u062a \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f:", reply_markup=priorities())
        return
    await state.update_data(priority=parsed.priority)
    await state.set_state(NewTask.assignee)
    await message.answer("\u062a\u0633\u06a9 \u062a\u0634\u062e\u06cc\u0635 \u062f\u0627\u062f\u0647 \u0634\u062f. \u0646\u0627\u0645 \u0645\u0633\u0626\u0648\u0644 \u067e\u06cc\u06af\u06cc\u0631\u06cc \u0631\u0627 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.")


async def free_meeting_slots(user_id: int, day: date, duration: timedelta, limit: int = 3) -> list[datetime]:
    day_start = datetime.combine(day, time(8, 0), tzinfo=settings.timezone)
    day_end = datetime.combine(day, time(19, 0), tzinfo=settings.timezone)
    meetings = await db.meetings_between(user_id, day_start, day_end + timedelta(seconds=1))
    quiet = await db.meeting_quiet_hours(user_id)
    result = []
    candidate = day_start
    now = datetime.now(settings.timezone)
    while candidate + duration <= day_end and len(result) < limit:
        end = candidate + duration
        minute = candidate.hour * 60 + candidate.minute
        end_minute = end.hour * 60 + end.minute
        blocked = any(candidate < m.ends_at and end > m.starts_at for m in meetings)
        blocked = blocked or any(minute < q_end and end_minute > q_start for _, q_start, q_end in quiet)
        if candidate > now and not blocked:
            result.append(candidate)
        candidate += timedelta(minutes=30)
    return result


def notification_setting_defaults() -> dict[str, int]:
    return {
        "digest_hour": settings.digest_hour,
        "digest_minute": settings.digest_minute,
        "morning_hour": settings.morning_hour,
        "morning_minute": settings.morning_minute,
        "digest_enabled": 1,
        "morning_enabled": 1,
        "deadline_warning_days": settings.deadline_warning_days,
        "report_warning_hours": settings.report_warning_hours,
        "deadline_warning_days_list": str(settings.deadline_warning_days),
        "reminder_warning_days_list": str(settings.deadline_warning_days),
    }


def format_clock(hour: int, minute: int) -> str:
    return fa_number(f"{hour:02d}:{minute:02d}")

async def show_meeting_settings(message: Message, user_id: int) -> None:
    quiet = await db.meeting_quiet_hours(user_id)
    display_name = await db.user_display_name(user_id)
    user_config = await db.user_settings(user_id, notification_setting_defaults())
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ ویرایش نام نمایشی", callback_data="settings:name:edit")
    builder.button(text="⏰ ساعت اعلان جلسات فردا", callback_data="settings:digest:time")
    builder.button(
        text="🔕 غیرفعال‌کردن جلسات فردا" if user_config["digest_enabled"] else "🔔 فعال‌کردن جلسات فردا",
        callback_data="settings:digest:toggle",
    )
    builder.button(text="🌅 ساعت اعلان جلسات امروز", callback_data="settings:morning:time")
    builder.button(text="⏳ هشدارهای قبل از ددلاین تسک‌ها", callback_data="settings:warning:deadline")
    builder.button(text="🔔 هشدار موارد اشتراکی", callback_data="settings:shared_alerts")
    builder.button(
        text="🔕 غیرفعال‌کردن جلسات امروز" if user_config["morning_enabled"] else "🔔 فعال‌کردن جلسات امروز",
        callback_data="settings:morning:toggle",
    )
    builder.button(text="\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u0628\u0627\u0632\u0647 \u0645\u0645\u0646\u0648\u0639\u0647", callback_data="settings:quiet:add")
    builder.button(text="⬅️ بازگشت", callback_data="menu:main")
    for quiet_id, start, end in quiet:
        builder.button(text=f"\U0001f5d1 {start//60:02d}:{start%60:02d} - {end//60:02d}:{end%60:02d}", callback_data=f"settings:quiet:del:{quiet_id}")
    builder.adjust(1)
    digest_status = "فعال" if user_config["digest_enabled"] else "غیرفعال"
    morning_status = "فعال" if user_config["morning_enabled"] else "غیرفعال"
    lines = [
        "<b>⚙️ تنظیمات</b>", "",
        f"نام نمایشی مدیر: <b>{html.escape(display_name or 'ثبت نشده')}</b>",
        "این نام در دکمه «جلسات نام مدیر» برای نیروها نمایش داده می‌شود.", "",
        "<b>🔔 اعلان جلسات شخصی</b>",
        f"جلسات فردا: <b>{digest_status}</b> — ساعت <b>{format_clock(user_config['digest_hour'], user_config['digest_minute'])}</b>",
        f"جلسات امروز: <b>{morning_status}</b> — ساعت <b>{format_clock(user_config['morning_hour'], user_config['morning_minute'])}</b>", "",
        f"هشدارهای قبل از ددلاین تسک‌ها: <b>{html.escape(user_config.get('deadline_warning_days_list') or str(settings.deadline_warning_days))}</b> روز قبل", "",
        "<b>تنظیمات زمان جلسه</b>", "در این بازه‌ها هیچ جلسه‌ای پیشنهاد نمی‌شود."
    ]
    if quiet:
        lines += ["", "\u0628\u0627\u0632\u0647\u200c\u0647\u0627\u06cc \u0641\u0639\u0627\u0644:"] + [f"\u2022 {a//60:02d}:{a%60:02d} \u062a\u0627 {b//60:02d}:{b%60:02d}" for _,a,b in quiet]
    else: lines += ["", "\u0647\u0646\u0648\u0632 \u0628\u0627\u0632\u0647\u200c\u0627\u06cc \u062b\u0628\u062a \u0646\u0634\u062f\u0647 \u0627\u0633\u062a."]
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.message(F.text == "\u2699\ufe0f \u062a\u0646\u0638\u06cc\u0645\u0627\u062a")
async def meeting_settings(message: Message) -> None:
    if await allowed(message.from_user.id) or await db.member_managers(message.from_user.id):
        await show_meeting_settings(message, message.from_user.id)
    else:
        await reject_if_needed(message)


async def ask_notification_time(
    callback: CallbackQuery, state: FSMContext, kind: str,
) -> None:
    if await reject_if_needed(callback):
        return
    user_config = await db.user_settings(callback.from_user.id, notification_setting_defaults())
    hour_key = "digest_hour" if kind == "digest" else "morning_hour"
    minute_key = "digest_minute" if kind == "digest" else "morning_minute"
    label = "جلسات فردا" if kind == "digest" else "جلسات امروز"
    await state.set_state(
        MeetingSettings.digest_time if kind == "digest" else MeetingSettings.morning_time
    )
    await callback.message.answer(
        f"ساعت فعلی اعلان {label}: <b>{format_clock(user_config[hour_key], user_config[minute_key])}</b>\n\n"
        "ساعت جدید را به شکل <code>22:00</code> یا <code>9</code> بفرستید.",
        reply_markup=back_button("nav:settings"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:digest:time")
async def edit_digest_time(callback: CallbackQuery, state: FSMContext) -> None:
    await ask_notification_time(callback, state, "digest")


@router.callback_query(F.data == "settings:morning:time")
async def edit_morning_time(callback: CallbackQuery, state: FSMContext) -> None:
    await ask_notification_time(callback, state, "morning")

@router.callback_query(F.data.startswith("settings:warning:"))
async def edit_warning_days(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    await state.set_state(MeetingSettings.warning_days)
    await state.update_data(warning_kind=kind)
    await callback.message.answer(
        "فاصله‌های هشدار ددلاین تسک‌ها را با فاصله بنویسید؛ "
        "مثال: <code>۷، ۱، ۰</code> (۰ یعنی همان روز موعد). "
        "برای لغو همه: <code>خاموش</code>",
        reply_markup=back_button("nav:settings"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:shared_alerts")
async def shared_alert_settings_menu(callback: CallbackQuery) -> None:
    managers = {}
    for manager_id, name, _ in await db.shared_event_managers(callback.from_user.id):
        managers[manager_id] = name or str(manager_id)
    for manager_id, name, can_view, *_ in await db.shared_task_managers(callback.from_user.id):
        if can_view:
            managers[manager_id] = name or str(manager_id)
    if not managers:
        await callback.answer("هنوز موردی از مدیران با شما به اشتراک گذاشته نشده است.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for manager_id, name in managers.items():
        builder.button(text=f"🔔 {name}", callback_data=f"sharedalert:edit:{manager_id}")
    builder.button(text="⬅️ بازگشت", callback_data="nav:settings")
    builder.adjust(1)
    await callback.message.answer("هشدارهای مستقل کدام تسک منیجر را تنظیم می‌کنید؟",
                                  reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("sharedalert:edit:"))
async def edit_shared_alert_settings(callback: CallbackQuery, state: FSMContext) -> None:
    manager_id = int(callback.data.rsplit(":", 1)[1])
    allowed_share = (
        await db.task_share_permissions(manager_id, callback.from_user.id)
        or await db.event_share_permission(manager_id, callback.from_user.id)
    )
    if not allowed_share:
        await callback.answer("این اشتراک دیگر فعال نیست.", show_alert=True)
        return
    cfg = await db.shared_alert_settings(callback.from_user.id, manager_id)
    await state.set_state(MeetingSettings.shared_alerts)
    await state.update_data(shared_alert_manager_id=manager_id)
    await callback.message.answer(
        "تنظیمات را در یک خط بفرستید:\n"
        "<code>تسک: ۱,۳,۷ | ایونت: ۶۰,۱۴۴۰</code>\n"
        "اعداد تسک برحسب روز و اعداد ایونت برحسب دقیقه هستند. برای خاموش‌کردن همه بنویسید «خاموش».\n\n"
        f"مقدار فعلی: تسک {','.join(map(str, cfg['task_warning_days']))} روز، "
        f"ایونت {','.join(map(str, cfg['event_warning_minutes']))} دقیقه",
        reply_markup=back_button("nav:settings"),
    )
    await callback.answer()


@router.message(MeetingSettings.shared_alerts)
async def save_shared_alert_settings(message: Message, state: FSMContext) -> None:
    raw = normalize_digits(message.text or "").strip().lower()
    data = await state.get_data()
    if raw in {"خاموش", "off", "none"}:
        await db.update_shared_alert_settings(
            message.from_user.id, int(data["shared_alert_manager_id"]), [], [], False
        )
    else:
        task_match = re.search(r"تسک\s*[:：]?\s*([^|]+)", raw)
        event_match = re.search(r"ایونت\s*[:：]?\s*(.+)", raw)
        try:
            task_days = [int(x) for x in re.findall(r"\d+", task_match.group(1))] if task_match else []
            event_minutes = [int(x) for x in re.findall(r"\d+", event_match.group(1))] if event_match else []
            if not task_days and not event_minutes:
                raise ValueError
            if any(x < 0 or x > 365 for x in task_days) or any(x < 5 or x > 10080 for x in event_minutes):
                raise ValueError
        except ValueError:
            await message.answer("قالب نامعتبر است؛ نمونه: <code>تسک: ۱,۳ | ایونت: ۶۰,۱۴۴۰</code>")
            return
        await db.update_shared_alert_settings(
            message.from_user.id, int(data["shared_alert_manager_id"]),
            task_days, event_minutes, True,
        )
    await state.clear()
    await message.answer("✅ هشدارهای مستقل موارد اشتراکی ذخیره شد.")
    await show_meeting_settings(message, message.from_user.id)

@router.message(MeetingSettings.warning_days)
async def save_warning_days(message: Message, state: FSMContext) -> None:
    data = await state.get_data(); raw = normalize_digits(message.text or "").strip()
    if raw in {"خاموش", "off", "none"}:
        days = []
    else:
        try:
            days = [int(x) for x in re.findall(r"\d+", raw)]
        except ValueError:
            days = []
        if not days or any(x < 0 or x > 365 for x in days):
            await message.answer("فاصله‌ها را به شکل عددی بین ۰ تا ۳۶۵ بفرستید؛ مثال: ۷، ۲، ۰")
            return
    await db.update_warning_days(message.from_user.id, data["warning_kind"], days)
    await state.clear(); await message.answer("✅ موعدهای هشدار ذخیره شد."); await show_meeting_settings(message, message.from_user.id)


async def save_notification_time(message: Message, state: FSMContext, kind: str) -> None:
    if await reject_if_needed(message):
        return
    raw = normalize_digits(message.text or "").strip()
    match = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", raw)
    if not match:
        await message.answer("ساعت را به شکل <code>22:00</code> یا <code>9</code> بفرستید.")
        return
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await message.answer("ساعت معتبر نیست؛ ساعت باید بین ۰ تا ۲۳ و دقیقه بین ۰ تا ۵۹ باشد.")
        return
    prefix = "digest" if kind == "digest" else "morning"
    await db.update_user_setting(message.from_user.id, f"{prefix}_hour", hour)
    await db.update_user_setting(message.from_user.id, f"{prefix}_minute", minute)
    await db.update_user_setting(message.from_user.id, f"{prefix}_enabled", 1)
    await state.clear()
    label = "جلسات فردا" if kind == "digest" else "جلسات امروز"
    await message.answer(f"✅ اعلان {label} هر روز ساعت <b>{format_clock(hour, minute)}</b> ارسال می‌شود.")
    await show_meeting_settings(message, message.from_user.id)


@router.message(MeetingSettings.digest_time)
async def save_digest_time(message: Message, state: FSMContext) -> None:
    await save_notification_time(message, state, "digest")


@router.message(MeetingSettings.morning_time)
async def save_morning_time(message: Message, state: FSMContext) -> None:
    await save_notification_time(message, state, "morning")


@router.callback_query(F.data.in_({"settings:digest:toggle", "settings:morning:toggle"}))
async def toggle_meeting_notification(callback: CallbackQuery) -> None:
    if await reject_if_needed(callback):
        return
    kind = "digest" if ":digest:" in callback.data else "morning"
    key = f"{kind}_enabled"
    user_config = await db.user_settings(callback.from_user.id, notification_setting_defaults())
    enabled = 0 if user_config[key] else 1
    await db.update_user_setting(callback.from_user.id, key, enabled)
    label = "جلسات فردا" if kind == "digest" else "جلسات امروز"
    await callback.answer(f"اعلان {label} {'فعال' if enabled else 'غیرفعال'} شد.")
    await show_meeting_settings(callback.message, callback.from_user.id)

@router.callback_query(F.data == "settings:name:edit")
async def edit_manager_display_name(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    current = await db.user_display_name(callback.from_user.id)
    await state.set_state(ProfileSetup.edit_manager_name)
    await callback.message.answer(
        f"نام نمایشی فعلی: <b>{html.escape(current or 'ثبت نشده')}</b>\n\n"
        "نام جدیدی را که باید در دکمه «جلسات نام مدیر» دیده شود بفرستید.",
        reply_markup=back_button("nav:settings"),
    )
    await callback.answer()


@router.message(ProfileSetup.edit_manager_name)
async def save_edited_manager_display_name(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    name = " ".join((message.text or "").split()).strip()
    if len(name) < 2:
        await message.answer("نام را واضح‌تر بفرستید؛ مثلاً «رضا».")
        return
    if not await db.set_user_display_name(message.from_user.id, name):
        await message.answer("ثبت نام انجام نشد؛ دوباره تلاش کنید.")
        return
    await state.clear()
    await message.answer(
        f"✅ نام نمایشی به «{html.escape(name[:80])}» تغییر کرد.\n"
        "این نام از این پس در منوی نیروها نمایش داده می‌شود."
    )
    await show_meeting_settings(message, message.from_user.id)

@router.callback_query(F.data == "settings:quiet:add")
async def add_quiet_range(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MeetingSettings.quiet_range)
    await callback.message.answer("\u0628\u0627\u0632\u0647 \u0645\u0645\u0646\u0648\u0639\u0647 \u0631\u0627 \u0628\u0641\u0631\u0633\u062a\u06cc\u062f\u061b \u0645\u062b\u0644\u0627\u064b: <code>14 \u062a\u0627 15</code> \u06cc\u0627 <code>12:30 - 13:30</code>")
    await callback.answer()


@router.message(MeetingSettings.quiet_range)
async def save_quiet_range(message: Message, state: FSMContext) -> None:
    raw = normalize_digits(message.text or "")
    found = re.findall(r"(\d{1,2})(?::(\d{2}))?", raw)
    if len(found) < 2:
        await message.answer("\u0628\u0627\u0632\u0647 \u0631\u0627 \u0645\u062b\u0644 \u00ab14 \u062a\u0627 15\u00bb \u0628\u0641\u0631\u0633\u062a\u06cc\u062f."); return
    values=[int(h)*60+int(m or 0) for h,m in found[:2]]
    if not (0 <= values[0] < values[1] <= 1440):
        await message.answer("\u0632\u0645\u0627\u0646 \u067e\u0627\u06cc\u0627\u0646 \u0628\u0627\u06cc\u062f \u0628\u0639\u062f \u0627\u0632 \u0632\u0645\u0627\u0646 \u0634\u0631\u0648\u0639 \u0628\u0627\u0634\u062f."); return
    await db.add_meeting_quiet_hour(message.from_user.id, *values); await state.clear()
    await message.answer("\u2705 \u0628\u0627\u0632\u0647 \u0645\u0645\u0646\u0648\u0639\u0647 \u062b\u0628\u062a \u0634\u062f.")
    await show_meeting_settings(message, message.from_user.id)


@router.callback_query(F.data.startswith("settings:quiet:del:"))
async def delete_quiet_range(callback: CallbackQuery) -> None:
    await db.delete_meeting_quiet_hour(int(callback.data.rsplit(":",1)[1]), callback.from_user.id)
    await callback.message.delete(); await show_meeting_settings(callback.message, callback.from_user.id); await callback.answer("\u062d\u0630\u0641 \u0634\u062f")


@router.message(StateFilter(None), F.text | F.caption)
async def route_human_commands(message: Message, state: FSMContext) -> None:
    command = normalize_user_command(message.text or message.caption or "")
    aliases = {
        "🔔 یادآورها": "reminders",
        "یادآورها": "reminders", "یادآوری ها": "reminders", "یادآوری": "reminders",
        "تسک ها": "tasks", "لیست کارها": "tasks",
        "جلسه های فردا": "tomorrow", "جلسه‌ها فردا": "tomorrow",
        "مدیریت دسترسی": "access", "مدیریت ادمین ها": "access",
        "راهنما": "help",
    }
    intent = aliases.get(command)
    if intent == "tasks":
        await list_tasks(message)
    elif intent == "tomorrow":
        await tomorrow_meetings(message)
    elif intent == "access":
        await access_management(message)
    elif intent == "help":
        await help_message(message)
    elif intent == "reminders":
        reminders = await db.reminders(message.from_user.id)
        if not reminders:
            await message.answer("یادآور فعالی برای نمایش ندارید.", reply_markup=reminders_menu())
        else:
            for index, reminder in enumerate(reminders, 1):
                await message.answer(
                    reminder_text(reminder, index),
                    reply_markup=reminder_actions(reminder.id),
                )
            await message.answer("برای ثبت یادآور جدید:", reply_markup=add_reminder_button())
    else:
        intent = _creation_intent_hint(command)
        if intent is None and ai_service.available:
            intent = await ai_service.classify_creation_intent(command)
        if intent == "task":
            await start_task_from_text(message, state, command)
        else:
            await meeting_message(message, state)


@router.message(F.voice)
async def route_voice(message: Message, state: FSMContext) -> None:
    if await reject_if_needed(message):
        return
    if not ai_service.available:
        await message.answer("کلید API هوش مصنوعی تنظیم نشده است.")
        return
    voice = await message.bot.get_file(message.voice.file_id)
    from io import BytesIO
    audio = BytesIO()
    await message.bot.download_file(voice.file_path, audio)
    transcript = await ai_service.transcribe_voice(audio.getvalue())
    if not transcript:
        await message.answer("نتوانستم ویس را به متن تبدیل کنم؛ دوباره ارسال کنید.")
        return
    await message.answer(f"📝 متن ویس:\n{clean_transcript(transcript)}")
    await route_human_commands(
        type("VoiceMessage", (), {"text": transcript, "caption": None,
                                  "from_user": message.from_user, "answer": message.answer})(),
        state,
    )


async def meeting_message(message: Message, state: FSMContext) -> None:
    state_data = await state.get_data()
    owner_id = int(state_data.get("meeting_owner_id") or message.from_user.id)
    if owner_id == message.from_user.id:
        if await reject_if_needed(message):
            return
    elif await db.event_share_permission(owner_id, message.from_user.id) != "edit":
        await state.clear()
        await message.answer("اجازه افزودن ایونت به این تقویم را ندارید.")
        return
    source_text = message.text or message.caption or ""
    parse_now = datetime.now(settings.timezone)
    parsed = None
    if ai_service.available:
        ai_parsed = await ai_service.parse_meeting(
            source_text, settings.timezone, parse_now
        )
        if ai_parsed:
            # AI returns structured date/time fields, but normalize its title
            # once more so a date/time fragment can never leak into the event
            # subject if the model echoes part of the source text.
            parsed = ParsedMeeting(
                summarize_meeting_title(ai_parsed.title), ai_parsed.starts_at, ai_parsed.ends_at
            )
            # Relative Persian dates are unambiguous and must not depend on
            # probabilistic model date arithmetic.
            parsed = enforce_explicit_relative_day(
                parsed, source_text, settings.timezone, parse_now
            )
            parsed = enforce_explicit_calendar_date(
                parsed, source_text, settings.timezone, parse_now
            )
    if parsed is None:
        parsed = parse_forwarded_meeting(source_text, settings.timezone, parse_now)
    if not parsed:
        await state.update_data(meeting_source=source_text)
        await state.set_state(NewMeeting.details)
        await message.answer("برای ثبت ایونت، تاریخ جلسه را بفرستید؛ ساعت شروع را هم اگر می‌دانید در همان پیام بنویسید.")
        return
    # Never silently invent a time.  Date-only announcements are common; ask
    # for the missing start time and parse it together with the original text.
    has_explicit_time = bool(re.search(r"(?:ساعت\s*)?\d{1,2}(?::\d{2})|(?:ساعت|از ساعت)\s*\d{1,2}", source_text))
    if not has_explicit_time:
        duration = parsed.ends_at - parsed.starts_at
        if duration <= timedelta(0) or duration > timedelta(hours=4): duration = timedelta(hours=1)
        slots = await free_meeting_slots(owner_id, parsed.starts_at.date(), duration)
        await state.update_data(meeting_source=source_text, meeting_draft=summarize_meeting_title(source_text), meeting_date=parsed.starts_at.date().isoformat(), meeting_duration=int(duration.total_seconds()))
        await state.set_state(NewMeeting.time)
        if slots:
            builder = InlineKeyboardBuilder()
            for slot in slots: builder.button(text=slot.strftime("%H:%M"), callback_data=f"meeting:suggest:{int(slot.timestamp())}")
            builder.adjust(1)
            await message.answer(
                "برای انتخاب زمان پیشنهادی، یکی از ساعت‌های آزاد را انتخاب کنید:",
                reply_markup=builder.as_markup(),
            )
        else:
            await message.answer(
                "برای این روز ساعت آزاد مناسبی پیدا نشد. لطفاً ساعت را به‌صورت دستی بفرستید."
            )
        return
    if not parsed.title or parsed.title.strip().lower() in {"جلسه", "meeting", "event", "رویداد"}:
        await state.update_data(meeting_source=source_text, meeting_starts_at=parsed.starts_at.isoformat(),
                                meeting_ends_at=parsed.ends_at.isoformat())
        await state.set_state(NewMeeting.title)
        await message.answer("موضوع ایونت چیست؟")
        return
    await state.set_state(NewMeeting.confirm)
    # Accept recurrence rules already present in the forwarded announcement
    # (for example: «یکشنبه‌ها و سه‌شنبه‌ها») instead of forcing the user
    # through the recurrence menu a second time.
    detected_recurrence = None
    try:
        from app.recurrence import parse_recurrence_text
        detected_recurrence = parse_recurrence_text(
            source_text, parsed.starts_at, settings.timezone
        )
    except (TypeError, ValueError):
        pass
    await state.update_data(
        meeting_title=parsed.title, starts_at=parsed.starts_at.isoformat(),
        ends_at=parsed.ends_at.isoformat(), source_text=source_text,
        **({"meeting_recurrence": detected_recurrence,
            "meeting_recurrence_prompted": True} if detected_recurrence else {}),
    )
    local = await schedule_warnings(owner_id, parsed.starts_at, parsed.ends_at)
    if local:
        builder = InlineKeyboardBuilder()
        builder.button(text="🕐 جابه‌جایی جلسه جدید", callback_data="meeting:move:new")
        builder.button(text="⚠️ ثبت با وجود هشدار", callback_data="meeting:confirm")
        builder.button(text="❌ لغو", callback_data="meeting:cancel")
        builder.adjust(1)
        await message.answer(
            warning_text(local) +
            f"\n\nجلسه جدید: <b>{html.escape(parsed.title)}</b>\n"
            f"شروع: {fmt_dt(parsed.starts_at)}\nپایان: {fmt_dt(parsed.ends_at)}\n\n"
            "جلسه جدید را جابه‌جا کنید یا با وجود تداخل ثبت کنید.",
            reply_markup=builder.as_markup(),
        )
    else:
        await message.answer(
            f"این جلسه را ثبت کنم؟\n\n<b>{html.escape(parsed.title)}</b>\n"
            f"شروع: {fmt_dt(parsed.starts_at)}\nپایان: {fmt_dt(parsed.ends_at)}",
            reply_markup=meeting_confirmation(),
        )


@router.message(NewMeeting.details)
async def meeting_missing_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    source = f"{data.get('meeting_source', '')}\n{message.text or ''}"
    parsed = parse_forwarded_meeting(source, settings.timezone)
    if not parsed:
        await message.answer("هنوز تاریخ را تشخیص ندادم. تاریخ را واضح‌تر بنویسید؛ مثلاً «۳ شهریور ۱۴۰۵ ساعت ۱۰».")
        return
    if not re.search(r"(?:ساعت\s*)?\d{1,2}(?::\d{2})|(?:ساعت|از ساعت)\s*\d{1,2}", source):
        await state.update_data(meeting_source=source, meeting_draft=summarize_meeting_title(source))
        await state.set_state(NewMeeting.time)
        await message.answer("ساعت شروع ایونت را هم بفرستید؛ مثلاً «۱۰:۳۰».")
        return
    owner_id = data.get("meeting_owner_id")
    await state.clear()
    if owner_id:
        await state.update_data(meeting_owner_id=owner_id)
    await meeting_message(message.model_copy(update={"text": source}), state)


@router.callback_query(NewMeeting.time, F.data.startswith("meeting:suggest:"))
async def choose_suggested_meeting_time(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    starts_at = datetime.fromtimestamp(int(callback.data.rsplit(":", 1)[1]), settings.timezone)
    ends_at = starts_at + timedelta(seconds=int(data.get("meeting_duration") or 3600))
    title = data.get("meeting_draft") or "جلسه"
    await state.update_data(meeting_title=title, starts_at=starts_at.isoformat(), ends_at=ends_at.isoformat(), source_text=data.get("meeting_source", ""))
    await state.set_state(NewMeeting.confirm)
    await callback.message.edit_text(
        f"این ایونت را ثبت کنم؟\n\n<b>{html.escape(title)}</b>\n"
        f"شروع: {fmt_dt(starts_at)}\nپایان: {fmt_dt(ends_at)}",
        reply_markup=meeting_confirmation(),
    )
    await callback.answer()


@router.message(NewMeeting.time)
async def meeting_missing_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw_time = (message.text or "").strip()
    time_match = re.search(r"(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))?", raw_time.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    if not time_match:
        await message.answer("ساعت را تشخیص ندادم؛ مثلاً «۱۰:۳۰» یا «ساعت ۱۴» بفرستید.")
        return
    hour, minute = int(time_match.group(1)), int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        await message.answer("ساعت معتبر نیست؛ مثلاً «۱۰:۳۰» یا «ساعت ۱۴» بفرستید.")
        return
    try:
        starts_at = datetime.fromisoformat(data["meeting_date"]).replace(
            hour=hour, minute=minute, second=0, microsecond=0, tzinfo=settings.timezone
        )
    except (KeyError, TypeError, ValueError):
        source = f"{data.get('meeting_source', '')}\nساعت {raw_time}"
        parsed = parse_forwarded_meeting(source, settings.timezone)
        if not parsed:
            await message.answer("تاریخ جلسه از اطلاعات قبلی قابل بازیابی نیست؛ لطفاً تاریخ و ساعت را یکجا بفرستید.")
            return
        starts_at = parsed.starts_at
    ends_at = starts_at + timedelta(hours=1)
    title = data.get("meeting_draft") or "جلسه"
    await state.update_data(meeting_title=title, starts_at=starts_at.isoformat(),
                            ends_at=ends_at.isoformat(), source_text=data.get("meeting_source", ""))
    await state.set_state(NewMeeting.confirm)
    await message.answer(
        f"این ایونت را ثبت کنم؟\n\n<b>{html.escape(title)}</b>\n"
        f"شروع: {fmt_dt(starts_at)}\nپایان: {fmt_dt(ends_at)}",
        reply_markup=meeting_confirmation(),
    )


@router.message(NewMeeting.title)
async def meeting_missing_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = summarize_meeting_title(message.text or "")
    if not title or title == "جلسه":
        await message.answer("موضوع ایونت را واضح‌تر بنویسید.")
        return
    source = data.get("meeting_source", "")
    starts_at = datetime.fromisoformat(data["meeting_starts_at"])
    ends_at = datetime.fromisoformat(data["meeting_ends_at"])
    await state.update_data(meeting_title=title, starts_at=starts_at.isoformat(),
                            ends_at=ends_at.isoformat(), source_text=source)
    await state.set_state(NewMeeting.confirm)
    await message.answer(
        f"این ایونت را ثبت کنم؟\n\n<b>{html.escape(title)}</b>\n"
        f"شروع: {fmt_dt(starts_at)}\nپایان: {fmt_dt(ends_at)}",
        reply_markup=meeting_confirmation(),
    )


async def _save_confirmed_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    starts_at = datetime.fromisoformat(data["starts_at"])
    ends_at = datetime.fromisoformat(data["ends_at"])
    owner_id = int(data.get("meeting_owner_id") or callback.from_user.id)
    if owner_id != callback.from_user.id and await db.event_share_permission(owner_id, callback.from_user.id) != "edit":
        await state.clear()
        await callback.answer("اجازه ویرایش این تقویم را ندارید.", show_alert=True)
        return
    recurrence = data.get("meeting_recurrence")
    if recurrence and recurrence.get("frequency"):
        _, meeting_id = await db.create_meeting_recurrence(
            owner_id, data["meeting_title"], starts_at, ends_at,
            data.get("source_text", ""), recurrence,
        )
        label = f"جلسه #{meeting_id} با تکرار فعال ثبت شد."
    else:
        meeting_id = await db.add_meeting(
            owner_id, data["meeting_title"], starts_at, ends_at, data.get("source_text", "")
        )
        label = f"جلسه #{meeting_id} بدون تکرار ثبت شد."
    await state.clear()
    await callback.message.edit_text("✅ " + label)
    await callback.answer()


async def _save_meeting_from_message(message: Message, state: FSMContext) -> None:
    """ثبت مستقیم ایونت بعد از واردکردن تکرار سفارشی."""
    data = await state.get_data()
    starts_at = datetime.fromisoformat(data["starts_at"])
    ends_at = datetime.fromisoformat(data["ends_at"])
    owner_id = int(data.get("meeting_owner_id") or message.from_user.id)
    recurrence = data.get("meeting_recurrence")
    if recurrence and recurrence.get("frequency"):
        _, meeting_id = await db.create_meeting_recurrence(
            owner_id, data["meeting_title"], starts_at, ends_at,
            data.get("source_text", ""), recurrence,
        )
        label = f"جلسه #{meeting_id} با تکرار فعال ثبت شد."
    else:
        meeting_id = await db.add_meeting(
            owner_id, data["meeting_title"], starts_at, ends_at, data.get("source_text", "")
        )
        label = f"جلسه #{meeting_id} بدون تکرار ثبت شد."
    await state.clear()
    await message.answer("✅ " + label, reply_markup=main_menu())


@router.callback_query(NewMeeting.confirm, F.data == "meeting:repeat")
async def choose_meeting_recurrence(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "نوع تکرار ایونت را انتخاب کنید:",
        reply_markup=meeting_recurrence_options(),
    )
    await callback.answer()


@router.callback_query(NewMeeting.confirm, F.data.startswith("meeting:repeat:"))
async def set_meeting_recurrence(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    if kind == "back":
        await callback.answer()
        return
    data = await state.get_data()
    starts_at = datetime.fromisoformat(data["starts_at"])
    if kind == "none":
        recurrence = None
    elif kind == "weekly":
        recurrence = {"frequency": "weekly", "interval": 1, "weekdays": [starts_at.weekday()]}
    elif kind in {"even_days", "odd_days"}:
        recurrence = {"frequency": kind, "interval": 1, "weekdays": []}
    elif kind == "custom":
        await state.set_state(NewMeeting.recurrence)
        await callback.message.answer(
            "روزهای هفته را بنویسید؛ مثلاً «هر هفته یکشنبه و سه‌شنبه» یا «هر هفته دوشنبه، چهارشنبه و جمعه»."
        )
        await callback.answer()
        return
    else:
        await callback.answer("نوع تکرار نامعتبر است.", show_alert=True)
        return
    await state.update_data(meeting_recurrence=recurrence)
    # انتخاب تکرار، آخرین مرحلهٔ ثبت ایونت است؛ تأیید جداگانه لازم نیست.
    await _save_confirmed_meeting(callback, state)


@router.message(NewMeeting.recurrence)
async def save_meeting_recurrence_text(message: Message, state: FSMContext) -> None:
    from app.recurrence import parse_recurrence_text
    data = await state.get_data()
    anchor = datetime.fromisoformat(data["starts_at"])
    try:
        recurrence = parse_recurrence_text(message.text or "", anchor, settings.timezone)
        if recurrence["frequency"] != "weekly" or recurrence.get("weekdays"):
            if data.get("recurrence_edit_series_id"):
                ok = await db.update_meeting_recurrence(
                    data["recurrence_edit_series_id"],
                    int(data.get("recurrence_edit_user_id") or message.from_user.id),
                    recurrence,
                )
                await state.clear()
                await message.answer(
                    "✅ تکرار ایونت ویرایش شد." if ok else "تکرار ایونت پیدا نشد.",
                    reply_markup=back_to_menu(),
                )
                return
            await state.update_data(meeting_recurrence=recurrence)
            await _save_meeting_from_message(message, state)
            return
    except (TypeError, ValueError):
        pass
    await message.answer(
        "روزهای هفته را واضح بنویسید؛ مثلاً «هر هفته یکشنبه و سه‌شنبه».",
        reply_markup=meeting_recurrence_options(),
    )


@router.callback_query(F.data.startswith("meeting:move:"))
async def ask_move_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    target = callback.data.rsplit(":", 1)[1]
    if target == "new":
        data = await state.get_data()
        if "starts_at" not in data:
            await callback.answer("اطلاعات جلسه جدید منقضی شده است.", show_alert=True)
            return
        starts_at = datetime.fromisoformat(data["starts_at"])
        ends_at = datetime.fromisoformat(data["ends_at"])
        await state.update_data(move_target="new", move_duration=int((ends_at - starts_at).total_seconds()))
        label = data.get("meeting_title", "جلسه جدید")
    else:
        meeting = await db.meeting(int(target), callback.from_user.id)
        if not meeting:
            await callback.answer("جلسه پیدا نشد.", show_alert=True)
            return
        await state.update_data(
            move_target=target, move_duration=int((meeting.ends_at - meeting.starts_at).total_seconds())
        )
        label = meeting.title
    await state.set_state(MoveMeeting.new_time)
    await callback.message.answer(
        f"زمان شروع جدید «{html.escape(label)}» را بفرستید؛ مثال:\n"
        "<code>۲۰ مهر ۱۴۰۵ ساعت ۱۴:۳۰</code> یا <code>پس‌فردا ساعت ۱۰</code>"
    )
    await callback.answer()


@router.message(MoveMeeting.new_time)
async def save_moved_meeting(message: Message, state: FSMContext) -> None:
    new_start = parse_datetime(message.text or "", settings.timezone)
    if not new_start:
        await message.answer("تاریخ و ساعت جدید را متوجه نشدم. دوباره بفرستید یا /cancel را بزنید.")
        return
    data = await state.get_data()
    owner_id = int(data.get("meeting_owner_id") or message.from_user.id)
    if owner_id != message.from_user.id and await db.event_share_permission(owner_id, message.from_user.id) != "edit":
        await state.clear()
        await message.answer("اجازه ویرایش این تقویم را ندارید.")
        return
    new_end = new_start + timedelta(seconds=data["move_duration"])
    target = data["move_target"]
    exclude_id = None if target == "new" else int(target)
    local = await schedule_warnings(owner_id, new_start, new_end, exclude_id)
    if local:
        await message.answer(
            warning_text(local) +
            "\n\nاین زمان هم مناسب نیست. یک زمان دیگر بفرستید یا /cancel را بزنید."
        )
        return
    if target == "new":
        await state.update_data(starts_at=new_start.isoformat(), ends_at=new_end.isoformat())
        await state.set_state(NewMeeting.confirm)
        await message.answer(
            f"زمان جدید بدون تداخل است. جلسه را ثبت کنم؟\n\n"
            f"<b>{html.escape(data['meeting_title'])}</b>\nشروع: {fmt_dt(new_start)}\nپایان: {fmt_dt(new_end)}",
            reply_markup=meeting_confirmation(),
        )
        return
    meeting = await db.meeting(int(target), owner_id)
    if not meeting:
        await state.clear()
        await message.answer("جلسه پیدا نشد.", reply_markup=main_menu())
        return
    await db.move_meeting(meeting.id, owner_id, new_start, new_end)
    await state.clear()
    await message.answer(
        f"✅ جلسه «{html.escape(meeting.title)}» جابه‌جا شد.\n"
        f"شروع جدید: {fmt_dt(new_start)}\nپایان جدید: {fmt_dt(new_end)}",
        reply_markup=main_menu(),
    )


@router.callback_query(NewMeeting.confirm, F.data == "meeting:cancel")
async def cancel_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("ثبت جلسه لغو شد.")
    await callback.answer()


@router.callback_query(NewMeeting.confirm, F.data == "meeting:confirm")
async def confirm_meeting(callback: CallbackQuery, state: FSMContext) -> None:
    if await reject_if_needed(callback):
        return
    data = await state.get_data()
    if not data.get("meeting_recurrence_prompted"):
        await state.update_data(meeting_recurrence_prompted=True)
        await callback.message.answer(
            "آیا این ایونت تکرار شود؟ می‌توانید تکرار هفتگی، روزهای زوج/فرد یا چند روز مشخص هفته را انتخاب کنید.",
            reply_markup=meeting_recurrence_options(),
        )
        await callback.answer()
        return
    await _save_confirmed_meeting(callback, state)


@router.message()
async def fallback(message: Message) -> None:
    if await reject_if_needed(message):
        return
    await message.answer("از منوی زیر انتخاب کنید، یا پیام جلسه را برایم فوروارد کنید.", reply_markup=main_menu())


async def send_daily_digest(bot: Bot) -> None:
    for user_id in await db.authorized_user_ids():
        try:
            await bot.send_message(user_id, await tomorrow_text(user_id))
        except Exception:
            logging.exception("Could not send daily digest to %s", user_id)


async def send_morning_schedule(bot: Bot) -> None:
    today = datetime.now(settings.timezone).date()
    for user_id in await db.authorized_user_ids():
        try:
            await bot.send_message(user_id, await user_day_schedule_text(user_id, today, "امروز"))
        except Exception:
            logging.exception("Could not send morning schedule to %s", user_id)


async def send_configurable_meeting_notifications(bot: Bot) -> None:
    now = datetime.now(settings.timezone).replace(second=0, microsecond=0)
    schedules = (
        ("digest", settings.digest_hour, settings.digest_minute),
        ("morning", settings.morning_hour, settings.morning_minute),
    )
    for kind, default_hour, default_minute in schedules:
        user_ids = await db.users_for_schedule(
            kind, now.hour, now.minute, default_hour, default_minute
        )
        for user_id in user_ids:
            if await db.notification_sent_today(user_id, kind, now.date()):
                continue
            try:
                if kind == "digest":
                    text = await tomorrow_text(user_id)
                else:
                    text = await user_day_schedule_text(user_id, now.date(), "امروز")
                await bot.send_message(user_id, text)
                await db.mark_notification_sent(user_id, kind, now.date())
            except Exception:
                logging.exception("Could not send %s meeting notification to %s", kind, user_id)

async def send_deadline_reminders(bot: Bot) -> None:
    now = datetime.now(settings.timezone)
    # Independent reminders fire at their own due time. They are never
    # synthesized from tasks and do not use task deadline warning settings.
    for reminder in await db.due_reminders(now):
        try:
            await bot.send_message(
                reminder.user_id,
                "🔔 یادآور شما فرا رسیده است.\n\n" + reminder_text(reminder),
                reply_markup=reminder_actions(reminder.id),
            )
            await db.mark_reminder_notified(reminder.id, now)
        except Exception:
            logging.exception("Could not send reminder for reminder %s", reminder.id)

    # Task deadline warnings are tracked independently for each configured
    # offset, so a task can notify at e.g. 7, 1 and 0 days before its deadline.
    for task, warning_days in await db.due_task_deadline_alerts(
        now, settings.deadline_warning_days
    ):
        try:
            if warning_days == 0:
                heading = "🚨 هشدار ددلاین تسک امروز است"
            else:
                heading = f"⏳ {fa_number(warning_days)} روز تا ددلاین تسک باقی مانده است"
            await bot.send_message(
                task.user_id,
                f"{heading}\n\n" + task_text(task, viewer_id=task.user_id),
                reply_markup=task_actions(task.id, task.status),
            )
            await db.mark_task_deadline_alert(task.id, warning_days, now)
        except Exception:
            logging.exception(
                "Could not send task deadline alert for task %s (%s days)",
                task.id, warning_days,
            )

    # Shared-item alerts are configured by each member and are independent
    # from the manager's own task/event notification settings.
    for member_id, manager_id, kind, item, threshold in await db.due_shared_alerts(now):
        try:
            manager_name = await db.user_display_name(manager_id) or str(manager_id)
            if kind == "task":
                item_id, title, due_at = item
                text = (
                    f"🔔 <b>هشدار تسک اشتراکی از {html.escape(manager_name)}</b>\n\n"
                    f"{html.escape(title)}\nددلاین: {fmt_dt(due_at)}"
                )
            else:
                item_id, title, starts_at, ends_at = item
                text = (
                    f"🔔 <b>هشدار ایونت اشتراکی از {html.escape(manager_name)}</b>\n\n"
                    f"{html.escape(title)}\nزمان: {fmt_dt(starts_at)} تا {fmt_dt(ends_at)}"
                )
            await bot.send_message(member_id, text, reply_markup=await member_reply_menu(member_id))
            await db.mark_shared_alert_sent(member_id, manager_id, kind, item_id, threshold, now)
        except Exception:
            logging.exception("Could not send shared alert to %s", member_id)


def _week_start(now: datetime) -> datetime:
    return (now - timedelta(days=(now.weekday() - 5) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)


async def send_weekly_management_report(bot: Bot) -> None:
    now = datetime.now(settings.timezone); start = _week_start(now); previous = start - timedelta(days=7)
    for user_id in await db.authorized_user_ids():
        try:
            current = await db.weekly_summary(user_id, start, now); old = await db.weekly_summary(user_id, previous, start)
            meetings = await db.meetings_between(user_id, start, now); old_meetings = await db.meetings_between(user_id, previous, start)
            attention = [(x["title"], x["progress"]) for x in current["stagnant"][:4]]
            grouped = {}
            for item in current["progressed"] + current["stagnant"]:
                grouped.setdefault(item.get("assignee") or "بدون مسئول", []).append(item["progress"])
            members = sorted(((n, round(sum(v)/len(v))) for n,v in grouped.items()), key=lambda x:x[1], reverse=True)
            image = weekly_management_card(current, old, len(meetings), len(old_meetings), attention, members)
            await bot.send_photo(user_id, BufferedInputFile(image, filename="weekly-management-report.png"), caption=(
                "<b>📊 گزارش خلاصه هفته</b>\n"
                f"✅ پیشرفت‌کرده: <b>{fa_number(len(current['progressed']))}</b>\n"
                f"⏸ بدون پیشرفت: <b>{fa_number(len(current['stagnant']))}</b>\n"
                f"🏁 تکمیل‌شده: <b>{fa_number(current['completed'])}</b>\n"
                f"📅 جلسات: <b>{fa_number(len(meetings))}</b>\n"
                f"📝 گزارش‌ها: <b>{fa_number(current['reports'])}</b>"))
            progressed = "\n".join(f"• {html.escape(x['title'])} — +{fa_number(x['gain'])}٪ (اکنون {fa_number(x['progress'])}٪)" for x in current["progressed"]) or "• موردی ثبت نشده است."
            stagnant = "\n".join(f"• {html.escape(x['title'])} — {fa_number(x['progress'])}٪" for x in current["stagnant"]) or "• موردی ثبت نشده است."
            await bot.send_message(user_id, f"<b>تسک‌های دارای پیشرفت</b>\n{progressed}\n\n<b>تسک‌های بدون پیشرفت</b>\n{stagnant}")
        except Exception:
            logging.exception("Could not send weekly management report to %s", user_id)


async def run() -> None:
    global settings, db, ai_service
    settings = load_settings()
    ai_service = AIService(settings)
    db = Database(settings.database_path)
    await db.init()
    await db.seed_owners(settings.allowed_user_ids)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        send_configurable_meeting_notifications,
        "interval",
        minutes=1,
        args=[bot],
        id="configurable_meeting_notifications",
        replace_existing=True,
        next_run_time=datetime.now(settings.timezone),
    )
    scheduler.add_job(send_deadline_reminders, "interval", minutes=30, args=[bot], next_run_time=datetime.now(settings.timezone))
    scheduler.add_job(send_weekly_management_report, "cron", day_of_week="fri", hour=12, minute=0,
                      args=[bot], id="weekly_management_report", replace_existing=True)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
