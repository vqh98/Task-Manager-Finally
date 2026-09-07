from __future__ import annotations

from io import BytesIO
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
import arabic_reshaper
from bidi.algorithm import get_display

from app.models import Task


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(text: object) -> str:
    """Shape Persian text for renderers without native RTL support."""
    compatible = str(text).translate(PERSIAN_DIGITS)
    return get_display(arabic_reshaper.reshape(compatible))


def _percent(value: object) -> str:
    # Put the sign before the logical number so bidi rendering displays «٪۶۸».
    return _fa(f"٪{value}")


def _font() -> font_manager.FontProperties:
    candidates = (
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts/B Zar.ttf",
        Path("C:/Windows/Fonts/B Zar.ttf"),
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    return font_manager.FontProperties(fname=str(path)) if path else font_manager.FontProperties()


def _number_font() -> font_manager.FontProperties:
    candidates = (
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    return font_manager.FontProperties(fname=str(path)) if path else font_manager.FontProperties()


def progress_chart(task: Task, history: list[tuple[datetime, int, int]], now: datetime) -> bytes:
    start = task.created_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=now.tzinfo)
    deadline = task.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    normalized_history = [
        (changed.replace(tzinfo=now.tzinfo) if changed.tzinfo is None else changed.astimezone(now.tzinfo),
         progress, changed_by)
        for changed, progress, changed_by in history
    ]
    points_x = [max(0, (changed - start).total_seconds() / 86400)
                for changed, _, _ in normalized_history]
    points_y = [progress for _, progress, _ in history]
    elapsed = max(0, (now - start).total_seconds() / 86400)
    deadline_day = max(0, (deadline - start).total_seconds() / 86400)
    remaining = (deadline - now).total_seconds() / 86400
    chart_end = max(deadline_day, elapsed, *(points_x or [0]), 1)
    font = _font()
    number_font = _number_font()

    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=160)
    fig.patch.set_facecolor("#eef3f8")
    ax.set_facecolor("#ffffff")
    fig.subplots_adjust(top=.76, bottom=.15, left=.09, right=.96)

    # Header and summary cards
    fig.text(.5, .94, _fa(task.title), ha="center", va="center",
             fontsize=18, fontweight="bold", color="#172033", fontproperties=font)
    fig.text(.5, .89, _fa("نمودار روند پیشرفت تسک"), ha="center", va="center",
             fontsize=10.5, color="#64748b", fontproperties=font)
    cards = [
        (_percent(task.progress), _fa("پیشرفت فعلی"), "#2563eb"),
        (_fa(f"{elapsed:.1f} روز"), _fa("گذشته از شروع"), "#059669"),
        (_fa(f"{abs(remaining):.1f} روز"),
         _fa("مانده تا ددلاین" if remaining >= 0 else "گذشته از ددلاین"),
         "#dc2626" if remaining < 0 else "#f59e0b"),
    ]
    for x, (value, label, color) in zip((.22, .5, .78), cards):
        fig.text(x, .82, value, ha="center", va="center", fontsize=15, fontweight="bold",
                 color=color, fontproperties=number_font,
                 bbox=dict(boxstyle="round,pad=.55", facecolor="#ffffff",
                           edgecolor="#dbe3ec", linewidth=1))
        fig.text(x, .775, label, ha="center", va="center", fontsize=9,
                 color="#64748b", fontproperties=font)

    # Progress line and a soft filled area
    ax.plot(points_x, points_y, color="#2563eb", marker="o", linewidth=3,
            markersize=7, markerfacecolor="#ffffff", markeredgewidth=2.5,
            markeredgecolor="#2563eb", zorder=4)
    if points_x:
        ax.fill_between(points_x, points_y, 0, color="#3b82f6", alpha=.10, zorder=1)
    for x, y in zip(points_x, points_y):
        ax.annotate(_percent(y), (x, y), xytext=(0, 12), textcoords="offset points",
                    ha="center", fontsize=9, fontweight="bold", color="#1d4ed8",
                    fontproperties=number_font,
                    bbox=dict(boxstyle="round,pad=.25", fc="#eff6ff", ec="none"))

    ax.axvline(elapsed, color="#059669", linewidth=2.2, zorder=2)
    ax.annotate(_fa("امروز"), xy=(elapsed, 101), xytext=(0, 6),
                textcoords="offset points", ha="center", color="#047857",
                fontsize=9, fontweight="bold", fontproperties=font)
    ax.axvline(deadline_day, color="#dc2626", linewidth=2.2, linestyle=(0, (5, 4)), zorder=2)
    ax.annotate(_fa("ددلاین"), xy=(deadline_day, 101), xytext=(0, 6),
                textcoords="offset points", ha="center", color="#b91c1c",
                fontsize=9, fontweight="bold", fontproperties=font)

    ax.set_xlabel(_fa("روزهای گذشته از شروع تسک"), fontsize=11, labelpad=12,
                  color="#475569", fontproperties=font)
    ax.set_ylabel(_fa("درصد پیشرفت"), fontsize=11, labelpad=12,
                  color="#475569", fontproperties=font)
    ax.set_ylim(-5, 108)
    ax.set_xlim(-chart_end * .02, chart_end * 1.04)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _fa(f"{value:g}")))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _percent(f"{value:g}")))
    ax.grid(axis="y", linestyle=(0, (2, 4)), color="#cbd5e1", alpha=.65)
    ax.grid(axis="x", visible=False)
    ax.tick_params(colors="#64748b", labelsize=9)
    for label in (*ax.get_xticklabels(), *ax.get_yticklabels()):
        label.set_fontproperties(number_font)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#dbe3ec")
    ax.spines["bottom"].set_color("#dbe3ec")

    output = BytesIO()
    fig.savefig(output, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output.getvalue()


def weekly_management_card(current: dict, previous: dict, meetings: int, previous_meetings: int,
                           attention: list[tuple[str, int]], members: list[tuple[str, int]]) -> bytes:
    """Render a compact weekly management dashboard."""
    font, number_font = _font(), _number_font()
    fig = plt.figure(figsize=(10, 7), dpi=160, facecolor="#eef3f8")
    fig.text(.5, .94, _fa("گزارش مدیریتی هفته"), ha="center", fontsize=20, fontweight="bold", color="#172033", fontproperties=font)
    cards = [("تسک‌های پیشرفت‌کرده", len(current.get("progressed", [])), len(previous.get("progressed", [])), "#2563eb"),
             ("تسک‌های تکمیل‌شده", current.get("completed", 0), previous.get("completed", 0), "#059669"),
             ("جلسات برگزارشده", meetings, previous_meetings, "#7c3aed"),
             ("تسک‌های بدون پیشرفت", len(current.get("stagnant", [])), len(previous.get("stagnant", [])), "#dc2626")]
    for i, (label, value, old, color) in enumerate(cards):
        x, y = .14 + (i % 2) * .5, .77 - (i // 2) * .2
        fig.text(x, y, _fa(value), fontsize=23, fontweight="bold", color=color, fontproperties=number_font,
                 bbox=dict(boxstyle="round,pad=.65", fc="white", ec="#dbe3ec"))
        fig.text(x+.12, y+.018, _fa(label), fontsize=11, color="#334155", fontproperties=font)
        fig.text(x+.12, y-.035, _fa(f"نسبت به هفته قبل: {int(value)-int(old):+d}"), fontsize=8.5, color="#64748b", fontproperties=font)
    fig.text(.9, .38, _fa("نیازمند توجه"), ha="right", fontsize=12, fontweight="bold", fontproperties=font)
    for i, (title, progress) in enumerate(attention[:4] or [("موردی ثبت نشده", 0)]):
        fig.text(.9, .33-i*.055, _fa(f"• {title} — {progress}%"), ha="right", fontsize=9, fontproperties=font)
    fig.text(.45, .38, _fa("میانگین پیشرفت اعضا"), ha="right", fontsize=12, fontweight="bold", fontproperties=font)
    for i, (name, progress) in enumerate(members[:4] or [("عضوی ثبت نشده", 0)]):
        fig.text(.45, .33-i*.055, _fa(f"• {name} — {progress}%"), ha="right", fontsize=9, fontproperties=font)
    fig.text(.5, .07, _fa("این گزارش به‌صورت خودکار جمعه ساعت ۱۲ ارسال شده است"), ha="center", fontsize=8.5, color="#94a3b8", fontproperties=font)
    output = BytesIO(); fig.savefig(output, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig)
    return output.getvalue()


def member_performance_card(name: str, stats: dict) -> bytes:
    """Backward-compatible member card built with the weekly dashboard renderer."""
    current = {"progressed": [], "stagnant": [], "completed": stats.get("done", 0)}
    previous = {"progressed": [], "stagnant": [], "completed": 0}
    return weekly_management_card(current, previous, 0, 0, [], [(name, stats.get("avg_progress", 0))])
