from __future__ import annotations

import copy


def expand_persian_command_cases(seeds:list[dict],target:int=300)->list[dict]:
    """Build deterministic conversational/ASR variants without changing expected semantics."""
    transforms=[
        lambda text:text,
        lambda text:"لطفاً "+text,
        lambda text:"می‌خوام "+text,
        lambda text:"میخوام "+text.replace(" را "," رو "),
        lambda text:text.replace(" را "," رو ").replace("بگذار","بزار"),
        lambda text:text.replace("‌", " "),
        lambda text:"لطفا "+text.replace("‌", " ").replace(" را "," رو "),
        lambda text:text+" لطفاً انجامش بده",
        lambda text:"این کار رو انجام بده: "+text,
        lambda text:text.replace("فردا","فردا،").replace("ساعت","ساعتِ"),
        lambda text:"می‌شه "+text+"؟",
        lambda text:"دستور مدیریتی: "+text,
    ]
    result=[]; seen=set()
    for transform_index,transform in enumerate(transforms):
        for seed_index,seed in enumerate(seeds):
            item=copy.deepcopy(seed); item["text"]=transform(seed["text"]).strip()
            key=(item["text"],tuple(item["operations"]))
            if key in seen: continue
            seen.add(key); item["variant"]="seed" if transform_index==0 else f"variant_{transform_index}"
            item["seed_index"]=seed_index+1; result.append(item)
            if len(result)>=target:return result
    return result
