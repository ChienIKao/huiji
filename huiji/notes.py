"""AI 整理會議紀錄：透過已登入的 Claude Code CLI 或 Codex CLI 產生結構化 JSON。

提示詞、範本與 JSON 結構改寫自 Shiyin（https://github.com/juinwei7/Shiyin，MIT License）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from .transcribe import NO_WINDOW, fmt_time

TIMEOUT = 15 * 60
SECTION_IDS = ["summary", "decisions", "actionItems", "openQuestions", "topics"]
SECTION_LABELS = {"summary": "摘要", "decisions": "決議", "actionItems": "待辦",
                  "openQuestions": "未決問題", "topics": "議題詳細"}

TEMPLATES = {
    "general": {"name": "一般會議", "description": "週會、專案會議、討論會：摘要、決議、待辦、未決問題、議題",
                "sections": SECTION_IDS, "custom": [], "instruction": ""},
    "one-on-one": {"name": "一對一", "description": "主管與成員的一對一會談",
                   "sections": ["summary", "actionItems", "openQuestions"],
                   "custom": [{"title": "近況與進展", "instruction": "對方最近在做的事、進度與卡關的地方"},
                              {"title": "回饋", "instruction": "雙方給彼此的回饋，標明是誰給誰"},
                              {"title": "需要的支持", "instruction": "對方提出需要協助或資源的地方"}],
                   "instruction": "這是一對一會談，語氣保持中性，不要評價個人。"},
    "interview": {"name": "訪談", "description": "使用者訪談、專家訪談、面試",
                  "sections": ["summary", "topics"],
                  "custom": [{"title": "受訪者背景", "instruction": "受訪者的角色、經歷、使用情境"},
                             {"title": "重點引述", "instruction": "保留受訪者的原話，每條一句，附時間點"},
                             {"title": "洞察與機會", "instruction": "從訪談內容歸納出的需求、痛點與機會"}],
                  "instruction": "這是訪談，重點是受訪者說了什麼，不要把訪談者的推測寫成受訪者的意見。"},
    "lecture": {"name": "課堂／演講", "description": "課程、演講、分享會、教育訓練",
                "sections": ["summary", "topics", "openQuestions"],
                "custom": [{"title": "重點概念", "instruction": "提到的重要概念、名詞與解釋"},
                           {"title": "問答", "instruction": "聽眾的提問與講者的回答"}],
                "instruction": "這是課堂或演講，依講者的段落整理大綱。"},
    "brainstorm": {"name": "腦力激盪", "description": "發想會議、工作坊",
                   "sections": ["summary", "decisions", "actionItems"],
                   "custom": [{"title": "點子清單", "instruction": "每個點子一條，盡量標出是誰提出的"},
                              {"title": "評估與取捨", "instruction": "對點子的討論、優缺點與取捨理由"}],
                   "instruction": "這是腦力激盪，不要遺漏任何被提出的點子，即使沒有被採納。"},
}

SYSTEM_PROMPT = """你是會議紀錄整理者。根據使用者提供的會議逐字稿，整理成結構化的會議紀錄，一律使用繁體中文（台灣用語）。

規則：
1. 只根據逐字稿與使用者筆記撰寫，不要補充沒有提到的資訊。
2. 逐字稿來自語音辨識，英文術語、人名、縮寫常被聽錯（同一個詞可能出現好幾種寫法）。請依上下文和詞彙表推斷正確寫法，但凡是推斷出來的專有名詞、人名，一定要列進 reviewItems 讓使用者確認。
3. 每一條紀錄（summary、decisions、actionItems、openQuestions、topics 的 points、custom 的 items）都要填 time：該內容在逐字稿中出現的時間點，寫成 mm:ss（例如 03:15，超過一小時寫 1:03:15），不要加括號。綜合多處的內容，填最主要的那一處。
4. 逐字稿每行標有說話者。actionItems 的 owner 只填明確提到的負責人，優先使用說話者的名字；不明確就留空字串並列進 reviewItems。
5. actionItems 的 due 依提示中的「會議日期」換算成 yyyy-MM-dd（例如「下週三」要依會議日期和星期幾推算，星期幾要算對）；沒提到期限就留空字串，提到但不明確就留空並列進 reviewItems。
6. reviewItems 最多 8 題，相近的問題要合併；只問會影響紀錄正確性的地方：專有名詞、人名、負責人、期限、方案內容。每題的 time 填相關段落的時間點。
7. 「使用者筆記」與「重點標記」代表使用者自己認為重要的內容，必須納入紀錄；筆記裡逐字稿沒有的資訊也可以寫入，但不要改變原意。
8. 依「範本」只產生指定的段落，沒指定的段落給空陣列。custom 依範本的自訂段落順序產生，title 用範本給的段落名稱。
9. topics 依議題分段，start 填該議題開始的時間點。
10. title：提示中有「現有標題」就原樣回傳；沒有就依內容取一個 20 字以內的標題。
11. 時間點格式一律是 mm:ss，超過一小時用 h:mm:ss，與逐字稿相同。沒有時間資訊時填空字串。
"""

REVISE_INSTRUCTIONS = """
請依使用者的回答修正會議紀錄後，回傳完整的會議紀錄：
- 逐字稿只附上與回答有關的段落；跟回答無關的內容一律照目前的會議紀錄原樣保留，不要改寫。
- 依回答修正所有相關內容（摘要、決議、待辦、未決問題、議題、自訂段落、標題都要一併檢查），時間點維持不變。
- 已回答的題目從 reviewItems 移除；目前會議紀錄裡沒被回答的題目原樣保留。
- 除非回答本身帶出新的疑點，否則不要新增題目。
"""

_ITEMS = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "time"],
                                     "properties": {"text": {"type": "string"}, "time": {"type": "string"}}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "decisions", "actionItems", "openQuestions", "topics", "custom", "reviewItems"],
    "properties": {
        "title": {"type": "string"},
        "summary": _ITEMS,
        "decisions": _ITEMS,
        "actionItems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["owner", "task", "due", "time"],
            "properties": {k: {"type": "string"} for k in ("owner", "task", "due", "time")}}},
        "openQuestions": _ITEMS,
        "topics": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "start", "points"],
            "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "points": _ITEMS}}},
        "custom": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "items"],
            "properties": {"title": {"type": "string"}, "items": _ITEMS}}},
        "reviewItems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["time", "question"],
            "properties": {"time": {"type": "string"}, "question": {"type": "string"}}}},
    },
}

WEEKDAYS = "一二三四五六日"


class NotesError(RuntimeError):
    pass


def speaker_name(speaker: str | None, names: dict) -> str:
    if not speaker:
        return ""
    if names.get(speaker):
        return names[speaker]
    try:
        return f"說話者 {int(speaker.split('_')[-1]) + 1}"
    except ValueError:
        return speaker


def build_prompt(meeting: dict, segments: list[dict], glossary: list[str],
                 transcript_label: str = "逐字稿（[時間] 說話者：內容）") -> str:
    t = TEMPLATES.get(meeting.get("template"), TEMPLATES["general"])
    d = date.fromisoformat(meeting["date"])
    lines = [
        f"會議日期：{meeting['date']}（星期{WEEKDAYS[d.weekday()]}）",
        f"現有標題：{meeting['title'] if meeting.get('title_locked') else ''}",
        f"詞彙表（正確寫法）：{'、'.join(glossary) if glossary else '無'}",
        "",
        f"範本：{t['description']}",
        "要產生的段落：" + "、".join(f"{s}（{SECTION_LABELS[s]}）" for s in t["sections"]),
    ]
    if t["custom"]:
        lines.append("自訂段落（放在 custom，依序）：")
        lines += [f"- {c['title']}：{c['instruction']}" for c in t["custom"]]
    if t["instruction"]:
        lines.append(f"額外指示：{t['instruction']}")
    notes = (meeting.get("user_notes") or "").strip()
    if notes:
        lines += ["", "使用者筆記（[時間] 是做筆記時的錄音時間）：", notes]
    if meeting.get("bookmarks"):
        lines += ["", "重點標記：" + "、".join(fmt_time(b["time"]) + (f" {b['label']}" if b.get("label") else "")
                                           for b in meeting["bookmarks"])]
    lines += ["", f"{transcript_label}："]
    names = meeting.get("speakers", {})
    for seg in segments:
        who = speaker_name(seg.get("speaker"), names)
        lines.append(f"[{fmt_time(seg['start'])}] {who + '：' if who else ''}{seg['text']}")
    return "\n".join(lines)


def _seconds(text: str) -> float | None:
    parts = re.split(r"[:：]", (text or "").strip())
    if not parts or not all(x.isdigit() for x in parts) or len(parts) > 3:
        return None
    total = 0
    for x in parts:
        total = total * 60 + int(x)
    return float(total)


def excerpts(segments: list[dict], times: list[str], window: float = 90) -> list[dict]:
    """只取每個時間點前後 window 秒的逐字稿：修正時不用把整份逐字稿再送一次"""
    points = [t for t in (_seconds(x) for x in times) if t is not None]
    if not points:
        return segments
    return [seg for seg in segments if any(seg["end"] >= t - window and seg["start"] <= t + window for t in points)]


def build_revise_prompt(meeting: dict, segments: list[dict], glossary: list[str], notes: dict,
                        answers: list[dict]) -> str:
    related = excerpts(segments, [a["time"] for a in answers])
    text = build_prompt(meeting, related, glossary, "逐字稿節錄（只列出與回答有關的段落）")
    text += "\n\n目前的會議紀錄（JSON）：\n" + json.dumps(notes, ensure_ascii=False)
    text += "\n\n使用者對待確認題目的回答：\n"
    for i, a in enumerate(answers, 1):
        text += f"{i}. [{a['time']}] {a['question']}\n   回答：{a['answer']}\n"
    return text + REVISE_INSTRUCTIONS


def _find(tool: str) -> str:
    exe = shutil.which(tool) or shutil.which(tool + ".cmd")
    if not exe:
        raise NotesError(f"找不到 {tool}，請先安裝並登入")
    return exe


def _clean_times(notes: dict) -> dict:
    def clean(v: str) -> str:
        return v.strip().strip("[]()（）【】").replace("：", ":")
    for key in ("summary", "decisions", "openQuestions", "actionItems", "reviewItems"):
        for item in notes.get(key, []):
            item["time"] = clean(item.get("time", ""))
    for topic in notes.get("topics", []):
        topic["start"] = clean(topic.get("start", ""))
        for p in topic.get("points", []):
            p["time"] = clean(p.get("time", ""))
    for section in notes.get("custom", []):
        for p in section.get("items", []):
            p["time"] = clean(p.get("time", ""))
    return notes


CLAUDE_MODELS = [("", "預設"), ("opus", "Opus（最準確）"), ("sonnet", "Sonnet（較快、較省）"),
                 ("haiku", "Haiku（最快、最省）")]


EFFORTS = [("", "預設"), ("low", "low（最快、最省）"), ("medium", "medium（一般整理建議）"),
           ("high", "high（較仔細，日期、人名比較不會錯）"), ("xhigh", "xhigh（最仔細、最慢）")]


def default_efforts() -> dict:
    """claude／codex 沒指定推理強度時用的是哪個：讀它們自己的設定檔"""
    out = {"claude": "", "codex": ""}
    home = Path.home()
    try:
        out["claude"] = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")).get("effortLevel", "")
    except (OSError, ValueError):
        pass
    try:
        m = re.search(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"',
                      (home / ".codex" / "config.toml").read_text(encoding="utf-8"), re.M)
        out["codex"] = m.group(1) if m else ""
    except OSError:
        pass
    return out


def default_models() -> dict:
    """claude／codex 沒指定模型時用的是哪個：讀它們自己的設定檔"""
    out = {"claude": "", "codex": ""}
    home = Path.home()
    try:
        out["claude"] = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")).get("model", "")
    except (OSError, ValueError):
        pass
    try:
        m = re.search(r'^\s*model\s*=\s*"([^"]+)"', (home / ".codex" / "config.toml").read_text(encoding="utf-8"), re.M)
        out["codex"] = m.group(1) if m else ""
    except OSError:
        pass
    return out


def generate(backend: str, model: str, prompt: str, on_process=None, effort: str = "") -> tuple[dict, dict]:
    """回傳（會議紀錄, 用量）；用量是 {model, effort, input_tokens, output_tokens, cost_usd}，拿不到的欄位是 None"""
    schema = json.dumps(SCHEMA, ensure_ascii=False)
    # 在空的暫存資料夾執行：不會讀到任何專案的 CLAUDE.md / AGENTS.md
    with tempfile.TemporaryDirectory() as d:
        if backend == "claude":
            args = [_find("claude"), "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                    "--strict-mcp-config", "--system-prompt", SYSTEM_PROMPT, "--json-schema", schema]
            if model:
                args += ["--model", model]
            if effort:
                args += ["--effort", effort]
            stdin = prompt
        elif backend == "codex":
            schema_file, out_file = Path(d) / "schema.json", Path(d) / "out.json"
            schema_file.write_text(schema, encoding="utf-8")
            args = [_find("codex"), "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                    "--color", "never", "--output-schema", str(schema_file), "-o", str(out_file)]
            if model:
                args += ["-m", model]
            if effort:
                args += ["-c", f'model_reasoning_effort="{effort}"']
            args.append("-")
            stdin = SYSTEM_PROMPT + "\n\n" + prompt
        else:
            raise NotesError(f"不支援的 AI：{backend}")

        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=d, creationflags=NO_WINDOW)
        if on_process:
            on_process(proc)
        try:
            out, err = proc.communicate(stdin.encode("utf-8"), timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise NotesError("AI 整理逾時")
        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")

        if backend == "claude":
            line = next((l for l in reversed(stdout.splitlines()) if l.startswith("{")), None)
            if not line:
                raise NotesError((stderr or stdout or f"claude 沒有回傳結果（結束代碼 {proc.returncode}）")[-600:])
            obj = json.loads(line)
            if obj.get("is_error") or obj.get("structured_output") is None:
                raise NotesError(str(obj.get("result") or stderr)[-600:])
            u = obj.get("usage") or {}
            models = obj.get("modelUsage") or {}
            used = next(iter(models.values()), {})
            usage = {
                "model": used.get("canonicalModel") or next(iter(models), model or None),
                "effort": effort or default_efforts()["claude"] or None,
                "input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                + (u.get("cache_read_input_tokens") or 0),
                "output_tokens": u.get("output_tokens"),
                "cost_usd": obj.get("total_cost_usd"),
            }
            return _clean_times(obj["structured_output"]), usage

        if proc.returncode != 0 or not out_file.exists() or not out_file.stat().st_size:
            msg = re.search(r'"message"\s*:\s*"([^"]+)"', stderr)
            if msg and "not supported" in msg.group(1):
                raise NotesError(f"Codex 不能用這個模型：{msg.group(1)}\nCodex 版本太舊時也會這樣，可以先更新（npm install -g @openai/codex），或到設定換一個模型、改用 Claude Code。")
            if msg:
                raise NotesError(f"Codex 回報錯誤：{msg.group(1)}")
            raise NotesError((stderr or f"codex 沒有回傳結果（結束代碼 {proc.returncode}）")[-600:])
        m = re.search(r"tokens used[:\s]*([\d,]+)", stderr + stdout, re.I)
        usage = {"model": model or default_models()["codex"] or None,
                 "effort": effort or default_efforts()["codex"] or None,
                 "input_tokens": None, "output_tokens": None, "cost_usd": None,
                 "total_tokens": int(m.group(1).replace(",", "")) if m else None}
        return _clean_times(json.loads(out_file.read_text(encoding="utf-8"))), usage


def available_backends() -> dict:
    return {name: bool(shutil.which(name) or shutil.which(name + ".cmd")) for name in ("claude", "codex")}


# ---- 匯出 ----
def to_markdown(meeting: dict, notes: dict | None, segments: list[dict] | None, include_transcript: bool) -> str:
    def t(v: str) -> str:
        return f" `{v}`" if v else ""

    names = meeting.get("speakers", {})
    out = [f"# {meeting['title']}", "", f"- 日期：{meeting['date']}"]
    if meeting.get("duration"):
        out.append(f"- 長度：{round(meeting['duration'] / 60)} 分鐘")
    if segments:
        people = sorted({s["speaker"] for s in segments if s.get("speaker")})
        if people:
            out.append("- 與會者：" + "、".join(speaker_name(p, names) for p in people))
    out.append("")
    if notes:
        for key, label in (("summary", "摘要"), ("decisions", "決議")):
            if notes.get(key):
                out += [f"## {label}", ""] + [f"- {i['text']}{t(i['time'])}" for i in notes[key]] + [""]
        if notes.get("actionItems"):
            out += ["## 待辦", "", "| 負責人 | 事項 | 期限 |", "|---|---|---|"]
            out += [f"| {a['owner']} | {a['task']}{t(a['time'])} | {a['due']} |" for a in notes["actionItems"]]
            out.append("")
        if notes.get("openQuestions"):
            out += ["## 未決問題", ""] + [f"- {i['text']}{t(i['time'])}" for i in notes["openQuestions"]] + [""]
        for c in notes.get("custom", []):
            if c.get("items"):
                out += [f"## {c['title']}", ""] + [f"- {i['text']}{t(i['time'])}" for i in c["items"]] + [""]
        if notes.get("topics"):
            out += ["## 議題詳細", ""]
            for topic in notes["topics"]:
                out += [f"### {topic['title']}{t(topic['start'])}", ""]
                out += [f"- {p['text']}{t(p['time'])}" for p in topic["points"]] + [""]
        if notes.get("reviewItems"):
            out += ["## 待確認", ""] + [f"- {r['question']}{t(r['time'])}" for r in notes["reviewItems"]] + [""]
    if include_transcript and segments:
        out += ["## 逐字稿", ""]
        for seg in segments:
            who = speaker_name(seg.get("speaker"), names)
            out.append(f"**[{fmt_time(seg['start'])}]** {who + '：' if who else ''}{seg['text']}  ")
        out.append("")
    return "\n".join(out)
