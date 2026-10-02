#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI Personal Agent v5 — Unified Autonomous/Controlled Edition

Telegram + OpenRouter + Web/Scientific/Jobs/Courses/Grants/Email/Memory/Tasks/Skills.

مبدأ التطوير الذاتي:
1) الوكيل يكتشف النقص.
2) ينشئ Skill أو Patch مقترحا.
3) يفحص Python syntax والأمان.
4) يحفظ المقترح في proposals/.
5) لا يفعّل تعديل agent.py تلقائيا.
6) /approve <id> يفعّل المقترح بعد موافقة المستخدم.
7) Skills قابلة للإضافة دون تعديل النواة.

مهم:
لا تضع مفاتيح API داخل هذا الملف.
استخدم Environment Variables / GitHub Secrets.
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
from pathlib import Path
from typing import Any


# ============================================================
# Config
# ============================================================

ROOT = Path(os.getenv("AGENT_ROOT", Path(__file__).resolve().parent))

MEMORY_FILE = ROOT / os.getenv("AGENT_MEMORY_FILE", "memory.json")
COOLDOWN_FILE = ROOT / os.getenv("AGENT_COOLDOWN_FILE", ".model_cooldown.json")
TASKS_FILE = ROOT / os.getenv("AGENT_TASKS_FILE", "tasks.json")

SKILLS_DIR = ROOT / "skills"
PROPOSALS_DIR = ROOT / "proposals"
LOG_FILE = ROOT / "agent.log"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()
EMAIL_HOST = os.getenv("EMAIL_HOST", "imap.gmail.com").strip()
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "993"))

SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()

MAX_TG = 3900
HTTP_TIMEOUT = 25
AI_TIMEOUT = 90
MAX_FETCH = 500_000

MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-r1:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

SYSTEM_PROMPT = """أنت AI Personal Agent.
أجب بالعربية المبسطة والدقيقة.
لا تخترع مصادر أو نتائج أو مفاتيح API.
إذا كانت البيانات حديثة استخدم أداة البحث المناسبة بدلا من التخمين.
عند البرمجة أعط كودا كاملا قابلا للاختبار.
لا تنفذ عمليات خارجية خطرة أو تغييرات ذات أثر دائم دون موافقة المستخدم.
إذا طلب المستخدم تطوير نفسك، افحص الأدوات والـskills الحالية ثم اقترح أو أنشئ تغييرا قابلا للاختبار والمراجعة.
"""


# ============================================================
# Utilities
# ============================================================

def now():
    return datetime.now(timezone.utc)


def iso():
    return now().isoformat()


def log(msg: str):
    line = f"[{iso()}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def atomic_write(path: Path, data: Any):
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


def read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def esc(x):
    return html.escape(str(x), quote=False)


# ============================================================
# Memory
# ============================================================

class Memory:

    def __init__(self):
        self.data = read_json(
            MEMORY_FILE,
            {
                "version": 5,
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
                "stats": {
                    "runs": 0,
                    "success": 0,
                    "failure": 0,
                    "commands": {}
                }
            }
        )

        self.data.setdefault(
            "stats",
            {
                "runs": 0,
                "success": 0,
                "failure": 0,
                "commands": {}
            }
        )
        self.data.setdefault("skills", [])
        self.data.setdefault("conversations", [])
        self.data.setdefault("errors", [])

    def save(self):
        self.data["last_updated"] = iso()

        if MEMORY_FILE.exists():
            try:
                shutil.copy2(MEMORY_FILE, str(MEMORY_FILE) + ".bak")
            except Exception:
                pass

        atomic_write(MEMORY_FILE, self.data)

    def conversation(self, user, answer, command):
        self.data["conversations"].append(
            {
                "time": iso(),
                "command": command,
                "user": str(user)[:1500],
                "agent": str(answer)[:3000]
            }
        )

        self.data["conversations"] = self.data["conversations"][-100:]
        self.data["stats"]["runs"] += 1

    def success(self, command):
        self.data["stats"]["success"] += 1
        c = self.data["stats"]["commands"]
        c[command] = c.get(command, 0) + 1

    def failure(self, command, err):
        self.data["stats"]["failure"] += 1

        self.data["errors"].append(
            {
                "time": iso(),
                "command": command,
                "error": str(err)[:1000]
            }
        )

        self.data["errors"] = self.data["errors"][-100:]

    def summary(self):
        s = self.data["stats"]

        return (
            f"<b>الذاكرة</b>\n"
            f"• التشغيلات: {s['runs']}\n"
            f"• الناجحة: {s['success']}\n"
            f"• الفاشلة: {s['failure']}\n"
            f"• Skills: {len(self.data.get('skills', []))}\n"
            f"• آخر تحديث: {esc(s['last_updated'][:19])}"
        )


# ============================================================
# HTTP / SSRF
# ============================================================

def private_ip(host):
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


def safe_url(url):
    try:
        p = urllib.parse.urlparse(url)

        if (
            p.scheme not in ("http", "https")
            or p.username
            or p.password
            or not p.hostname
        ):
            return False

        h = p.hostname

        if h.lower() in ("localhost", "localhost.localdomain"):
            return False

        if private_ip(h):
            return False

        infos = socket.getaddrinfo(
            h,
            None,
            type=socket.SOCK_STREAM
        )

        return all(
            not private_ip(info[4][0])
            for info in infos
        )

    except Exception:
        return False


def fetch(url, timeout=HTTP_TIMEOUT, max_size=MAX_FETCH, headers=None):
    if not safe_url(url):
        raise ValueError("الرابط غير مسموح أو يشير إلى عنوان داخلي")

    hdr = {
        "User-Agent": "AI-Personal-Agent/5.0",
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/json,"
            "text/plain,"
            "*/*"
        )
    }

    if headers:
        hdr.update(headers)

    req = urllib.request.Request(url, headers=hdr)

    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_size + 1)

        if len(raw) > max_size:
            raise ValueError("المحتوى أكبر من الحد المسموح")

        charset = r.headers.get_content_charset() or "utf-8"

        return raw.decode(charset, errors="replace")


def get_json(url, **kw):
    return json.loads(fetch(url, **kw))


# ============================================================
# Telegram
# ============================================================

def tg(method, data=None, timeout=40):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN غير مضبوط")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

    body = urllib.parse.urlencode(data or {}).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=timeout) as r:
        result = json.loads(r.read().decode())

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram error"))

    return result.get("result")


def tg_html(text):
    text = str(text).replace("\r\n", "\n")

    blocks = []

    def b(m):
        blocks.append(m.group(1))
        return f"\0B{len(blocks)-1}\0"

    text = re.sub(
        r"```(?:[^\n]*)\n?(.*?)```",
        b,
        text,
        flags=re.S
    )

    codes = []

    def c(m):
        codes.append(m.group(1))
        return f"\0C{len(codes)-1}\0"

    text = re.sub(r"`([^`\n]+)`", c, text)

    text = html.escape(text, quote=False)

    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<b>\1</b>",
        text,
        flags=re.S
    )

    text = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        r"<i>\1</i>",
        text
    )

    for i, x in enumerate(blocks):
        text = text.replace(
            f"\0B{i}\0",
            "<pre>" + html.escape(x, quote=False) + "</pre>"
        )

    for i, x in enumerate(codes):
        text = text.replace(
            f"\0C{i}\0",
            "<code>" + html.escape(x, quote=False) + "</code>"
        )

    return text


def chunks(text, limit=MAX_TG):
    if len(text) <= limit:
        return [text]

    out = []
    cur = ""

    for line in str(text).splitlines(True):
        if len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""

            while len(line) > limit:
                out.append(line[:limit])
                line = line[limit:]

            cur = line

        elif len(cur) + len(line) <= limit:
            cur += line

        else:
            out.append(cur)
            cur = line

    if cur:
        out.append(cur)

    return out


def send(chat_id, text):
    rendered = tg_html(text)

    try:
        for x in chunks(rendered):
            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": x,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true"
                }
            )

    except Exception:
        plain = re.sub(r"<[^>]+>", "", rendered)

        for x in chunks(plain):
            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": x,
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
        x = self.data.get(model)

        if not x:
            return True

        try:
            return datetime.fromisoformat(x) <= now()

        except Exception:
            self.data.pop(model, None)
            return True

    def set(self, model, seconds):
        self.data[model] = (
            now() + timedelta(seconds=seconds)
        ).isoformat()

    def save(self):
        atomic_write(COOLDOWN_FILE, self.data)


def ai(prompt, task="general", max_tokens=2500, system=None):
    if not OPENROUTER_API_KEY:
        return None, "OPENROUTER_API_KEY غير مضبوط"

    mem = Memory()
    cd = Cooldown()

    preferred = (
        mem.data
        .get("model_preferences", {})
        .get(task)
    )

    models = (
        ([preferred] if preferred else [])
        + [m for m in MODELS if m != preferred]
    )

    last = "لا يوجد نموذج متاح"

    for model in models:
        if not cd.ok(model):
            continue

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system or SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/",
                "X-Title": "AI Personal Agent v5"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
                obj = json.loads(r.read().decode())

            content = (
                (obj.get("choices") or [{}])[0]
                .get("message") or {}
            ).get("content")

            if not content:
                raise RuntimeError("استجابة فارغة")

            mem.data.setdefault("model_preferences", {})[task] = model
            mem.save()
            cd.save()

            return content.strip(), None

        except urllib.error.HTTPError as e:
            last = f"{model}: HTTP {e.code}"

            if e.code == 401:
                return None, "مفتاح OpenRouter غير صالح"

            if e.code == 403:
                return None, "OpenRouter رفض الطلب (403)"

            cd.set(model, 3600 if e.code == 429 else 600)

        except Exception as e:
            last = f"{model}: {str(e)[:200]}"
            cd.set(model, 120)

    cd.save()

    return None, last


# ============================================================
# Web / News / Scientific
# ============================================================

def search_web(query, limit=8):
    url = (
        "https://html.duckduckgo.com/html/?"
        + urllib.parse.urlencode({"q": query})
    )

    raw = fetch(url, timeout=20, max_size=700_000)

    results = []

    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        raw,
        re.S
    ):
        link = html.unescape(m.group(1))

        title = re.sub(
            "<.*?>",
            "",
            html.unescape(m.group(2))
        ).strip()

        if link.startswith("//"):
            link = "https:" + link

        results.append((title, link))

        if len(results) >= limit:
            break

    return results


def cmd_search(q):
    if not q:
        return "اكتب ما تريد البحث عنه."

    try:
        results = search_web(q)

        if not results:
            return "لم تظهر نتائج من محرك البحث."

        lines = [f"<b>نتائج البحث: {esc(q)}</b>"]

        for i, (title, url) in enumerate(results, 1):
            lines.append(
                f"{i}. <b>{esc(title)}</b>\n"
                f"{esc(url)}"
            )

        return "\n\n".join(lines)

    except Exception as e:
        return f"تعذر البحث: {esc(e)}"


def cmd_news(q):
    q = q or "latest news"

    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(
            {
                "q": q,
                "hl": "ar",
                "gl": "EG",
                "ceid": "EG:ar"
            }
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
            f"<b>{esc(title)}</b>\n"
            f"{esc(date)}\n"
            f"{esc(link)}"
        )

    return "\n\n".join(lines)


def cmd_wiki(q):
    if not q:
        return "اكتب الموضوع."

    url = (
        "https://ar.wikipedia.org/api/rest_v1/page/summary/"
        + urllib.parse.quote(q.replace(" ", "_"))
    )

    d = get_json(url)

    return (
        f"<b>{esc(d.get('title', q))}</b>\n"
        f"{esc(d.get('extract', 'لا يوجد ملخص.'))[:3500]}"
    )


def cmd_weather(city):
    city = city or "Cairo"

    g = get_json(
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

    r = g.get("results") or []

    if not r:
        return "لم أجد المدينة."

    p = r[0]

    w = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": p["latitude"],
                "longitude": p["longitude"],
                "current": (
                    "temperature_2m,"
                    "relative_humidity_2m,"
                    "wind_speed_10m"
                ),
                "timezone": "auto"
            }
        )
    )

    c = w.get("current", {})

    return (
        f"<b>الطقس — {esc(p.get('name', city))}</b>\n"
        f"• الحرارة: {c.get('temperature_2m', '؟')}°C\n"
        f"• الرطوبة: {c.get('relative_humidity_2m', '؟')}%\n"
        f"• الرياح: {c.get('wind_speed_10m', '؟')} كم/س"
    )


def cmd_arxiv(q):
    if not q:
        return "اكتب موضوع البحث."

    url = (
        "https://export.arxiv.org/api/query?"
        + urllib.parse.urlencode(
            {
                "search_query": f"all:{q}",
                "start": 0,
                "max_results": 8,
                "sortBy": "submittedDate",
                "sortOrder": "descending"
            }
        )
    )

    xml = fetch(url, max_size=700_000)
    root = ET.fromstring(xml)

    ns = {"a": "http://www.w3.org/2005/Atom"}
    lines = [f"<b>arXiv: {esc(q)}</b>"]

    for e in root.findall("a:entry", ns):
        title = (
            e.findtext("a:title", default="", namespaces=ns)
            or ""
        ).strip()

        aid = e.findtext(
            "a:id",
            default="",
            namespaces=ns
        )

        summary = (
            e.findtext(
                "a:summary",
                default="",
                namespaces=ns
            )
            or ""
        ).strip()

        lines.append(
            f"<b>{esc(title)}</b>\n"
            f"{esc(summary[:600])}\n"
            f"{esc(aid)}"
        )

    return "\n\n".join(lines) if len(lines) > 1 else "لا توجد نتائج."


def cmd_pubmed(q):
    if not q:
        return "اكتب موضوع البحث."

    d = get_json(
        "https://eutils.ncbi.nlm.nih.gov/"
        "entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode(
            {
                "db": "pubmed",
                "term": q,
                "retmax": 8,
                "retmode": "json"
            }
        )
    )

    ids = (
        d.get("esearchresult", {})
        .get("idlist", [])
    )

    if not ids:
        return "لا توجد نتائج."

    s = get_json(
        "https://eutils.ncbi.nlm.nih.gov/"
        "entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode(
            {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "json"
            }
        )
    )

    lines = [f"<b>PubMed: {esc(q)}</b>"]

    for i in ids:
        x = s.get("result", {}).get(i, {})

        lines.append(
            f"<b>{esc(x.get('title', ''))}</b>\n"
            f"PMID: {esc(i)}"
        )

    return "\n\n".join(lines)


def cmd_openalex(q):
    if not q:
        return "اكتب موضوع البحث."

    d = get_json(
        "https://api.openalex.org/works?"
        + urllib.parse.urlencode(
            {
                "search": q,
                "per-page": 8
            }
        )
    )

    lines = [f"<b>OpenAlex: {esc(q)}</b>"]

    for x in d.get("results", []):
        lines.append(
            f"<b>{esc(x.get('title', ''))}</b>\n"
            f"السنة: {x.get('publication_year', '؟')}\n"
            f"DOI: {esc(x.get('doi', ''))}"
        )

    return "\n\n".join(lines)


def cmd_semantic(q):
    if not q:
        return "اكتب موضوع البحث."

    headers = {}

    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    d = get_json(
        "https://api.semanticscholar.org/"
        "graph/v1/paper/search?"
        + urllib.parse.urlencode(
            {
                "query": q,
                "limit": 8,
                "fields": "title,year,authors,abstract,url"
            }
        ),
        headers=headers
    )

    lines = [f"<b>Semantic Scholar: {esc(q)}</b>"]

    for x in d.get("data", []):
        authors = ", ".join(
            a.get("name", "")
            for a in x.get("authors", [])[:4]
        )

        lines.append(
            f"<b>{esc(x.get('title', ''))}</b>\n"
            f"{esc(authors)} — {x.get('year', '؟')}\n"
            f"{esc(x.get('url', ''))}"
        )

    return "\n\n".join(lines)


# ============================================================
# Jobs / Grants / Courses
# ============================================================

def cmd_jobs(q):
    q = q or "ai"

    d = get_json(
        "https://remotive.com/api/remote-jobs?"
        + urllib.parse.urlencode(
            {
                "search": q,
                "limit": 10
            }
        )
    )

    jobs = d.get("jobs", [])

    lines = [f"<b>وظائف Remote: {esc(q)}</b>"]

    for j in sorted(
        jobs,
        key=lambda x: x.get("publication_date", ""),
        reverse=True
    )[:8]:
        lines.append(
            f"<b>{esc(j.get('title', ''))}</b>\n"
            f"{esc(j.get('company_name', ''))}\n"
            f"{esc(j.get('candidate_required_location', ''))}\n"
            f"{esc(j.get('url', ''))}"
        )

    return (
        "\n\n".join(lines)
        if len(lines) > 1
        else "لا توجد نتائج."
    )


def search_opportunities(q, kind):
    query = (
        q
        or {
            "grants": "scholarships grants fellowship",
            "courses": "free AI Python courses"
        }[kind]
    )

    results = search_web(query, 8)

    if not results:
        return "لا توجد نتائج."

    lines = [
        f"<b>{'منح وفرص' if kind == 'grants' else 'دورات وتدريب'}: "
        f"{esc(query)}</b>"
    ]

    for t, u in results:
        lines.append(
            f"<b>{esc(t)}</b>\n"
            f"{esc(u)}"
        )

    return "\n\n".join(lines)


# ============================================================
# Gmail read-only
# ============================================================

def decode_header(v):
    if not v:
        return ""

    out = []

    for part, enc in email.header.decode_header(v):
        if isinstance(part, bytes):
            out.append(
                part.decode(
                    enc or "utf-8",
                    errors="replace"
                )
            )
        else:
            out.append(part)

    return "".join(out)


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


def cmd_email(q):
    box = email_connect()

    try:
        box.select("INBOX", readonly=True)

        typ, data = box.search(None, "ALL")
        ids = data[0].split()[-10:]

        lines = ["<b>آخر رسائل البريد</b>"]

        for mid in reversed(ids):
            typ, msgdata = box.fetch(mid, "(RFC822)")
            raw = msgdata[0][1]
            msg = email.message_from_bytes(raw)

            subject = decode_header(msg.get("Subject"))
            sender = decode_header(msg.get("From"))

            if (
                q
                and q.lower()
                not in (subject + " " + sender).lower()
            ):
                continue

            lines.append(
                f"<b>{esc(subject)}</b>\n"
                f"{esc(sender)}"
            )

        return "\n\n".join(lines)

    finally:
        try:
            box.logout()
        except Exception:
            pass


# ============================================================
# Skills
# ============================================================

def list_skills():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    out = []

    for p in sorted(SKILLS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue

        out.append(p.stem)

    return out


def validate_python(source):
    """يفحص Python باستخدام AST بدون تنفيذ الكود."""

    try:
        tree = ast.parse(source)

    except SyntaxError as e:
        return [f"syntax:{e.msg}:{e.lineno}"]

    findings = []

    forbidden_modules = {
        "subprocess",
        "ctypes",
        "multiprocessing",
        "pty",
        "pickle"
    }

    forbidden_calls = {
        "eval",
        "exec",
        "__import__",
        "system",
        "popen",
        "call",
        "run"
    }

    forbidden_attributes = {
        "system",
        "popen",
        "Popen",
        "call",
        "run",
        "rmtree",
        "remove",
        "unlink"
    }

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]

                if root in forbidden_modules:
                    findings.append(f"import:{root}")

        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]

            if root in forbidden_modules:
                findings.append(f"import:{root}")

        elif isinstance(node, ast.Call):

            if isinstance(node.func, ast.Name):
                if node.func.id in forbidden_calls:
                    findings.append(f"call:{node.func.id}")

            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden_attributes:
                    findings.append(f"attribute:{node.func.attr}")

        elif isinstance(node, ast.Attribute):
            if node.attr in forbidden_attributes:
                findings.append(f"attribute:{node.attr}")

    return sorted(set(findings))


def cmd_skills():
    xs = list_skills()

    return (
        "<b>Skills</b>\n"
        + (
            "\n".join("• " + esc(x) for x in xs)
            if xs
            else "لا توجد Skills إضافية."
        )
    )


def create_skill_request(description):
    prompt = f"""أنشئ Python Skill صغيرة لمهمة الوكيل التالية:

{description}

المطلوب:
- أعط كود Python كامل.
- لا تستخدم subprocess أو eval أو exec أو shell.
- لا تنفذ حذفا أو إرسالا خارجيا.
- يجب أن يحتوي الملف على:
  NAME = "...";
  DESCRIPTION = "...";
  def run(arg, context): ...
- run تعيد نصا.

أخرج الكود فقط.
"""

    code, err = ai(
        prompt,
        "skill",
        3500,
        SYSTEM_PROMPT
        + "\nأنت مهندس plugins. اكتب Skill آمنة قابلة للمراجعة."
    )

    if not code:
        return None, err

    m = re.search(
        r"```(?:python)?\s*(.*?)```",
        code,
        re.S | re.I
    )

    if m:
        code = m.group(1).strip()

    return code, None


def install_skill(name, source):
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]{0,63}",
        name
    ):
        raise ValueError("اسم Skill غير صالح")

    hits = validate_python(source)

    if hits:
        raise ValueError("Skill رفضت: " + ", ".join(hits))

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    p = SKILLS_DIR / (name + ".py")

    if p.exists():
        raise FileExistsError(f"Skill موجودة بالفعل: {name}")

    try:
        compile(source, str(p), "exec")

        temp = p.with_suffix(".tmp.py")
        temp.write_text(source, encoding="utf-8")

        py_compile.compile(
            str(temp),
            doraise=True
        )

        temp.unlink(missing_ok=True)

    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass

        p.unlink(missing_ok=True)
        raise

    p.write_text(source, encoding="utf-8")

    return p


# ============================================================
# Self-development / Patch security
# ============================================================

def validate_agent_patch(diff):
    """يسمح للـPatch بتعديل agent.py فقط."""

    if not diff or not diff.strip():
        return False, "الـPatch فارغ."

    if len(diff) > 200_000:
        return False, "الـPatch أكبر من الحد المسموح."

    lines = diff.splitlines()
    found_file = False

    for line in lines:

        if line.startswith("diff --git "):
            parts = line.split()

            if len(parts) != 4:
                return False, "صيغة diff غير صالحة."

            if parts[2] != "a/agent.py":
                return False, "الـPatch يحاول تعديل ملف غير agent.py."

            if parts[3] != "b/agent.py":
                return False, "الـPatch يحاول تعديل ملف غير agent.py."

            found_file = True

        elif line.startswith("--- "):
            if line not in ("--- a/agent.py", "--- /dev/null"):
                return False, "مسار الملف القديم غير مسموح."

        elif line.startswith("+++ "):
            if line != "+++ b/agent.py":
                return False, "مسار الملف الجديد غير مسموح."

    if not found_file:
        return False, "لم يتم العثور على diff صالح لـ agent.py."

    return True, ""


def propose_evolution(request):
    prompt = f"""أنت مهندس صيانة لهذا الوكيل.

طلب المستخدم:
{request}

أنشئ unified diff صالحا لتعديل الملف agent.py فقط.

قواعد صارمة:
- لا تضف أسرارا أو مفاتيح API.
- لا تحذف أو تتجاوز آليات الأمان.
- لا تضف تنفيذ shell تلقائي.
- لا تستخدم subprocess أو os.system أو eval أو exec.
- لا تغير نظام الموافقة.
- لا تعدل ملفات خارج agent.py.
- يجب أن يكون الـdiff قابلا للتطبيق بواسطة git apply.
- أعد diff فقط، بدون Markdown.
"""

    diff, err = ai(
        prompt,
        "evolution",
        6000,
        SYSTEM_PROMPT
        + "\nأنت مهندس برمجيات مسؤول عن patch آمن."
    )

    if not diff:
        return None, err

    diff = diff.strip()

    if diff.startswith("```"):
        diff = re.sub(r"^```[^\n]*\n", "", diff)
        diff = re.sub(r"\n```$", "", diff)

    diff = diff.strip()

    valid, reason = validate_agent_patch(diff)

    if not valid:
        return None, "تم رفض الـPatch: " + reason

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    pid = f"patch-{int(time.time())}"
    p = PROPOSALS_DIR / (pid + ".diff")

    p.write_text(diff, encoding="utf-8")

    check = subprocess.run(
        [
            "git",
            "apply",
            "--check",
            "--whitespace=error-all",
            str(p)
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True
    )

    valid_git = check.returncode == 0

    meta = {
        "id": pid,
        "type": "agent_patch",
        "created": iso(),
        "request": request,
        "valid": valid_git,
        "status": "pending" if valid_git else "invalid",
        "check_output": (check.stdout + check.stderr)[:3000]
    }

    atomic_write(
        PROPOSALS_DIR / (pid + ".json"),
        meta
    )

    if not valid_git:
        return (
            None,
            "الـPatch تم إنشاؤه لكنه فشل في فحص git apply:\n"
            + check.stderr[:2000]
        )

    return pid, None


def approve_patch(pid):
    if not re.fullmatch(r"patch-\d+", pid):
        return "معرّف Patch غير صالح."

    p = PROPOSALS_DIR / (pid + ".diff")

    if not p.exists():
        return "الـPatch غير موجود."

    meta_path = PROPOSALS_DIR / (pid + ".json")
    meta = read_json(meta_path, {})

    if meta.get("status") == "applied":
        return "هذا الـPatch تم تطبيقه بالفعل."

    diff = p.read_text(encoding="utf-8")

    valid, reason = validate_agent_patch(diff)

    if not valid:
        return "تم رفض الـPatch: " + reason

    check = subprocess.run(
        [
            "git",
            "apply",
            "--check",
            "--whitespace=error-all",
            str(p)
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True
    )

    if check.returncode != 0:
        return "فشل فحص الـPatch:\n" + check.stderr[:2000]

    agent_file = Path(__file__).resolve()
    backup = ROOT / f"agent.py.backup-{int(time.time())}"

    try:
        shutil.copy2(agent_file, backup)
    except Exception as e:
        return "تعذر إنشاء النسخة الاحتياطية: " + str(e)

    apply_result = subprocess.run(
        [
            "git",
            "apply",
            "--whitespace=error-all",
            str(p)
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True
    )

    if apply_result.returncode != 0:
        return (
            "فشل تطبيق الـPatch.\n"
            "النسخة الأصلية لم يتم تغييرها.\n\n"
            + apply_result.stderr[:2000]
        )

    try:
        py_compile.compile(str(agent_file), doraise=True)

    except Exception as e:

        try:
            shutil.copy2(backup, agent_file)

        except Exception as rollback_error:
            return (
                "التعديل تسبب في خطأ Python وفشل التراجع عنه تلقائيا.\n"
                f"خطأ الفحص: {e}\n"
                f"خطأ التراجع: {rollback_error}\n"
                f"النسخة الاحتياطية: {backup}"
            )

        meta["status"] = "reverted"
        meta["reverted_at"] = iso()
        meta["error"] = str(e)

        atomic_write(meta_path, meta)

        return (
            "التعديل كسر Python syntax، وتم التراجع عنه تلقائيا.\n"
            + str(e)
        )

    meta["approved_at"] = iso()
    meta["status"] = "applied"
    meta["backup"] = backup.name

    atomic_write(meta_path, meta)

    return (
        f"تم تطبيق {pid} بنجاح واجتاز فحص Python.\n"
        f"النسخة الاحتياطية: {backup.name}"
    )


# ============================================================
# Tasks
# ============================================================

def task_add(text, interval_minutes):
    tasks = read_json(TASKS_FILE, [])

    tid = f"task-{int(time.time())}"

    tasks.append(
        {
            "id": tid,
            "text": text,
            "interval": int(interval_minutes),
            "last_run": None,
            "enabled": True,
            "created": iso()
        }
    )

    atomic_write(TASKS_FILE, tasks)

    return tid


def task_due(t):
    if not t.get("enabled"):
        return False

    if not t.get("last_run"):
        return True

    try:
        last = datetime.fromisoformat(t["last_run"])
    except Exception:
        return True

    return (
        now() - last
        >= timedelta(minutes=int(t.get("interval", 1440)))
    )


def run_tasks(memory):
    tasks = read_json(TASKS_FILE, [])

    for t in tasks:
        if not task_due(t):
            continue

        try:
            ans = process(t["text"], memory)
            t["last_run"] = iso()

            if CHAT_ID:
                send(
                    CHAT_ID,
                    f"<b>مهمة تلقائية</b>\n{ans}"
                )

        except Exception as e:
            log("task error: " + str(e))

    atomic_write(TASKS_FILE, tasks)


# ============================================================
# Dispatcher
# ============================================================

def help_text():
    return """<b>AI Personal Agent v5</b>

<b>أساسي</b>
/start /help /ping /health /memory

<b>ويب ومعلومات</b>
/search /news /wiki /weather /summarize

<b>علمي</b>
/arxiv /pubmed /openalex /semantic

<b>فرص</b>
/jobs /grants /courses

<b>بريد — قراءة فقط</b>
/email

<b>برمجة</b>
/code /review /learn

<b>التطوير الذاتي</b>
/skills
/skill create وصف المهارة
/evolve وصف التطوير
/approve patch-ID

<b>الأتمتة</b>
/task add دقائق | الأمر
/tasks

مثال:
<code>/evolve أضف دعما لميزة X</code>

الوكيل سيُنشئ Patch ويختبره؛
لا يطبقه حتى تستخدم /approve.
"""


def health():
    return (
        f"<b>Health</b>\n"
        f"• Telegram: {'OK' if TELEGRAM_TOKEN else 'MISSING'}\n"
        f"• OpenRouter: {'OK' if OPENROUTER_API_KEY else 'MISSING'}\n"
        f"• Gmail: {'configured' if EMAIL_USER and EMAIL_PASS else 'not configured'}\n"
        f"• Skills: {len(list_skills())}\n"
        f"• Root: {esc(ROOT)}"
    )


def process(text, memory):
    raw = (text or "").strip()

    if not raw:
        return "اكتب أمرا أو سؤالا."

    if raw.startswith("/"):
        raw = raw[1:]

    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    try:
        if cmd in ("start", "hello"):
            return "أهلا. اكتب /help لعرض كل الوظائف."

        if cmd == "help":
            return help_text()

        if cmd == "ping":
            return "pong — الوكيل يعمل."

        if cmd == "health":
            return health()

        if cmd == "memory":
            return memory.summary()

        if cmd == "search":
            return cmd_search(arg)

        if cmd == "news":
            return cmd_news(arg)

        if cmd == "wiki":
            return cmd_wiki(arg)

        if cmd == "weather":
            return cmd_weather(arg)

        if cmd == "arxiv":
            return cmd_arxiv(arg)

        if cmd == "pubmed":
            return cmd_pubmed(arg)

        if cmd == "openalex":
            return cmd_openalex(arg)

        if cmd in ("semantic", "scholar"):
            return cmd_semantic(arg)

        if cmd == "jobs":
            return cmd_jobs(arg)

        if cmd == "grants":
            return search_opportunities(arg, "grants")

        if cmd == "courses":
            return search_opportunities(arg, "courses")

        if cmd == "email":
            return cmd_email(arg)

        if cmd == "skills":
            return cmd_skills()

        if cmd == "skill":
            a = arg.strip()

            if a.startswith("create "):
                desc = a[7:].strip()

                if not desc:
                    return "اكتب وصف المهارة."

                code, err = create_skill_request(desc)

                if not code:
                    return "تعذر إنشاء Skill: " + esc(err)

                name = "skill_" + str(int(time.time()))

                try:
                    p = install_skill(name, code)

                except Exception as e:
                    return "تم رفض إنشاء الـSkill:\n" + esc(str(e))

                memory.data["skills"].append(
                    {
                        "name": name,
                        "description": desc,
                        "created": iso()
                    }
                )

                memory.save()

                return (
                    "تم إنشاء Skill بعد اجتياز الفحص:\n"
                    f"<code>{esc(name)}</code>\n"
                    f"المسار: <code>{esc(p)}</code>"
                )

            return "استخدم: <code>/skill create وصف المهارة</code>"

        if cmd == "evolve":
            if not arg:
                return "اكتب ما تريد أن يطوره الوكيل."

            pid, err = propose_evolution(arg)

            if pid:
                return (
                    f"تم إنشاء Patch: <code>{pid}</code>\n"
                    "تم فحص الـPatch، ولا يتم تطبيقه تلقائيا.\n"
                    f"استخدم <code>/approve {pid}</code> للتفعيل."
                )

            return "تعذر إنشاء Patch: " + esc(err)

        if cmd == "approve":
            return approve_patch(arg.strip())

        if cmd == "task":
            a = arg.strip()

            if a.startswith("add "):
                rest = a[4:]

                m = re.match(r"(\d+)\s*\|\s*(.+)", rest, re.S)

                if not m:
                    return "الصيغة: /task add 60 | /jobs ai"

                tid = task_add(
                    m.group(2),
                    int(m.group(1))
                )

                return f"تم إنشاء المهمة: <code>{tid}</code>"

            return "استخدم /task add دقائق | الأمر"

        if cmd == "tasks":
            ts = read_json(TASKS_FILE, [])

            return (
                "<b>Tasks</b>\n"
                + (
                    "\n".join(
                        f"• {esc(t['id'])}: "
                        f"{esc(t['text'])} — "
                        f"{'ON' if t.get('enabled') else 'OFF'}"
                        for t in ts
                    )
                    or "لا توجد مهام."
                )
            )

        if cmd in ("code", "review", "learn"):
            system = SYSTEM_PROMPT

            if cmd == "code":
                system += "\nأنت مبرمج محترف. أعط الكود كاملا."

            if cmd == "review":
                system += "\nراجع الأمان والمنطق والأداء."

            if cmd == "learn":
                system += "\nاشرح للمبتدئ خطوة بخطوة."

            ans, err = ai(
                arg,
                cmd,
                3500,
                system
            )

            return ans or "تعذر التنفيذ: " + esc(err)

        if cmd == "summarize":
            if not safe_url(arg):
                return "الرابط غير صالح أو غير مسموح."

            txt = re.sub(
                r"<script.*?</script>|<style.*?</style>",
                " ",
                fetch(arg),
                flags=re.I | re.S
            )

            txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"\s+", " ", txt)[:30000]

            ans, err = ai(
                "لخص بالعربية:\n" + txt,
                "summarize",
                2200
            )

            return ans or "تعذر التلخيص: " + esc(err)

        ans, err = ai(raw, "general", 2500)

        return ans or "تعذر تنفيذ الطلب: " + esc(err)

    except Exception as e:
        memory.failure(cmd, e)
        log(traceback.format_exc())

        return (
            f"حدث خطأ في <code>{esc(cmd)}</code>: "
            f"{esc(str(e)[:500])}"
        )


# ============================================================
# Telegram polling
# ============================================================

def poll():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير مضبوط")

    mem = Memory()
    offset = None

    log("Agent v5 polling started")

    while True:
        try:
            params = {"timeout": 50}

            if offset is not None:
                params["offset"] = offset

            updates = tg(
                "getUpdates",
                params,
                timeout=60
            )

            for u in updates or []:
                offset = u["update_id"] + 1

                msg = u.get("message") or {}
                chat = msg.get("chat") or {}

                cid = str(chat.get("id", ""))
                text = msg.get("text") or ""

                if not text:
                    continue

                if CHAT_ID and cid != CHAT_ID:
                    continue

                ans = process(text, mem)

                mem.conversation(
                    text,
                    ans,
                    text.split()[0][:50]
                )

                mem.success("message")
                mem.save()

                send(cid, ans)

        except KeyboardInterrupt:
            break

        except Exception as e:
            log("poll error: " + str(e))
            time.sleep(4)


# ============================================================
# Scheduled mode
# ============================================================

def once():
    mem = Memory()
    run_tasks(mem)
    mem.save()


# ============================================================
# Self-test
# ============================================================

def selftest():
    print("Running self-test...")

    try:
        py_compile.compile(
            str(Path(__file__).resolve()),
            doraise=True
        )

        print("agent.py syntax: OK")

    except Exception as e:
        print("agent.py syntax: FAILED")
        print(e)
        raise

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skill_errors = []

    for skill in sorted(SKILLS_DIR.glob("*.py")):
        if skill.name.startswith("_"):
            continue

        try:
            source = skill.read_text(encoding="utf-8")
            hits = validate_python(source)

            if hits:
                skill_errors.append(
                    f"{skill.name}: " + ", ".join(hits)
                )
                continue

            py_compile.compile(
                str(skill),
                doraise=True
            )

            print(f"skill {skill.name}: OK")

        except Exception as e:
            skill_errors.append(f"{skill.name}: {e}")

    if skill_errors:
        print("Skills: FAILED")

        for error in skill_errors:
            print(" - " + error)

        raise SystemExit(1)

    print("Skills: OK")
    print("Available skills:", list_skills())
    print("Self-test: OK")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    mode = os.getenv("AGENT_MODE", "poll").lower()

    if mode == "once":
        once()

    elif mode == "selftest":
        selftest()

    else:
        poll()
