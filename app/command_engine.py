from __future__ import annotations

import json
import re


ORDINALS={
    "اول":0,"اولی":0,"نخست":0,
    "دوم":1,"دومی":1,"دومین":1,
    "سوم":2,"سومی":2,"سومین":2,
    "چهارم":3,"چهارمی":3,"پنجم":4,"پنجمی":4,
}


def load_context(raw:str)->dict:
    try:
        value=json.loads(raw or "{}")
        if isinstance(value,dict): return value
    except (ValueError,TypeError): pass
    return {"legacy":raw} if raw else {}


def dump_context(context:dict)->str:
    return json.dumps(context,ensure_ascii=False,separators=(",",":"))


def context_for_prompt(context:dict)->str:
    """Compact, explicit context that the model can resolve without guessing."""
    return dump_context(context)[-6000:]


def _ordinal_index(text:str)->int|None:
    clean=re.sub(r"[\u200c\s]+"," ",(text or "").strip())
    for word,index in ORDINALS.items():
        if re.search(rf"(?:همان\s+)?{word}(?:\s|$)",clean): return index
    numeric=re.search(r"(?:شماره|مورد)\s*([1-9])",clean)
    return int(numeric.group(1))-1 if numeric else None


def resolve_references(action:dict,context:dict)->dict:
    """Resolve ordinal/demonstrative targets deterministically from the last shown result."""
    result=dict(action)
    operation=result.get("operation","")
    entity_type="meetings" if "meeting" in operation else "tasks"
    entities=context.get("last_results",{}).get(entity_type,[])
    for field in ("target","second_target"):
        value=str(result.get(field) or "")
        index=_ordinal_index(value)
        if index is not None and index < len(entities):
            result[field]=entities[index].get("title",value)
            result[f"_{field}_id"]=entities[index].get("id")
        elif value.strip() in {"همان","همون","این","همین","قبلی","همان مورد","همون مورد","این مورد",
                               "این جلسه","همین جلسه","همان جلسه","همون جلسه","این تسک","همین تسک","همان تسک","همون تسک"} or any(
                                   value.strip().startswith(prefix) for prefix in ("این جلسه ","همین جلسه ","همان جلسه ","همون جلسه ","این تسک ","همین تسک ","همان تسک ","همون تسک ")):
            selected=context.get("last_selected",{}).get(entity_type)
            if selected:
                result[field]=selected.get("title",value); result[f"_{field}_id"]=selected.get("id")
    return result


def remember_results(context:dict,entity_type:str,items:list[dict],operation:str)->dict:
    updated=dict(context); results=dict(updated.get("last_results",{})); results[entity_type]=items[:20]
    updated["last_results"]=results; updated["last_operation"]=operation
    return updated


def remember_selected(context:dict,entity_type:str,item:dict,operation:str)->dict:
    updated=dict(context); selected=dict(updated.get("last_selected",{})); selected[entity_type]=item
    updated["last_selected"]=selected; updated["last_operation"]=operation
    return updated


def normalize_action(action:dict,source:str,context:dict)->dict:
    """Correct common model inconsistencies using explicit language and conversation state."""
    result=dict(action); clean=source.replace("‌"," ")
    clean=re.sub(r"[\u200c\s]+"," ",clean).strip()
    references=("همان","همون","دومی","دومین","سومی","سومین","اولی","این جلسه","این تسک","همین مورد")
    referenced=any(word in clean for word in references); last_op=context.get("last_operation","")
    explicit_meeting=any(word in clean for word in ("جلسه","قرار","رویداد"))
    explicit_task=any(word in clean for word in ("تسک","کار","وظیفه"))
    if explicit_meeting and result.get("operation")=="update_task_deadline":
        result["operation"]="move_meeting"
        if not str(result.get("target") or "").strip() and str(result.get("title") or "").strip():
            result["target"],result["title"]=result["title"].strip(),""
    elif explicit_task and not explicit_meeting and result.get("operation")=="move_meeting": result["operation"]="update_task_deadline"
    elif referenced and not explicit_meeting and not explicit_task and last_op in {"list_tasks","risky_tasks","overdue_tasks","missing_reports"} and result.get("operation")=="move_meeting":
        result["operation"]="update_task_deadline"
    elif referenced and not explicit_meeting and not explicit_task and last_op=="list_meetings" and result.get("operation")=="update_task_deadline":
        result["operation"]="move_meeting"
    if result.get("operation")=="create_task" and result.get("date_phrase") and str(result.get("deadline_phrase") or "").strip() in {"","تا","تا تاریخ"}:
        result["deadline_phrase"],result["date_phrase"]=result["date_phrase"],""
    if result.get("operation")=="create_task" and not str(result.get("title") or "").strip() and str(result.get("target") or "").strip():
        result["title"],result["target"]=result["target"].strip(),""
    note_command=re.search(r"(?:برای\s+)?(?:تسک|کار|وظیفه)\s+(.+?)\s+(?:بنویس|یادداشت\s+کن|توضیح\s+بزن)\s+(.+)$",clean)
    if note_command and result.get("operation")=="create_task":
        result["operation"]="update_task_note"
        result["target"]=note_command.group(1).strip(" ،؛")
        result["note"]=(result.get("note") or note_command.group(2)).strip()
        result["title"]=""
    # A new first-person commitment is task creation, even when a similarly named old task is in context.
    # Updating an existing deadline requires explicit change/reschedule language or a demonstrative reference.
    if result.get("operation")=="update_task_deadline":
        deadline_change=any(word in clean for word in (
            "ددلاین","مهلت","تمدید","تغییر","عوض","عقب","جلو","زودتر","دیرتر","بنداز","ببر"
        ))
        commitment=any(word in clean for word in ("باید","لازم است","لازمه","تحویل بدهم","تحویل بدم","انجام بدهم","انجام بدم"))
        if commitment and not referenced and not deadline_change:
            result["operation"]="create_task"
            result["title"]=(result.get("title") or result.get("target") or "").strip()
            result["target"]=""
            result["relative_days"]=0; result["relative_minutes"]=0
    # In Persian, «از علی پیگیری کنم» identifies Ali as the accountable person;
    # the first-person verb describes the manager's follow-up, not self-assignment.
    if result.get("operation")=="create_task" and "پیگیری" in clean:
        other=re.search(r"(?:را\s+)?از\s+([آ-ی][آ-ی\u200c\- ]{1,45}?)\s+پیگیری",clean)
        if other:
            candidate=re.sub(r"\s+"," ",other.group(1)).strip(" ،؛")
            if candidate not in {"او","ایشان","وی","خودم","من","ش"}:
                result["assignee"]=candidate
    if result.get("operation") not in {"move_meeting","update_task_deadline"}:
        result["relative_days"]=0; result["relative_minutes"]=0
    if result.get("operation")=="move_meeting" and result.get("date_phrase") and result.get("start_time"):
        result["relative_days"]=0; result["relative_minutes"]=0
    for field in ("relative_days","relative_minutes"):
        if result.get(field):
            if any(word in clean for word in ("عقب","دیرتر","بعدتر","بعد")): result[field]=abs(result[field])
            elif any(word in clean for word in ("جلو","زودتر","قبل تر","قبل‌تر","قبل")): result[field]=-abs(result[field])
    if result.get("operation")=="availability": result["target"]=""
    if referenced and result.get("operation") in {"update_task_progress","update_task_note","update_task_deadline","update_task_status","move_meeting"}:
        result["target"]=source
    return result
