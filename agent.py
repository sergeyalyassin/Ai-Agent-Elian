#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Personal Agent v3 — Professional Edition
============================================
تحسينات v3:
- تهدئة النماذج الفاشلة (cooldown)
- تحويل Markdown إلى HTML الآمن لتيليجرام
- تقسيم الرسائل مع احترام أسوار الأكواد
- Open-Meteo بدلاً من wttr.in
- كتابة ذرية للذاكرة + نسخ احتياطية
- حماية SSRF
- بحث ويب متعدد المزودين
- استخراج محسّن من PubMed / Scholar
"""

import os, sys, json, random, re, time, socket
import imaplib, email as email_lib
import subprocess, traceback, tempfile, shutil
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.header import decode_header

# ============================================================
# الإعدادات
# ============================================================
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID           = os.environ.get("CHAT_ID", "").strip()
OPENROUTER_API_KEY= os.environ.get("OPENROUTER_API_KEY", "").strip()
EMAIL_USER        = os.environ.get("EMAIL_USER", "").strip()
EMAIL_PASS        = os.environ.get("EMAIL_PASS", "").strip()

MEMORY_FILE       = "memory.json"
SKILL_FILE        = "SKILL.md"
COOLDOWN_FILE     = ".model_cooldown.json"
MAX_TG_MSG_LEN    = 4000

# ============================================================
# قائمة النماذج المجانية الموثوقة (مرتّبة حسب الأولوية)
# ============================================================
TRUSTED_MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-r1:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "google/gemma-2-9b-it:free",
    "microsoft/phi-3-medium-128k-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "cognitivecomputations/dolphin3.0-mistral-24b:free",
]

FALLBACK_MODELS = [
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "qwen/qwen3-coder:free",
    "deepseek/deepseek-r1-distill-qwen-32b:free",
    "mistralai/codestral-2501:free",
    "cohere/north-mini-code:free",
    "tiiuae/falcon-180b-chat:free",
    "moonshotai/moonlight-16b-a3b-instruct:free",
    "thudm/glm-4-9b-chat:free",
    "yi/yi-coder-9b-chat:free",
]

ALL_MODELS = TRUSTED_MODELS + FALLBACK_MODELS

# ============================================================
# إدارة تهدئة النماذج (Cooldown)
# ============================================================
class ModelCooldowns:
    """يتتبع تهدئة النماذج التي فشلت مؤقتاً (429 / 5xx)"""
    def __init__(self):
        self.data = {}
        if os.path.exists(COOLDOWN_FILE):
            try:
                with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def is_available(self, model):
        until = self.data.get(model)
        if not until:
            return True
        try:
            if datetime.fromisoformat(until) > datetime.utcnow():
                return False
            del self.data[model]
            return True
        except Exception:
            return True

    def set_cooldown(self, model, seconds):
        self.data[model] = (datetime.utcnow() + timedelta(seconds=seconds)).isoformat()

    def save(self):
        try:
            with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
        except Exception:
            pass

# ============================================================
# الذاكرة الدائمة (كتابة ذرية + نسخ احتياطية)
# ============================================================
class Memory:
    def __init__(self):
        self.data = self._load()

    def _default(self):
        return {
            "version": 3,
            "created_at": datetime.utcnow().isoformat(),
            "last_updated": datetime.utcnow().isoformat(),
            "user": {"name": "", "language": "ar",
                     "preferences": {}, "timezone": "Africa/Cairo"},
            "conversations": [], "errors": [], "successes": [],
            "skills": [],
            "stats": {"total_runs": 0, "successful_commands": 0,
                      "failed_commands": 0, "commands_used": {}},
            "model_preferences": {}, "notes": []
        }

    def _load(self):
        default = self._default()
        if not os.path.exists(MEMORY_FILE):
            return default
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # ترحيل آمن: إضافة المفاتيح الجديدة
            for k, v in default.items():
                if k not in loaded:
                    loaded[k] = v
            return loaded
        except Exception as e:
            print(f"warn: memory corrupted, trying backup: {e}")
            # محاولة استرداد من نسخة احتياطية
            if os.path.exists(MEMORY_FILE + ".bak"):
                try:
                    with open(MEMORY_FILE + ".bak", "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return default

    def save(self):
        """كتابة ذرية: temp -> fsync -> rename"""
        self.data["last_updated"] = datetime.utcnow().isoformat()
        try:
            # نسخة احتياطية أولاً
            if os.path.exists(MEMORY_FILE):
                try:
                    shutil.copy2(MEMORY_FILE, MEMORY_FILE + ".bak")
                except Exception:
                    pass
            # كتابة ذرية
            dir_ = os.path.dirname(os.path.abspath(MEMORY_FILE)) or "."
            fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".mem_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, MEMORY_FILE)
            finally:
                if os.path.exists(tmp):
                    try: os.remove(tmp)
                    except Exception: pass
            return True
        except Exception as e:
            print(f"warn save memory: {e}")
            return False

    def commit_to_github(self):
        """رفع الذاكرة مع معالجة تعارض بسيطة"""
        if not os.path.exists(".git"):
            return
        try:
            for cmd in (
                ["git", "config", "user.email", "agent@github.com"],
                ["git", "config", "user.name", "AI Agent"],
                ["git", "add", MEMORY_FILE],
            ):
                subprocess.run(cmd, check=False, capture_output=True)
            if os.path.exists(SKILL_FILE):
                subprocess.run(["git", "add", SKILL_FILE], check=False, capture_output=True)
            r = subprocess.run(
                ["git", "commit", "-m", f"memory update {datetime.utcnow().isoformat()}"],
                check=False, capture_output=True, text=True)
            if "nothing to commit" in (r.stdout or ""):
                return
            # محاولة push مع pull --rebase عند التعارض
            p = subprocess.run(["git", "push"], check=False, capture_output=True, text=True)
            if p.returncode != 0:
                subprocess.run(["git", "pull", "--rebase", "--autostash"],
                               check=False, capture_output=True)
                subprocess.run(["git", "push"], check=False, capture_output=True)
        except Exception as e:
            print(f"warn commit: {e}")

    # واجهات
    def log_conversation(self, u, a, cmd=""):
        self.data["conversations"].append({
            "time": datetime.utcnow().isoformat(), "command": cmd,
            "user": str(u)[:500], "agent": str(a)[:500]})
        self.data["conversations"] = self.data["conversations"][-50:]
        self.data["stats"]["total_runs"] += 1

    def log_error(self, cmd, err, ctx=""):
        self.data["errors"].append({
            "time": datetime.utcnow().isoformat(), "command": cmd,
            "error": str(err)[:500], "context": str(ctx)[:300]})
        self.data["errors"] = self.data["errors"][-100:]
        self.data["stats"]["failed_commands"] += 1

    def log_success(self, cmd, summ=""):
        self.data["successes"].append({
            "time": datetime.utcnow().isoformat(), "command": cmd,
            "summary": str(summ)[:300]})
        self.data["successes"] = self.data["successes"][-100:]
        self.data["stats"]["successful_commands"] += 1
        st = self.data["stats"]["commands_used"]
        st[cmd] = st.get(cmd, 0) + 1

    def add_skill(self, name, desc=""):
        if name in [s.get("name") for s in self.data["skills"]]:
            return False
        self.data["skills"].append({
            "name": name, "description": str(desc)[:300],
            "learned_at": datetime.utcnow().isoformat()})
        return True

    def has_skill(self, name):
        return name in [s.get("name") for s in self.data["skills"]]

    def set_preference(self, k, v):
        self.data["user"]["preferences"][k] = v

    def best_model_for(self, t):
        return self.data["model_preferences"].get(t)

    def set_best_model(self, t, m):
        self.data["model_preferences"][t] = m

    def summary(self):
        s = self.data["stats"]
        sk = ", ".join([x["name"] for x in self.data["skills"][-5:]])
        return (f"<b>ملخص الذاكرة</b>\n"
                f"• إجمالي التشغيلات: {s['total_runs']}\n"
                f"• أوامر ناجحة: {s['successful_commands']}\n"
                f"• أوامر فاشلة: {s['failed_commands']}\n"
                f"• مهارات: {len(self.data['skills'])}\n"
                f"• آخر مهارات: {sk or 'لا يوجد'}\n"
                f"• آخر تحديث: {self.data['last_updated'][:19]}")

# ============================================================
# الشخصية
# ============================================================
SYSTEM_PROMPT = """أنت مساعد ذكاء اصطناعي شخصي، تتحدث مع المستخدم كصديق مقرب.
قواعدك: العربية المبسطة مع لمسة عامية، إيموجي باعتدال، لا تبدأ بـ "بالتأكيد"،
كن مختصراً، وأنت خبير برمجة. تجنب Markdown المعقّد لأن الرد يُرسل لتيليجرام."""

def greeting():
    return "أهلاً يا صديقي! اكتب <code>help</code> لتعرف الأوامر."

def help_message():
    return """<b>الأوامر المتاحة:</b>

<b>الفرص:</b>
• <code>jobs ai</code> — وظائف
• <code>grants</code> — منح
• <code>courses</code> — دورات

<b>البحث العام:</b>
• <code>search كلمة</code>
• <code>news</code> — أخبار
• <code>weather القاهرة</code> — طقس
• <code>wiki موضوع</code>
• <code>summarize رابط</code>

<b>البحث العلمي:</b>
• <code>arxiv موضوع</code>
• <code>pubmed موضوع</code>
• <code>scholar موضوع</code>

<b>البريد والمهارات:</b>
• <code>email</code>
• <code>skill اسم</code>

<b>البرمجة:</b>
• <code>code سؤال</code>
• <code>review كود</code>
• <code>learn موضوع</code>

<b>أخرى:</b>
• <code>memory</code> — ملخص الذاكرة
• <code>help</code>"""

def format_error(cmd, reason=""):
    return (f"معلش يا صديقي، مشكلة في <code>{cmd}</code>.\n"
            f"السبب: {reason or 'غير معروف'}")

def unknown_command(cmd):
    return f"مش فاهم <code>{cmd}</code>. اكتب <code>help</code>."

# ============================================================
# تحويل Markdown → HTML آمن لتيليجرام
# ============================================================
def md_to_tg_html(text):
    """تحويل بسيط وآمن: **bold**، *bold*، `code`، ```block```"""
    if not text:
        return ""
    # حماية أكواد ``` ``` أولاً
    code_blocks = []
    def _stash(m):
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks)-1}\x00"
    text = re.sub(r"```(?:[a-zA-Z0-9]+)?\n?(.*?)```", _stash, text, flags=re.DOTALL)
    # حماية `inline`
    inline = []
    def _stash_inline(m):
        inline.append(m.group(1))
        return f"\x00CI{len(inline)-1}\x00"
    text = re.sub(r"`([^`\n]+)`", _stash_inline, text)
    # Escape HTML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Markdown → HTML
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<b>\1</b>", text)
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    # استرجاع الأكواد
    for i, cb in enumerate(code_blocks):
        safe = cb.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre>{safe}</pre>")
    for i, il in enumerate(inline):
        safe = il.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CI{i}\x00", f"<code>{safe}</code>")
    return text

# ============================================================
# تقسيم رسائل تيليجرام مع احترام أسوار الأكواد
# ============================================================
def split_for_telegram(text, limit=MAX_TG_MSG_LEN):
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
        if len(current) + len(line) + 1 > limit:
            # أغلق أي كود مفتوح مؤقتاً
            if in_code:
                current += "\n```"
            chunks.append(current)
            # أعد فتح الكود في المقطع الجديد
            current = "```\n" if in_code else ""
        current += line + "\n"
    if current.strip():
        if in_code:
            current += "```"
        chunks.append(current)
    return chunks

# ============================================================
# إرسال تيليجرام (بصيغة HTML)
# ============================================================
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("warn: no telegram creds")
        return False
    html = md_to_tg_html(text)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = True
    for ch in split_for_telegram(html):
        try:
            data = urllib.parse.urlencode({
                "chat_id": CHAT_ID, "text": ch,
                "disable_web_page_preview": "true",
                "parse_mode": "HTML"}).encode()
            urllib.request.urlopen(url, data, timeout=30)
        except urllib.error.HTTPError:
            # fallback: نص عادي
            try:
                plain = re.sub(r"<[^>]+>", "", ch)
                data = urllib.parse.urlencode({
                    "chat_id": CHAT_ID, "text": plain,
                    "disable_web_page_preview": "true"}).encode()
                urllib.request.urlopen(url, data, timeout=30)
            except Exception as e2:
                print(f"warn send: {e2}")
                ok = False
        except Exception as e:
            print(f"warn send: {e}")
            ok = False
    return ok

# ============================================================
# HTTP Client مع حماية SSRF
# ============================================================
_PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")
def _is_safe_url(url):
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname or ""
        if not host:
            return False
        if host.startswith(_PRIVATE_PREFIXES):
            return False
        if host in ("localhost", "::1"):
            return False
        # منع IP مباشر داخلي 172.16-31
        if host.startswith("172."):
            try:
                second = int(host.split(".")[1])
                if 16 <= second <= 31:
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return False

def _fetch_text(url, timeout=30, max_size=500_000):
    if not _is_safe_url(url):
        raise ValueError("URL غير مسموح")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_size)
    return raw.decode("utf-8", errors="ignore")

def _fetch_json(url, timeout=30):
    return json.loads(_fetch_text(url, timeout))

# ============================================================
# OpenRouter مع Cooldown + تصنيف الأخطاء
# ============================================================
def call_ai(prompt, system=None, task_type="general", max_tokens=2000):
    if not OPENROUTER_API_KEY:
        return None, "OPENROUTER_API_KEY missing"
    system = system or SYSTEM_PROMPT
    mem = Memory()
    cooldowns = ModelCooldowns()

    preferred = mem.best_model_for(task_type)
    ordered = ([preferred] if preferred else []) + [
        m for m in ALL_MODELS if m != preferred
    ]

    last_err = None
    attempts = 0
    max_attempts = 8

    for model in ordered:
        if attempts >= max_attempts:
            break
        if not cooldowns.is_available(model):
            continue
        attempts += 1
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": 0.7}
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com",
                    "X-Title": "AI Personal Agent"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]
            mem.set_best_model(task_type, model)
            cooldowns.save()
            return content, None
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429:
                cooldowns.set_cooldown(model, 3600)  # ساعة
                last_err = f"{model}: 429 rate-limit"
            elif code in (401, 403):
                cooldowns.set_cooldown(model, 86400)  # يوم
                last_err = f"{model}: {code} auth"
            elif code == 404:
                cooldowns.set_cooldown(model, 86400)
                last_err = f"{model}: 404"
            elif 500 <= code < 600:
                cooldowns.set_cooldown(model, 600)  # 10 دقائق
                last_err = f"{model}: {code} server"
            else:
                cooldowns.set_cooldown(model, 300)
                last_err = f"{model}: HTTP {code}"
        except (urllib.error.URLError, socket.timeout) as e:
            cooldowns.set_cooldown(model, 120)
            last_err = f"{model}: timeout"
        except Exception as e:
            cooldowns.set_cooldown(model, 60)
            last_err = f"{model}: {str(e)[:100]}"

    cooldowns.save()
    return None, last_err or "كل النماذج فشلت"

# ============================================================
# 1) الفرص — jobs, grants, courses
# ============================================================
def cmd_jobs(arg, mem):
    q = arg.strip() or "ai"
    try:
        params = urllib.parse.urlencode({"search": q, "limit": 10})
        data = _fetch_json(f"https://remotive.com/api/remote-jobs?{params}")
        jobs = data.get("jobs", [])
        if not jobs:
            return f"لا توجد وظائف لـ <code>{q}</code>.", None
        jobs.sort(key=lambda j: j.get("publication_date", ""), reverse=True)
        lines = [f"<b>وظائف {q}</b> ({len(jobs)})\n"]
        for j in jobs[:8]:
            lines.append(f"<b>{j.get('title','')}</b>\n"
                         f"{j.get('company_name','')}\n"
                         f"{j.get('candidate_required_location','')}\n"
                         f"{j.ge
