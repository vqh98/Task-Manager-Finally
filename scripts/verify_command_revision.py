import asyncio
import sys
from dataclasses import asdict
from datetime import datetime

from app.ai import AIInterpreter
from app.config import load_settings


BASE={"operation":"move_meeting","title":"","target":"جلسه فروش","second_target":"",
      "date_phrase":"فردا","start_time":"10:00","end_time":"11:00","deadline_phrase":"",
      "assignee":"علی","participant":"","priority":0,"progress":-1,"relative_days":0,
      "relative_minutes":0,"note":"","status":"","notify":True,"missing_fields":[],
      "confidence":.95,"requires_confirmation":True}


async def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    settings=load_settings(); ai=AIInterpreter(settings.openai_api_key,settings.openai_text_model,
        settings.openai_transcription_model,settings.openai_base_url,settings.openai_transcription_fallback_model,
        settings.openai_advanced_text_model)
    checks=[
        ("فقط ساعتش ۱۱ باشد","start_time","11:00"),
        ("مسئولش مریم باشد","assignee","مریم"),
        ("لازم نیست خبرش کنی","notify",False),
    ]
    for instruction,field,expected in checks:
        revised=asdict(await ai.revise_command(BASE,instruction,datetime.now(settings.timezone).isoformat()))
        assert revised[field]==expected,(instruction,field,revised[field])
        assert revised["target"]==BASE["target"]
        print("PASS",instruction)


if __name__=="__main__": asyncio.run(main())
