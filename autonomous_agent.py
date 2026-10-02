#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autonomous runtime: persistent planning, tool execution, verification and recovery."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent as base
from core.agent_loop import AgentLoop
from core.tool_registry import ToolRegistry

ROOT = base.ROOT
STATE_FILE = ROOT / os.getenv("AGENT_STATE_FILE", "task_state.json")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))
MAX_REPLANS = int(os.getenv("AGENT_MAX_REPLANS", "3"))
MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "2"))
FULL_ACCESS = os.getenv("AGENT_FULL_ACCESS", "0") == "1"
MAX_WORKERS = int(os.getenv("AGENT_MAX_WORKERS", "2"))
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="elian-agent")
RUNNING = set()

def iso():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid state")
        data.setdefault("version", 2)
        data.setdefault("tasks", {})
        return data
    except Exception:
        return {"version": 2, "tasks": {}}


def save_state(state):
    base.atomic_write(STATE_FILE, state)


def new_task(goal):
    state = load_state()
    tid = f"task-{int(time.time() * 1000)}"
    state["tasks"][tid] = {
        "id": tid,
        "goal": str(goal)[:10000],
        "status": "queued",
        "step_index": 0,
        "plan": [],
        "results": [],
        "replans": 0,
        "created": iso(),
        "updated": iso(),
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
        if len(out) >= 500:
            break
    return {"files": out, "count": len(out)}


def read_file(path):
    p = safe_path(path)
    return {
        "path": str(p.relative_to(ROOT)),
        "content": p.read_text(encoding="utf-8", errors="replace")[:100000],
    }


def write_file(path, content, append=False):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with p.open("a", encoding="utf-8") as f:
            f.write(str(content))
    else:
        p.write_text(str(content), encoding="utf-8")
    return {"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size}


def delete_file(path):
    p = safe_path(path)
    if p == ROOT:
        raise PermissionError("لا يمكن حذف مساحة العمل نفسها")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)
    return {"deleted": str(p.relative_to(ROOT))}


def copy_file(source, destination):
    src, dst = safe_path(source), safe_path(destination)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return {"source": str(src.relative_to(ROOT)), "destination": str(dst.relative_to(ROOT))}


def move_file(source, destination):
    src, dst = safe_path(source), safe_path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"source": str(src.relative_to(ROOT)), "destination": str(dst.relative_to(ROOT))}


def web_search(query):
    return {"results": [{"title": t, "url": u} for t, u in base.search_web(query, 10)]}


def open_url(url):
    if not base.safe_url(url):
        raise ValueError("الرابط غير صالح أو غير مسموح")
    return {"url": url, "text": base.strip_html(base.fetch(url, max_size=900000))[:50000]}


def browser(action, **kwargs):
    """Real browser automation through optional Playwright/Chromium."""
    try:
        from tools import browser as browser_module
    except ImportError as exc:
        raise RuntimeError("Playwright/browser module غير متاح") from exc
    fn = getattr(browser_module, "browser_" + str(action), None)
    if not fn:
        raise ValueError("Browser action غير معروفة: " + str(action))
    return fn(**kwargs)

def shell(command, timeout=120):
    command = str(command).strip()
    if not command:
        raise ValueError("أمر فارغ")

    dangerous = [
        r"\brm\s+-rf\s+/(?:\s|$)",
        r"\bmkfs(?:\.|\s)",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"curl\s+[^|]*\|\s*(?:sh|bash)",
        r"wget\s+[^|]*\|\s*(?:sh|bash)",
    ]
    if not FULL_ACCESS and any(re.search(x, command, re.I) for x in dangerous):
        raise PermissionError("الأمر محظور. فعّل AGENT_FULL_ACCESS=1 إذا كان هذا مقصودا.")

    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=int(timeout),
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-50000:],
        "stderr": completed.stderr[-30000:],
    }


def python_exec(code):
    return shell("python3 -c " + repr(str(code)), timeout=120)


def git(action, args=""):
    allowed = {
        "status", "diff", "log", "branch", "show", "add", "commit",
        "push", "pull", "fetch", "checkout", "switch", "merge", "reset",
        "restore", "tag", "remote", "rev-parse",
    }
    action = str(action).strip()
    if action not in allowed:
        raise ValueError("عملية git غير مسموحة")
    return shell("git " + action + ((" " + str(args)) if args else ""), timeout=180)


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
        "User-Agent": "Ai-Agent-Elian",
    }
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=headers, method=str(method).upper()
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else {}


def process_list_files(**x): return list_files(**x)
def process_read_file(**x): return read_file(**x)


REGISTRY = ToolRegistry()
REGISTRY.register("list_files", process_list_files, "list workspace files", "low", True, 30)
REGISTRY.register("read_file", process_read_file, "read a UTF-8 workspace file", "low", True, 30)
REGISTRY.register("write_file", write_file, "create or replace a workspace file", "high", False, 30)
REGISTRY.register("delete_file", delete_file, "delete a workspace file or directory", "critical", False, 30)
REGISTRY.register("copy_file", copy_file, "copy a workspace file or directory", "medium", True, 30)
REGISTRY.register("move_file", move_file, "move a workspace file or directory", "high", False, 30)
REGISTRY.register("web_search", web_search, "search the public web", "low", True, 30)
REGISTRY.register("open_url", open_url, "fetch and extract a public web page", "low", True, 45)
REGISTRY.register("browser", browser, "control a Chromium browser: open, click, fill, press, snapshot, screenshot, close", "high", False, 60)
REGISTRY.register("shell", shell, "execute an OS shell command in the workspace", "critical", False, 120)
REGISTRY.register("python", python_exec, "execute Python code", "critical", False, 120)
REGISTRY.register("git", git, "inspect or modify the Git repository", "high", False, 180)
REGISTRY.register("github", github, "call the GitHub REST API using the workflow token", "high", False, 45)


def parse_json(text):
    text = re.sub(r"^json\s*", "", str(text).strip(), flags=re.I)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("النموذج لم يرجع JSON صالحا")
    return json.loads(match.group(0))


def make_plan(goal, previous=None):
    prompt = (
        "أنت المخطط التنفيذي لوكيل مستقل. حوّل الهدف إلى خطوات قابلة للتنفيذ. "
        "أخرج JSON فقط. لا تشرح خارج JSON.\n"
        f"الهدف: {goal}\n\n"
        "الأدوات المتاحة:\n" + REGISTRY.schema_text() + "\n"
        "كل خطوة يجب أن تحتوي tool وinput وpurpose وverify. "
        "verify يمكن أن يكون: returncode_zero أو nonempty أو "
        "path_exists أو contains أو equals، أو نصا يصف دليل التحقق. "
        "لا تستخدم أداة غير موجودة."
    )
    if previous:
        prompt += "\nنتائج الخطوات السابقة:\n" + json.dumps(
            previous[-8:], ensure_ascii=False
        )[:24000]

    prompt += (
        '\nالشكل: {"summary":"...","steps":[{"tool":"...",'
        '"input":{},"purpose":"...","verify":"..."}]}'
    )
    answer, error = base.ai(
        prompt,
        "agent",
        5000,
        system="أنت Planner لوكيل مستقل. أخرج JSON صالحا فقط.",
        include_memory=True,
    )
    if not answer:
        raise RuntimeError(error)

    plan = parse_json(answer)
    steps = []
    for step in (plan.get("steps") or [])[:MAX_STEPS]:
        if (
            isinstance(step, dict)
            and step.get("tool") in REGISTRY.names()
            and isinstance(step.get("input", {}), dict)
        ):
            steps.append(step)
    if not steps:
        raise ValueError("الخطة لا تحتوي خطوات أدوات صالحة")
    plan["steps"] = steps
    return plan


def verify_step(step, result, task=None):
    rule = step.get("verify", "") if isinstance(step, dict) else ""

    if isinstance(result, dict) and "returncode" in result:
        if result["returncode"] != 0:
            return False

    if isinstance(rule, dict):
        kind = rule.get("type")
        if kind == "path_exists":
            return safe_path(rule["path"]).exists()
        if kind == "contains":
            needle = str(rule.get("text", ""))
            return needle in json.dumps(result, ensure_ascii=False)
        if kind == "equals":
            return result == rule.get("value")
        if kind == "nonempty":
            return bool(result)
        if kind == "returncode_zero":
            return isinstance(result, dict) and result.get("returncode") == 0

    rule_text = str(rule).strip().lower()
    if rule_text in {"", "nonempty"}:
        return bool(result)
    if rule_text == "returncode_zero":
        return isinstance(result, dict) and result.get("returncode") == 0
    if rule_text.startswith("contains:"):
        return rule_text[9:] in json.dumps(result, ensure_ascii=False).lower()
    if rule_text.startswith("path_exists:"):
        return safe_path(str(rule)[12:].strip()).exists()

    # For a free-form final verification, ask the model for a strict verdict.
    if task and task.get("goal") and len(str(step.get("purpose", ""))) > 0:
        evidence = json.dumps(result, ensure_ascii=False)[:18000]
        answer, _ = base.ai(
            "تحقق من الدليل التالي بالنسبة للهدف. أجب بكلمة PASS أو FAIL فقط.\n"
            f"الهدف: {task.get('goal')}\n"
            f"تعليمات التحقق: {rule}\nالدليل: {evidence}",
            "review",
            1000,
            system="أنت Verifier. لا تشرح. أجب PASS أو FAIL فقط.",
            include_memory=False,
        )
        return str(answer or "").strip().upper().startswith("PASS")

    return bool(result)


def final_verify(goal, results):
    if not results:
        return False
    answer, _ = base.ai(
        "قرر هل تم تحقيق الهدف بالكامل اعتمادا على سجل التنفيذ. "
        "أجب PASS أو FAIL فقط.\n"
        f"الهدف: {goal}\nالسجل:\n{json.dumps(results[-10:], ensure_ascii=False)[:30000]}",
        "review",
        1000,
        system="أنت مدقق نهائي صارم. لا تشرح. أجب PASS أو FAIL فقط.",
        include_memory=False,
    )
    return str(answer or "").strip().upper().startswith("PASS")


def run_goal(goal, resume_id=None):
    tid = resume_id or new_task(goal)
    task = get_task(tid)
    if not task:
        return "المهمة غير موجودة."

    loop = AgentLoop(
        registry=REGISTRY,
        load_task=get_task,
        update_task=update_task,
        plan=make_plan,
        verify=verify_step,
        max_steps=MAX_STEPS,
        max_replans=MAX_REPLANS,
        max_retries=MAX_RETRIES,
    )

    try:
        result = loop.run(tid, task["goal"], resume=bool(resume_id))
        if result.get("status") == "completed":
            final = final_verify(result["goal"], result.get("results", []))
            update_task(tid, status="completed" if final else "failed",
                        verified=final, final_verified_at=iso())
            result = get_task(tid)
        lines = [
            f"Task: {tid}",
            f"الحالة: {result.get('status')}",
            f"الخطوات: {len(result.get('results') or [])}",
            f"إعادة التخطيط: {result.get('replans', 0)}",
        ]
        if result.get("error"):
            lines.append("الخطأ: " + str(result["error"])[:1000])
        return "\n".join(lines)
    except Exception as exc:
        update_task(tid, status="failed", error=str(exc),
                    traceback=traceback.format_exc()[-6000:])
        return f"تعذر إكمال المهمة {tid}: {exc}"


def resume_task(tid):
    task = get_task(tid)
    if not task:
        return "المهمة غير موجودة."
    if task.get("status") == "completed":
        return "المهمة مكتملة بالفعل."
    return run_goal(task["goal"], resume_id=tid)


def cancel_task(tid):
    task = get_task(tid)
    if not task:
        return "المهمة غير موجودة."
    update_task(tid, status="cancelled", cancelled_at=iso())
    return f"تم إلغاء {tid}."


def task_status(tid=None):
    if tid:
        task = get_task(tid)
        if not task:
            return "المهمة غير موجودة."
        return json.dumps(task, ensure_ascii=False, indent=2)[:15000]
    tasks = list(load_state().get("tasks", {}).values())[-15:][::-1]
    if not tasks:
        return "لا توجد مهام."
    return "<b>Autonomous Tasks</b>\n" + "\n".join(
        f"{x['id']} — {x['status']} — step {x.get('step_index', 0)} — {x['goal'][:120]}"
        for x in tasks
    )


def _notify_task(task):
    if not task or not base.TELEGRAM_TOKEN or not base.CHAT_ID:
        return
    try:
        base.send(base.CHAT_ID, f"المهمة {task.get('id')} انتهت.\\nالحالة: {task.get('status')}\\n"
                  f"الخطوات: {len(task.get('results') or [])}\\nإعادة التخطيط: {task.get('replans', 0)}")
    except Exception as exc:
        base.log("task notification error: " + str(exc))

def _run_background(tid):
    if tid in RUNNING:
        return
    RUNNING.add(tid)
    try:
        result = run_goal(get_task(tid)["goal"], resume_id=tid)
        _notify_task(get_task(tid))
        return result
    except Exception as exc:
        update_task(tid, status="failed", error=str(exc),
                    traceback=traceback.format_exc()[-8000:])
        _notify_task(get_task(tid))
    finally:
        RUNNING.discard(tid)

def submit_task(tid):
    task = get_task(tid)
    if not task:
        return "المهمة غير موجودة."
    if tid in RUNNING:
        return "المهمة تعمل بالفعل: " + tid
    update_task(tid, status="queued")
    EXECUTOR.submit(_run_background, tid)
    return f"بدأت المهمة في الخلفية: {tid}\\nاستخدم /autostatus {tid} لمتابعتها."

def resume_recoverable_tasks():
    for task in load_state().get("tasks", {}).values():
        if task.get("status") in {"queued", "planning", "running", "recovering", "replanning"}:
            submit_task(task["id"])

def handle_message(text, memory):
    raw = str(text or "").strip()
    if not raw:
        return "اكتب المهمة."

    if raw.startswith("/autostatus"):
        parts = raw.split(maxsplit=1)
        return task_status(parts[1] if len(parts) > 1 else None)

    if raw.startswith("/resume "):
        return resume_task(raw.split(maxsplit=1)[1].strip())

    if raw.startswith("/cancel "):
        return cancel_task(raw.split(maxsplit=1)[1].strip())

    if raw.startswith("/access"):
        return (
            "AGENT_FULL_ACCESS=" + ("1" if FULL_ACCESS else "0") +
            "\nصلاحيات الأدوات: " + ", ".join(REGISTRY.names())
        )

    if raw.startswith("/tools"):
        return "<b>Tools</b>\n" + REGISTRY.schema_text()

    if raw.startswith("/"):
        return base.process(raw, memory)

    tid = new_task(raw)
    return submit_task(tid)
def poll():
    if not base.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير مضبوط")

    memory = base.Memory()
    offset = None
    base.tg("deleteWebhook", {"drop_pending_updates": "false"}, timeout=30)
    me = base.tg("getMe", timeout=30)
    base.log("Real Agent polling connected @" + str(me.get("username", "")))
    resume_recoverable_tasks()
    while True:
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
            base.log("agent poll error: " + base.compact(exc, 1000))
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
