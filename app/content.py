"""Static content shared by both apps: helpdesk services, price bands and the cyber-check questions."""
import json
from pathlib import Path

_DATA = json.loads((Path(__file__).with_name("content.json")).read_text(encoding="utf-8"))

# content.json uses short icon names; the pages use Lucide icons
_LUCIDE = {"app": "app-window", "bolt": "zap", "box": "monitor-speaker", "nodes": "network", "phone": "smartphone",
           "shield": "shield-check", "userplus": "user-plus", "video": "cctv", "wall": "brick-wall",
           "case": "briefcase", "file": "file-lock", "cloud": "cloud-upload", "lock": "key-round"}
for _item in _DATA["help"] + _DATA["sections"]:
    _item["icon"] = _LUCIDE.get(_item["icon"], _item["icon"])

HELP = _DATA["help"]
CATS = {c["id"]: c for c in HELP}
TOP_CATS = _DATA["top"]
BANDS = _DATA["bands"]
BAND_ADVICE = _DATA["band_advice"]
PRIORITIES = _DATA["priorities"]
CERTS = _DATA["certs"]
SECTIONS = _DATA["sections"]
N_QUESTIONS = sum(len(s["qs"]) for s in SECTIONS)

# answer options: value, label, score (None = does not count)
OPTIONS = [tuple(o) for o in _DATA["options"]]
OPTION_SCORE = {o[0]: o[2] for o in OPTIONS}
LABEL = {o[0]: o[1] for o in OPTIONS}
LEVELS = [tuple(l) for l in _DATA["levels"]]  # (min score, label, css key)

TICKET_STATUS = {
    "quote": ("Price sent", "warn"),
    "negotiating": ("Negotiating", "hot"),
    "accepted": ("Awaiting payment", "warn"),
    "paid": ("Paid", "ok"),
}
WORK_STATUS = {
    "assigned": ("Assigned", "warn"),
    "in progress": ("In progress", "hot"),
    "resolved": ("Resolved", "ok"),
}


def issue(cat_id, idx):
    cat = CATS.get(cat_id)
    if not cat or not (0 <= idx < len(cat["issues"])):
        return None, None
    return cat, cat["issues"][idx]


def search(term, limit=8):
    term = term.strip().lower()
    if len(term) < 2:
        return []
    hits = []
    for c in HELP:
        for i, s in enumerate(c["issues"]):
            if term in f'{s["title"]} {s["why"]} {c["name"]}'.lower():
                hits.append({"cat": c["id"], "cat_name": c["name"], "idx": i, "title": s["title"], "icon": c["icon"]})
    return hits[:limit]


# ---------- cyber check ----------

def questions(size):
    out = []
    for sec in SECTIONS:
        for i, q in enumerate(sec["qs"]):
            if q["audience"] == "A" or size == "L":
                out.append({**q, "sec": sec, "key": f'{sec["id"]}{i}'})
    return out


def score(qs, answers):
    got = mx = 0
    for q in qs:
        s = OPTION_SCORE.get(answers.get(q["key"]))
        if s is None:
            continue
        got += s * q["weight"]
        mx += q["weight"]
    return round(got / mx * 100) if mx else None


def level(s):
    if s is None:
        return ("Not answered", "none")
    for floor, label, key in LEVELS:
        if s >= floor:
            return (label, key)


def summary(size, answers):
    qs = questions(size)
    secs = []
    for sec in SECTIONS:
        lst = [q for q in qs if q["sec"] is sec]
        if lst:
            sc = score(lst, answers)
            secs.append({"sec": sec, "qs": lst, "score": sc, "level": level(sc), "n": len(lst),
                         "done": sum(1 for q in lst if answers.get(q["key"]))})
    gaps = [q for q in qs if answers.get(q["key"]) in ("no", "unsure", "partly")]
    gaps.sort(key=lambda q: (-q["weight"], answers.get(q["key"]) == "partly"))
    overall = score(qs, answers)
    return {"qs": qs, "secs": secs, "overall": overall, "level": level(overall), "gaps": gaps,
            "answered": sum(1 for q in qs if answers.get(q["key"]))}
