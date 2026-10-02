#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI Personal Agent v6 — Unified Autonomous/Controlled Edition

Telegram + OpenRouter + Web/Scientific/Jobs/Courses/Grants/Email/Memory/Tasks/Skills.

Design:
- No API keys in source code.
- Telegram is the primary interface.
- OpenRouter provides the LLM with model fallback/cooldown.
- External HTTP requests use URL validation and redirects are rejected.
- Gmail is read-only in v6.
- Skills are generated into skills/ after static validation and may be
  executed explicitly with /runskill.
- Self-evolution creates a reviewed git patch and never applies automatically.
- Scheduled GitHub Actions can run TASK in command mode or due tasks in once mode.
"""

from __future__ import annotations

import ast
import email
import email.header
import html
import imaplib
import ipaddress
import json
import os
import py_compile
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


# ============================================================
# Config
# ============================================================

ROOT = Path(os.getenv("AGENT_ROOT", Path(__file__).resolve().parent)).resolve()

MEMORY_FILE = ROOT / os.getenv("AGENT_MEMORY_FILE", "memory.json")
COOLDOWN_FILE = ROOT / os.getenv("AGENT_COOLDOWN_FILE", ".model_cooldown.json")
TASKS_FILE = ROOT / os.getenv("AGENT_TASKS_FILE", "tasks.json")
SAVED_FILE = ROOT / os.getenv("AGENT_SAVED_FILE", "saved_items.json")
WATCH_FILE = ROOT / os.getenv("AGENT_WATCH_FILE", "watchers.json")
ACTIONS_FILE = ROOT / os.getenv("AGENT_ACTIONS_FILE", "pending_actions.json")

SKILLS_DIR = ROOT / "skills"
PROPOSALS_DIR = ROOT / "proposals"
LOG_FILE = ROOT / "agent.log"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()
EMAIL_HOST = os.getenv("EMAIL_HOST", "imap.gmail.com").strip()

try:
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "993"))
except ValueError:
    EMAIL_PORT = 993

SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()

MAX_TG = 3900
HTTP_TIMEOUT = 25
AI_TIMEOUT = 90
MAX_FETCH = 500_000
MAX_PAGE_TEXT = 35_000
MAX_HISTORY = 100
MAX_MEMORY_NOTES = 500
MAX_SAVED = 1000
MAX_TASKS = 200
MAX_WATCHERS = 100
MAX_ACTIONS = 200

# These are fallbacks only. When OpenRouter is reachable, free models are
# discovered dynamically from /models unless OPENROUTER_MODELS is supplied.
FALLBACK_MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-r1:free",
]

ENV_MODELS = [
    x.strip()
    for x in os.getenv("OPENROUTER_MODELS", "").split(",")
    if x.strip()
]

SYSTEM_PROMPT = """أنت AI Personal Agent.
أجب بالعربية المبسطة والدقيقة ما لم يطلب المستخدم لغة أخرى.
لا تخترع مصادر أو نتائج أو مفاتيح API أو صلاحيات.
عند المعلومات الحديثة استخدم أداة البحث المناسبة بدل التخمين.
عند البرمجة أعط كودا كاملا قابلا للاختبار.
لا تقترح تنفيذ عمليات خارجية حساسة وكأنها حدثت فعلا.
لا ترسل أو تحذف أو تعدل بريدا في هذا الإصدار.
لا تطبق patches أو تغييرات ذات أثر دائم تلقائيا.
عند استخدام المصادر، ميّز بين البيانات التي جاءت من المصدر وبين استنتاجك.
"""


# ============================================================
# Utilities
# ============================================================

def now() -> datetime:
    return datetime.now(timezone.utc)


def iso() -> str:
    return now().isoformat()


def log(msg: str) -> None:
    line = f"[{iso()}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def compact(text: Any, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def safe_int(value: Any, default: int, minimum: int | None = None,
             maximum: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def unique_keep_order(values):
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def strip_html(text: str) -> str:
    parser = TextExtractor()
    parser.feed(text or "")
    parser.close()
    return re.sub(r"\s+", " ", parser.text()).strip()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


# ============================================================
# Memory
# ============================================================

class Memory:
    def __init__(self):
        self.data = read_json(
            MEMORY_FILE,
            {
                "version": 6,
                "created_at": iso(),
                "last_updated": iso(),
                "user": {
                    "language": "ar",
                    "timezone": "Africa/Cairo"
                },
                "conversations": [],
                "errors": [],
                "successes": [],
                "skills": [],
                "notes": [],
                "projects": [],
                "model_preferences": {},
                "learning": {
                    "goals": [],
                    "flashcards": [],
                    "progress": []
                },
                "stats": {
                    "runs": 0,
                    "success": 0,
                    "failure": 0,
                    "commands": {}
                }
            }
        )
        self.data.setdefault("version", 6)
        self.data.setdefault("created_at", iso())
        self.data.setdefault("last_updated", iso())
        self.data.setdefault("user", {"language": "ar", "timezone": "Africa/Cairo"})
        self.data.setdefault("conversations", [])
        self.data.setdefault("errors", [])
        self.data.setdefault("successes", [])
        self.data.setdefault("skills", [])
        self.data.setdefault("notes", [])
        self.data.setdefault("projects", [])
        self.data.setdefault("model_preferences", {})
        self.data.setdefault("learning", {"goals": [], "flashcards": [], "progress": []})
        self.data.setdefault(
            "stats",
            {"runs": 0, "success": 0, "failure": 0, "commands": {}}
        )
        self.data["stats"].setdefault("runs", 0)
        self.data["stats"].setdefault("success", 0)
        self.data["stats"].setdefault("failure", 0)
        self.data["stats"].setdefault("commands", {})

    def save(self):
        self.data["last_updated"] = iso()
        if MEMORY_FILE.exists():
            try:
                shutil.copy2(MEMORY_FILE, str(MEMORY_FILE) + ".bak")
            except OSError:
                pass
        atomic_write(MEMORY_FILE, self.data)

    def conversation(self, user, answer, command):
        self.data["conversations"].append(
            {
                "time": iso(),
                "command": str(command)[:100],
                "user": str(user)[:1500],
                "agent": str(answer)[:5000]
            }
        )
        self.data["conversations"] = self.data["conversations"][-MAX_HISTORY:]
        self.data["stats"]["runs"] += 1

    def success(self, command):
        self.data["stats"]["success"] += 1
        commands = self.data["stats"]["commands"]
        commands[command] = commands.get(command, 0) + 1

        self.data["successes"].append(
            {"time": iso(), "command": str(command)[:100]}
        )
        self.data["successes"] = self.data["successes"][-MAX_HISTORY:]

    def failure(self, command, err):
        self.data["stats"]["failure"] += 1
        self.data["errors"].append(
            {
                "time": iso(),
                "command": str(command)[:100],
                "error": compact(err, 1000)
            }
        )
        self.data["errors"] = self.data["errors"][-MAX_HISTORY:]

    def remember(self, kind, text, tags=None):
        entry = {
            "id": f"{kind}-{int(time.time() * 1000)}",
            "kind": kind,
            "text": str(text)[:4000],
            "tags": list(tags or [])[:20],
            "created": iso()
        }
        self.data["notes"].append(entry)
        self.data["notes"] = self.data["notes"][-MAX_MEMORY_NOTES:]
        self.save()
        return entry

    def recall(self, query, limit=8):
        words = {x.lower() for x in re.findall(r"\w+", str(query))}
        if not words:
            return []

        matches = []
        for entry in self.data.get("notes", []) + self.data.get("projects", []):
            haystack = " ".join(
                (
                    entry.get("text", ""),
                    " ".join(map(str, entry.get("tags", [])))
                )
            ).lower()
            score = sum(word in haystack for word in words)
            if score:
                matches.append((score, entry))

        matches.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in matches[:limit]]

    def context(self, query, limit=5):
        entries = self.recall(query, limit)
        if not entries:
            return ""
        return "\n".join(
            f"- {compact(entry.get('text', ''), 900)}"
            for entry in entries
        )

    def summary(self):
        s = self.data["stats"]
        last_updated = self.data.get("last_updated", "غير متاح")
        return (
            "<b>الذاكرة</b>\n"
            f"• التشغيلات: {s['runs']}\n"
            f"• الناجحة: {s['success']}\n"
            f"• الفاشلة: {s['failure']}\n"
            f"• الملاحظات: {len(self.data.get('notes', []))}\n"
            f"• المشاريع: {len(self.data.get('projects', []))}\n"
            f"• Skills: {len(self.data.get('skills', []))}\n"
            f"• آخر تحديث: {esc(last_updated[:19])}"
        )


# ============================================================
# HTTP / SSRF
# ============================================================

def private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def resolve_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    if not infos:
        return False

    return all(not private_ip(info[4][0]) for info in infos)


def safe_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(str(url).strip())
        if (
            p.scheme not in ("http", "https")
            or p.username
            or p.password
            or not p.hostname
            or len(str(url)) > 4000
        ):
            return False

        host = p.hostname
        if host.lower() in {"localhost", "localhost.localdomain"}:
            return False
        if private_ip(host):
            return False
        return resolve_public(host)
    except Exception:
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("إعادة التوجيه غير مسموحة لأسباب أمنية")


def fetch(url, timeout=HTTP_TIMEOUT, max_size=MAX_FETCH, headers=None):
    if not safe_url(url):
        raise ValueError("الرابط غير مسموح أو يشير إلى عنوان داخلي")

    request_headers = {
        "User-Agent": "AI-Personal-Agent/6.0",
        "Accept": (
            "text/html,application/xhtml+xml,application/json,"
            "text/plain,*/*"
        )
    }
    if headers:
        request_headers.update(headers)

    req = urllib.request.Request(str(url), headers=request_headers)
    opener = urllib.request.build_opener(NoRedirect())

    with opener.open(req, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            if int(content_length) > max_size:
                raise ValueError("المحتوى أكبر من الحد المسموح")

        raw = response.read(max_size + 1)
        if len(raw) > max_size:
            raise ValueError("المحتوى أكبر من الحد المسموح")

        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def get_json(url, **kwargs):
    return json.loads(fetch(url, **kwargs))


# ============================================================
# Telegram
# ============================================================

def tg(method, data=None, timeout=40):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN غير مضبوط")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    body = urllib.parse.urlencode(data or {}).encode()

    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode())

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram error"))
    return result.get("result")


def tg_html(text):
    text = str(text).replace("\r\n", "\n")

    blocks = []

    def block_repl(match):
        blocks.append(match.group(1))
        return f"\x00B{len(blocks) - 1}\x00"

    text = re.sub(
        r"```(?:[^\n]*)\n?(.*?)```",
        block_repl,
        text,
        flags=re.S
    )

    codes = []

    def code_repl(match):
        codes.append(match.group(1))
        return f"\x00C{len(codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", code_repl, text)
    text = html.escape(text, quote=False)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)

    for i, value in enumerate(blocks):
        text = text.replace(
            f"\x00B{i}\x00",
            "<pre>" + html.escape(value, quote=False) + "</pre>"
        )

    for i, value in enumerate(codes):
        text = text.replace(
            f"\x00C{i}\x00",
            "<code>" + html.escape(value, quote=False) + "</code>"
        )

    return text


def chunks(text, limit=MAX_TG):
    text = str(text)
    if len(text) <= limit:
        return [text]

    result = []
    current = ""

    for line in text.splitlines(True):
        if len(line) > limit:
            if current:
                result.append(current)
                current = ""
            while len(line) > limit:
                result.append(line[:limit])
                line = line[limit:]
            current = line
        elif len(current) + len(line) <= limit:
            current += line
        else:
            result.append(current)
            current = line

    if current:
        result.append(current)
    return result


def send(chat_id, text):
    rendered = tg_html(text)
    try:
        for part in chunks(rendered):
            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": part,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true"
                }
            )
    except Exception:
        plain = re.sub(r"<[^>]+>", "", rendered)
        for part in chunks(plain):
            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": part,
                    "disable_web_page_preview": "true"
                }
            )


# ============================================================
# OpenRouter
# ============================================================

class Cooldown:
    def __init__(self):
        self.data = read_json(COOLDOWN_FILE, {})

    def ok(self, model):
        value = self.data.get(model)
        if not value:
            return True
        try:
            return datetime.fromisoformat(value) <= now()
        except Exception:
            self.data.pop(model, None)
            return True

    def set(self, model, seconds):
        self.data[model] = (now() + timedelta(seconds=seconds)).isoformat()

    def save(self):
        atomic_write(COOLDOWN_FILE, self.data)


def discover_free_models():
    if not OPENROUTER_API_KEY:
        return []

    try:
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "User-Agent": "AI-Personal-Agent/6.0"
            }
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            obj = json.loads(response.read().decode())

        models = []
        for item in obj.get("data", []):
            model_id = str(item.get("id", ""))
            pricing = item.get("pricing") or {}
            if not model_id:
                continue
            input_price = str(pricing.get("prompt", ""))
            output_price = str(pricing.get("completion", ""))
            if input_price in {"0", "0.0"} and output_price in {"0", "0.0"}:
                models.append(model_id)

        return models[:40]
    except Exception:
        return []


def model_candidates(task):
    cd = Cooldown()
    preferred = Memory().data.get("model_preferences", {}).get(task)

    dynamic = discover_free_models()
    base = ENV_MODELS or dynamic or FALLBACK_MODELS

    # Simple task preferences without pretending to understand model quality.
    coding = [m for m in base if any(k in m.lower() for k in ("coder", "code", "qwen"))]
    reasoning = [m for m in base if any(k in m.lower() for k in ("r1", "reason", "deepseek"))]
    selected = coding + reasoning + list(base) if task in {"code", "review", "evolution"} else list(base)

    models = unique_keep_order(([preferred] if preferred else []) + selected)

    return [m for m in models if cd.ok(m)]


def ai(prompt, task="general", max_tokens=2500, system=None, include_memory=True):
    if not OPENROUTER_API_KEY:
        return None, "OPENROUTER_API_KEY غير مضبوط"

    memory = Memory()
    cooldown = Cooldown()
    context = memory.context(prompt, limit=5) if include_memory else ""

    final_prompt = str(prompt)
    if context:
        final_prompt += "\n\nسياق ذاكرة مرتبط، استخدمه فقط عندما يكون ذا صلة:\n" + context

    last_error = "لا يوجد نموذج متاح"

    for model in model_candidates(task):
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system or SYSTEM_PROMPT
                },
                {"role": "user", "content": final_prompt}
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/",
                "X-Title": "AI Personal Agent v6"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as response:
                obj = json.loads(response.read().decode())

            choice = (obj.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")

            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict)
                )

            if not content:
                raise RuntimeError("استجابة فارغة")

            memory.data.setdefault("model_preferences", {})[task] = model
            memory.save()
            cooldown.save()
            return str(content).strip(), None

        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode(errors="replace")
            except Exception:
                pass

            last_error = f"{model}: HTTP {exc.code}"
            if exc.code == 401:
                return None, "مفتاح OpenRouter غير صالح"
            if exc.code == 403:
                return None, "OpenRouter رفض الطلب (403)"
            if exc.code == 404:
                cooldown.set(model, 86400)
            elif exc.code == 429:
                cooldown.set(model, 3600)
            else:
                cooldown.set(model, 600)

            if body:
                log(f"OpenRouter {model}: {compact(body, 500)}")

        except Exception as exc:
            last_error = f"{model}: {compact(exc, 250)}"
            cooldown.set(model, 120)

    cooldown.save()
    return None, last_error


# ============================================================
# Web / News / Pages
# ============================================================

def search_web(query, limit=8):
    query = str(query).strip()
    if not query:
        return []

    url = (
        "https://html.duckduckgo.com/html/?"
        + urllib.parse.urlencode({"q": query})
    )
    raw = fetch(url, timeout=20, max_size=700_000)

    results = []
    seen = set()

    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        raw,
        re.S
    ):
        link = html.unescape(match.group(1))
        title = strip_html(html.unescape(match.group(2))).strip()

        if link.startswith("//"):
            link = "https:" + link

        if not title or not link or link in seen:
            continue

        seen.add(link)
        results.append((title, link))
        if len(results) >= limit:
            break

    return results


def search_lines(title, query, limit=8):
    results = search_web(query, limit)
    lines = [f"<b>{esc(title)}: {esc(query)}</b>"]
    if not results:
        lines.append("لا توجد نتائج.")
        return "\n\n".join(lines), results

    for i, (name, url) in enumerate(results, 1):
        lines.append(f"{i}. <b>{esc(name)}</b>\n{esc(url)}")

    return "\n\n".join(lines), results


def cmd_search(query, memory=None):
    try:
        output, _ = search_lines("نتائج البحث", query, 8)
        return output
    except Exception as exc:
        return f"تعذر البحث: {esc(exc)}"


def cmd_news(query):
    q = query or "latest news"
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(
            {"q": q, "hl": "ar", "gl": "EG", "ceid": "EG:ar"}
        )
    )
    xml = fetch(url, timeout=20, max_size=400_000)
    root = ET.fromstring(xml)
    lines = [f"<b>أخبار: {esc(q)}</b>"]

    for item in root.findall(".//item")[:8]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        date = item.findtext("pubDate") or ""
        lines.append(
            f"<b>{esc(title)}</b>\n{esc(date)}\n{esc(link)}"
        )

    return "\n\n".join(lines)


def cmd_open(url):
    if not safe_url(url):
        return "الرابط غير صالح أو غير مسموح."

    raw = fetch(url, timeout=25, max_size=800_000)
    text = strip_html(raw)[:MAX_PAGE_TEXT]
    if not text:
        return "لم أستطع استخراج نص مفيد من الصفحة."

    return (
        f"<b>محتوى الصفحة</b>\n"
        f"<code>{esc(url)}</code>\n\n"
        f"{esc(text)}"
    )


def cmd_summarize(url):
    if not safe_url(url):
        return "الرابط غير صالح أو غير مسموح."

    raw = fetch(url, timeout=25, max_size=1_000_000)
    text = strip_html(raw)[:MAX_PAGE_TEXT]
    if not text:
        return "لم أستطع استخراج نص من الصفحة."

    answer, error = ai(
        "لخص الصفحة بالعربية في نقاط واضحة. اذكر القيود أو المعلومات غير المؤكدة "
        "ولا تخترع تفاصيل.\n\n" + text,
        "summarize",
        2600
    )
    return answer or "تعذر التلخيص: " + esc(error)


def cmd_wiki(query):
    if not query:
        return "اكتب الموضوع."

    url = (
        "https://ar.wikipedia.org/api/rest_v1/page/summary/"
        + urllib.parse.quote(query.replace(" ", "_"))
    )
    d = get_json(url)
    return (
        f"<b>{esc(d.get('title', query))}</b>\n"
        f"{esc(d.get('extract', 'لا يوجد ملخص.'))[:3500]}"
    )


def cmd_weather(city):
    city = city or "Cairo"
    geocode = get_json(
        "https://geocoding-api.open-meteo.com/v1/search?"
        + urllib.parse.urlencode(
            {
                "name": city,
                "count": 1,
                "language": "ar",
                "format": "json"
            }
        )
    )
    results = geocode.get("results") or []
    if not results:
        return "لم أجد المدينة."

    place = results[0]
    weather = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "timezone": "auto"
            }
        )
    )
    current = weather.get("current", {})
    return (
        f"<b>الطقس — {esc(place.get('name', city))}</b>\n"
        f"• الحرارة: {current.get('temperature_2m', '؟')}°C\n"
        f"• الرطوبة: {current.get('relative_humidity_2m', '؟')}%\n"
        f"• الرياح: {current.get('wind_speed_10m', '؟')} كم/س"
    )


# ============================================================
# Scientific search
# ============================================================

def cmd_arxiv(query):
    if not query:
        return "اكتب موضوع البحث."

    url = (
        "https://export.arxiv.org/api/query?"
        + urllib.parse.urlencode(
            {
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": 8,
                "sortBy": "submittedDate",
                "sortOrder": "descending"
            }
        )
    )
    root = ET.fromstring(fetch(url, max_size=700_000))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    lines = [f"<b>arXiv: {esc(query)}</b>"]

    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        aid = entry.findtext("a:id", default="", namespaces=ns)
        summary = (
            entry.findtext("a:summary", default="", namespaces=ns) or ""
        ).strip()
        lines.append(
            f"<b>{esc(title)}</b>\n{esc(summary[:650])}\n{esc(aid)}"
        )

    return "\n\n".join(lines) if len(lines) > 1 else "لا توجد نتائج."


def cmd_pubmed(query):
    if not query:
        return "اكتب موضوع البحث."

    search = get_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode(
            {"db": "pubmed", "term": query, "retmax": 8, "retmode": "json"}
        )
    )
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return "لا توجد نتائج."

    summary = get_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode(
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        )
    )
    lines = [f"<b>PubMed: {esc(query)}</b>"]

    for pmid in ids:
        item = summary.get("result", {}).get(pmid, {})
        lines.append(
            f"<b>{esc(item.get('title', ''))}</b>\n"
            f"PMID: {esc(pmid)}"
        )

    return "\n\n".join(lines)


def cmd_openalex(query):
    if not query:
        return "اكتب موضوع البحث."

    data = get_json(
        "https://api.openalex.org/works?"
        + urllib.parse.urlencode({"search": query, "per-page": 8})
    )
    lines = [f"<b>OpenAlex: {esc(query)}</b>"]

    for item in data.get("results", []):
        lines.append(
            f"<b>{esc(item.get('title', ''))}</b>\n"
            f"السنة: {item.get('publication_year', '؟')}\n"
            f"DOI: {esc(item.get('doi', ''))}"
        )

    return "\n\n".join(lines) if len(lines) > 1 else "لا توجد نتائج."


def cmd_semantic(query):
    if not query:
        return "اكتب موضوع البحث."

    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    data = get_json(
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode(
            {
                "query": query,
                "limit": 8,
                "fields": "title,year,authors,abstract,url,externalIds"
            }
        ),
        headers=headers
    )
    lines = [f"<b>Semantic Scholar: {esc(query)}</b>"]

    for item in data.get("data", []):
        authors = ", ".join(
            str(author.get("name", ""))
            for author in item.get("authors", [])[:4]
        )
        doi = (item.get("externalIds") or {}).get("DOI", "")
        abstract = compact(item.get("abstract", ""), 600)
        lines.append(
            f"<b>{esc(item.get('title', ''))}</b>\n"
            f"{esc(authors)} — {item.get('year', '؟')}\n"
            f"DOI: {esc(doi)}\n"
            f"{esc(abstract)}\n"
            f"{esc(item.get('url', ''))}"
        )

    return "\n\n".join(lines) if len(lines) > 1 else "لا توجد نتائج."


def cmd_crossref(query):
    if not query:
        return "اكتب عنوان الورقة أو اسم الباحث أو كلمات البحث."

    data = get_json(
        "https://api.crossref.org/works?"
        + urllib.parse.urlencode(
            {
                "query.bibliographic": query,
                "rows": 8,
                "select": "title,author,DOI,published,abstract,URL"
            }
        )
    )
    items = data.get("message", {}).get("items", [])
    if not items:
        return "لا توجد نتائج من Crossref."

    lines = [f"<b>Crossref: {esc(query)}</b>"]

    for item in items:
        title = (item.get("title") or ["بدون عنوان"])[0]
        authors = ", ".join(
            " ".join(filter(None, (author.get("given"), author.get("family"))))
            for author in item.get("author", [])[:4]
        )
        abstract = strip_html(item.get("abstract", ""))
        doi = item.get("DOI", "")
        url = item.get("URL", "")

        lines.append(
            f"<b>{esc(title)}</b>\n"
            f"{esc(authors or 'المؤلفون غير متاحين')}\n"
            f"DOI: {esc(doi)}\n"
            f"{esc(abstract[:600])}\n"
            f"{esc(url)}"
        )

    return "\n\n".join(lines)


# ============================================================
# Jobs / Grants / Courses
# ============================================================

def cmd_jobs(query):
    q = query or "ai"
    try:
        data = get_json(
            "https://remotive.com/api/remote-jobs?"
            + urllib.parse.urlencode({"search": q, "limit": 10})
        )
    except Exception as exc:
        return "تعذر الوصول إلى مصدر الوظائف: " + esc(exc)

    jobs = data.get("jobs", [])
    lines = [f"<b>وظائف Remote: {esc(q)}</b>"]

    for job in sorted(
        jobs,
        key=lambda item: item.get("publication_date", ""),
        reverse=True
    )[:8]:
        lines.append(
            f"<b>{esc(job.get('title', ''))}</b>\n"
            f"{esc(job.get('company_name', ''))}\n"
            f"{esc(job.get('candidate_required_location', ''))}\n"
            f"{esc(job.get('url', ''))}"
        )

    return "\n\n".join(lines) if len(lines) > 1 else "لا توجد نتائج."


def search_opportunities(query, kind):
    defaults = {
        "grants": "scholarship fellowship grant 2026",
        "courses": "free AI Python course certificate 2026"
    }
    q = query or defaults[kind]

    try:
        results = search_web(q, 8)
    except Exception as exc:
        return "تعذر البحث: " + esc(exc)

    title = "منح وفرص" if kind == "grants" else "دورات وتدريب"
    lines = [f"<b>{title}: {esc(q)}</b>"]

    if not results:
        return "لا توجد نتائج."

    for title_text, url in results:
        lines.append(f"<b>{esc(title_text)}</b>\n{esc(url)}")

    return "\n\n".join(lines)


# ============================================================
# Gmail read-only
# ============================================================

def decode_header(value):
    if not value:
        return ""

    decoded = []
    for part, encoding in email.header.decode_header(value):
        if isinstance(part, bytes):
            decoded.append(
                part.decode(encoding or "utf-8", errors="replace")
            )
        else:
            decoded.append(str(part))
    return "".join(decoded)


def email_connect():
    if not EMAIL_USER or not EMAIL_PASS:
        raise RuntimeError("EMAIL_USER وEMAIL_PASS غير مضبوطين")

    box = imaplib.IMAP4_SSL(
        EMAIL_HOST,
        EMAIL_PORT,
        ssl_context=ssl.create_default_context()
    )
    box.login(EMAIL_USER, EMAIL_PASS)
    return box


def email_preview(message):
    parts = message.walk() if message.is_multipart() else [message]

    for part in parts:
        if (
            part.get_content_type() != "text/plain"
            or part.get_content_disposition() == "attachment"
        ):
            continue

        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return compact(payload.decode(charset, errors="replace"), 1500)

    return ""


def classify_email(text):
    lower = str(text).lower()
    categories = {
        "وظائف": ("job", "career", "interview", "application", "وظيف"),
        "منح": ("scholarship", "fellowship", "grant", "منح"),
        "دورات": ("course", "certificate", "training", "coursera", "edx", "دورة"),
        "موعد نهائي": ("deadline", "due date", "last date", "الموعد النهائي"),
    }
    for name, words in categories.items():
        if any(word in lower for word in words):
            return name
    return "عام"


def cmd_email(query):
    box = email_connect()
    try:
        status, data = box.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("تعذر فتح Inbox للقراءة")

        status, search_data = box.search(None, "ALL")
        if status != "OK":
            raise RuntimeError("تعذر البحث في البريد")

        ids = (search_data[0] or b"").split()[-20:]
        lines = ["<b>آخر رسائل البريد — قراءة فقط</b>"]
        matched = 0

        for message_id in reversed(ids):
            status, msgdata = box.fetch(message_id, "(RFC822)")
            if status != "OK" or not msgdata:
                continue

            raw = None
            for item in msgdata:
                if isinstance(item, tuple) and len(item) > 1:
                    raw = item[1]
                    break
            if not raw:
                continue

            message = email.message_from_bytes(raw)
            subject = decode_header(message.get("Subject"))
            sender = decode_header(message.get("From"))
            preview = email_preview(message)
            searchable = f"{subject} {sender} {preview}"

            if query and query.lower() not in searchable.lower():
                continue

            matched += 1
            category = classify_email(searchable)
            lines.append(
                f"<b>{esc(subject or 'بدون عنوان')}</b>\n"
                f"{esc(sender)}\n"
                f"التصنيف: {esc(category)}\n"
                f"{esc(preview[:600])}"
            )

        if matched == 0:
            lines.append("لا توجد رسائل مطابقة.")
        return "\n\n".join(lines)
    finally:
        try:
            box.logout()
        except Exception:
            pass


# ============================================================
# Saved items
# ============================================================

def save_item(kind, title, url="", details=""):
    items = read_json(SAVED_FILE, [])
    title = str(title).strip()
    url = str(url).strip()

    fingerprint = (kind, title.lower(), url.lower())
    for item in items:
        current = (
            item.get("kind"),
            str(item.get("title", "")).lower(),
            str(item.get("url", "")).lower()
        )
        if current == fingerprint:
            return None

    item = {
        "id": f"item-{int(time.time() * 1000)}",
        "kind": kind,
        "title": title[:500],
        "url": url[:2000],
        "details": str(details)[:2500],
        "saved_at": iso()
    }
    items.append(item)
    atomic_write(SAVED_FILE, items[-MAX_SAVED:])
    return item


def list_saved(kind=""):
    items = read_json(SAVED_FILE, [])
    if kind:
        items = [item for item in items if item.get("kind") == kind]

    if not items:
        return "لا توجد عناصر محفوظة."

    lines = ["<b>العناصر المحفوظة</b>"]
    for item in items[-20:][::-1]:
        lines.append(
            f"• <b>{esc(item.get('title', ''))}</b> — "
            f"{esc(item.get('kind', ''))}\n"
            f"{esc(item.get('url', ''))}"
        )
    return "\n".join(lines)


def save_from_result(query):
    results = search_web(query, 8)
    if not results:
        return "لا توجد نتائج للحفظ."

    first_title, first_url = results[0]
    item = save_item("web", first_title, first_url, f"Query: {query}")
    if not item:
        return "النتيجة الأولى محفوظة بالفعل."
    return (
        f"تم الحفظ: <b>{esc(first_title)}</b>\n"
        f"{esc(first_url)}"
    )


# ============================================================
# Learning
# ============================================================

def learning_command(arg, memory):
    arg = str(arg).strip()

    if arg.startswith("goal "):
        goal = arg[5:].strip()
        if not goal:
            return "اكتب هدف التعلم بعد goal."

        memory.data["learning"]["goals"].append(
            {"text": goal[:500], "created": iso(), "status": "active"}
        )
        memory.save()
        return "تم حفظ هدف التعلم: " + esc(goal)

    if arg.startswith("flashcard add "):
        content = arg[len("flashcard add "):]
        parts = content.split("|", 1)
        if len(parts) != 2:
            return "الصيغة: /learn flashcard add السؤال | الإجابة"

        memory.data["learning"]["flashcards"].append(
            {
                "question": parts[0].strip()[:1000],
                "answer": parts[1].strip()[:2000],
                "created": iso(),
                "reviewed": 0
            }
        )
        memory.save()
        return "تمت إضافة البطاقة التعليمية."

    if arg in {"progress", "plan", "flashcards"}:
        learning = memory.data["learning"]
        return (
            "<b>التعلم الشخصي</b>\n"
            f"• الأهداف: {len(learning['goals'])}\n"
            f"• البطاقات: {len(learning['flashcards'])}\n"
            f"• سجل التقدم: {len(learning['progress'])}\n"
            "استخدم /learn goal هدفك\n"
            "أو /learn flashcard add سؤال | إجابة"
        )

    answer, error = ai(
        "أنشئ درسا للمبتدئ حول الموضوع التالي. "
        "قسّمه إلى شرح قصير ثم أمثلة ثم تمرين ثم اختبار قصير ثم معيار نجاح:\n"
        + arg,
        "learn",
        3500,
        SYSTEM_PROMPT + "\nاشرح خطوة بخطوة ولا تقفز فوق الأساسيات."
    )
    return answer or "تعذر إعداد الدرس: " + esc(error)


# ============================================================
# Skills
# ============================================================

SKILL_ALLOWED_IMPORTS = {
    "json",
    "re",
    "math",
    "statistics",
    "datetime",
    "urllib.parse",
}

SKILL_FORBIDDEN_MODULES = {
    "subprocess",
    "ctypes",
    "multiprocessing",
    "pty",
    "pickle",
    "socket",
    "ssl",
    "imaplib",
    "os",
    "pathlib",
    "shutil",
    "sys",
    "importlib",
}

SKILL_FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "__import__",
    "open",
    "input",
    "compile",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
}

SKILL_FORBIDDEN_ATTRIBUTES = {
    "system",
    "popen",
    "Popen",
    "call",
    "run",
    "rmtree",
    "remove",
    "unlink",
    "write_text",
    "write_bytes",
    "chmod",
    "chown",
}


def list_skills():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return [
        path.stem
        for path in sorted(SKILLS_DIR.glob("*.py"))
        if not path.name.startswith("_")
    ]


def validate_python(source, skill_mode=False):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax:{exc.msg}:{exc.lineno}"]

    findings = []
    forbidden_modules = SKILL_FORBIDDEN_MODULES if skill_mode else {
        "subprocess", "ctypes", "multiprocessing", "pty", "pickle"
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden_modules:
                    findings.append(f"import:{root}")
                elif skill_mode and root not in SKILL_ALLOWED_IMPORTS:
                    findings.append(f"import_not_allowed:{root}")

        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden_modules:
                findings.append(f"import:{root}")
            elif skill_mode and root not in SKILL_ALLOWED_IMPORTS:
                findings.append(f"import_not_allowed:{root}")

        elif isinstance(node, ast.Name):
            if node.id in SKILL_FORBIDDEN_NAMES:
                findings.append(f"name:{node.id}")

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in SKILL_FORBIDDEN_NAMES:
                    findings.append(f"call:{node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in SKILL_FORBIDDEN_ATTRIBUTES:
                    findings.append(f"attribute:{node.func.attr}")

        elif isinstance(node, ast.Attribute):
            if node.attr in SKILL_FORBIDDEN_ATTRIBUTES:
                findings.append(f"attribute:{node.attr}")

    return sorted(set(findings))


def create_skill_request(description):
    prompt = f"""أنشئ Python Skill صغيرة للمهمة التالية:

{description}

شروط صارمة:
- أخرج الكود فقط.
- لا تستخدم shell أو subprocess أو os أو eval أو exec أو open.
- استخدم فقط المكتبات المسموحة: json, re, math, statistics, datetime, urllib.parse.
- يجب أن يحتوي الملف على:
NAME = "..."
DESCRIPTION = "..."
def run(arg, context): ...
- run تعيد نصا فقط.
- لا تنفذ حذفا أو إرسالا أو كتابة ملفات.
"""
    code, error = ai(
        prompt,
        "skill",
        3500,
        SYSTEM_PROMPT + "\nأنت مهندس plugins. اكتب Skill صغيرة قابلة للمراجعة."
    )
    if not code:
        return None, error

    match = re.search(r"```(?:python)?\s*(.*?)```", code, re.S | re.I)
    if match:
        code = match.group(1).strip()

    return code.strip(), None


def validate_skill_contract(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "Python syntax غير صالح."

    assigned = set()
    has_run = False

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = []
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        targets.append(target.id)
            elif isinstance(node.target, ast.Name):
                targets.append(node.target.id)
            assigned.update(targets)

        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True

    missing = []
    if "NAME" not in assigned:
        missing.append("NAME")
    if "DESCRIPTION" not in assigned:
        missing.append("DESCRIPTION")
    if not has_run:
        missing.append("run")

    return None if not missing else "العقد ناقص: " + ", ".join(missing)


def install_skill(name, source):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        raise ValueError("اسم Skill غير صالح")

    if len(source) > 100_000:
        raise ValueError("Skill أكبر من الحد المسموح")

    findings = validate_python(source, skill_mode=True)
    if findings:
        raise ValueError("Skill رفضت: " + ", ".join(findings))

    contract_error = validate_skill_contract(source)
    if contract_error:
        raise ValueError(contract_error)

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SKILLS_DIR / f"{name}.py"

    if path.exists():
        raise FileExistsError(f"Skill موجودة بالفعل: {name}")

    temp = path.with_suffix(".tmp.py")
    try:
        temp.write_text(source, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.unlink(missing_ok=True)
        path.write_text(source, encoding="utf-8")
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise

    return path


def run_skill(name, arg):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        return "اسم Skill غير صالح."

    path = SKILLS_DIR / f"{name}.py"
    if not path.exists():
        return "الـSkill غير موجودة."

    source = path.read_text(encoding="utf-8")
    findings = validate_python(source, skill_mode=True)
    contract_error = validate_skill_contract(source)

    if findings or contract_error:
        return (
            "تم رفض تشغيل الـSkill.\n"
            + "\n".join(findings)
            + (("\n" + contract_error) if contract_error else "")
        )

    # Run an explicitly requested skill in a short-lived isolated Python
    # process with a bounded timeout. This is defense-in-depth, not a
    # complete OS sandbox.
    with tempfile.TemporaryDirectory(prefix="agent-skill-") as temp_dir:
        temp_root = Path(temp_dir)
        skill_file = temp_root / "skill.py"
        runner_file = temp_root / "runner.py"
        skill_file.write_text(source, encoding="utf-8")

        runner_file.write_text(
            r"""import json
import sys
from pathlib import Path

skill_path = Path(sys.argv[1])
argument = sys.argv[2]

safe_builtins = {
    "__import__": __import__,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "any": any,
    "all": all,
    "abs": abs,
    "round": round,
}

namespace = {"__builtins__": safe_builtins}
source = skill_path.read_text(encoding="utf-8")
exec(compile(source, str(skill_path), "exec"), namespace, namespace)

result = namespace["run"](
    argument,
    {
        "now": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "root": str(skill_path.parent),
    },
)

print(json.dumps({"result": str(result)[:8000]}, ensure_ascii=False))
""",
            encoding="utf-8"
        )

        try:
            completed = subprocess.run(
                [
                    __import__("sys").executable,
                    "-I",
                    str(runner_file),
                    str(skill_file),
                    str(arg)[:4000],
                ],
                cwd=str(temp_root),
                env={"PYTHONIOENCODING": "utf-8"},
                capture_output=True,
                text=True,
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            return "فشل تشغيل Skill: تجاوزت المهلة المحددة."
        except Exception as exc:
            return "فشل تشغيل Skill: " + esc(exc)

        if completed.returncode != 0:
            return "فشل تشغيل Skill: " + compact(completed.stderr, 1200)

        try:
            payload = json.loads(completed.stdout)
            return str(payload.get("result", ""))[:8000]
        except Exception:
            return "فشل تشغيل Skill: استجابة غير صالحة."


def cmd_skills():
    skills = list_skills()
    return (
        "<b>Skills</b>\n"
        + (
            "\n".join("• " + esc(name) for name in skills)
            if skills else "لا توجد Skills إضافية."
        )
        + "\n\nلتشغيل Skill: <code>/runskill الاسم الوسيط</code>"
    )


# ============================================================
# Pending actions / permissions
# ============================================================

def create_pending_action(kind, description, payload=""):
    actions = read_json(ACTIONS_FILE, [])
    action = {
        "id": f"action-{int(time.time() * 1000)}",
        "kind": str(kind)[:100],
        "description": str(description)[:2000],
        "payload": str(payload)[:4000],
        "status": "pending",
        "created": iso()
    }
    actions.append(action)
    atomic_write(ACTIONS_FILE, actions[-MAX_ACTIONS:])
    return action


def resolve_action(action_id, approved):
    actions = read_json(ACTIONS_FILE, [])
    for action in actions:
        if (
            action.get("id") == action_id
            and action.get("status") == "pending"
        ):
            action["status"] = "approved" if approved else "rejected"
            action["resolved_at"] = iso()
            atomic_write(ACTIONS_FILE, actions)
            return action
    return None


def pending_actions_text():
    actions = [
        item
        for item in read_json(ACTIONS_FILE, [])
        if item.get("status") == "pending"
    ]
    if not actions:
        return "لا توجد عمليات معلقة."

    lines = ["<b>العمليات المعلقة</b>"]
    for action in actions[-20:]:
        lines.append(
            f"• <code>{esc(action.get('id'))}</code>\n"
            f"{esc(action.get('description'))}\n"
            "للموافقة: /approveaction ID\n"
            "للرفض: /rejectaction ID"
        )
    return "\n\n".join(lines)


# ============================================================
# Self-development / patch security
# ============================================================

def validate_agent_patch(diff):
    if not diff or not diff.strip():
        return False, "الـPatch فارغ."
    if len(diff) > 200_000:
        return False, "الـPatch أكبر من الحد المسموح."

    found_file = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) != 4:
                return False, "صيغة diff غير صالحة."
            if parts[2] != "a/agent.py" or parts[3] != "b/agent.py":
                return False, "الـPatch يحاول تعديل ملف غير agent.py."
            found_file = True

        elif line.startswith("--- ") and line != "--- a/agent.py":
            return False, "مسار الملف القديم غير مسموح."

        elif line.startswith("+++ ") and line != "+++ b/agent.py":
            return False, "مسار الملف الجديد غير مسموح."

        # Prevent obvious attempts to smuggle unrelated shell commands or
        # destructive Python into an LLM-generated patch. This is in addition
        # to git apply --check and syntax validation, not a sandbox.
        elif any(
            token in line
            for token in (
                "os.system(",
                "subprocess.Popen(",
                "subprocess.run(",
                "eval(",
                "exec(",
            )
        ):
            return False, "الـPatch يحتوي على تنفيذ خطير محظور."

    if not found_file:
        return False, "لم يتم العثور على diff صالح لـ agent.py."

    return True, ""


def propose_evolution(request):
    prompt = f"""أنت مهندس صيانة لهذا الوكيل.

طلب المستخدم:
{request}

أنشئ unified diff صالحا لتعديل agent.py فقط.

قواعد:
- لا تضف أسرارا.
- لا تتجاوز موافقة المستخدم.
- لا تضف عمليات حذف أو إرسال تلقائي.
- لا تستخدم os.system أو subprocess أو eval أو exec.
- لا تعدل ملفات خارج agent.py.
- يجب أن يكون diff قابلا للتطبيق بواسطة git apply.
- أخرج diff فقط بدون Markdown.
"""
    diff, error = ai(
        prompt,
        "evolution",
        7000,
        SYSTEM_PROMPT + "\nأنت مهندس برمجيات مسؤول عن patch آمن."
    )
    if not diff:
        return None, error

    diff = diff.strip()
    match = re.match(r"^```[^\n]*\n(.*)\n```$", diff, re.S)
    if match:
        diff = match.group(1).strip()

    valid, reason = validate_agent_patch(diff)
    if not valid:
        return None, "تم رفض الـPatch: " + reason

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    pid = f"patch-{int(time.time())}"
    path = PROPOSALS_DIR / f"{pid}.diff"
    path.write_text(diff, encoding="utf-8")

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=error-all", str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30
    )

    valid_git = check.returncode == 0
    metadata = {
        "id": pid,
        "type": "agent_patch",
        "created": iso(),
        "request": str(request)[:3000],
        "valid": valid_git,
        "status": "pending" if valid_git else "invalid",
        "check_output": compact(check.stdout + check.stderr, 3000)
    }
    atomic_write(PROPOSALS_DIR / f"{pid}.json", metadata)

    if not valid_git:
        return (
            None,
            "تم إنشاء الـPatch لكنه فشل في git apply --check:\n"
            + compact(check.stderr, 1800)
        )

    return pid, None


def approve_patch(pid):
    if not re.fullmatch(r"patch-\d+", pid):
        return "معرّف Patch غير صالح."

    path = PROPOSALS_DIR / f"{pid}.diff"
    meta_path = PROPOSALS_DIR / f"{pid}.json"

    if not path.exists():
        return "الـPatch غير موجود."

    meta = read_json(meta_path, {})
    if meta.get("status") == "applied":
        return "هذا الـPatch تم تطبيقه بالفعل."

    diff = path.read_text(encoding="utf-8")
    valid, reason = validate_agent_patch(diff)
    if not valid:
        return "تم رفض الـPatch: " + reason

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=error-all", str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30
    )
    if check.returncode != 0:
        return "فشل فحص الـPatch:\n" + compact(check.stderr, 2000)

    agent_file = Path(__file__).resolve()
    backup = ROOT / f"agent.py.backup-{int(time.time())}"

    try:
        shutil.copy2(agent_file, backup)
    except Exception as exc:
        return "تعذر إنشاء النسخة الاحتياطية: " + esc(exc)

    try:
        result = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30
        )
    except Exception as exc:
        return "تعذر تطبيق الـPatch: " + esc(exc)

    if result.returncode != 0:
        return "فشل تطبيق الـPatch:\n" + compact(result.stderr, 2000)

    try:
        py_compile.compile(str(agent_file), doraise=True)
        meta["status"] = "applied"
        meta["approved_at"] = iso()
        meta["backup"] = backup.name
        atomic_write(meta_path, meta)
        return (
            f"تم تطبيق {pid} واجتاز فحص Python.\n"
            f"النسخة الاحتياطية: {backup.name}"
        )

    except Exception as exc:
        try:
            shutil.copy2(backup, agent_file)
            status = "reverted"
        except Exception as rollback_exc:
            status = "rollback_failed"
            meta["rollback_error"] = str(rollback_exc)

        meta["status"] = status
        meta["reverted_at"] = iso()
        meta["error"] = str(exc)
        atomic_write(meta_path, meta)

        if status == "rollback_failed":
            return (
                "فشل فحص Python بعد التطبيق وفشل التراجع التلقائي.\n"
                f"النسخة الاحتياطية: {backup}\n"
                f"خطأ الفحص: {exc}"
            )

        return "فشل فحص Python وتم التراجع عن التعديل: " + str(exc)


# ============================================================
# Tasks and watchers
# ============================================================

def task_add(text, interval_minutes):
    interval = safe_int(interval_minutes, 1440, minimum=1, maximum=60 * 24 * 365)
    tasks = read_json(TASKS_FILE, [])
    if len(tasks) >= MAX_TASKS:
        raise ValueError("وصل عدد المهام إلى الحد الأقصى")

    task = {
        "id": f"task-{int(time.time() * 1000)}",
        "text": str(text)[:2000],
        "interval": interval,
        "last_run": None,
        "last_error": None,
        "enabled": True,
        "created": iso()
    }
    tasks.append(task)
    atomic_write(TASKS_FILE, tasks)
    return task


def task_due(task):
    if not task.get("enabled", True):
        return False
    if not task.get("last_run"):
        return True

    try:
        last = datetime.fromisoformat(task["last_run"])
    except Exception:
        return True

    return now() - last >= timedelta(
        minutes=safe_int(task.get("interval"), 1440, minimum=1)
    )


def set_task_enabled(task_id, enabled):
    tasks = read_json(TASKS_FILE, [])
    for task in tasks:
        if task.get("id") == task_id:
            task["enabled"] = bool(enabled)
            task["updated"] = iso()
            atomic_write(TASKS_FILE, tasks)
            return task
    return None


def delete_task(task_id):
    tasks = read_json(TASKS_FILE, [])
    remaining = [task for task in tasks if task.get("id") != task_id]
    if len(remaining) == len(tasks):
        return False
    atomic_write(TASKS_FILE, remaining)
    return True


def list_tasks():
    tasks = read_json(TASKS_FILE, [])
    if not tasks:
        return "لا توجد مهام."

    lines = ["<b>Tasks</b>"]
    for task in tasks:
        status = "ON" if task.get("enabled", True) else "OFF"
        lines.append(
            f"• <code>{esc(task.get('id'))}</code> [{status}]\n"
            f"{esc(task.get('text'))}\n"
            f"كل {task.get('interval')} دقيقة"
        )
    return "\n\n".join(lines)


def watcher_add(kind, query):
    watchers = read_json(WATCH_FILE, [])
    if len(watchers) >= MAX_WATCHERS:
        raise ValueError("وصل عدد المراقبات إلى الحد الأقصى")

    watcher = {
        "id": f"watch-{int(time.time() * 1000)}",
        "kind": kind,
        "query": str(query)[:1000],
        "last_seen": [],
        "enabled": True,
        "created": iso()
    }
    watchers.append(watcher)
    atomic_write(WATCH_FILE, watchers)
    return watcher


def watcher_query(kind, query):
    if kind == "jobs":
        results = [
            (job.get("title", ""), job.get("url", ""))
            for job in get_json(
                "https://remotive.com/api/remote-jobs?"
                + urllib.parse.urlencode({"search": query, "limit": 10})
            ).get("jobs", [])
        ]
        return [(title, url) for title, url in results if url]

    return search_web(query, 10)


def run_watchers():
    watchers = read_json(WATCH_FILE, [])
    new_items = []

    for watcher in watchers:
        if not watcher.get("enabled", True):
            continue

        try:
            results = watcher_query(watcher.get("kind", "web"), watcher.get("query", ""))
            current = []
            for title, url in results[:10]:
                fingerprint = f"{compact(title, 300)}|{url}"
                current.append(fingerprint)

                if fingerprint not in set(watcher.get("last_seen", [])):
                    new_items.append(
                        {
                            "kind": watcher.get("kind"),
                            "watcher_id": watcher.get("id"),
                            "title": title,
                            "url": url
                        }
                    )

            watcher["last_seen"] = (current + watcher.get("last_seen", []))[:50]
            watcher["last_checked"] = iso()
            watcher["last_error"] = None

        except Exception as exc:
            watcher["last_error"] = compact(exc, 500)
            watcher["last_checked"] = iso()

    atomic_write(WATCH_FILE, watchers)
    return new_items


def run_tasks(memory):
    tasks = read_json(TASKS_FILE, [])
    changed = False

    for task in tasks:
        if not task_due(task):
            continue

        try:
            answer = process(task.get("text", ""), memory)
            task["last_run"] = iso()
            task["last_error"] = None
            changed = True

            if CHAT_ID:
                send(
                    CHAT_ID,
                    f"<b>مهمة تلقائية</b>\n{esc(task.get('id'))}\n\n{answer}"
                )
        except Exception as exc:
            task["last_run"] = iso()
            task["last_error"] = compact(exc, 1000)
            changed = True
            log("task error: " + traceback.format_exc())

    if changed:
        atomic_write(TASKS_FILE, tasks)


# ============================================================
# Help / health / dispatcher
# ============================================================

def help_text():
    return """<b>AI Personal Agent v6</b>

<b>أساسي</b>
/start /help /ping /health /memory

<b>ويب ومعلومات</b>
/search /open /news /wiki /weather /summarize

<b>بحث علمي</b>
/arxiv /pubmed /openalex /semantic /scholar /crossref

<b>فرص</b>
/jobs /grants /courses
/saveweb بحث
/saved [kind]

<b>Gmail — قراءة فقط</b>
/email [كلمة بحث]

<b>برمجة وتعلم</b>
/code /review /learn

<b>Skills</b>
/skills
/skill create وصف
/runskill الاسم الوسيط

<b>ذاكرة</b>
/remember نوع | نص
/recall كلمة البحث

<b>الأتمتة</b>
/task add دقائق | الأمر
/tasks
/task on ID
/task off ID
/task delete ID
/watch add نوع | بحث
/watches
/watch on ID
/watch off ID

<b>الموافقات</b>
/pending
/approveaction ID
/rejectaction ID

<b>التطوير الذاتي</b>
/evolve وصف
/approve patch-ID

<b>أوضاع التشغيل</b>
AGENT_MODE=poll
AGENT_MODE=command مع TASK
AGENT_MODE=once
AGENT_MODE=selftest
"""


def health():
    return (
        "<b>Health</b>\n"
        f"• Python: OK\n"
        f"• Telegram: {'OK' if TELEGRAM_TOKEN else 'MISSING'}\n"
        f"• OpenRouter: {'OK' if OPENROUTER_API_KEY else 'MISSING'}\n"
        f"• Gmail: {'configured' if EMAIL_USER and EMAIL_PASS else 'not configured'}\n"
        f"• Skills: {len(list_skills())}\n"
        f"• Root: <code>{esc(ROOT)}</code>"
    )


def process(text, memory):
    raw = str(text or "").strip()
    if not raw:
        return "اكتب أمرا أو سؤالا."

    if raw.startswith("/"):
        raw = raw[1:]

    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        if command in {"start", "hello"}:
            return "أهلا. استخدم /help لعرض الوظائف."

        if command == "help":
            return help_text()

        if command == "ping":
            return "pong — الوكيل يعمل."

        if command == "health":
            return health()

        if command == "memory":
            return memory.summary()

        if command == "remember":
            pieces = arg.split("|", 1)
            if len(pieces) != 2:
                return "الصيغة: /remember نوع | النص"
            entry = memory.remember(pieces[0].strip() or "note", pieces[1].strip())
            return f"تم الحفظ في الذاكرة: <code>{esc(entry['id'])}</code>"

        if command == "recall":
            entries = memory.recall(arg, 8)
            if not entries:
                return "لم أجد معلومات مرتبطة."
            return "<b>من الذاكرة</b>\n" + "\n".join(
                f"• {esc(entry.get('text', ''))}" for entry in entries
            )

        if command == "search":
            return cmd_search(arg, memory)

        if command == "open":
            return cmd_open(arg)

        if command == "news":
            return cmd_news(arg)

        if command == "wiki":
            return cmd_wiki(arg)

        if command == "weather":
            return cmd_weather(arg)

        if command == "summarize":
            return cmd_summarize(arg)

        if command == "arxiv":
            return cmd_arxiv(arg)

        if command == "pubmed":
            return cmd_pubmed(arg)

        if command == "openalex":
            return cmd_openalex(arg)

        if command in {"semantic", "scholar"}:
            return cmd_semantic(arg)

        if command == "crossref":
            return cmd_crossref(arg)

        if command == "jobs":
            return cmd_jobs(arg)

        if command == "grants":
            return search_opportunities(arg, "grants")

        if command == "courses":
            return search_opportunities(arg, "courses")

        if command == "email":
            return cmd_email(arg)

        if command == "saveweb":
            return save_from_result(arg)

        if command == "saved":
            return list_saved(arg)

        if command == "learn":
            return learning_command(arg, memory)

        if command in {"code", "review"}:
            system = SYSTEM_PROMPT
            if command == "code":
                system += "\nأنت مبرمج محترف. أعط الكود كاملا مع افتراضات واضحة."
            else:
                system += "\nراجع الأمان والمنطق والأداء وقابلية الصيانة، ثم اقترح إصلاحات."

            answer, error = ai(arg, command, 4500, system)
            return answer or "تعذر التنفيذ: " + esc(error)

        if command == "skills":
            return cmd_skills()

        if command == "skill":
            action = arg.strip()
            if action.startswith("create "):
                description = action[7:].strip()
                if not description:
                    return "اكتب وصف المهارة."

                source, error = create_skill_request(description)
                if not source:
                    return "تعذر إنشاء Skill: " + esc(error)

                name = "skill_" + str(int(time.time()))
                try:
                    path = install_skill(name, source)
                except Exception as exc:
                    return "تم رفض إنشاء الـSkill:\n" + esc(exc)

                memory.data["skills"].append(
                    {
                        "name": name,
                        "description": description[:1000],
                        "created": iso()
                    }
                )
                memory.save()

                return (
                    "تم إنشاء Skill بعد الفحص:\n"
                    f"<code>{esc(name)}</code>\n"
                    f"المسار: <code>{esc(path)}</code>\n"
                    "التشغيل لا يحدث تلقائيا. استخدم /runskill."
                )
            return "استخدم: <code>/skill create وصف المهارة</code>"

        if command == "runskill":
            bits = arg.split(maxsplit=1)
            if len(bits) != 2:
                return "الصيغة: /runskill اسم المهارة الوسيط"
            return run_skill(bits[0], bits[1])

        if command == "evolve":
            if not arg:
                return "اكتب ما تريد تطويره."
            pid, error = propose_evolution(arg)
            if pid:
                return (
                    f"تم إنشاء Patch: <code>{pid}</code>\n"
                    "تم فحصه ولم يتم تطبيقه.\n"
                    f"للتطبيق بعد مراجعتك: /approve {pid}"
                )
            return "تعذر إنشاء Patch: " + esc(error)

        if command == "approve":
            return approve_patch(arg)

        if command == "pending":
            return pending_actions_text()

        if command in {"approveaction", "rejectaction"}:
            approved = command == "approveaction"
            action = resolve_action(arg, approved)
            if not action:
                return "العملية غير موجودة أو لم تعد معلقة."
            return (
                f"تم {'قبول' if approved else 'رفض'} العملية "
                f"<code>{esc(arg)}</code>."
            )

        if command == "task":
            bits = arg.split(maxsplit=1)
            if not bits:
                return "استخدم /task add دقائق | الأمر"

            action = bits[0].lower()
            rest = bits[1] if len(bits) > 1 else ""

            if action == "add":
                match = re.match(r"(\d+)\s*\|\s*(.+)", rest, re.S)
                if not match:
                    return "الصيغة: /task add 60 | /jobs ai"
                task = task_add(match.group(2), int(match.group(1)))
                return f"تم إنشاء المهمة: <code>{esc(task['id'])}</code>"

            task_id = rest.strip()
            if action in {"on", "off"}:
                task = set_task_enabled(task_id, action == "on")
                return (
                    "تم تحديث المهمة."
                    if task else "المهمة غير موجودة."
                )

            if action == "delete":
                return (
                    "تم حذف المهمة."
                    if delete_task(task_id)
                    else "المهمة غير موجودة."
                )

            return "استخدم /task add|on|off|delete."

        if command == "tasks":
            return list_tasks()

        if command == "watch":
            bits = arg.split(maxsplit=1)
            if not bits:
                return "استخدم /watch add نوع | الاستعلام"

            action = bits[0].lower()
            rest = bits[1] if len(bits) > 1 else ""

            if action == "add":
                match = rest.split("|", 1)
                if len(match) != 2:
                    return "الصيغة: /watch add web | AI jobs Egypt"
                kind = match[0].strip().lower()
                query = match[1].strip()
                watcher = watcher_add(kind, query)
                return f"تم إنشاء مراقبة: <code>{esc(watcher['id'])}</code>"

            if action in {"on", "off"}:
                watchers = read_json(WATCH_FILE, [])
                watcher_id = rest.strip()
                for watcher in watchers:
                    if watcher.get("id") == watcher_id:
                        watcher["enabled"] = action == "on"
                        atomic_write(WATCH_FILE, watchers)
                        return "تم تحديث المراقبة."
                return "المراقبة غير موجودة."

            return "استخدم /watch add أو /watch on ID أو /watch off ID."

        if command == "watches":
            watchers = read_json(WATCH_FILE, [])
            if not watchers:
                return "لا توجد مراقبات."

            return "<b>المراقبات</b>\n" + "\n".join(
                f"• <code>{esc(w.get('id'))}</code> "
                f"[{'ON' if w.get('enabled', True) else 'OFF'}]\n"
                f"{esc(w.get('kind'))}: {esc(w.get('query'))}"
                for w in watchers
            )

        # Natural-language fallback.
        answer, error = ai(arg or raw, "general", 3000, SYSTEM_PROMPT)
        return answer or "تعذر تنفيذ الطلب: " + esc(error)

    except Exception as exc:
        memory.failure(command, exc)
        log(traceback.format_exc())
        return (
            f"حدث خطأ في <code>{esc(command)}</code>: "
            f"{esc(str(exc)[:700])}"
        )


# ============================================================
# Telegram polling / command / scheduled modes
# ============================================================

def poll():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير مضبوط")

    memory = Memory()
    offset = None

    # Polling and webhooks cannot be used at the same time. If a webhook
    # was configured previously, remove it before starting long polling.
    try:
        tg("deleteWebhook", {"drop_pending_updates": "false"}, timeout=30)
        log("Telegram webhook cleared; polling is active")
    except Exception as exc:
        log("Telegram webhook cleanup failed: " + compact(exc, 500))

    log("AI Personal Agent v6 polling started")
    log("CHAT_ID filter: " + (CHAT_ID if CHAT_ID else "disabled"))

    while True:
        try:
            payload = {"timeout": 50}
            if offset is not None:
                payload["offset"] = offset

            updates = tg("getUpdates", payload, timeout=60)

            for update in updates or []:
                offset = update.get("update_id", 0) + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                message_text = message.get("text") or ""

                if not message_text:
                    continue
                if CHAT_ID and chat_id != CHAT_ID:
                    continue

                answer = process(message_text, memory)
                memory.conversation(
                    message_text,
                    answer,
                    message_text.split()[0][:100]
                )
                memory.success("message")
                memory.save()
                send(chat_id, answer)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log("poll error: " + compact(exc, 700))
            time.sleep(4)


def command_mode():
    task = os.getenv("TASK", "help").strip() or "help"
    memory = Memory()

    answer = process(task, memory)
    memory.conversation(task, answer, task.split()[0][:100])
    memory.success("workflow")
    memory.save()

    print(re.sub(r"<[^>]+>", "", tg_html(answer)))

    if CHAT_ID and TELEGRAM_TOKEN:
        send(CHAT_ID, f"<b>Workflow</b>\n{answer}")


def once():
    memory = Memory()
    run_tasks(memory)

    new_items = run_watchers()
    if new_items and CHAT_ID and TELEGRAM_TOKEN:
        lines = [f"<b>نتائج جديدة في المراقبة: {len(new_items)}</b>"]
        for item in new_items[:15]:
            lines.append(
                f"<b>{esc(item['title'])}</b>\n{esc(item['url'])}"
            )
        send(CHAT_ID, "\n\n".join(lines))

    memory.save()


# ============================================================
# Self-test
# ============================================================

def selftest():
    print("Running self-test...")

    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    print("agent.py syntax: OK")

    assert safe_url("http://127.0.0.1") is False
    assert safe_url("https://localhost") is False
    assert safe_url("ftp://example.com") is False
    assert tg_html("**x**") == "<b>x</b>"

    sample = """
NAME = "sample"
DESCRIPTION = "sample"
def run(arg, context):
    return str(arg)
"""
    assert not validate_python(sample, skill_mode=True)
    assert validate_skill_contract(sample) is None

    bad_skill = "import os\nNAME='x'\nDESCRIPTION='x'\ndef run(arg, context): return 'x'\n"
    assert validate_python(bad_skill, skill_mode=True)

    bad_diff = """diff --git a/evil.py b/evil.py
--- a/evil.py
+++ b/evil.py
@@ -1 +1 @@
-x
+y
"""
    ok, _ = validate_agent_patch(bad_diff)
    assert not ok

    print("Safety self-tests: OK")
    print("Skills:", list_skills())
    print("Self-test: OK")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    mode = os.getenv("AGENT_MODE", "poll").strip().lower()

    if mode == "once":
        once()
    elif mode == "command":
        command_mode()
    elif mode == "selftest":
        selftest()
    else:
        poll()
