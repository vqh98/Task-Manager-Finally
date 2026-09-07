import asyncio
import sys

from app.ai import AIInterpreter
from dataclasses import asdict
from app.command_engine import context_for_prompt, normalize_action, resolve_references
from app.config import load_settings


async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings=load_settings()
    ai=AIInterpreter(settings.openai_api_key,settings.openai_text_model,settings.openai_transcription_model,
                     settings.openai_base_url,settings.openai_transcription_fallback_model,
                     settings.openai_advanced_text_model)
    context={"last_results":{"tasks":[{"id":1,"title":"گزارش فروش"},{"id":2,"title":"سرمقاله خبرفردا"}]},
             "last_selected":{"meetings":{"id":"g1","title":"جلسه فروش"}},"last_operation":"list_tasks"}
    cases=[
        "همان دومی را سه روز عقب بینداز",
        "یک تسک برای تحویل گزارش تا آخر هفته بساز و بعد تسک‌های پرریسک را نشان بده",
        "این جلسه را فردا ساعت 11 بگذار",
        "برای جلسه با علی یک وقت مناسب پیدا کن",
        "تا آخر هفته باید سرمقاله خبرفردا را تحویل بدهم",
        "تا فردا باید گزارش فروش را از علی پیگیری کنم",
        "زمان جلسه فروش را با جلسه شورای محتوا عوض کن",
        "فردا ساعت ده با علی جلسه بررسی فروش بگذار و به او خبر بده",
    ]
    for text in cases:
        plan=await ai.plan_commands(text,"۲۶ مرداد ۱۴۰۵ ساعت ۱۲:۰۰",context_for_prompt(context))
        normalized=[resolve_references(normalize_action(asdict(a),text,context),context) for a in plan.actions]
        print(text,"=>",[(a["operation"],a["title"],a["target"],a["second_target"],a["assignee"],a["participant"],
                           a["deadline_phrase"],a["date_phrase"],a["start_time"],a["relative_days"],
                           a["relative_minutes"],a["notify"]) for a in normalized])


if __name__=="__main__": asyncio.run(main())
