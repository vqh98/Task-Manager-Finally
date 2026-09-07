"""Live regression evaluation for the Persian management-command interpreter."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.ai import AIInterpreter
from app.command_engine import context_for_prompt, normalize_action, resolve_references
from app.command_corpus import expand_persian_command_cases
from app.config import load_settings


ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT/"tests"/"fixtures"/"management_commands_fa.json"


def sample_context(kind: str | None) -> dict:
    base={"last_results":{},"last_selected":{}}
    if kind=="tasks":
        base["last_results"]["tasks"]=[{"id":1,"title":"گزارش فروش"},{"id":2,"title":"سرمقاله خبرفردا"}]
        base["last_selected"]["tasks"]={"id":1,"title":"گزارش فروش"}; base["last_operation"]="list_tasks"
    elif kind=="meetings":
        base["last_results"]["meetings"]=[{"id":"g1","title":"جلسه فروش"},{"id":"g2","title":"جلسه بودجه"}]
        base["last_selected"]["meetings"]={"id":"g1","title":"جلسه فروش"}; base["last_operation"]="list_meetings"
    return base


def matches(case: dict, actions: list[dict]) -> tuple[bool,list[str]]:
    issues=[]
    actual=[action["operation"] for action in actions]
    if actual!=case["operations"]: issues.append(f"operations={actual}")
    if "notify" in case and (not actions or actions[0].get("notify") is not case["notify"]):
        issues.append(f"notify={actions[0].get('notify') if actions else None}")
    if case.get("assignee") and (not actions or case["assignee"] not in actions[0].get("assignee","")):
        issues.append(f"assignee={actions[0].get('assignee') if actions else None}")
    return not issues,issues


async def main(limit: int | None=None, indices: set[int] | None=None) -> int:
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    settings=load_settings()
    ai=AIInterpreter(settings.openai_api_key,settings.openai_text_model,settings.openai_transcription_model,
                     settings.openai_base_url,settings.openai_transcription_fallback_model,
                     settings.openai_advanced_text_model)
    all_cases=expand_persian_command_cases(json.loads(CORPUS.read_text(encoding="utf-8")),300)
    cases=[case for index,case in enumerate(all_cases,1) if not indices or index in indices][:limit]
    passed=0; failures=[]; model_counts={}; semaphore=asyncio.Semaphore(4)
    now=datetime.now(settings.timezone).isoformat()
    async def evaluate(index:int,case:dict)->dict:
        async with semaphore:
            context=sample_context(case.get("context"))
            try:
                plan=await ai.plan_commands(case["text"],now,context_for_prompt(context))
                actions=[resolve_references(normalize_action(asdict(item),case["text"],context),context)
                         for item in plan.actions]
                ok,issues=matches(case,actions)
                return {"index":index,"case":case,"ok":ok,"issues":issues,"actions":actions,"model":plan.model_used}
            except Exception as exc:
                return {"index":index,"case":case,"ok":False,"issues":[f"error={type(exc).__name__}: {exc}"],
                        "actions":[],"model":"error"}
    results=await asyncio.gather(*(evaluate(index,case) for index,case in enumerate(cases,1)))
    for item in sorted(results,key=lambda value:value["index"]):
        case=item["case"]; model_counts[item["model"]]=model_counts.get(item["model"],0)+1
        if item["ok"]: passed+=1
        else: failures.append({"index":item["index"],"text":case["text"],"issues":item["issues"],"actual":item["actions"]})
        print(f"[{item['index']:02}/{len(cases)}] {'PASS' if item['ok'] else 'FAIL'} {case['text']}",flush=True)
    score=round(100*passed/max(len(cases),1),1)
    report={"score":score,"passed":passed,"total":len(cases),"models":model_counts,"failures":failures}
    (ROOT/"command-evaluation-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"\nScore: {score}% ({passed}/{len(cases)})")
    return 0 if score>=90 else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--limit",type=int)
    parser.add_argument("--indices",help="comma-separated one-based corpus indices")
    args=parser.parse_args(); selected={int(value) for value in args.indices.split(",")} if args.indices else None
    raise SystemExit(asyncio.run(main(args.limit,selected)))
