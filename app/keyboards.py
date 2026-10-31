from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👔 پنل مدیر")],
        [KeyboardButton(text="➕ تسک جدید"), KeyboardButton(text="📋 لیست تسک‌ها")],
        [KeyboardButton(text="📆 جلسات امروز"), KeyboardButton(text="📅 جلسات فردا")],
        [KeyboardButton(text="🗓 مدیریت جلسات")],
        [KeyboardButton(text="🔔 یادآورها")],
        [KeyboardButton(text="📊 داشبورد"), KeyboardButton(text="🤖 فرمان مدیریتی")],
        [KeyboardButton(text="🧑‍🤝‍🧑 چارت نیروها")],
        [KeyboardButton(text="⚙️ تنظیمات")],
        [KeyboardButton(text="🚀 مینی‌اپ")],
        [KeyboardButton(text="👥 مدیریت دسترسی"), KeyboardButton(text="❓ راهنما")],
    ], resize_keyboard=True)


def reminders_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 همه یادآورها", callback_data="reminders:all")],
        [InlineKeyboardButton(text="⏳ فیلتر بر اساس زمان", callback_data="reminders:filter")],
        [InlineKeyboardButton(text="➕ ثبت یادآور جدید", callback_data="reminder:new")],
        [InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main")],
    ])


def reminder_time_filters() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 ۳ روز آینده", callback_data="reminders:within:3")],
        [InlineKeyboardButton(text="📅 ۷ روز آینده", callback_data="reminders:within:7")],
        [InlineKeyboardButton(text="📅 ۲ هفته آینده", callback_data="reminders:within:14")],
        [InlineKeyboardButton(text="📅 یک ماه آینده", callback_data="reminders:within:30")],
        [InlineKeyboardButton(text="⬅️ بازگشت به یادآورها", callback_data="reminders:menu")],
    ])


def reminder_list_footer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ ثبت یادآور جدید", callback_data="reminder:new"),
    ], [
        InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main"),
    ]])


def reminder_actions(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ انجام دادم", callback_data=f"reminder:done:{reminder_id}")],
        [InlineKeyboardButton(text="✏️ ویرایش", callback_data=f"reminder:edit:{reminder_id}"),
         InlineKeyboardButton(text="🗑 حذف", callback_data=f"reminder:delete:{reminder_id}")],
        [InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main")],
    ])


def add_reminder_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ یادآور جدید", callback_data="reminder:new"),
    ]])


def reminder_repeat_options(prefix: str = "reminder:repeat", back_callback: str = "nav:reminders") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="یک‌بار مصرف", callback_data=f"{prefix}:once")],
        [InlineKeyboardButton(text="روزانه", callback_data=f"{prefix}:daily"),
         InlineKeyboardButton(text="روز درمیان", callback_data=f"{prefix}:every_other_day")],
        [InlineKeyboardButton(text="روزهای زوج", callback_data=f"{prefix}:even_days"),
         InlineKeyboardButton(text="روزهای فرد", callback_data=f"{prefix}:odd_days")],
        [InlineKeyboardButton(text="هفتگی", callback_data=f"{prefix}:weekly"),
         InlineKeyboardButton(text="ماهانه", callback_data=f"{prefix}:monthly")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back_callback)],
    ])


def reminder_weekdays(prefix: str = "reminder:weekday", back_callback: str = "nav:reminder_repeat") -> InlineKeyboardMarkup:
    names = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{i}") for i, name in enumerate(names)],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back_callback)],
    ])


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main"),
    ]])


def management_exit() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚪 خروج از فرمان مدیریتی",
            callback_data="menu:main",
        ),
    ]])


def back_button(callback_data: str = "menu:main") -> InlineKeyboardMarkup:
    """A consistent one-step inline back button for form prompts and sub-pages."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ بازگشت", callback_data=callback_data),
    ]])


def member_menu(manager_names: list[str] | None = None) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="📌 تسک‌های واگذارشده به من")]]
    rows.append([KeyboardButton(text="👔 پنل مدیر")])
    rows.extend(
        [KeyboardButton(text=f"تسک منیجر {name}")]
        for name in (manager_names or [])
        if name.strip()
    )
    rows.append([KeyboardButton(text="⚙️ تنظیمات")])
    rows.append([KeyboardButton(text="❓ راهنما")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def priorities(back_callback: str = "nav:task_deadline") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 فوری و مهم", callback_data="priority:1")],
        [InlineKeyboardButton(text="🟠 مهم، غیرفوری", callback_data="priority:2")],
        [InlineKeyboardButton(text="🟡 فوری، کم‌اهمیت", callback_data="priority:3")],
        [InlineKeyboardButton(text="🟢 غیرفوری و کم‌اهمیت", callback_data="priority:4")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back_callback)],
    ])


def meeting_duration_label(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60} ساعت"
    return f"{minutes} دقیقه"


def meeting_confirmation(
    duration_minutes: int = 60,
    recurrence_label: str = "بدون تکرار",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ثبت جلسه", callback_data="meeting:confirm"),
        InlineKeyboardButton(text="❌ لغو", callback_data="meeting:cancel"),
    ], [
        InlineKeyboardButton(
            text=f"⏱ طول جلسه: {meeting_duration_label(duration_minutes)}",
            callback_data="meeting:duration",
        ),
    ], [
        InlineKeyboardButton(
            text=f"🔁 تکرار: {recurrence_label}",
            callback_data="meeting:repeat",
        ),
    ]])


def meeting_duration_options(back_callback: str = "meeting:duration:back") -> InlineKeyboardMarkup:
    options = [(15, "۱۵ دقیقه"), (20, "۲۰ دقیقه"), (30, "۳۰ دقیقه"),
               (60, "۱ ساعت"), (120, "۲ ساعت"), (180, "۳ ساعت")]
    rows = [[InlineKeyboardButton(text=label, callback_data=f"meeting:duration:{minutes}")]
            for minutes, label in options]
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meeting_recurrence_options() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="بدون تکرار", callback_data="meeting:repeat:none")],
        [InlineKeyboardButton(text="هر هفته (همین روز)", callback_data="meeting:repeat:weekly")],
        [InlineKeyboardButton(text="روزهای زوج", callback_data="meeting:repeat:even_days"),
         InlineKeyboardButton(text="روزهای فرد", callback_data="meeting:repeat:odd_days")],
        [InlineKeyboardButton(text="چند روز مشخص هفته", callback_data="meeting:repeat:custom")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="meeting:repeat:back")],
    ])


def meeting_range_options() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 جلسات امروز", callback_data="meetings:range:today")],
        [InlineKeyboardButton(text="📅 جلسات فردا", callback_data="meetings:range:tomorrow")],
        [InlineKeyboardButton(text="تا آخر هفته", callback_data="meetings:range:week_end")],
        [InlineKeyboardButton(text="هفته آینده", callback_data="meetings:range:next_week")],
        [InlineKeyboardButton(text="همه جلسات", callback_data="meetings:range:all")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="menu:main")],
    ])


def task_actions(
    task_id: int, status: str = "open", *, page_key: str | None = None,
    page: int = 0, total: int = 1,
) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="📈 ثبت پیشرفت", callback_data=f"task:progress:{task_id}"),
        InlineKeyboardButton(text="📝 توضیح", callback_data=f"task:note:{task_id}"),
    ]]
    if status != "done":
        rows.append([InlineKeyboardButton(text="✅ انجام شد", callback_data=f"task:done:{task_id}")])
        status_buttons = []
        if status != "open":
            status_buttons.append(InlineKeyboardButton(text="▶️ ادامه تسک", callback_data=f"task:status:open:{task_id}"))
        if status != "paused":
            status_buttons.append(InlineKeyboardButton(text="⏸ توقف", callback_data=f"task:status:paused:{task_id}"))
        if status != "suspended":
            status_buttons.append(InlineKeyboardButton(text="⏳ تعلیق", callback_data=f"task:status:suspended:{task_id}"))
        rows.append(status_buttons)
    rows.append([InlineKeyboardButton(text="🗂 سابقه گزارش‌ها", callback_data=f"task:reports:{task_id}")])
    rows.append([InlineKeyboardButton(text="📉 نمودار پیشرفت", callback_data=f"task:chart:{task_id}")])
    rows.append([InlineKeyboardButton(text="📨 مدیریت رونوشت", callback_data=f"task:cc:menu:{task_id}")])
    if page_key is not None and total > 1:
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton(
                text="➡️ تسک قبلی", callback_data=f"taskpage:{page_key}:{page - 1}",
            ))
        if page + 1 < total:
            navigation.append(InlineKeyboardButton(
                text="تسک بعدی ⬅️", callback_data=f"taskpage:{page_key}:{page + 1}",
            ))
        rows.append(navigation)
    if page_key is not None:
        rows.append([InlineKeyboardButton(
            text="📤 ارسال همه تسک‌ها", callback_data=f"tasksendall:{page_key}",
        )])
        rows.append([InlineKeyboardButton(text="⬅️ بازگشت به فیلترها", callback_data="tasklist:menu")])
    rows.append([InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_list_filters() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 همه تسک‌ها", callback_data="tasklist:all")],
        [InlineKeyboardButton(text="🎯 فیلتر ماتریس آیزنهاور", callback_data="tasklist:priority")],
        [InlineKeyboardButton(text="⏰ فیلتر بر اساس ددلاین", callback_data="tasklist:deadline")],
        [InlineKeyboardButton(text="📈 فیلتر بر اساس میزان پیشرفت", callback_data="tasklist:progress")],
        [InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="menu:main")],
    ])


def task_priority_filters() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 فوری و مهم", callback_data="tasklist:priority:1")],
        [InlineKeyboardButton(text="🟠 مهم، غیرفوری", callback_data="tasklist:priority:2")],
        [InlineKeyboardButton(text="🟡 فوری، کم‌اهمیت", callback_data="tasklist:priority:3")],
        [InlineKeyboardButton(text="🟢 غیرفوری و کم‌اهمیت", callback_data="tasklist:priority:4")],
        [InlineKeyboardButton(text="⬅️ بازگشت به فیلترها", callback_data="tasklist:menu")],
    ])


def task_deadline_filters() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 ۷ روز آینده", callback_data="tasklist:deadline:7")],
        [InlineKeyboardButton(text="📅 ۲ هفته آینده", callback_data="tasklist:deadline:14")],
        [InlineKeyboardButton(text="📅 یک ماه آینده", callback_data="tasklist:deadline:30")],
        [InlineKeyboardButton(text="⬅️ بازگشت به فیلترها", callback_data="tasklist:menu")],
    ])


def task_progress_filters() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="۰٪ پیشرفت", callback_data="tasklist:progress:zero")],
        [InlineKeyboardButton(text="زیر ۳۰٪ پیشرفت", callback_data="tasklist:progress:lt30")],
        [InlineKeyboardButton(text="زیر ۵۰٪ پیشرفت", callback_data="tasklist:progress:lt50")],
        [InlineKeyboardButton(text="زیر ۸۰٪ پیشرفت", callback_data="tasklist:progress:lt80")],
        [InlineKeyboardButton(text="بین ۸۰ تا ۹۹٪", callback_data="tasklist:progress:80_99")],
        [InlineKeyboardButton(text="✅ تسک‌های تکمیل‌شده", callback_data="tasklist:progress:done")],
        [InlineKeyboardButton(text="⬅️ بازگشت به فیلترها", callback_data="tasklist:menu")],
    ])
