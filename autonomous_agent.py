#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded autonomous runtime layered on top of agent.py."""
from __future__ import annotations
import json, os, re, subprocess, time, traceback, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import agent as base

ROOT = base.ROOT
STATE_FILE = ROOT / os.getenv("AGENT_STATE_FILE", "task_state.json")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))

def iso():
    return datetime.now(timezone.utc).isoformat()

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "tasks": {}}

def save_state(state):
    base.atomic_write(STATE_FILE, state)

def new_task(goal):
    state = load_state()
    tid = f"task-{int(time.time() * 1000)}"
    state["tasks"][tid] = {
        "id": tid, "goal": goal[:10000], "status": "running",
        "step_index": 0, "plan": [], "results": [],
        "created": iso(), "updated": iso()
    }
    save_state(state)
    return tid

def get_task(tid):
    return load_state().get("tasks", {}).get(tid)

def update_task(tid, **changes):
    state = load_state()
    task = state.get("tasks", {}).get(tid)
    if not task:
        return None
    task.update(changes)
    task["updated"] = iso()
    save_state(state)
    return task

def safe_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError("المسار خارج مساحة الوكيل")
    return path

def list_files(path="."):
    ignored = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    out = []
    for item in safe_path(path).rglob("*"):
        if any(part in ignored for part in item.parts):
            continue
        out.append(str(item.relative_to(ROOT)))
        if len(out) >= 200:
            break
    return {"files": out}

def read_file(path):
    p = safe_path(path)
    return {
        "path": str(p.relative_to(ROOT)),
        "content": p.read_text(encoding="utf-8", errors="replace")[:50000]
    }

def write_file(path, content):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content), encoding="utf-8")
    return {"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size}

def web_search(query):
    return {
        "results": [{"title": title, "url": url}
                    for title, url in base.search_web(query, 8)]
    }

def open_url(url):
    return {
        "url": url,
        "text": base.strip_html(base.fetch(url, max_size=900000))[:30000]
    }

def shell(command, timeout=90):
    command = str(command).strip()
    blocked = [
        r"\brm\s+-rf\s+/", r"\bmkfs\b", r"\bshutdown\b",
        r"\breboot\b", r"\bsudo\b",
        r"curl\s+[^|]*\|\s*(sh|bash)",
        r"wget\s+[^|]*\|\s*(sh|bash)"
    ]
    if any(re.search(x, command, re.I) for x in blocked):
        if os.getenv("AUTONOMOUS_ALLOW_DANGEROUS") != "1":
            raise PermissionError("الأمر محظور تلقائيا لأسباب أمنية")
    completed = subprocess.run(
        command, shell=True, cwd=str(ROOT),
        capture_output=True, text=True, timeout=timeout
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-30000:],
        "stderr": completed.stderr[-15000:]
    }

def python_exec(code):
    return shell("python3 -c " + repr(str(code)))

def git(action, args=""):
    allowed = {"status", "diff", "log", "branch", "show", "add", "commit"}
    if action not in allowed:
        raise ValueError("عملية git غير مسموحة")
    return shell("git " + action + ((" " + str(args)) if args else ""), 120)

def github(url, method="GET", payload=None):
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN غير مضبوط")
    if not str(url).startswith("https://api.github.com/"):
        raise ValueError("رابط GitHub API غير مسموح")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "Ai-Agent-Elian"
    }
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method.upper()
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())

TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "web_search": web_search,
    "open_url": open_url,
    "shell": shell,
    "python": python_exec,
    "git": git,
    "github": github
}

def parse_json(text):
    text = str(text).strip()
    text = re.sub(r"^json\s*", "", text, flags=re.I)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("النموذج لم يرجع JSON صالحا")
    return json.loads(match.group(0))

def make_plan(goal, previous=None):
    prompt = (
        "حوّل الهدف إلى خطة تنفيذية لوكيل مستقل. أخرج JSON فقط.\n"
        f"الهدف: {goal}\n\n"
        "الأدوات المتاحة: " + ", ".join(TOOLS.keys())
    )
    if previous:
        prompt += "\nنتائج سابقة:\n" + json.dumps(
            previous[-6:], ensure_ascii=False
        )[:16000]
    prompt += (
        "\nالشكل المطلوب: "
        '{"summary":"...","steps":[{"tool":"...","input":{},'
        '"purpose":"...","verify":"..."}]}'
        "\nاجعل الخطوات قابلة للتنفيذ ولا تستخدم أداة غير موجودة."
    )
    answer, error = base.ai(
        prompt, "agent", 4500,
        system="أنت Planner لوكيل مستقل. أخرج JSON فقط.",
        include_memory=True
    )
    if not answer:
        raise RuntimeError(error)
    plan = parse_json(answer)
    steps = []
    for step in (plan.get("steps") or [])[:MAX_STEPS]:
        if step.get("tool") in TOOLS and isinstance(step.get("input", {}), dict):
            steps.append(step)
    if not steps:
        raise ValueError("الخطة لا تحتوي خطوات أدوات صالحة")
    plan["steps"] = steps
    return plan

def step_ok(result):
    if isinstance(result, dict) and "returncode" in result:
        return result["returncode"] == 0
    if isinstance(result, dict):
        return any(bool(value) for value in result.values())
    return bool(str(result or "").strip())

def run_goal(goal):
    tid = new_task(goal)
    try:
        plan = make_plan(goal)
        update_task(tid, plan=plan["steps"])
        results = []
        for index, step in enumerate(plan["steps"]):
            update_task(tid, step_index=index, status="running")
            inputs = dict(step.get("input") or {})
            previous = json.dumps(results[-3:], ensure_ascii=False)[:12000]
            for key, value in list(inputs.items()):
                if isinstance(value, str):
                    inputs[key] = value.replace("{{PREVIOUS_RESULTS}}", previous)
            try:
                result = TOOLS[step["tool"]](**inputs)
                ok = step_ok(result)
                results.append({
                    "step": index, "tool": step["tool"],
                    "purpose": step.get("purpose", ""),
                    "ok": ok, "result": result
                })
                update_task(tid, results=results)
                if not ok:
                    plan = make_plan(goal, results)
                    update_task(tid, plan=plan["steps"])
                    break
            except Exception as exc:
                results.append({
                    "step": index, "tool": step["tool"],
                    "ok": False, "error": str(exc)
                })
                update_task(tid, results=results)
                plan = make_plan(goal, results)
                update_task(tid, plan=plan["steps"])
                break

        tail = results[-min(3, len(results)):] if results else []
        success = bool(tail) and all(item.get("ok") for item in tail)
        update_task(
            tid, status="completed" if success else "failed",
            results=results
        )
        lines = [
            "تمت معالجة المهمة عبر Autonomous Runtime.",
            f"Task: {tid}",
            f"الخطوات المنفذة: {len(results)}"
        ]
        for item in results[-8:]:
            status = "نجح" if item.get("ok") else "فشل"
            lines.append(f"• {item.get('tool')}: {status}")
        return "\n".join(lines)
    except Exception as exc:
        update_task(tid, status="failed", error=str(exc))
        base.log(traceback.format_exc())
        return f"تعذر إكمال المهمة {tid}: {exc}"

def resume_task(tid):
    task = get_task(tid)
    if not task:
        return "المهمة غير موجودة."
    if task.get("status") == "completed":
        return "المهمة مكتملة بالفعل."
    return run_goal(task["goal"])

def handle_message(text, memory):
    raw = str(text or "").strip()
    if not raw:
        return "اكتب المهمة."
    if raw.startswith("/autostatus"):
        tasks = list(load_state().get("tasks", {}).values())[-10:][::-1]
        if not tasks:
            return "لا توجد مهام مستقلة."
        return "<b>Autonomous Tasks</b>\n" + "\n".join(
            f"{task['id']} — {task['status']} — {task['goal'][:120]}"
            for task in tasks
        )
    if raw.startswith("/resume "):
        return resume_task(raw.split(maxsplit=1)[1])
    if raw.startswith("/"):
        return base.process(raw, memory)
    return run_goal(raw)

def poll():
    if not base.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير مضبوط")
    memory = base.Memory()
    offset = None
    base.tg("deleteWebhook", {"drop_pending_updates": "false"}, timeout=30)
    me = base.tg("getMe", timeout=30)
    base.log("Autonomous polling connected @" + str(me.get("username", "")))
    while True:
        try:
            payload = {"timeout": 50}
            if offset is not None:
                payload["offset"] = offset
            for update in base.tg("getUpdates", payload, timeout=60) or []:
                offset = update.get("update_id", 0) + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = message.get("text") or ""
                if not text or (base.CHAT_ID and chat_id != base.CHAT_ID):
                    continue
                answer = handle_message(text, memory)
                memory.conversation(text, answer, text.split()[0][:100])
                memory.success("message")
                memory.save()
                base.send(chat_id, answer)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            base.log("autonomous poll error: " + base.compact(exc, 700))
            time.sleep(4)

def command_mode():
    goal = os.getenv("TASK", "").strip()
    if not goal or goal.lower() == "help":
        print(base.help_text())
        return
    answer = run_goal(goal)
    print(answer)
    if base.CHAT_ID and base.TELEGRAM_TOKEN:
        base.send(base.CHAT_ID, "<b>Autonomous workflow</b>\n" + answer)

if __name__ == "__main__":
    mode = os.getenv("AGENT_MODE", "poll").strip().lower()
    if mode == "command":
        command_mode()
    elif mode == "once":
        base.once()
    else:
        poll()
