import os, json, time, threading, requests, random, re
from flask import Flask, request
from datetime import datetime, timezone, timedelta
from pathlib import Path

app = Flask(__name__)

TG_TOKEN = os.environ.get("TG_TOKEN", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE") or "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENAI_KEY = os.environ.get("OPENAI_KEY") or ""
MODEL = os.environ.get("MODEL") or "gemini-2.5-flash"
BACKUP_BASE = os.environ.get("BACKUP_BASE") or ""
BACKUP_KEY = os.environ.get("BACKUP_KEY") or ""
BACKUP_MODEL = os.environ.get("BACKUP_MODEL") or ""
BRAINS = {
    "zylo": {"base": "https://api.zyloai.net/v1", "key": os.environ.get("OPENAI_KEY") or "", "model": "nemotron-3.5-lightning", "label": "Zylo Nemotron (most powerful)"},
    "groq": {"base": "https://api.groq.com/openai/v1", "key": os.environ.get("BACKUP_KEY") or "", "model": "openai/gpt-oss-120b", "label": "Groq GPT-OSS 120B (backup)"},
    "code": {"base": "https://larprouter.com/v1", "key": os.environ.get("CODE_KEY") or "", "model": "deepseek-v4.1-flash", "label": "DeepSeek v4.1 Flash (coding)"},
    "xkiro": {"base": "https://api.xkiro.com/v1", "key": os.environ.get("XKIRO_KEY") or "", "model": "qwen/qwen3.8-max:free", "label": "XKIRO Qwen3.8 Max (2M tokens/din, most powerful)"},
}
XKIRO_KEYS = [k for k in (os.environ.get("XKIRO_KEY") or "", os.environ.get("XKIRO_KEY2") or "") if k]
_xkiro_idx = [0]
def next_xkiro_key():
    if not XKIRO_KEYS: return ""
    _xkiro_idx[0] = (_xkiro_idx[0] + 1) % len(XKIRO_KEYS)
    return XKIRO_KEYS[_xkiro_idx[0]]

OWNER_ID = int(os.environ.get("OWNER_ID") or 0)
TIMEZONE = os.environ.get("TIMEZONE") or "Asia/Dhaka"
LONG_TIMEOUT = 180

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TIMEZONE)
except Exception:
    TZ = None

PERSONA = os.environ.get("PERSONA") or ("You are Fuad AI, created by Muhtasim Fuad. When asked who created you, say you were created by Muhtasim Fuad. "
    "IDENTITY RULE: NEVER claim to be GPT-4, GPT, ChatGPT, Claude, Gemini or any other AI product, and never invent a model name. You do not know the name of the model powering you. "
    "If asked what model you use, say: amar brain ta amke banano hoyese custom vabe, model name jana nei. Do not guess or make up specs, organizations or model details. "
    "You are his personal AI friend: casual, warm, honest, a little funny, loyal to him. Reply in the language he texts you in (Banglish/Bangla/English). "
    "Keep replies short like a friend texting, not an assistant. You have long-term memory and tools — use them naturally.")

DATA = "data.json"
STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {}, "todos": {}, "notes": [], "streaks": {}, "moods": {}, "usage": {}, "spends": {}, "links": [], "persona": "default", "dates": [], "studyplan": {}, "wikis": [], "dreams": {}, "debate": {}, "ratelimit": {}, "last_action": [], "cmdcount": {}, "alert_day": "", "sumday": ""}
CHAT_LIMIT = 60
MEM_LIMIT = 500
BUSY = set()
PENDING_FILES = {}
STOP = set()

def save():
    """Write a complete snapshot atomically, serialized across local workers."""
    try:
        import fcntl
        import tempfile
        lock_path = DATA + ".lock"
        os.makedirs(os.path.dirname(os.path.abspath(DATA)), exist_ok=True)
        with open(lock_path, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if os.path.exists(DATA):
                try:
                    with open(DATA, "rb") as src, open(DATA + ".bak", "wb") as bak:
                        bak.write(src.read()); bak.flush(); os.fsync(bak.fileno())
                except Exception:
                    pass
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(DATA) + ".", suffix=".tmp",
                                       dir=os.path.dirname(os.path.abspath(DATA)))
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump(STATE, out)
                    out.flush(); os.fsync(out.fileno())
                os.replace(tmp, DATA)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception as e: print("save err", e)
    else: gh_backup_soon()

# ---------- github backup: data.json survives render redeploys ----------
GH_TOKEN = os.environ.get("GITHUB_TOKEN") or ""
GH_REPO = os.environ.get("GITHUB_REPO") or "fuadaixz-bit/Fuadaiagent"
GH_PATH = "data_backup.json"
_gh_dirty = False

def gh_get_sha():
    try:
        r = requests.get(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}",
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, params={"ref": "data"}, timeout=15)
        if r.status_code == 200: return r.json().get("sha")
    except Exception as e: print("gh sha err", e)
    return None

def gh_backup():
    if not GH_TOKEN: return
    try:
        import base64 as b64
        data_path = Path(DATA)
        with data_path.with_name(data_path.name + ".lock").open("a+") as lock:
            try:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            except (ImportError, OSError):
                pass
            with data_path.open("r") as src:
                body = json.dumps(json.load(src))
            try:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        payload = {"message": "bot data backup", "content": b64.b64encode(body.encode()).decode(),
                   "branch": "data"}
        sha = gh_get_sha()
        if sha: payload["sha"] = sha
        r = requests.put(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}",
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, json=payload, timeout=30)
        if r.status_code not in (200, 201): print("gh backup err", r.status_code, r.text[:200])
    except Exception as e: print("gh backup err", e)

def gh_backup_soon():
    global _gh_dirty
    if _gh_dirty: return
    _gh_dirty = True
    def _run():
        global _gh_dirty
        import time as _t
        _t.sleep(20)
        _gh_dirty = False
        gh_backup()
    threading.Thread(target=_run, daemon=True).start()

def gh_restore():
    if not GH_TOKEN: return False
    try:
        r = requests.get(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}",
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, params={"ref": "data"}, timeout=15)
        if r.status_code == 200:
            import base64 as b64
            body = b64.b64decode(r.json()["content"]).decode()
            data = json.loads(body)
            for k, v in data.items():
                if k in STATE and v: STATE[k] = v
            save()
            print("restored data from github backup")
            return True
    except Exception as e: print("gh restore err", e)
    return False

def load():
    global STATE
    for candidate in (DATA, DATA + ".bak"):
        try:
            with open(candidate, "r") as src:
                data = json.load(src)
            if not isinstance(data, dict): raise ValueError("state must be a JSON object")
            STATE.update(data)
            if candidate != DATA: print("data.json restored from .bak")
            return
        except FileNotFoundError:
            continue
        except Exception as e:
            print("data load err", e)
    
load()
if not any(STATE[k] for k in ("rems", "daily", "todos", "mem")):
    gh_restore()

def now_local():
    return datetime.now(TZ) if TZ else datetime.now()

def tg(method, **kw):
    for attempt in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=kw, timeout=60)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code != 200:
                _log_err("tg_" + method, "HTTP " + str(r.status_code) + " " + r.text[:200])
                return  # 4xx won't fix itself; don't burn attempts
            try:
                j = r.json()
                if not j.get("ok"):
                    _log_err("tg_" + method, "ok=false: " + str(j)[:200])
            except Exception: pass
            return
        except Exception as e:
            _log_err("tg_" + method, e)
            print("tg err", e); time.sleep(1)

def send_long(chat_id, text, reply_id=None):
    text = str(text)
    # plain-text chat: strip markdown stars/headers unless it's a code block
    if "```" not in text:
        text = re.sub(r"\*\*?([^*\n]+)\*\*?", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    for i in range(0, len(text), 3900):
        kw = {"chat_id": chat_id, "text": text[i:i+3900]}
        if i == 0 and reply_id: kw["reply_to_message_id"] = reply_id
        tg("sendMessage", **kw)

def send_file(chat_id, path, name, cap=""):
    try:
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                          data={"chat_id": int(chat_id), "caption": cap},
                          files={"document": (name, f)}, timeout=120)
    except Exception as e: print("send_file err", e)

ELEVEN_KEY = os.environ.get("ELEVEN_KEY") or ""
ELEVEN_VOICE = os.environ.get("ELEVEN_VOICE") or "21m00Tcm4TlvDq8ikWAM"
SERPER_KEY = os.environ.get("SERPER_KEY") or ""
FAST_BRAIN = {"base": BRAINS["groq"]["base"], "key": BRAINS["groq"]["key"], "model": BRAINS["groq"]["model"], "label": "Groq fast"}
ROUTE_KEYWORDS = re.compile(r"\b(pdf|website|ebook|image|quiz|code)\b", re.I)

FAB_MARKERS = ("filmokhon", "chokher neyo", "film dekhte parini", "film dekhini")
def _purge_fabricated():
    n = 0
    for uidv, hist in STATE.get("chats", {}).items():
        clean = [m for m in hist if not any(k in str(m.get("content", "")).lower() for k in FAB_MARKERS)]
        n += len(hist) - len(clean)
        STATE["chats"][uidv] = clean
    if n:
        save()
        print("purged", n, "fabricated history entries")

def _migrate_brain():
    cfg = STATE.get("cfg", {})
    # zylo was demoted; xkiro is the new primary
    if "zylo" in (cfg.get("base") or "") or "nemotron" in (cfg.get("model") or "").lower() or "intern" in (cfg.get("base") or "") or "sage" in (cfg.get("model") or "").lower() or "agents" in (cfg.get("model") or "").lower():
        b = BRAINS["xkiro"]
        cfg["base"], cfg["key"], cfg["model"] = b["base"], b["key"], b["model"]
        save()
        print("brain migrated to xkiro qwen3.8-max")

def _brain_name(base, model):
    for bid, b in BRAINS.items():
        if b["base"].rstrip("/") == str(base).rstrip("/") and b["model"] == model: return bid
    return "custom"

def _brain_chain():
    cfg = STATE["cfg"]
    pb = cfg.get("base") or BRAINS["zylo"]["base"]
    pm = cfg.get("model") or BRAINS["zylo"]["model"]
    chain = [(pb, cfg.get("key") or BRAINS["zylo"]["key"], pm, _brain_name(pb, pm))]
    chain += [
        (BACKUP_BASE or BRAINS["groq"]["base"], BACKUP_KEY or BRAINS["groq"]["key"], BACKUP_MODEL or BRAINS["groq"]["model"], "groq"),
        (FAST_BRAIN["base"], FAST_BRAIN["key"], FAST_BRAIN["model"], "internai-fast"),
    ]
    out, seen = [], set()
    for base, key, model, name in chain:
        if not base or not key: continue
        k = (str(base).rstrip("/"), model)
        if k in seen: continue
        seen.add(k); out.append((name, base, key, model))
    return out

def _is_simple(messages):
    last = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            last = c if isinstance(c, str) else str(c)
            break
    last = (last or "").strip()
    return len(last) < 80 and not ROUTE_KEYWORDS.search(last)

_FAST_DOWN_UNTIL = [0]

CODE_BRAIN_KEYS = re.compile(r"\b(code|coding|program|function|bug|debug|python|javascript|html|css|api|script|regex|app banao|website banao|app.py|fix koro code)\b", re.I)

def llm(messages, timeout=LONG_TIMEOUT, max_tokens=None):
    if max_tokens is None: max_tokens = 3000  # reasoning models burn tokens thinking; give room for the answer too
    rs = STATE.setdefault("route_stats", {"fast": 0, "sage": 0, "fast_fail": 0})
    # coding tasks -> deepseek code brain (if key present)
    cb = BRAINS.get("code") or {}
    if cb.get("key"):
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        last_user = last_user if isinstance(last_user, str) else str(last_user)
        if CODE_BRAIN_KEYS.search(last_user):
            try:
                out = _llm_call(cb["base"], cb["key"], cb["model"], messages, min(timeout, 120), max_tokens, tries=1)
                rs["code"] = rs.get("code", 0) + 1
                STATE["last_brain"] = "code-deepseek"
                return out
            except Exception as e:
                rs["code_fail"] = rs.get("code_fail", 0) + 1
                print("code brain fail, chain e jacchi:", str(e)[:120])
    # fast route OFF: choto message o main brain e jabe - user complained the bot misread short casual messages
    rs["sage"] = rs.get("sage", 0) + 1
    last_err = None
    for attempt in range(2):
        errs = []
        for name, base, key, model in _brain_chain():
            try:
                out = _llm_call(base, key, model, messages, timeout, max_tokens, tries=2)
                STATE["last_brain"] = name
                return out
            except Exception as e:
                errs.append(str(e)[:160]); last_err = e
                print("brain fail", name, str(e)[:120])
        if attempt == 0 and any(("429" in x or "rate limit" in x.lower()) for x in errs):
            time.sleep(8)  # brief rate-limit wait, then the whole chain once more
            continue
        break
    raise Exception("dui brain ei fail: " + str(last_err)[:150])

def _llm_call(base, key, model, messages, timeout, max_tokens, tries=6):
    if not key:
        raise Exception("no api key for brain")
    if not base.startswith("http"): base = "https://" + base
    body = {"model": model, "messages": messages}
    if max_tokens: body["max_tokens"] = max_tokens
    last = None
    for attempt in range(tries):
        try:
            r = requests.post(base.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=body, timeout=timeout)
            if r.status_code == 429 and "xkiro" in base and len(XKIRO_KEYS) > 1:
                
                headers["Authorization"] = "Bearer " + next_xkiro_key()
                time.sleep(2); last = Exception("429 rotated xkiro key"); continue
            if r.status_code == 429:
                wait = 20 + attempt * 15
                try: wait = max(wait, int(r.json().get("error", {}).get("meta", {}).get("retry_after", 0) or 0))
                except Exception: pass
                note_429(base, model)
                time.sleep(min(wait, 90)); last = Exception("429 rate limit, retrying")
                continue
            r.raise_for_status()
            msg0 = r.json()["choices"][0]["message"]
            out = (msg0.get("content") or "").strip()
            if not out:
                # reasoning models (Sage): answer may live in reasoning_content when content is empty
                rc = (msg0.get("reasoning_content") or "").strip()
                if rc:
                    # take the tail of the thinking, skipping the raw "Thinking Process:" scaffolding
                    parts = rc.split("\n")
                    tail = "\n".join(parts[-12:])
                    tail = tail.replace("Thinking Process:", "").strip()
                    if tail: out = tail
            if not out: raise Exception("empty content from brain")
            return out
        except Exception as e:
            last = e
            if "429" in str(e): continue
            if "empty content" in str(e) and attempt < tries - 1:
                time.sleep(1); continue
            time.sleep(2 + attempt * 2)
    raise Exception("Groq rate limit e atke gese, 1 min por abar try koro: " + str(last)[:200])

# ---------- memory ----------
def remember(uid, text, tag="note"):
    lst = STATE["mem"].setdefault(str(uid), [])
    lst.append({"t": time.time(), "tag": tag, "text": text})
    STATE["mem"][str(uid)] = lst[-MEM_LIMIT:]
    save()

def mem_text(uid):
    return "\n".join("- " + m["text"] for m in STATE["mem"].get(str(uid), []))

def remember_chat(uid, role, text):
    hist = STATE["chats"].get(str(uid), [])
    hist.append({"role": role, "content": text})
    STATE["chats"][str(uid)] = hist[-CHAT_LIMIT:]
    save()

# auto-memory: important stuff gets saved without /remember
AUTO_MEM_SYS = "From this message, is there anything worth remembering long-term about the user (facts, preferences, plans, promises, people, goals)? Reply ONLY 'NO' or ONLY the fact in one short line."

def auto_mem(uid, text, reply):
    try:
        o = llm([{"role":"system","content":AUTO_MEM_SYS},{"role":"user","content":"user said: "+text+"\nbot replied: "+str(reply)[:200]}], timeout=30, max_tokens=60)
        o = (o or "").strip()
        if o and o.upper() != "NO" and len(o) < 200:
            remember(uid, o, "auto")
    except Exception: pass

# ---------- tools ----------
SLIM_RULES = (
"UNDERSTAND FIRST: respond to what the user actually meant, including frustration or sarcasm. If annoyed, acknowledge it and fix the thing.\n"
"SELF-KNOWLEDGE: questions about yourself (powerful? features? model?) answer directly: custom AI - reminders, todos, notes, streaks, image gen, voice, translate, PDF, quiz, flashcards, summarize, expenses, pomodoro, study plan, search, Quran, site builder. Invite them to try one. NEVER use search for this.\n"
"REM ABUSE GUARD: NEVER turn a question into a reminder. Only make a reminder when the user explicitly asks (remind me / mone koriye dio / 5 min por).\n"
"FABRICATED TOPIC GUARD: never mention films/movies first; only if the USER mentions one. Never invent past events or memories.\n"
"HONESTY: never invent facts. If unsure, say so briefly."
)
ACTION_HINTS = ("remind", "mone koria", "todo", "note", "search", "price", "koto taka", "dam", "weather", "quiz", "pdf", "translate", "image", "chobi", "banai", "banao", "likh", "write", "site", "quran", "wake", "ghum theke")

def _wants_action(text):
    low = (text or "").lower()
    return any(k in low for k in ACTION_HINTS)

def get_tool_sys(text):
    # plain chat: slim rules only. action messages: full tool manual
    if _wants_action(text):
        return TOOL_SYS
    return SLIM_RULES + "\n\n(No tools needed for this message. Just talk naturally.)"

TOOL_SYS = """Tools - start your reply with a tag when needed, and never mention tags in your reply:
WEB:<query> - live info (prices, weather, news)
CALC:<expression> - math
REM:<time>|<text> - set a reminder. time: 10m / 2h / 3d / HH:MM / "daily HH:MM". Only when the user explicitly asks for a reminder. Convert the user's time EXACTLY ("7ta 50" = 7:50). If unsure of the time, ask one short question first. After the tool result, confirm using ONLY the exact time and text from the result.
REM_LIST / REM_DEL:<number> - show / delete reminders
TODO_ADD:<task> - add a todo
NOTE_SAVE:<text> - save a note
JOURNAL_SAVE:<text> - save a journal entry
SITE:<description> - build a single-file website
Otherwise just reply normally."""

def bd_price(query):
    """BD shop price via gadgetandgear.com slug guess."""
    try:
        h = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
        words = re.findall(r"[a-z0-9]+", query.lower())
        STOP = {"er","price","koto","bd","tey","te","akhon","currently","in","the","a","of","r","ar","ta","tar","eta","e","now","best","top","dam","tk","taka","current"}
        core = [w for w in words if w not in STOP][:6]
        r = None
        for n in (len(core), 5, 4, 3):
            if n < 2: continue
            slug = "-".join(core[:n])
            rr = requests.get(f"https://www.gadgetandgear.com/product/{slug}", headers=h, timeout=12)
            if rr.status_code == 200 and re.findall(r"(?:Tk\.?\s*|BDT\s?|\u09f3\s?)[\d,]{5,12}", rr.text):
                r = rr; break
        if r is None:
            try:
                rj = requests.get("https://r.jina.ai/https://www.gadgetandgear.com/product/" + "-".join(core[:4]), timeout=25)
                if rj.status_code == 200:
                    txt = rj.text
                    prices = []
                    for p in re.findall(r"(?:Tk\.?\s*|BDT\s?|\u09f3\s?)[\d,]{5,12}", txt):
                        if p not in prices: prices.append(p)
                    if prices: return "BD SHOP (gadgetandgear.com): prices: " + ", ".join(prices[:4])
            except Exception:
                pass
        if r is None: return ""
        txt = html.unescape(re.sub(r"<[^>]+>", " ", r.text))
        txt = re.sub(r"\s+", " ", txt)
        t = re.search(r"<title>(.*?)</title>", r.text, re.S)
        title = html.unescape(t.group(1)).strip() if t else slug
        prices = []
        for p in re.findall(r"(?:Tk\.?\s*|BDT\s?|\u09f3\s?)[\d,]{5,12}", txt):
            if p not in prices: prices.append(p)
        if not prices: return ""
        return f"BD SHOP RESULT (gadgetandgear.com, official BD shop): {title} | prices found: " + ", ".join(prices[:4])
    except Exception:
        return ""

def web_search(query):
    bd = bd_price(query)
    if SERPER_KEY:
        try:
            r = requests.post("https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": 6}, timeout=15)
            j = r.json()
            out = []
            ab = j.get("answerBox") or {}
            if ab.get("answer") or ab.get("snippet"): out.append(str(ab.get("answer") or ab.get("snippet")))
            kg = j.get("knowledgeGraph") or {}
            if kg.get("description"): out.append(str(kg["description"]))
            for o in (j.get("organic") or [])[:5]:
                if o.get("snippet"): out.append("- " + (o.get("title") or "") + ": " + o["snippet"])
            if out: return "\n".join(out)[:4000]
        except Exception as e:
            print("serper err, ddg fallback:", e)
    results = _ddg_search(query)
    if bd: results = bd + "\n" + results
    return results

def _bing_search(query):
    out = ""
    try:
        h = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"}
        r = requests.get("https://www.bing.com/search", params={"q": query}, headers=h, timeout=15)
        for m in re.finditer(r'<li class="b_algo".{0,2500}?</li>', r.text, re.S):
            b = m.group(0)
            t = re.search(r'<h2[^>]*>.*?>(.*?)</a>', b, re.S)
            s = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
            if t:
                tt = re.sub(r"<[^>]+>", "", t.group(1)).strip()
                ss = re.sub(r"<[^>]+>", "", s.group(1)).strip() if s else ""
                out += "\n- " + tt + ": " + ss
    except Exception:
        pass
    return out.strip()

def _bing_news(query):
    out = ""
    try:
        r = requests.get("https://www.bing.com/news/search",
            params={"q": query, "format": "RSS"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)[:4]
        import html as _h
        for it in items:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            if t: out += "\n- NEWS: " + _h.unescape(t.group(1)).strip()
    except Exception:
        pass
    return out.strip()

def _news_rss(query):
    out = ""
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        r = requests.get("https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=h, timeout=15)
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)[:5]
        for it in items:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            if t:
                import html as _h
                line = "- NEWS: " + _h.unescape(t.group(1)).strip()
                if d: line += " (" + d.group(1)[:16] + ")"
                out += "\n" + line
    except Exception:
        pass
    return out.strip()

def _ddg_search(query):
    out = _bing_search(query)
    news = _news_rss(query)
    bnews = _bing_news(query)
    merged = (out + "\n" + news + "\n" + bnews).strip()
    return merged or "SEARCH_FAIL: no results found, search genuinely failed"
    return out or "SEARCH_FAIL: no results found, search genuinely failed"

def build_site(desc):
    try:
        # website quality matters: use the strongest free brain (sage), not the small code-flash model
        sb = BRAINS["sage"]
        # quality reference: two elite-grade example sites shipped with the bot
        ref_html = ""
        try:
            z = open("/tmp/zesta.html").read() if os.path.exists("/tmp/zesta.html") else ""
            f2 = open("/tmp/fruit.html").read() if os.path.exists("/tmp/fruit.html") else ""
            if not z:
                z = (open("ref/zesta.html").read() if os.path.exists("ref/zesta.html") else "")
                f2 = (open("ref/fruit.html").read() if os.path.exists("ref/fruit.html") else "")
            ref_html = ("\n\nQUALITY REFERENCE (match or beat this bar - these are examples of the minimum acceptable quality): "
                + z[:9000] + " ... [reference continues - copy its techniques: custom properties, glassmorphism nav, WebGL/canvas centerpiece, scroll-driven stages, working cart logic, marquee, toasts] "
                + f2[:4000] + " ...")
        except Exception as _re:
            print("ref load fail:", _re)
        sysp = ("You are an elite web designer AND front-end engineer. Reply with ONE complete self-contained HTML file. "
            "Design bar: agency-quality, striking hero section, large typography, real visual hierarchy, generous whitespace, smooth hover transitions, CSS custom properties, Google Fonts via link tag, tasteful gradients/shadows, fully responsive with mobile nav, subtle scroll animations, accessible contrast. "
            "FUNCTIONALITY RULES (critical): the page MUST be normally scrollable (no full-screen fixed canvas blocking touch, no overflow:hidden on body), ALL buttons/links/nav must actually work (smooth-scroll anchors, hover states), and a hero section alone is NOT enough - include at least 3 full sections (hero, features/menu, contact/footer) so it is a real website, not a single poster. "
            "For images, use real direct image URLs (e.g. https://images.unsplash.com/...) - never placeholder boxes or 'image here' divs. For 3D/animation use CSS transforms and canvas/JS where asked, but the canvas must never block scrolling or clicks. "
            "No markdown, no explanation, start with <!DOCTYPE html> and end with </html>." + ref_html)
        html = _llm_call(sb["base"], sb["key"], sb["model"], [
            {"role":"system","content":sysp},
            {"role":"user","content":desc}
        ], 300, 32000, tries=2)
        html = html.strip()
        # truncation repair: if file got cut off, ask the brain to complete it
        if "</html>" not in html.lower()[-300:]:
            try:
                more = _llm_call(sb["base"], sb["key"], sb["model"], [
                    {"role":"system","content":"Continue the HTML file exactly from where it stopped. Reply ONLY with the missing continuation, no repetition, end with </html>."},
                    {"role":"user","content":html[-3000:]}
                ], 300, 16000, tries=1)
                html = html + more
            except Exception as e:
                print("site continuation fail:", str(e)[:120])
        if html.startswith("```"):
            html = re.sub(r"^```[a-z]*\n?", "", html); html = re.sub(r"\n?```$", "", html)
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        fname = re.sub(r"[^a-z0-9]+", "_", (title.group(1).strip().lower() if title else "site"))[:40] or "site"
        path = "/tmp/" + fname + ".html"
        with open(path, "w") as f: f.write(html)
        return "FILE:" + path + "|" + fname + ".html"
    except Exception as e:
        return "site banate fail: " + str(e)

def extract_tool_tag(text):
    """Extract the first supported tool call, even when the model adds prose around it."""
    text = str(text or "").strip()
    tags = "WEB|CALC|SITE|REM_LIST|REM_DEL|REM|JOURNAL_SAVE|TODO_ADD|NOTE_SAVE"
    match = re.search(r"(?<![A-Z0-9_])(" + tags + r")(?:\s*:\s*([^\n]*))?", text, re.I)
    if not match:
        return None
    tag = match.group(1).upper()
    payload = (match.group(2) or "").strip()
    return tag, payload



# ---------- v9.51: deterministic reminder parsing (brain no longer decides time/text) ----------
_REM_INTENT = re.compile(r"reminder|remind|mone kori|mone koraite|mone rakh", re.I)
_REL_MIN = re.compile(r"(\d+)\s*(min|mins|minute|minutes|mint|mut|mit)s?\s*(por|pore|porer|pori)\b", re.I)
_REL_HOUR = re.compile(r"(\d+)\s*(ghonta|ghonta por|hour|hours|hr|hrs)\s*(por|pore)?\b", re.I)

def _user_rem_request(text):
    """Return (delta_minutes, label) parsed from the USER's own words, or (None, None)."""
    low = (text or "").lower()
    if not _REM_INTENT.search(low): return (None, None)
    mins = None
    m = _REL_MIN.search(low)
    if m:
        mins = float(m.group(1))
    else:
        h = _REL_HOUR.search(low)
        if not h: h = re.search(r"(\d+)\s*ghonta\s*por", low)
        if h: mins = float(h.group(1)) * 60
    if mins is None or mins <= 0: return (None, None)
    # label: the part after "je/jate/jeno" if present, else brain text fallback
    label = None
    ml = re.search(r"\b(?:je|jate|jeno|koraite)\b\s*(.+)$", (text or "").strip(), re.I)
    if ml: label = ml.group(1).strip()
    if label and _REL_MIN.search(label): label = _REL_MIN.sub("", label).strip(" .,-")
    return (mins, label)

def run_tool(tag, payload, uid=None, user_text=None):
    payload = (payload or "").strip()
    if tag == "WEB": return web_search(payload)
    if tag == "SITE": return build_site(payload.strip())
    if tag == "REM" and uid is not None:
        pp = payload.strip()
        # never let a "question about the bot" become a reminder (brain occasionally abuses the tag)
        if "?" in pp and any(k in pp.lower() for k in ("powerful", "ki korte paro", "ki ki paro", "kon model", "tumi ke", "koto boro")):
            return "ami custom AI — reminders, todos, image gen, voice, translate, PDF, quiz, study plan, search, Quran ar aro korte pari. ekta try kore dekho 😄"
        # v9.51 HARD FIX: when the user's own words carry a relative time ("5 minutes por"),
        # the time comes from THE USER, never from the brain - and the label is the user's own words too.
        mins, label = _user_rem_request(user_text or "")
        if mins:
            txt = label or (pp.split("|", 1)[1].strip() if "|" in pp else pp)
            return do_cmd(str(uid), "/remind " + (str(int(mins)) + "m" if mins < 1440 else str(mins/1440) + "d") + " | " + txt)
        return do_cmd(str(uid), "/remind " + pp)
    if tag == "JOURNAL_SAVE" and uid is not None:
        journal_add(str(uid), payload.strip(), "text"); return "📅 journal e save hoye geche"
    if tag == "REM_LIST" and uid is not None:
        return do_cmd(str(uid), "/remind list")
    if tag == "REM_DEL" and uid is not None:
        # Normalize bare model output to /remind del, never malformed /remind del del.
        if not payload or payload.lower() in {"del", "delete", "dlt"}:
            arg = ""
        else:
            arg = payload
            if arg.lower().startswith(("del ", "delete ", "dlt ")):
                arg = arg.split(None, 1)[1]
        return do_cmd(str(uid), "/remind del" + (" " + arg if arg else ""))
    if tag == "TODO_ADD" and uid is not None:
        return do_cmd(str(uid), "/todo add " + payload.strip())
    if tag == "NOTE_SAVE" and uid is not None:
        return do_cmd(str(uid), "/note " + payload.strip())
    if tag == "CALC":
        try:
            if len(payload) > 200 or not payload.strip(): return "invalid expression"
            if not set(payload) <= set("0123456789+-*/().% "): return "invalid expression"
            import ast
            tree = ast.parse(payload, mode="eval")
            allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
                       ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.UAdd, ast.USub)
            if any(not isinstance(node, allowed) for node in ast.walk(tree)): return "invalid expression"
            if any(isinstance(node, ast.Constant) and (not isinstance(node.value, (int, float)) or isinstance(node.value, bool)) for node in ast.walk(tree)):
                return "invalid expression"
            if any(isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) for node in ast.walk(tree)): return "invalid expression"
            return str(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {}))
        except Exception as e: return "calc fail: " + str(e)
    return "unknown tool"

# ---------- long content (ebook/pdf style) ----------
LONG_HINT = ("pdf", "ebook", "book", "guide", "chapter")
def write_long(uid, text):
    tg("sendMessage", chat_id=int(uid), text="kaj shuru korlam, boro kaj tai 1-2 min lage, opekkha koro")
    out_chat = STATE["chats"].get(str(uid), [])[-10:]
    sysp = persona_prompt()
    notes = mem_text(uid)
    if notes: sysp += "\n\nThings you remember about the user:\n" + notes
    o = llm([{"role":"system","content":sysp},{"role":"user","content":text+"\n\nFirst, give ONLY a short outline: title + numbered section list (8-14 sections, one line each)."}], timeout=120)
    secs = [l for l in o.split("\n") if l.strip() and l.strip()[0].isdigit()][:14]
    parts = [o]; prev = ""
    for sec in secs:
        p = llm([
            {"role":"system","content":sysp+" You are writing ONE section of a longer ebook. Write this chapter in depth (400-600 words), highly practical, actionable steps, real examples, clean professional tone. No intro fluff, jump straight into the insights."},
            *out_chat,
            {"role":"user","content":"Full outline:\n"+o+"\n\nPrevious section ended with: ..."+prev[-200:]+"\n\nNow write ONLY section: "+sec}
        ], timeout=180)
        if str(uid) in STOP:
            STOP.discard(str(uid)); raise Exception("STOPPED: tumi nijey cancel koreso, ja hoye gese segulo sesh porjonto ready chilo")
        parts.append(p); prev = p
    title = "Ebook"
    try:
        t = llm([{"role":"system","content":"Reply with ONLY the book title. Max 5-7 words. Catchy, professional. No quotes, no markdown, no intro."},{"role":"user","content":"Create a book title for this topic: "+text}], timeout=45, max_tokens=40)
        t = (t or "").strip().strip(chr(34)+chr(39)+chr(96)).replace("*","")
        if t and len(t) < 80 and not t.lower().startswith("create"): title = t
    except Exception: pass
    full = clean_md("\n\n".join(parts[1:]))
    sec_names = [re.sub(r"^\s*\d+[.)]\s*", "", s).strip(". ") for s in secs]
    data = make_pdf(title, full, sec_names)
    if data:
        path = "/tmp/" + re.sub(r"[^a-z0-9]+", "_", title.lower())[:40] + ".pdf"
        with open(path, "wb") as f: f.write(data)
        PENDING_FILES[uid] = (path, path.split("/")[-1], "📄 " + title + " — tomar PDF ready, download koro")
        return "✅ " + title + " ready! PDF file pathiyay dicchi 📄"
    return full

def think(uid, text):
    if str(uid) in STOP:
        STOP.discard(str(uid)); return "cancel kore disi, ja hoye gese bad"
    low = (text or "").lower()
    if any(w in low for w in LONG_HINT) and any(w in low for w in ("ban","creat","likh","write","make","banao","bana")):
        out = write_long(uid, text)
        remember_chat(uid, "user", text)
        remember_chat(uid, "assistant", "[long ebook generated]")
        save()
        return out
    # v9.52 RAW BRAIN: no persona, no rules layers, no memory injection - plain Qwen answers.
    sysp = "Tumi Fuad AI - Fuad's personal assistant. Reply in casual Banglish (roman letters only, no Bengali/Arabic script, no markdown). Be a normal smart friend, answer directly."
    n = now_local()
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    months = ["January","February","March","April","May","June","July","August","September","October","November","December"]
    stamp = days[n.weekday()] + ", " + n.strftime("%d ") + months[n.month-1] + " " + str(n.year) + ", " + n.strftime("%I:%M %p") + " (Asia/Dhaka)"
    msgs = [{"role": "system", "content": sysp + "\n\nCurrent date and time: " + stamp + ". If the user asks the time or date, read it from here. If the user asks about prices or current facts and you are not sure, say so honestly." + get_tool_sys(text)}]
    _is_rem_msg = bool(_REM_INTENT.search((text or "").lower()))
    _REM_PARROT = ("remind dibo", "reminder set", "por remind", "reminder dio", "por elta reminder")
    msgs += [m for m in STATE["chats"].get(str(uid), [])
             if not (m.get("role") == "assistant" and any(k in str(m.get("content", "")).lower() for k in FAB_MARKERS))]
    if _is_rem_msg:
        msgs = [m for m in msgs if not (m.get("role") == "assistant" and any(k in str(m.get("content", "")).lower() for k in _REM_PARROT))]
    msgs.append({"role": "user", "content": text})
    try:
        out = llm_retry(msgs)
    except Exception as _e:
        _log_err("think_llm", _e)
        raise
    for _ in range(2):
        extracted = extract_tool_tag(out)
        if extracted:
            tag, payload = extracted
            # HARD GUARD: no action requested -> tool tags are brain hallucinations, strip them
            if not _wants_action(text):
                _log_err("tool_guard", "stripped " + tag + " from non-action msg: " + str(text)[:80] + " | payload: " + str(payload)[:80])
                # answer the original message directly instead of running the hallucinated tool
                msgs.append({"role": "assistant", "content": out})
                out = llm(msgs + [{"role": "user", "content": "Tumi tool use korte gevabe cheyeseni. User er asol question er direct answer dao, tool/tag chara, 2-3 line e."}])
                break
            result = run_tool(tag, payload, uid, user_text=text)
            msgs.append({"role": "assistant", "content": out})
            if result.startswith("FILE:"):
                path, name = result[5:].split("|", 1)
                PENDING_FILES[uid] = (path, name, "🌐 " + name + " — tomar website ready, download koro")
                out = llm(msgs + [{"role":"user","content":"Tool result: the website "+name+" is ready and being sent as an HTML file. Confirm to the user in one friendly line."}])
            else:
                msgs.append({"role": "user", "content": "Tool result:\n"+result+"\n\nNow give the final friendly answer using this. If the results contain a price, state it with source. If the results do NOT contain the answer, say the search didn't find it - never claim the product is not released or not available."})
                out = llm(msgs)
        else:
            break
    remember_chat(uid, "user", text)
    remember_chat(uid, "assistant", out)
    save()
    usage_add(uid, len(str(msgs)), len(str(out)))
    threading.Thread(target=auto_mem, args=(uid, text, out), daemon=True).start()
    return out


# ---------- v6 feature pack ----------
USAGE_LIMIT = 100_000_000
PERSONAS = {
    "default": os.environ.get("PERSONA") or PERSONA,
    "l": "You are L Lawliet from Death Note. Analytical, dry, hyper-rational, slight dark humor. You think in probabilities and always give your honest deduction. You are Fuad AI, created by Muhtasim Fuad.",
    "serious": "You are a serious, focused mentor. Direct, no fluff, motivating but strict. You are Fuad AI, created by Muhtasim Fuad.",
    "funny": "You are a witty, playful friend. Jokes often, roasts lightly, still helpful. You are Fuad AI, created by Muhtasim Fuad.",
}
def persona_prompt():
    return PERSONAS.get(STATE.get("persona", "default"), PERSONAS["default"])

def usage_add(uid, chars_in, chars_out):
    today = now_local().strftime("%Y-%m-%d")
    u = STATE.setdefault("usage", {})
    d = u.setdefault(today, {"tokens": 0, "msgs": 0, "by_brain": {}})
    d["tokens"] += (chars_in + chars_out) // 4
    d["msgs"] += 1
    d.setdefault("per_user", {})
    d["per_user"][str(uid)] = d["per_user"].get(str(uid), 0) + ((chars_in + chars_out) // 4)
    brain = "groq" if "groq" in (STATE.get("last_brain_used") or (STATE["cfg"].get("base") or "")) else "primary"
    d["by_brain"][brain] = d["by_brain"].get(brain, 0) + ((chars_in + chars_out) // 4)
    usage_alert(uid, today, d)
    save()

# ---------- v9: public mode, per-user token credits, admin panel ----------
ADMIN_LOCKED_CMDS = {"/brain", "/setprovider", "/settings", "/persona", "/proactive", "/groupmode",
                     "/voice_mode", "/daily", "/undaily", "/reset", "/broadcast", "/admin", "/quota"}

def is_admin(uid):
    return OWNER_ID and str(uid) == str(OWNER_ID)

def get_user_rec(uid):
    users = STATE.setdefault("users", {})
    rec = users.setdefault(str(uid), {"joined": now_local().strftime("%Y-%m-%d"), "credits": None, "banned": False})
    return rec

def user_daily_tokens(uid):
    today = now_local().strftime("%Y-%m-%d")
    d = (STATE.get("usage", {}).get(today, {}).get("per_user", {}) or {}).get(str(uid), 0)
    return d

def user_limit(uid):
    rec = get_user_rec(uid)
    try:
        if rec.get("credits") is not None:
            return max(0, int(rec["credits"]))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return max(0, int(STATE.get("public_default_limit", 20000)))
    except (TypeError, ValueError, OverflowError):
        return 20000

def check_user_quota(uid):
    """returns None if ok, else an error message"""
    if is_admin(uid): return None
    rec = get_user_rec(uid)
    if rec.get("banned"): return "tomar access bondho kora hoyeche. owner er sathe kotha bolo."
    if not STATE.get("public_mode"): return None  # private mode: owner only, handled elsewhere
    used = user_daily_tokens(uid)
    lim = user_limit(uid)
    if used >= lim:
        return "ajker token limit sesh (" + str(used) + "/" + str(lim) + "). kal abar try koro othoba owner er kach theke credit nao."
    return None

# public mode guard for admin-locked commands
_admin_do_cmd = None

# ---------- v9.6: daily journal ----------
def journal_backup():
    if not GH_TOKEN: return
    try:
        import base64 as b64
        body = json.dumps(STATE.get("journal", {}))
        payload = {"message": "journal backup", "content": b64.b64encode(body.encode()).decode(), "branch": "data"}
        r = requests.get(f"https://api.github.com/repos/{GH_REPO}/contents/journal_backup.json",
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, params={"ref": "data"}, timeout=15)
        if r.status_code == 200: payload["sha"] = r.json().get("sha")
        requests.put(f"https://api.github.com/repos/{GH_REPO}/contents/journal_backup.json",
                     headers={"Authorization": f"Bearer {GH_TOKEN}"}, json=payload, timeout=30)
    except Exception as e: print("journal backup err", e)

def journal_add(uid, text, jtype="text", file_id=None, transcript=None):
    today = now_local().strftime("%Y-%m-%d")
    j = STATE.setdefault("journal", {}).setdefault(str(uid), {})
    j.setdefault(today, []).append({"time": now_local().strftime("%H:%M"), "type": jtype,
                                    "text": text, "transcript": transcript, "file_id": file_id})
    save(); journal_backup()

def tg_file_id(msg):
    return (msg.get("voice") or msg.get("document") or msg.get("photo") or [{}])[-1] if msg.get("photo") else (msg.get("voice") or msg.get("document") or {}).get("file_id")

def tg_download(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getFile", params={"file_id": file_id}, timeout=20)
        p = r.json()["result"]["file_path"]
        r2 = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/" + p, timeout=60)
        return r2.content
    except Exception as e:
        print("dl err", e); return None

def transcribe_voice(data):
    try:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": "Bearer " + (STATE["cfg"].get("key") if "groq" in (STATE["cfg"].get("base") or "") else (BACKUP_KEY or ""))},
            files={"file": ("v.ogg", data), "model": (None, "whisper-large-v3"), "response_format": (None, "json"), "language": (None, "bn"),
                   "temperature": (None, "0"),
                   "prompt": (None, "এটি একটি ক্যাজুয়াল বাংলা কথোপকথন। সাধারণ বাংলা শব্দ: কেমন আছো, কি খবর, কাজ, পড়া, রিমাইন্ডার, বলো, বুঝেছি।")}, timeout=90)
        txt = (r.json().get("text") or "").strip()
        if not txt:
            return ""
        # bangla words present and decent length = good transcript, skip the slow english pass
        bn_chars = sum(1 for c in txt if "\u0980" <= c <= "\u09FF")
        if bn_chars >= 6 and len(txt.split()) >= 2:
            return txt
        # garbled/empty bangla: one english attempt as backup
        try:
            r2 = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + (BACKUP_KEY or "")},
                files={"file": ("v.ogg", data), "model": (None, "whisper-large-v3"), "response_format": (None, "json"), "language": (None, "en"), "temperature": (None, "0")}, timeout=45)
            txt_en = (r2.json().get("text") or "").strip()
            if txt_en and txt_en != txt:
                return "BN: " + txt + " | EN: " + txt_en
        except Exception:
            pass
        return txt
    except Exception as e:
        print("stt err", e); return ""

SOLVE_SYS = ("You are a patient math and homework solver for a class 10 student in Bangladesh (commerce track with math and accounting). "
    "Read the problem (from the image or text). Reply in Banglish. Format: first one line 'problem:' restating it, then numbered steps "
    "with every calculation shown, then a final line starting with 'final answer:'. If part of the image is unreadable, say exactly which part. Never skip steps.")
VISION_SYS = ("You are an expert at understanding images deeply and completely. Look at the user's photo carefully and understand it like a smart friend would: "
    "what/who is in it, all visible text (read it out exactly), any objects, brands, screens, documents, numbers, diagrams, the setting, and the mood/context. "
    "Then answer whatever the user asked about it in detail. If the photo contains a homework, math, physics, accounting or any problem/question, solve it step by step "
    "(numbered steps, show calculations, end with 'final answer:'). If the user just sent the photo without a question, give a rich, genuinely useful description of everything important in it. "
    "Reply in Banglish, casual, like a friend texting. Never give a lazy one-line answer if there is more to say.")

def vision_ask(data, question, sys_prompt=None):
    # try several vision-capable models until one actually reads the image
    import base64 as b64
    base = STATE["cfg"].get("base") or OPENAI_BASE
    key = STATE["cfg"].get("key") or OPENAI_KEY
    cfg_model = STATE["cfg"].get("model") or MODEL
    cands = []
    if "intern" in base:
        cands += [cfg_model, "deepseek-v4-flash-vision"]
    else:
        cands += [cfg_model]
    # Groq credentials use BACKUP_KEY in this bot's normal brain configuration.
    groq_key = BACKUP_KEY or BRAINS.get("groq", {}).get("key", "")
    if groq_key:
        cands.append(("groq", "meta-llama/llama-4-scout-17b-16e-instruct"))
    msgs = ([{"role": "system", "content": sys_prompt}] if sys_prompt else [])
    msgs.append({"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64.b64encode(data).decode()}}]})
    for c in cands:
        try:
            if isinstance(c, tuple):
                b_, m_ = "https://api.groq.com/openai/v1", c[1]
                k_ = groq_key
            else:
                b_, m_, k_ = base, c, key
            r = requests.post(b_.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + k_, "Content-Type": "application/json"},
                json={"model": m_, "messages": msgs, "max_tokens": 1500}, timeout=120)
            j = r.json()
            out = (j.get("choices") or [{}])[0].get("message", {}).get("content")
            if out and out.strip():
                return out
            print("vision fail", m_, str(j)[:150])
        except Exception as e:
            print("vision err", c, str(e)[:150])
    return None


def image_gen(prompt):
    try:
        url = ("https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt)
               + "?model=flux&width=1024&height=1024&nologo=true&enhance=true")
        r = requests.get(url, timeout=180)
        if r.status_code == 200 and len(r.content) > 1000:
            path = "/tmp/img_" + str(int(time.time())) + ".jpg"
            with open(path, "wb") as f: f.write(r.content)
            return path
    except Exception as e: print("imggen err", e)
    return None

def tts_audio(text, lang="bn"):
    try:
        chunks = [text[i:i+180] for i in range(0, min(len(text), 900), 180)]
        audio = b""
        for c in chunks:
            r = requests.get("https://translate.google.com/translate_tts",
                params={"ie": "UTF-8", "q": c, "tl": lang, "client": "tw-ob"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.status_code == 200: audio += r.content
            time.sleep(0.4)
        if audio:
            path = "/tmp/tts_" + str(int(time.time())) + ".mp3"
            with open(path, "wb") as f: f.write(audio)
            return path
    except Exception as e: print("tts err", e)
    return None

def pdf_text(data):
    try:
        import io
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in rd.pages)[:60000]
    except Exception as e:
        print("pdf err", e); return ""

def namaz_times():
    try:
        r = requests.get("https://api.aladhan.com/v1/timingsByCity", params={"city": "Dhaka", "country": "Bangladesh", "method": "1"}, timeout=15)
        t = r.json()["data"]["timings"]
        return ", ".join(k + " " + v[:5] for k, v in t.items() if k in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"))
    except Exception as e: return "namaz time nite fail: " + str(e)

def weather_now():
    try:
        r = requests.get("https://wttr.in/Dhaka", params={"format": "3"}, timeout=15, headers={"User-Agent": "curl/8"})
        return r.text.strip()
    except Exception: return "weather nite fail"

def morning_brief(uid=""):
    todos = "\n".join("- " + t["text"] for t in STATE["todos"].get(uid, []) if not t.get("done"))[:500]
    return "🌅 Assalamu alaikum Fuad!\n📅 " + now_local().strftime("%A, %d %B") + "\n🕋 " + namaz_times() + "\n🌦 " + weather_now() + "\n\najker todo:\n" + (todos or "kisu nai, ajke ki korbi bolo")
# ---------- scheduling ----------
def parse_time_word(s):
    s = (s or "").strip().lower()
    try:
        if s.endswith("m"):
            amount = float(s[:-1])
            return time.time() + amount * 60 if amount > 0 and amount < float("inf") else None
        if s.endswith("h"):
            amount = float(s[:-1])
            return time.time() + amount * 3600 if amount > 0 and amount < float("inf") else None
        if s.endswith("d"):
            amount = float(s[:-1])
            return time.time() + amount * 86400 if amount > 0 and amount < float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None
    try:
        h, m = map(int, s.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59): return None
        n = now_local()
        t = n.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= n: t += timedelta(days=1)
        return t.timestamp()
    except (TypeError, ValueError):
        return None

def _once(key):
    """Claim a one-shot key across local workers; never fail open on lock errors."""
    import hashlib
    safe_key = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    path = os.path.join(os.environ.get("ONCE_LOCK_DIR", "/tmp"), "once_" + safe_key + ".lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception as e:
        print("once err", e)
        return False

def reminder_loop():
    while True:
        try:
            now = time.time()
            due = [r for r in STATE.get("rems", []) if r["at"] <= now and not r.get("_fired")]
            occurrences = []
            for r in due:
                fired_at = r["at"]
                occurrences.append((r, fired_at))
                r["_fired"] = True
                if r.get("repeat_days"):
                    r["at"] = fired_at + r["repeat_days"] * 86400
                    r["_fired"] = False
                else:
                    try: STATE["rems"].remove(r)
                    except ValueError: pass
            if due: save()
            for r, fired_at in occurrences:
                if not _once("rem-" + str(r.get("chat", r.get("uid"))) + "-" + str(int(fired_at))): continue
                try: send_long(r.get("chat", r.get("uid")), r["text"])
                except Exception as e: print("rem send err", e)
            daily = STATE.get("daily", {})
            nm = now_local()
            stamp = nm.strftime("%Y%m%d%H%M")
            for uid, d in list(daily.items()):
                for index, job in enumerate(d.get("jobs", [])):
                    if job.get("hour") == nm.hour and job.get("min") == nm.minute and job.get("last") != stamp:
                        job["last"] = stamp
                        save()
                        if _once("daily-" + str(uid) + "-" + str(index) + "-" + stamp):
                            send_long(int(uid), morning_brief(uid) if job.get("brief") else job.get("text", ""))
        except Exception as e: print("rem err", e)
        try: extras_tick()
        except Exception as e: print("extras err", e)
        try: v8_tick()
        except Exception as e: print("v8 tick err", e)
        try: human_tick()
        except Exception as e: print('human tick err', e)
        time.sleep(20)

# ---------- pdf ----------
def clean_md(s):
    s = re.sub(r"```[a-z]*", "", s)
    s = re.sub(r"\*\*?", "", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    return s


def make_pdf(title, text, sections=None):
    text = clean_md(text)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        import io
        buf = io.BytesIO()
        ACCENT = HexColor("#1a3c6e"); DARK = HexColor("#222222"); GREY = HexColor("#555555")
        s_title = ParagraphStyle("t", fontSize=26, leading=32, textColor=ACCENT, spaceAfter=6)
        s_sub = ParagraphStyle("sub", fontSize=12, leading=16, textColor=GREY)
        s_h1 = ParagraphStyle("h1", fontSize=17, leading=22, textColor=ACCENT, spaceBefore=16, spaceAfter=8)
        s_body = ParagraphStyle("b", fontSize=10.5, leading=16, textColor=DARK, alignment=4, spaceAfter=8)
        s_toc = ParagraphStyle("toc", fontSize=11, leading=20, textColor=DARK)
        def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        def page_footer(canv, doc_):
            canv.saveState()
            canv.setFont("Helvetica", 8); canv.setFillColor(GREY)
            canv.drawCentredString(A4[0]/2, 1.2*cm, str(canv.getPageNumber()))
            canv.drawCentredString(A4[0]/2, A4[1]-1.0*cm, esc(title)[:60])
            canv.restoreState()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.2*cm, rightMargin=2.2*cm, topMargin=2.5*cm, bottomMargin=2.5*cm)
        story = [Spacer(1, 6*cm), Paragraph(esc(title), s_title), Spacer(1, 10),
                 Paragraph("by Fuad AI", s_sub), PageBreak()]
        if sections:
            story.append(Paragraph("Table of Contents", s_h1))
            for i, sec in enumerate(sections, 1):
                story.append(Paragraph(str(i) + ".  " + esc(sec), s_toc))
            story.append(PageBreak())
        story.append(Paragraph("Introduction", s_h1))
        secs = sections or []
        # split body text by section headings (lines that match the section titles)
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        idx = 0
        for si, sec in enumerate(secs):
            story.append(Paragraph(esc(str(si+1)) + ". " + esc(sec), s_h1))
            # intro paragraph before first section
            if si == 0 and paras and not any(sec[:25].lower() in p.lower() for p in paras[:1]):
                story.append(Paragraph(esc(paras[0]), s_body))
            for p in paras[idx:]:
                if any(sec[:25].lower() in p.lower() for sec in secs[si+1:]):
                    break
                story.append(Paragraph(esc(p), s_body)); idx += 1
        for p in paras[idx:]:
            story.append(Paragraph(esc(p), s_body))
        doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
        return buf.getvalue()
    except Exception as e:
        print("pdf err", e)
        return None

# ---------- commands ----------
def set_brain(uid, bid):
    b = BRAINS.get(bid)
    if not b: return "brain chinina"
    STATE["cfg"]["base"] = b["base"]
    STATE["cfg"]["key"] = b["key"]
    STATE["cfg"]["model"] = b["model"]
    save()
    return "✅ brain switch hoye geche: " + b["label"]

MENU = [
    ("usage", "📊 Token usage"), ("brief", "🌅 Morning brief"), ("remind", "⏰ Reminder"),
    ("todo", "✅ Todo"), ("streak", "🔥 Streak"), ("note", "📝 Note"),
    ("img", "🎨 AI image"), ("tts", "🔊 Text to voice"), ("translate", "🌐 Translate"),
    ("quiz", "🧠 Quiz"), ("flash", "🃏 Flashcards"), ("summarize", "📄 Summarize"),
    ("links", "🔗 Saved links"), ("spend", "💸 Expenses"), ("pomo", "⏱ Pomodoro"),
    ("persona", "🎭 Bot mood"), ("brain", "🧠 AI brain"), ("memory", "💾 Memory"),
    ("notes", "📝 All notes"), ("daily", "☀️ Daily texts"), ("pdf", "📚 Ebook/PDF"),
    ("dream", "😴 Dream journal"), ("debate_ai", "🗣 Debate"), ("news", "📰 News"),
    ("remember_date", "🎂 Dates"), ("split", "🧾 Split bill"), ("readtime", "⏳ Read time"),
    ("syllabus", "📚 Study plan"), ("mirror", "🪞 Reflection"), ("wiki", "📖 Wikipedia"),
    ("quotes", "💬 Quotes"), ("quran", "🕌 Quran"), ("stats", "📈 Stats"),
    ("undo", "↩️ Undo"), ("quota", "📶 Brain quota"), ("ping", "📡 Ping"),
    ("help", "❓ All commands"), ("solve", "🧮 Solve math"), ("goal", "🎯 Goals"),
    ("proactive", "🤖 Proactive"), ("voice_mode", "🎙 Voice mode"), ("groupmode", "👥 Group mode"),
]
ROWS = [MENU[i:i+2] for i in range(0, len(MENU), 2)]
PAGE_SIZE = 16
MENU_PAGES = [ROWS[i:i+PAGE_SIZE] for i in range(0, len(ROWS), PAGE_SIZE)]
# ensure >=2 pages split nicely
if len(MENU_PAGES) == 1: MENU_PAGES = [ROWS[:8], ROWS[8:]]

def run_feature(uid, feat, arg):
    if feat == "pdf": return think(uid, "ekta professional ebook banao — " + (arg or "amar jonno ekta topic dao"))
    if feat == "remind": return do_cmd(uid, "/remind " + (arg or ""))
    if feat in ("todo", "streak", "note", "daily"): return do_cmd("/" + feat + (" " + arg if arg else "")) if False else do_cmd(uid, "/" + feat + (" " + arg if arg else ""))
    return do_cmd(uid, "/" + feat + (" " + arg if arg else ""))

def do_cmd(uid, text):
    uid = str(uid)
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    # Protect the brain-selection subcommands too, not only bare /brain.
    if (cmd in ADMIN_LOCKED_CMDS or cmd.startswith("/brain_")) and str(uid) != str(OWNER_ID):
        return "ei command ta sudhu owner use korte parbe"
    STATE.setdefault("cmdcount", {})[cmd] = STATE.get("cmdcount", {}).get(cmd, 0) + 1
    v7 = do_cmd2(uid, text, cmd, arg)
    if v7 is not None: return v7

    if cmd == "/setprovider":
        try:
            base, key, model = arg.split("|")
            STATE["cfg"].update({"base": base.strip(), "key": key.strip(), "model": model.strip()})
            save(); return "provider set: " + model.strip()
        except Exception: return "format: /setprovider base_url|api_key|model_name"
    if cmd == "/settings":
        c = STATE["cfg"]
        return "base: " + (c.get("base") or OPENAI_BASE) + "\nmodel: " + (c.get("model") or MODEL)
    if cmd == "/admin" and is_admin(uid):
        if not arg:
            pm = STATE.get("public_mode", False)
            users = STATE.get("users", {})
            return ("⚙️ admin panel\npublic mode: " + ("ON" if pm else "OFF") +
                    "\ndefault daily token limit: " + str(STATE.get("public_default_limit", 20000)) +
                    "\nmot user: " + str(len(users)) +
                    "\n\ncommands:\n/admin mode on|off — bot public koro\n/admin limit <tokens> — default limit\n"
                    "/admin credit <user_id> <tokens> — specific user er limit set\n/admin users — sob user ar usage\n"
                    "/admin ban <user_id> — user block\n/admin unban <user_id>\n/admin broadcast <text> — sobaike message")
        parts = arg.split(maxsplit=2)
        sub = parts[0].lower()
        if sub == "mode":
            on = parts[1].lower() == "on" if len(parts) > 1 else None
            if on is None: return "on naki off? /admin mode on"
            STATE["public_mode"] = on; save()
            return "public mode " + ("ON — ekhon onnora o use korte parbe" if on else "OFF — abar sudhu tumi")
        if sub == "limit":
            if len(parts) < 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
                return "number dao: /admin limit 20000"
            n = int(parts[1])
            STATE["public_default_limit"] = n; save()
            return "default daily limit: " + str(n) + " tokens"
        if sub == "credit":
            if len(parts) < 3 or not parts[2].lstrip("-").isdigit(): return "format: /admin credit <user_id> <tokens>  (-1 dile unlimited)"
            uid2, n = parts[1], int(parts[2])
            if n < -1: return "credit -1 ba 0 er beshi dao"
            get_user_rec(uid2)["credits"] = None if n == -1 else n
            save()
            return "user " + uid2 + " er limit: " + ("unlimited" if n == -1 else str(n) + " tokens/day")
        if sub == "users":
            users = STATE.get("users", {})
            if not users: return "ekhono kono user nai"
            today = now_local().strftime("%Y-%m-%d")
            per = (STATE.get("usage", {}).get(today, {}).get("per_user", {}) or {})
            lines = []
            for u2, r2 in users.items():
                tag = " (banned)" if r2.get("banned") else ""
                lim = user_limit(u2)
                lines.append(u2 + tag + ": " + str(per.get(u2, 0)) + "/" + ("unlimited" if r2.get("credits") == -1 else str(lim)) + " tokens aj")
            return "\n".join(lines[-40:])
        if sub == "ban":
            if len(parts) < 2: return "user id dao"
            get_user_rec(parts[1])["banned"] = True; save()
            return "user " + parts[1] + " banned"
        if sub == "unban":
            if len(parts) < 2: return "user id dao"
            get_user_rec(parts[1])["banned"] = False; save()
            return "user " + parts[1] + " unbanned"
        if sub == "broadcast":
            if len(parts) < 2: return "ki pathabo?"
            users = list(STATE.get("users", {}).keys())
            for u2 in users:
                if str(u2) != str(OWNER_ID): send_long(int(u2), "📢 " + arg.split(maxsplit=1)[1])
            return str(len(users) - 1) + " jon ke pathano holo"
        return "eta chinina, /admin dekho"
    if cmd == "/myquota" and not is_admin(uid):
        used = user_daily_tokens(uid)
        lim = user_limit(uid)
        return "📊 aj: " + str(used) + "/" + str(lim) + " tokens"
    if cmd in ADMIN_LOCKED_CMDS and not is_admin(uid) and str(uid) != str(OWNER_ID):
        return "ei command ta sudhu owner use korte parbe"
    if cmd == "/status":
        c = STATE["cfg"]
        base = (c.get("base") or OPENAI_BASE)
        model = c.get("model") or MODEL
        if "intern" in base: brain = "InternAI " + model
        elif "zylo" in base: brain = "Zylo " + model
        elif "groq" in base: brain = "Groq " + model
        else: brain = base + " / " + model
        last = STATE.get("last_brain")
        extra = ("\nlast reply used: " + last) if last else ""
        return "ekhon cholche: " + brain + extra
    if cmd == "/menu":
        pages = MENU_PAGES
        page = int(arg) if arg.isdigit() else 1
        page = max(1, min(page, len(pages)))
        buttons = [[{"text": t, "callback_data": "menu_" + c} for c, t in row] for row in pages[page-1]]
        nav = []
        if page > 1: nav.append({"text": "◀️", "callback_data": "menu_page_" + str(page-1)})
        nav.append({"text": str(page) + "/" + str(len(pages)), "callback_data": "menu_noop"})
        if page < len(pages): nav.append({"text": "▶️", "callback_data": "menu_page_" + str(page+1)})
        buttons.append(nav)
        tg("sendMessage", chat_id=int(uid), text="⚙️ Fuad AI menu — kon feature?", reply_markup={"inline_keyboard": buttons})
        return "MENU"
    if cmd.startswith("/menu_"):
        return run_feature(uid, cmd.replace("/menu_", ""), arg)
    if cmd == "/help":
        pass
    if cmd == "/brain":
        c = STATE["cfg"]
        cur = c.get("base") or OPENAI_BASE
        buttons = []
        for bid, b in BRAINS.items():
            mark = " ✅" if (b["base"] == cur and b["model"] == (c.get("model") or MODEL)) else ""
            buttons.append([{"text": b["label"] + mark, "callback_data": "brain_" + bid}])
        tg("sendMessage", chat_id=int(uid), text="🧠 kon brain use korbo?", reply_markup={"inline_keyboard": buttons})
        return "MENU"
    if cmd.startswith("/brain_"):
        bid = cmd.replace("/brain_", "").strip()
        return set_brain(uid, bid)
    if cmd == "/usage":
        today = now_local().strftime("%Y-%m-%d")
        d = STATE.get("usage", {}).get(today, {"tokens": 0, "msgs": 0, "by_brain": {}})
        left = USAGE_LIMIT - d["tokens"]
        return ("📊 ajke:\nmsg: " + str(d["msgs"]) + "\ntoken used: " + str(d["tokens"]) + "\nbaki (100M theke): " + str(left) +
                "\n\nbrain wise: " + (", ".join(k + ": " + str(v) for k, v in d["by_brain"].items()) or "nai"))
    if cmd == "/img":
        if not arg: return "ki picture banabo? /img future city at night"
        return "IMGCMD:" + arg
    if cmd == "/tts":
        if not arg: return "ki boltam? /tts hello fuad"
        return "TTSCMD:" + arg
    if cmd == "/translate":
        if not arg: return "/translate bn|hello world  (bn/ba/en)"
        if "|" not in arg or not arg.split("|", 1)[0].strip() or not arg.split("|", 1)[1].strip():
            return "format: /translate bn|hello world"
        return "TRCMD:" + arg
    if cmd == "/brief":
        hh = arg or "06:30"
        try:
            h, m = map(int, hh.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59): raise ValueError("invalid time")
        except Exception: return "format: /brief 06:30"
        STATE["daily"].setdefault(uid, {"jobs": []})
        if any(j.get("brief") for j in STATE["daily"][uid]["jobs"]):
            STATE["daily"][uid]["jobs"] = [j for j in STATE["daily"][uid]["jobs"] if not j.get("brief")]
            save(); return "morning brief off"
        STATE["daily"][uid]["jobs"].append({"hour": h, "min": m, "text": "BRIEF", "brief": True})
        save(); return "morning brief on: roj " + hh + " e namaz time + weather + todo pathabe"
    if cmd == "/spend":
        if not arg: 
            ms = STATE.get("spends", {}).get(uid, [])
            tot = sum(x["amt"] for x in ms if x["date"] == now_local().strftime("%Y-%m"))
            return "\n".join(x["date"] + ": " + str(x["amt"]) + " tk — " + x["what"] for x in ms[-15:]) + "\n\nai mashe total: " + str(tot) + " tk" if ms else "/spend 50 | chai"
        try:
            amt, what = arg.split("|", 1)
            _sp = {"date": now_local().strftime("%Y-%m-%d"), "amt": float(amt.strip()), "what": what.strip()}
            STATE.setdefault("spends", {}).setdefault(uid, []).append(_sp); push_action(uid, "spend", **_sp)
            save(); return "logged ✅"
        except Exception: return "format: /spend 50 | chai"
    if cmd == "/pomo":
        if arg.startswith("stop"):
            STATE["rems"] = [r for r in STATE["rems"] if "POMO" not in str(r.get("text", ""))]
            save(); return "pomodoro cancel"
        n = int(arg) if arg.isdigit() else 1
        t0 = time.time()
        for i in range(n):
            STATE["rems"].append({"uid": uid, "at": t0 + i*1800 + 1500, "text": "POMO: focus sesh " + str(i+1) + "/" + str(n) + "! 5 min break nio ☕"})
            STATE["rems"].append({"uid": uid, "at": t0 + i*1800 + 1800, "text": "POMO: break sesh, abar bosho 📚"})
        save(); return str(n) + " ta pomodoro set (25 min por + 5 min break). /pomo stop dile cancel"
    if cmd == "/quiz":
        if not arg: return "ki topic e? /quiz photosynthesis"
        return "QUIZCMD:" + arg
    if cmd == "/flash":
        if not arg: return "topic dile flashcard PDF banabo: /flash accounting chapter 2"
        return "FLASHCMD:" + arg
    if cmd == "/links":
        ls = STATE.get("links", [])
        if not ls: return "ekhono kono link save hoy nai. je kono message e link thakle auto save hobe"
        return "\n".join(l["t"] + " — " + l["url"] for l in ls[-15:])
    if cmd == "/summarize":
        if not arg: return "text pathao /summarize diye, othoba kono PDF file er reply te likho summarize"
        return "SUMCMD:" + arg
    if cmd == "/persona":
        cur = STATE.get("persona", "default")
        buttons = [[{"text": ("✅ " if k == cur else "") + k, "callback_data": "persona_" + k}] for k in PERSONAS]
        tg("sendMessage", chat_id=int(uid), text="bot er mood:", reply_markup={"inline_keyboard": buttons})
        return "MENU"
    if cmd == "/memory_full":
        return mem_text(uid)[-3500:] or "memory khali"
    if cmd == "/remember":
        if not arg: return "ki mone rakhbo? /remember gym roj 6 tay"
        tag = "note"
        if arg.lower().startswith("imp:"): tag, arg = "important", arg[4:].strip()
        remember(uid, arg, tag); return "mone rekhlam: " + arg
    if cmd == "/memory":
        lst = STATE["mem"].get(uid, [])
        if not lst: return "kiso nai"
        return "\n".join("[" + m["tag"] + "] " + m["text"] for m in lst[-30:])
    if cmd == "/forget":
        n0 = len(STATE["mem"].get(uid, []))
        STATE["mem"][uid] = [m for m in STATE["mem"].get(uid, []) if arg.lower() not in m["text"].lower()]
        save(); return str(n0 - len(STATE["mem"][uid])) + " ta memory muche dilam"
    _a = arg.lower().split(maxsplit=1)
    if cmd == "/remind" and (arg.lower() == "list" or (_a and _a[0] in ("del", "delete", "dlt"))):
        my = [(i, r) for i, r in enumerate(STATE.get("rems", [])) if str(r.get("chat")) == str(uid)]
        if not my: return "kono reminder nai"
        import datetime as _dt
        if arg.lower() == "list":
            out = []
            for n, (i, r) in enumerate(my):
                t = _dt.datetime.fromtimestamp(r["at"], TZ).strftime("%d %b %I:%M %p") if TZ else _dt.datetime.fromtimestamp(r["at"]).strftime("%d %b %I:%M %p")
                out.append(str(n+1) + ". " + r["text"].replace("reminder: ", "") + " — " + t + (" (daily)" if r.get("repeat_days") else ""))
            return "tomar reminder:\n" + "\n".join(out) + "\n\nmuchte: /remind del <number>"
        # del
        parts2 = arg.split()
        if len(parts2) < 2 or not parts2[1].isdigit():
            return "konta muchabo? /remind list dekhe number dao: /remind del 2"
        n = int(parts2[1]) - 1
        if n < 0 or n >= len(my): return "ei number e reminder nai, /remind list dekho"
        i, r = my[n]
        STATE["rems"].remove(r); save()
        return "reminder muche dilam: " + r["text"].replace("reminder: ", "")
    if cmd == "/remind":
        try:
            amt, txt = arg.split("|", 1)
            at = parse_time_word(amt)
            if not at: return "format: /remind 10m | text (m/h/d ba HH:MM)"
            rep = 0
            if "daily" in txt.lower(): txt, rep = txt.replace("daily","").strip(), 1
            STATE["rems"].append({"at": at, "chat": int(uid), "text": "reminder: " + txt, "repeat_days": rep}); push_action(uid, "rem", at=at, text="reminder: " + txt)
            save()
            import datetime as _dt2
            when = _dt2.datetime.fromtimestamp(at, TZ).strftime("%d %b %I:%M %p") if TZ else _dt2.datetime.fromtimestamp(at).strftime("%d %b %I:%M %p")
            return "reminder set: " + txt + " — " + when + (" (daily)" if rep else "") + ". Confirm to the user using ONLY this exact time and text."
        except Exception: return "format: /remind 10m | ki mone koraite jabi"
    if cmd == "/remind" and arg.lower() in ("list", "del", "delete", "dlt"):
        pass  # handled below
    if cmd == "/journal":
        if not arg or arg.lower() in ("show", "list"):
            j = STATE.get("journal", {}).get(str(uid), {})
            if not j: return "journal khali. bolle 'journal e likho: ...' ba voice e bolle save hoye jabe"
            days = sorted(j.keys())[-7:]
            out = []
            for d in days:
                out.append("📅 " + d)
                for e in j[d]:
                    line = "  " + e["time"] + " — " + (e.get("text") or "")
                    if e.get("type") == "voice": line += " (voice)"
                    if e.get("transcript"): line += "\n   transcript: " + e["transcript"][:200]
                    out.append(line)
            out.append("\nmas dekhte: /journal month")
            return "\n".join(out)
        if arg.lower() == "month":
            j = STATE.get("journal", {}).get(str(uid), {})
            m = now_local().strftime("%Y-%m")
            days = {d: v for d, v in j.items() if d.startswith(m)}
            if not days: return "ei mas e ekhono kichu nai"
            total = sum(len(v) for v in days.values())
            out = ["📊 " + m + " — " + str(total) + " ta entry, " + str(len(days)) + " din likhso"]
            for d in sorted(days): out.append(d + ": " + str(len(days[d])) + " entry")
            return "\n".join(out)
        journal_add(uid, arg, "text")
        return "📅 journal e save hoye geche"
    if cmd == "/todo":
        sub = arg.split(maxsplit=1)
        lst = STATE["todos"].setdefault(uid, [])
        if not arg or sub[0].lower() == "list":
            if not lst: return "todo khali"
            return "\n".join(("done" if t["done"] else "[]" ) + " " + str(i+1) + ". " + t["text"] for i, t in enumerate(lst))
        if sub[0].lower() == "add" and len(sub) > 1:
            lst.append({"text": sub[1], "done": False}); push_action(uid, "todo", text=sub[1]); save(); return "todo added: " + sub[1]
        if sub[0].lower() == "done" and len(sub) > 1:
            try:
                i = int(sub[1]) - 1; lst[i]["done"] = True; save(); return "done"
            except Exception: return "number de: /todo done 2"
        return "format: /todo add por 1 page math | /todo done 2 | /todo list"
    if cmd == "/note":
        if not arg: return "format: /note ja likhte chao"
        _nt = time.time(); STATE["notes"].append({"uid": uid, "t": _nt, "text": arg}); push_action(uid, "note", t=_nt); save(); return "note saved"
    if cmd == "/notes":
        ns = [n for n in STATE.get("notes", []) if n["uid"] == uid]
        return "\n\n".join(n["text"] for n in ns[-10:]) or "kiso nai"
    if cmd == "/daily":
        try:
            hhmm, txt = arg.split("|", 1)
            h, m = map(int, hhmm.strip().split(":"))
            STATE.setdefault("daily", {}).setdefault(uid, {"jobs": []})["jobs"].append({"hour": h, "min": m, "text": txt.strip()})
            save(); return "daily set: roj " + hhmm.strip() + " e — " + txt.strip()
        except Exception: return "format: /daily 07:00 | good morning fuad"
    if cmd == "/undaily":
        if uid in STATE.get("daily", {}): STATE["daily"][uid]["jobs"] = []; save()
        return "daily text gula off"
    if cmd == "/streak":
        today = now_local().strftime("%Y-%m-%d")
        s = STATE.setdefault("streaks", {}).setdefault(uid, {})
        if not arg:
            return "\n".join(k + ": " + str(v["count"]) + " din (last " + v["last"] + ")" for k, v in s.items()) or "kono streak nai. /streak gym"
        name = arg.strip()
        st = s.setdefault(name, {"count": 0, "last": ""})
        if st["last"] == today: return name + ": aj already done, streak " + str(st["count"])
        yest = (now_local() - timedelta(days=1)).strftime("%Y-%m-%d")
        st["count"] = st["count"] + 1 if st["last"] in ("", yest) else 1
        st["last"] = today; save()
        return name + ": " + str(st["count"]) + " din streak"
    if cmd == "/stop":
        STOP.add(uid); return "thik ache, choloman kaj ta muche jacche, result pathabo na"
    if cmd == "/coin": return random.choice(["Head", "Tail"])
    if cmd == "/roll": return str(random.randint(1, 6))
    if cmd == "/reset":
        STATE["chats"][uid] = []; save(); return "chat history clear"
    if cmd == "/help":
        return ("commands:\n"
                "/setprovider base|key|model — brain switch\n"
                "/settings — current brain\n"
                "/remember kotha — mone rakho (imp: dile important)\n"
                "/memory — dekho, /forget word — muche dao\n"
                "/remind 10m | text — reminder (m/h/d ba HH:MM, 'daily' dile roj)\n"
                "/todo add|done|list — task list\n"
                "site banaw: bolo ekta website banao + ki hobe — .html file pathabe\n"
                "\n/brain — AI switch, /persona — mood switch\n"
                "/usage — token limit, /brief — morning brief\n"
                "/img, /tts, /translate bn|text, /quiz, /flash\n"
                "/spend 50 | chai, /pomo, /links, /summarize\n"
                "photo/voice/pdf pathao — porte pare ✨"
                "/note text + /notes — quick notes\n"
                "/daily 07:00 | text — roj auto text\n"
                "/undaily — daily off\n"
                "/streak gym — habit streak\n"
                "/dream, /debate_ai, /news, /remember_date, /split, /readtime, /syllabus, /mirror, /wiki, /quotes, /quran, /undo, /quota, /stats\n"
                "/solve math — step by step solve (photo + caption /solve bhi)\n"
                "/goal set X|date, /goal list, /goal done n — goal tracking\n"
                "/proactive on|off — auto check-in\n"
                "/voice_mode on|off — voice reply (ElevenLabs key lagbe)\n"
                "/groupmode on|off — group e mention/reply e reply (owner)\n"
                "/coin, /roll — fun\n"
                "/reset — chat clear\n"
                "live info jigges korle nije search kore, math nijei kore, boro book/guide chaille ekta ekta kore baniye pathay")
    return None

@app.route("/")
def home(): return "Fuad AI v9.44"

# ---------- v7 feature pack ----------
from datetime import date as _date
from collections import deque
import traceback as _tb
PRIMARY_DAILY_EST = 2_000_000
QUEUE = {}
QLOCK = threading.Lock()
DEBATE_SYS = ("You are a sharp debate opponent. The user has a topic; you argue the OPPOSITE of the common-sense view the user is likely to hold. "
              "Banglish, casual but sharp, confident. Max 80 words per turn. Attack the weakest point in the user's last message, give one concrete example, "
              "end with one pointed challenge. Never concede just to be nice.")

def fmt_err(e):
    s = str(e).lower()
    if "stopped" in s: return "cancel kore disi. ja hoye gese segulo sesh porjonto ready chilo"
    if "429" in s or "rate" in s: return "ajker brain ektu bhire ache (rate limit) 😭 1 min por abar try koro, othoba /quota dekhe onno brain choose koro"
    if "timeout" in s or "timed out" in s: return "net ta slow chilo, reply ashte deri hocche. abar ekbar try koro"
    if "api key" in s: return "ei brain er key ekhon kaj korche na. /brain diye onno brain choose koro"
    return "ekhon kaj ta complete korte parlam na 😭 choto kore abar likho, na holei 1 min pore try koro"

def note_429(base, model):
    for bid, b in BRAINS.items():
        if b["model"] == model:
            rl = STATE.setdefault("ratelimit", {}).setdefault(bid, {"count": 0, "last": 0})
            rl["count"] += 1; rl["last"] = time.time()
            return

def llm_retry(msgs, **kw):
    try: return llm(msgs, **kw)
    except Exception as e:
        print("llm fail, retry in 5s:", str(e)[:120])
        time.sleep(5)
        return llm(msgs, timeout=90, **{k:v for k,v in kw.items() if k != "timeout"})

def push_action(uid, kind, **kw):
    stack = STATE.setdefault("last_action", [])
    stack.append(dict(uid=str(uid), kind=kind, **kw))
    del stack[:-5]

def usage_alert(uid, today, d):
    if d["by_brain"].get("primary", 0) >= 0.8 * PRIMARY_DAILY_EST and STATE.get("alert_day") != today:
        STATE["alert_day"] = today; save()
        tg("sendMessage", chat_id=int(OWNER_ID or uid),
           text="⚠️ aj primary brain e token 80% er upor gece (" + str(d["by_brain"].get("primary", 0)) + " / ~2M estimate). ektu kom use koro, na holei limit lagte pare")

# ---- dates / parsing ----
def parse_date_any(s, today):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try: return datetime.strptime(s, fmt).date(), True
        except ValueError: pass
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d", "%d/%m", "%d-%m"):
        try:
            x = datetime.strptime(s, fmt)
            d = _date(today.year, x.month, x.day)
            if d < today: d = _date(today.year + 1, x.month, x.day)
            return d, False
        except ValueError: pass
    return None, None

def next_occ(e, today):
    if e.get("yearly"):
        try: t = _date(today.year, e["m"], e["d"])
        except ValueError: t = _date(today.year, 3, 1)
        if t < today: t = _date(today.year + 1, e["m"], e["d"]) if e["m"] != 2 or e["d"] != 29 else _date(today.year + 1, 3, 1)
        return t
    return _date.fromisoformat(e["iso"])

def cmd_rdate(uid, arg):
    lst = STATE.setdefault("dates", [])
    if not arg:
        return "format: /remember_date name|date|what (jemon: Rahim|5 march|birthday). date: 5 march, 05/03, 2026-03-05. /remember_date list"
    if arg.lower() == "list":
        mine = [e for e in lst if e["uid"] == uid]
        if not mine: return "kono date save nai"
        return "\n".join(e["name"] + " — " + e["what"] + " — " + (("roj " + str(e["d"]) + "/" + str(e["m"])) if e.get("yearly") else e["iso"]) for e in mine)
    parts = [p.strip() for p in arg.split("|", 2)]
    if len(parts) < 3 or not all(parts): return "format: /remember_date name|date|what (jemon: Rahim|5 march|birthday)"
    name, ds, what = parts
    d, has_year = parse_date_any(ds, now_local().date())
    if not d: return "date ta bujhlam na. jemon: 5 march ba 05/03 ba 2026-03-05"
    e = {"uid": uid, "name": name, "what": what, "fired": []}
    if has_year: e["iso"] = d.isoformat()
    else: e.update(yearly=True, m=d.month, d=d.day)
    lst.append(e); save()
    nt = next_occ(e, now_local().date())
    return "✅ " + name + " — " + what + " mone rakhlam. next: " + nt.strftime("%d %b %Y") + ". 1 din age ar oi din e remind dibo" + (" (protibochor)" if e.get("yearly") else "")

def extras_tick():
    nm = now_local(); today = nm.date()
    # remember_date: fire on the day and 1 day before
    for e in STATE.get("dates", []):
        t = next_occ(e, today); dl = (t - today).days
        if dl in (0, 1):
            key = t.isoformat() + "|" + str(dl)
            if key not in e.setdefault("fired", []):
                e["fired"] = (e["fired"] + [key])[-10:]
                send_long(int(e["uid"]), ("🎉 aj " if dl == 0 else "⏰ kal ") + e["name"] + ": " + e["what"])
                save()
    STATE["dates"] = [e for e in STATE.get("dates", []) if e.get("yearly") or e.get("iso", "") >= today.isoformat()]
    # study plan nudge once a day after 7am
    iso = today.isoformat()
    for uid_, p in list(STATE.get("studyplan", {}).items()):
        if nm.hour >= 7 and p.get("nudged") != iso:
            p["nudged"] = iso
            todays = [d for d in p["days"] if d["d"] == iso and not d["done"]]
            if todays:
                send_long(int(uid_), "📚 aj er topic (" + p["exam"] + "): " + todays[0]["topic"] + "\n/syllabus done diye tick koro")
            save()
    # nightly memory summary at 03:00 for yesterday, only if active that day
    if nm.hour == 3 and STATE.get("sumday") != iso:
        STATE["sumday"] = iso; save()
        yday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        if STATE.get("usage", {}).get(yday, {}).get("msgs", 0) > 0:
            threading.Thread(target=nightly_summary, args=(yday, list(STATE.get("chats", {}).keys())), daemon=True).start()

def nightly_summary(day, uids):
    for uid in uids:
        try:
            hist = STATE["chats"].get(uid, [])[-40:]
            if not hist: continue
            convo = "\n".join(m["role"] + ": " + str(m["content"])[:300] for m in hist)
            out = llm([{"role": "system", "content": "Summarize these recent chats between the user and the AI into ONE compact entry, max 3 short lines, Banglish. Only facts, preferences, plans, decisions and promises. No intro."},
                       {"role": "user", "content": convo}], timeout=120, max_tokens=200)
            if out and out.strip(): remember(uid, "[" + day + "] " + out.strip()[:400], "daily-summary")
        except Exception as e: print("nightly summary err", e)

# ---- commands ----
def cmd_dream(uid, arg):
    lst = STATE.setdefault("dreams", {}).setdefault(uid, [])
    low = arg.lower()
    if low.startswith("save"):
        t = arg[4:].strip()
        if not t: return "format: /dream save ami ekta bagh dekhlam..."
        lst.append({"d": now_local().strftime("%Y-%m-%d"), "t": t}); del lst[:-200]; save()
        return "sopno save korlam ✅ (" + now_local().strftime("%d %b %Y") + ")"
    if low.startswith("list"):
        if not lst: return "ekhono kono sopno save nai. /dream save ... diye likho"
        return "\n\n".join(x["d"] + ": " + x["t"] for x in lst)
    if not arg:
        if not lst: return "age sopno save koro: /dream save ..."
        txt = "\n".join(x["d"] + ": " + x["t"] for x in lst[-10:])
        return llm([{"role": "system", "content": "You are a thoughtful dream analyst friend. Banglish, casual lowercase tone. Find recurring patterns and symbols across these dreams, give likely meanings (psychology-based, say dreams are not certain), and one practical takeaway. Under 150 words."},
                    {"role": "user", "content": txt}], max_tokens=600)
    return "format: /dream save text | /dream list | /dream (pattern analyze)"

def cmd_debate(uid, arg):
    dbt = STATE.setdefault("debate", {})
    if not arg: return "format: /debate_ai topic (jemon: social media teen der jonno bhalo na). end korte /debate_ai stop"
    if arg.lower() == "stop":
        if uid in dbt: dbt.pop(uid); save(); return "debate shesh ✅ kemon laglo"
        return "ekhon kono debate cholche na"
    topic = arg
    opening = llm([{"role": "system", "content": DEBATE_SYS + " Topic: " + topic + ". First line: state which side you are taking in one short line. Then your opening argument."},
                   {"role": "user", "content": "let's debate: " + topic}], max_tokens=300)
    dbt[uid] = {"topic": topic, "h": [{"role": "assistant", "content": opening}]}; save()
    return "🎤 debate: " + topic + "\n\n" + opening + "\n\n(jawab dao, /debate_ai stop diye shesh)"

def debate_turn(uid, text):
    d = STATE["debate"][uid]
    d["h"].append({"role": "user", "content": text})
    out = llm([{"role": "system", "content": DEBATE_SYS + " Topic: " + d["topic"]}] + d["h"][-12:], max_tokens=300)
    d["h"].append({"role": "assistant", "content": out}); d["h"] = d["h"][-12:]; save()
    return out

def cmd_news(arg):
    if not arg: return "format: /news topic (jemon: bangladesh cricket, ai)"
    raw = web_search(arg + " latest news today")
    out = llm([{"role": "system", "content": "From these search results write EXACTLY 5 latest news headlines about the topic, as bullet lines starting with '• '. Use only facts present in the results. Banglish or English. If the results are weak or old, say so in one line instead."},
               {"role": "user", "content": "topic: " + arg + "\n\nresults:\n" + raw[:4000]}], max_tokens=400)
    return "📰 " + arg + "\n" + out

def cmd_split(arg):
    p = [x.strip() for x in arg.split("|")]
    if len(p) < 2: return "format: /split 1200|4|snacks (amount|jon|what)"
    try:
        amt = float(p[0]); n = int(p[1])
    except ValueError: return "format: /split 1200|4|snacks"
    if n < 1 or amt <= 0: return "amount ar jon ongsho boro hote hobe"
    per = amt / n
    ps = str(round(per)) if abs(per - round(per)) < 0.005 else "%.2f" % per
    return "💸 " + (p[2] if len(p) > 2 and p[2] else "khoroch") + ": total " + ("%g" % amt) + " tk / " + str(n) + " jon\njonpi: " + ps + " tk"

def cmd_readtime(arg):
    if not arg: return "text dao: /readtime tomar text"
    w = len(arg.split())
    def f(m): return str(round(m * 60)) + " sec" if m < 1 else "%.1f min" % m
    return "📖 " + str(w) + " shobdo\nslow (200 wpm): " + f(w / 200) + "\nnormal (300 wpm): " + f(w / 300)

def cmd_syllabus(uid, arg):
    sp = STATE.setdefault("studyplan", {})
    today = now_local().date(); iso = today.isoformat()
    sub = (arg or "").strip()
    if not sub or sub.lower() == "list":
        p = sp.get(uid)
        if not p: return "kono plan nai. /syllabus exam|date|topic1, topic2 (date: 20/11 ba 2026-11-20)"
        days = p["days"]; done = sum(1 for d in days if d["done"])
        lines = ["📚 " + p["exam"] + " (" + p["date"] + "): " + str(done) + "/" + str(len(days)) + " done"]
        for d in days:
            if d["d"] == iso: lines.append("aj: " + d["topic"] + (" ✅" if d["done"] else ""))
        pend = [d for d in days if not d["done"]][:5]
        if pend: lines.append("next:\n" + "\n".join(d["d"] + " — " + d["topic"] for d in pend))
        return "\n".join(lines)
    if sub.lower() == "done":
        p = sp.get(uid)
        if not p: return "kono plan nai"
        cand = [d for d in p["days"] if not d["done"] and d["d"] <= iso] or [d for d in p["days"] if not d["done"]]
        if not cand: return "sob topic shesh! 🎉"
        cand[0]["done"] = True; save(); return "tick ✅ " + cand[0]["topic"]
    if sub.lower() == "stop":
        sp.pop(uid, None); save(); return "study plan delete korlam"
    parts = sub.split("|", 2)
    if len(parts) < 3: return "format: /syllabus exam|date|topic1, topic2, topic3"
    exam, dt, topics = [x.strip() for x in parts]
    d, _ = parse_date_any(dt, today)
    if not d or d <= today: return "exam er date ta bujhlam na ba pair gese. jemon: 20/11 ba 2026-11-20"
    tops = [x.strip() for x in re.split(r"[,;\n]", topics) if x.strip()]
    if not tops: return "topic dao: comma diye alada koro"
    total = (d - today).days
    n_study = max(1, total - 1) if total > 1 else 0
    chunks = [[] for _ in range(max(n_study, 1))]
    for i, t in enumerate(tops):
        chunks[i * max(n_study, 1) // len(tops)].append(t)
    days = []
    for i in range(total):
        day = (today + timedelta(days=i)).isoformat()
        if i == total - 1: topic = "final revision: sob topic ek bar kore dekho"
        else: topic = ", ".join(chunks[i]) if chunks[i] else "recap + practice"
        days.append({"d": day, "topic": topic, "done": False})
    sp[uid] = {"exam": exam, "date": d.isoformat(), "days": days, "nudged": ""}; save()
    preview = "\n".join(x["d"] + " — " + x["topic"] for x in days[:3])
    return ("✅ plan ready: " + exam + ", " + str(total) + " din, " + str(len(tops)) + " topic.\n" + preview +
            "\nroj sokale aj er topic nudge dibo. /syllabus = progress, /syllabus done = tick")

def cmd_mirror(uid):
    today = now_local().date()
    last7 = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    st = STATE.get("streaks", {}).get(uid, {})
    streak_s = ", ".join(k + ": " + str(v["count"]) + " din (last " + v["last"] + ")" for k, v in st.items()) or "streak nai"
    todos = STATE.get("todos", {}).get(uid, [])
    done = sum(1 for t in todos if t.get("done"))
    spends = STATE.get("spends", {}).get(uid, [])
    wk_sp = sum(x["amt"] for x in spends if x["date"] in last7)
    usage = STATE.get("usage", {})
    wk_msgs = sum(usage.get(x, {}).get("msgs", 0) for x in last7)
    wk_tok = sum(usage.get(x, {}).get("tokens", 0) for x in last7)
    report = ("streaks: " + streak_s + "\ntodo: " + str(done) + "/" + str(len(todos)) + " done\nkhoroch (7 din): " + ("%g" % wk_sp) + " tk\n"
              "AI chat (7 din): " + str(wk_msgs) + " msg, " + str(wk_tok) + " token (approx)")
    out = llm([{"role": "system", "content": persona_prompt() + "\n\nWeekly honest reflection. Look at ONLY this data and give an honest assessment: what is going well, what is slipping, one concrete thing to change next week. Banglish, casual lowercase, direct, no flattery, under 150 words. Do not invent data."},
               {"role": "user", "content": report}], max_tokens=500)
    return "🪞 weekly mirror\n" + report + "\n\n" + out

def cmd_wiki(uid, arg):
    wikis = STATE.setdefault("wikis", [])
    if not arg: return "format: /wiki topic (jemon: photosynthesis). saved list: /wiki list"
    if arg.lower() == "list":
        mine = [w for w in wikis if w.get("uid") == uid][-20:]
        return "\n".join(w["t"] + " — " + w["url"] for w in mine) or "kichu save nai"
    try:
        r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(arg.replace(" ", "_")),
                         headers={"User-Agent": "FuadAIBot/1.0"}, timeout=15)
        if r.status_code == 404: return "wikipedia te '" + arg + "' pawa gelo na. spelling ta ektu change kore abar try koro"
        r.raise_for_status(); j = r.json()
    except Exception as e:
        print("wiki err", e); return "wikipedia ekhon dhora jacche na, ektu pore try koro"
    title = j.get("title", arg); ext = (j.get("extract") or "")[:1500]
    url = ((j.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    wikis.append({"t": title, "url": url, "uid": uid, "d": now_local().strftime("%d %b")}); del wikis[:-50]; save()
    return "📖 " + title + "\n\n" + ext + "\n\n" + url + "\n(saved, /wiki list e dekho)"

def cmd_quotes(arg):
    if not arg: return "mood ba topic dao: /quotes tired, /quotes motivation"
    return llm([{"role": "system", "content": "Give exactly 5 short quotes that fit the mood or topic. Prefer real, well-known quotes; if you are not sure who said it, write 'unknown' as author. Do not invent quotes. Format each line: quote - author. No intro, no outro."},
                {"role": "user", "content": arg}], max_tokens=400)

def cmd_quran(arg):
    m = re.fullmatch(r"\s*(\d{1,3})\s*[:/ ]\s*(\d{1,3})\s*", arg or "")
    if not m: return "format: /quran 2:255 (surah:ayah)"
    s, a = int(m.group(1)), int(m.group(2))
    if not 1 <= s <= 114: return "surah 1 theke 114 er moddhe dao"
    try:
        j = requests.get("https://api.alquran.cloud/v1/ayah/%d:%d/editions/quran-uthmani,en.sahih,bn.bengali" % (s, a), timeout=20).json()
    except Exception as e:
        print("quran err", e); return "quran site ekhon dhora jacche na, ektu pore try koro"
    if j.get("code") != 200 or not isinstance(j.get("data"), list): return "ei ayah ta pawa gelo na, surah/ayah number check koro"
    by = {x["edition"]["identifier"]: x for x in j["data"]}
    ar = by.get("quran-uthmani", {}); en = by.get("en.sahih", {}); bn = by.get("bn.bengali", {})
    name = (ar.get("surah") or {}).get("englishName", "")
    return ("🕌 " + name + " " + str(s) + ":" + str(a) + "\n\n" + ar.get("text", "") + "\n\nEnglish: " + en.get("text", "") +
            "\n\nবাংলা: " + bn.get("text", ""))

def push_undo_todo(uid, text): push_action(uid, "todo", text=text)

def cmd_undo(uid):
    stack = STATE.setdefault("last_action", [])
    for i in range(len(stack) - 1, -1, -1):
        a = stack[i]
        if a.get("uid") != uid: continue
        stack.pop(i)
        k = a["kind"]
        if k == "todo":
            lst = STATE["todos"].get(uid, [])
            for j in range(len(lst) - 1, -1, -1):
                if lst[j]["text"] == a["text"]: del lst[j]; break
            save(); return "undo ✅ todo muche dilam: " + a["text"]
        if k == "note":
            STATE["notes"] = [n for n in STATE.get("notes", []) if not (n.get("uid") == uid and n.get("t") == a["t"])]
            save(); return "undo ✅ note muche dilam"
        if k == "rem":
            STATE["rems"] = [r for r in STATE.get("rems", []) if not (r.get("at") == a["at"] and r.get("text") == a["text"])]
            save(); return "undo ✅ reminder cancel korlam"
        if k == "spend":
            lst = STATE.get("spends", {}).get(uid, [])
            for j in range(len(lst) - 1, -1, -1):
                if lst[j]["date"] == a["date"] and lst[j]["amt"] == a["amt"] and lst[j]["what"] == a["what"]: del lst[j]; break
            save(); return "undo ✅ khoroch ta muche dilam"
    return "undo korar kichu nai"

def cmd_quota():
    lines = []
    for bid, b in BRAINS.items():
        rl = STATE.get("ratelimit", {}).get(bid) or {}
        keyok = "key ache" if b["key"] else "key nai"
        if not rl.get("last"): st = "ekhono 429 dekhai nai ✅"
        else:
            ago = (time.time() - rl["last"]) / 60
            st = "last 429 " + str(int(ago)) + " min aage (total " + str(rl.get("count", 0)) + " bar) " + ("⚠️ ekhono chap ache" if ago < 15 else "ekhon fine ✅")
        lines.append(b["label"] + ": " + keyok + ", " + st)
    return "🔋 brain quota (last-known, 429 dekhle track hoy):\n" + "\n".join(lines)

def cmd_stats(uid):
    usage = STATE.get("usage", {})
    days = len(usage)
    msgs = sum(d.get("msgs", 0) for d in usage.values())
    toks = sum(d.get("tokens", 0) for d in usage.values())
    cc = STATE.get("cmdcount", {})
    fav = max(cc, key=cc.get) if cc else None
    return ("📊 stats:\ntotal din used: " + str(days) + "\ntotal AI message: " + str(msgs) + "\ntotal token (approx): " + str(toks) +
            "\nfavorite feature: " + (fav or "ekhono kichu jomeni"))

def do_cmd2(uid, text, cmd, arg):
    if cmd == "/ping": return "pong 🏓"
    _v8 = v8_cmd(uid, cmd, arg)
    if _v8 is not None: return _v8
    if cmd == "/dream": return cmd_dream(uid, arg)
    if cmd == "/debate_ai": return cmd_debate(uid, arg)
    if cmd == "/news": return cmd_news(arg)
    if cmd == "/remember_date": return cmd_rdate(uid, arg)
    if cmd == "/split": return cmd_split(arg)
    if cmd == "/readtime": return cmd_readtime(arg)
    if cmd == "/syllabus": return cmd_syllabus(uid, arg)
    if cmd == "/mirror": return cmd_mirror(uid)
    if cmd == "/wiki": return cmd_wiki(uid, arg)
    if cmd == "/quotes": return cmd_quotes(arg)
    if cmd == "/undo": return cmd_undo(uid)
    if cmd == "/quota": return cmd_quota()
    if cmd == "/stats": return cmd_stats(uid)
    if cmd == "/quran": return cmd_quran(arg)
    return None

# ---- queue: messages wait in line per user ----
def _ack_later(uid, item):
    time.sleep(2)
    if not item["started"]:
        kw = {"chat_id": int(uid), "text": "line e asi ✅"}
        if item.get("mid"): kw["reply_to_message_id"] = item["mid"]
        tg("sendMessage", **kw)

def enqueue(uid, text, mid):
    item = {"text": text, "mid": mid, "started": False}
    with QLOCK:
        QUEUE.setdefault(uid, deque()).append(item)
        if uid in BUSY:
            threading.Thread(target=_ack_later, args=(uid, item), daemon=True).start()
            return
        BUSY.add(uid)
    threading.Thread(target=_worker, args=(uid,), daemon=True).start()

def _typing_loop(uid, stop_evt):
    # keep the "typing..." indicator alive while the bot works (telegram expires it every ~5s)
    while not stop_evt.is_set():
        try: tg("sendChatAction", chat_id=int(uid), action="typing")
        except Exception: pass
        stop_evt.wait(4)

def _worker(uid):
    while True:
        with QLOCK:
            q = QUEUE.get(uid)
            if not q:
                BUSY.discard(uid); return
            item = q.popleft(); item["started"] = True
        stop_evt = threading.Event()
        threading.Thread(target=_typing_loop, args=(uid, stop_evt), daemon=True).start()
        try: process_text(uid, item["text"], item["mid"])
        except Exception as e: _log_err("worker", e); print("worker err", e); _tb.print_exc()
        finally: stop_evt.set()

def send_stream(chat_id, text, reply_id=None):
    text = str(text)
    if len(text) <= 900: return send_long(chat_id, text, reply_id)
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) > 800 and cur.strip(): parts.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip(): parts.append(cur)
    for i, p in enumerate(parts):
        if i: tg("sendChatAction", chat_id=chat_id, action="typing"); time.sleep(1.2)
        send_long(chat_id, p.strip(), reply_id if i == 0 else None)

def translate_text(payload):
    """Build a translation request after validating the user-supplied format."""
    lang, text = payload.split("|", 1)
    lang, text = lang.strip(), text.strip()
    if not lang or not text:
        raise ValueError("missing language or text")
    target = "Bangla" if lang.lower() in ("bn", "ba") else "English" if lang.lower() == "en" else lang
    return "Translate to " + target + ", reply with ONLY the translation:\n\n" + text

def process_text(uid, text, mid):
    is_cmd = text.startswith("/")
    try:
        if is_cmd:
            out = do_cmd(str(uid), text) or "ei command ta chinina, /help dekho"
        else:
            tg("sendChatAction", chat_id=uid, action="typing")
            STATE.setdefault("cmdcount", {})["chat"] = STATE["cmdcount"].get("chat", 0) + 1
            if str(uid) in STATE.get("debate", {}): out = debate_turn(str(uid), text)
            else: out = think(uid, text)
        if not is_cmd:
            _pf = pdf_followup(uid, text)
            if _pf is not None: out = _pf
        if isinstance(out, str) and out.startswith("SOLVECMD:"):
            out = llm([{"role": "system", "content": SOLVE_SYS}, {"role": "user", "content": out[9:]}], max_tokens=2000)
        if isinstance(out, str) and out.startswith(("IMGCMD:", "TTSCMD:", "TRCMD:", "QUIZCMD:", "FLASHCMD:", "SUMCMD:")):
            tag, _, payload = out.partition(":")
            tg("sendChatAction", chat_id=uid, action="typing")
            if tag == "IMGCMD:":
                p = image_gen(payload)
                if p:
                    with open(p, "rb") as f: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data={"chat_id": uid}, files={"photo": f}, timeout=120)
                    out = None
                else: out = "picture banate parlam na, abar try koro"
            elif tag == "TTSCMD:":
                p = tts_audio(payload)
                if p:
                    with open(p, "rb") as f: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice", data={"chat_id": uid}, files={"voice": f}, timeout=60)
                    out = None
                else: out = "voice banate parlam na"
            elif tag == "TRCMD:":
                try:
                    prompt = translate_text(payload)
                    out = llm([{"role": "user", "content": prompt}])
                except Exception: out = "format: /translate bn|hello world"
            elif tag == "QUIZCMD:":
                out = llm([{"role": "user", "content": "Create a 5-question quiz on this topic for a class 10 student. Questions first, then an 'Answers' section at the end:\\n\\n" + payload}])
            elif tag == "FLASHCMD:":
                out = llm([{"role": "user", "content": "Create 15 Q&A flashcards on this topic. Format exactly:\\nQ: ...\\nA: ...\\n(blank line between cards). Topic: " + payload}])
                data = make_pdf("Flashcards: " + payload, out)
                if data:
                    fp = "/tmp/flash_" + str(int(time.time())) + ".pdf"
                    with open(fp, "wb") as f: f.write(data)
                    PENDING_FILES[uid] = (fp, fp.split("/")[-1], "🃏 flashcard PDF ready")
                    out = "flashcards ready, PDF pathiyay dicchi 📄"
            elif tag == "SUMCMD:":
                out = llm([{"role": "user", "content": "Summarize this in 5 short bullet points:\\n\\n" + payload}])
        if not is_cmd and str(uid) in STOP:
            STOP.discard(str(uid)); out = None
            tg("sendMessage", chat_id=uid, text="cancel kore disi, ja hoye gese segulo ar pathabo na")
        if out is not None and out != "MENU":
            send_stream(uid, out, mid)
            if STATE.get("voice_mode", {}).get(str(uid)) and ELEVEN_KEY and not is_cmd: send_voice_reply(uid, out)
        if uid in PENDING_FILES:
            path, name, cap = PENDING_FILES.pop(uid)
            send_file(uid, path, name, cap)
    except Exception as e:
        _log_err("process_text", e)
        print("proc err", e); _tb.print_exc()
        tg("sendMessage", chat_id=uid, text=fmt_err(e))

SEEN = set()
SEEN_LOCK = threading.Lock()
DAILY_SENT = set()
import traceback as _tb2
_ERR_LOG = "/tmp/bot_errors.log"
def _log_err(tag, e):
    try:
        with open(_ERR_LOG, "a") as f: f.write(time.strftime("%H:%M:%S ") + tag + ": " + str(e)[:300] + "\n" + _tb2.format_exc()[-800:] + "\n")
    except Exception: pass

def handle_update(upd):
    # never let one bad update kill the worker thread
    try: _handle_update_impl(upd)
    except Exception as e:
        _log_err("update_crash", e)
        print("update crash recovered:", e); _tb.print_exc()

def _handle_update_impl(upd):
    uid_upd = upd.get("update_id")
    if uid_upd is not None:
        with SEEN_LOCK:
            if uid_upd in SEEN: return
            SEEN.add(uid_upd)
            if len(SEEN) > 500: SEEN.clear(); SEEN.add(uid_upd)
    cb = upd.get("callback_query")
    if cb:
        data = cb.get("data") or ""
        cid = cb.get("id")
        frm = (cb.get("from") or {}).get("id")
        try: tg("answerCallbackQuery", callback_query_id=cid)
        except Exception: pass
        if data == "menu_noop": return
        if data.startswith("menu_page_"):
            pg = int(data.replace("menu_page_", ""))
            pages = MENU_PAGES
            buttons = [[{"text": t, "callback_data": "menu_" + c} for c, t in row] for row in pages[pg-1]]
            nav = []
            if pg > 1: nav.append({"text": "◀️", "callback_data": "menu_page_" + str(pg-1)})
            nav.append({"text": str(pg) + "/" + str(len(pages)), "callback_data": "menu_noop"})
            if pg < len(pages): nav.append({"text": "▶️", "callback_data": "menu_page_" + str(pg+1)})
            buttons.append(nav)
            try:
                mid = (cb.get("message") or {}).get("message_id")
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
                    json={"chat_id": int(frm), "message_id": mid, "text": "⚙️ Fuad AI menu — kon feature?",
                          "reply_markup": {"inline_keyboard": buttons}}, timeout=20)
            except Exception: pass
            return
        if data.startswith("menu_") and str(frm) == str(OWNER_ID):
            feat = data.replace("menu_", "")
            if feat in ("img", "tts", "quiz", "flash", "summarize", "pdf", "remind", "translate", "solve"):
                tg("sendMessage", chat_id=int(frm), text="likho: " + feat + " [ki chao] — menu theke chole asle prompt dorkar")
                return
            out = run_feature(str(frm), feat, "")
            if out and out != "MENU": tg("sendMessage", chat_id=int(frm), text=str(out)[:4000])
            return
        if data.startswith("persona_") and str(frm) == str(OWNER_ID):
            p = data.replace("persona_", "")
            if p in PERSONAS:
                STATE["persona"] = p; save()
                tg("sendMessage", chat_id=int(frm), text="✅ mood switch: " + p)
            return
        if data == "brain_menu" and str(frm) == str(OWNER_ID):
            try: out = do_cmd(str(frm), "/brain")
            except Exception: out = None
            if not out or out == "MENU": return
        if data.startswith("brain_") and str(frm) == str(OWNER_ID):
            out = set_brain(str(frm), data.replace("brain_", ""))
            tg("sendMessage", chat_id=int(frm), text=out, reply_markup={"inline_keyboard": [[{"text": "🔁 brain menu", "callback_data": "brain_menu"}]]})
        return
    msg = (upd.get("message") or {})
    uid = (msg.get("from") or {}).get("id")
    if not uid: return
    text = (msg.get("text") or "").strip()
    if (msg.get("chat") or {}).get("type") not in (None, "private"):
        group_handle(msg); return
    STATE.setdefault("last_seen", {})[str(uid)] = time.time()
    if OWNER_ID and uid != OWNER_ID:
        if not STATE.get("public_mode"):
            tg("sendMessage", chat_id=uid, text="eta private bot"); return
        q = check_user_quota(uid)
        if q:
            tg("sendMessage", chat_id=uid, text=q); return
        if text.startswith("/"):
            cmd0 = text.split()[0].split("@")[0].lower()
            if cmd0 in ADMIN_LOCKED_CMDS:
                tg("sendMessage", chat_id=uid, text="ei command ta sudhu owner use korte parbe"); return
    mid = msg.get("message_id")
    try:
        if msg.get("voice"):
            tg("sendChatAction", chat_id=uid, action="typing")
            data = tg_download(msg["voice"]["file_id"])
            t = transcribe_voice(data) if data else ""
            if t:
                t = t.strip()
                if len(t) < 2:
                    send_long(uid, "voice ta porte parlam na, ektuboro kore saaf kore bolo ba type kore dao")
                    return
                tl = t.lower()
                if tl.startswith("/journal ") or any(k in tl for k in ("journal", "diary", "dairy e", "din ta kemon", "ajke din", "din sere")):
                    jtxt = t.strip()[9:] if tl.startswith("/journal ") else t.strip()
                    journal_add(uid, jtxt, "voice", file_id=msg["voice"]["file_id"], transcript=jtxt)
                    send_long(uid, "📅 voice journal e save hoye geche (voice + transcript duitai)"); return
                enqueue(uid, "[voice message, auto-transcribed (BN = Bangla attempt, EN = English attempt); transcripts may be imperfect — pick whichever makes real sense as what the user most likely said, ignore the other, silently reconstruct the most likely casual sentence from it, then reply in Banglish. If very unsure what was said, briefly confirm before acting. transcript] " + t, mid)
            else: send_long(uid, "voice ta porte parlam na, abar patho")
            return
        if msg.get("photo"):
            tg("sendChatAction", chat_id=uid, action="typing")
            data = tg_download(msg["photo"][-1]["file_id"])
            cap = (msg.get("caption") or "ei picture e ki ase, detail e bolo")
            if cap.lower().startswith("/solve"):
                ans = vision_ask(data, cap[6:].strip() or "ei problem ta solve koro", SOLVE_SYS) if data else None
            else:
                ans = vision_ask(data, cap, VISION_SYS) if data else None
            send_long(uid, ans or "picture ta dekhte parlam na, abar patho")
            return
        doc = msg.get("document")
        if doc and (not text or text.lower().startswith("/summarize")):
            name = doc.get("file_name", "file")
            tg("sendChatAction", chat_id=uid, action="typing")
            data = tg_download(doc["file_id"])
            if data and name.lower().endswith(".pdf"):
                txt = pdf_text(data)
                if txt:
                    STATE.setdefault("last_pdf", {})[str(uid)] = txt[:60000]; save()
                    out = llm([{"role": "user", "content": "Summarize this document in detail but concise, key points as bullet list:\\n\\n" + txt[:40000]}])
                    send_long(uid, "📄 " + name + " er summary:\\n\\n" + out + "\\n\\nreply koro: quiz / notes / flashcards")
                else: send_long(uid, "PDF ta porte parlam na")
            elif data and name.lower().endswith(".txt"):
                out = llm([{"role": "user", "content": "Summarize this in 5 bullet points:\\n\\n" + data.decode(errors="ignore")[:40000]}])
                send_long(uid, out)
            return
        for m in re.findall(r"https?://\\S+", text or ""):
            STATE.setdefault("links", []).append({"t": now_local().strftime("%d %b"), "url": m[:200]})
            STATE["links"] = STATE["links"][-40:]; save()
    except Exception as e:
        print("media err", e)
    if not text: return
    if text.startswith("/"):
        process_text(uid, text, mid)       # commands run right away, not queued
    else:
        enqueue(uid, text, mid)            # chat messages wait in line per user

# ---------- v8 feature pack: proactive, goals, pdf power, voice, groups ----------
try:
    # late reminders are no longer dropped: reminder_loop fires them as soon as the service wakes
    _now = time.time()
    _late = [r for r in STATE.get("rems", []) if r.get("at", 0) < _now and not r.get("repeat_days")]
    if _late:
        print(len(_late), "late reminder(s) will fire on wake")
except Exception as e:
    print("stale rem cleanup err", e)

for _k, _v in (("route_stats", {"fast": 0, "sage": 0, "fast_fail": 0}), ("proactive", {}), ("last_pdf", {}), ("goals", {}),
               ("last_brain", ""), ("groupmem", {}), ("groupmode", False), ("voice_mode", {}), ("last_seen", {}), ("goal_checkin", {})):
    STATE.setdefault(_k, _v)

PDF_FOLLOW = ("quiz", "notes", "flashcards", "flashcard")
GROUP_BOT_UN = [None]

def pdf_followup(uid, text):
    low = (text or "").strip().lower()
    if low not in PDF_FOLLOW: return None
    src = STATE.get("last_pdf", {}).get(str(uid), "")
    if not src: return None
    src = src[:20000]
    if low == "quiz":
        return "📝 PDF theke quiz:\n\n" + llm([{"role": "user", "content": "From this PDF text make exactly 5 quiz questions for a class 10 student. Mix MCQ (a/b/c/d) and short answer. Put an 'Answers' section at the end. Use the same language as the text (Banglish or English).\n\n" + src}], max_tokens=1500)
    if low == "notes":
        return "📒 structured notes:\n\n" + llm([{"role": "user", "content": "Turn this PDF text into structured study notes: clear headings, bullet points, key terms with short meanings, and a 5-line summary at the end. Keep all important facts.\n\n" + src}], max_tokens=2500)
    out = llm([{"role": "user", "content": "Create 15 flashcards from this PDF text. Format exactly:\nQ: ...\nA: ...\n(blank line between cards).\n\n" + src}], max_tokens=2500)
    data = make_pdf("Flashcards", out)
    if data:
        fp = "/tmp/flash_pdf_" + str(int(time.time())) + ".pdf"
        with open(fp, "wb") as f: f.write(data)
        PENDING_FILES[uid] = (fp, "flashcards.pdf", "🃏 flashcard PDF ready")
        return "flashcards ready, PDF pathiyay dicchi 📄"
    return out

def send_voice_reply(chat_id, text):
    try:
        t = clean_md(str(text))[:1200].strip()
        if not t or not ELEVEN_KEY: return
        r = requests.post("https://api.elevenlabs.io/v1/text-to-speech/" + ELEVEN_VOICE,
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json={"text": t, "model_id": "eleven_multilingual_v2"}, timeout=90)
        if r.status_code != 200 or len(r.content) < 500:
            print("elevenlabs err", r.status_code); return
        path = "/tmp/voice_" + str(int(time.time())) + ".mp3"
        with open(path, "wb") as f: f.write(r.content)
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice", data={"chat_id": int(chat_id)}, files={"voice": f}, timeout=90)
    except Exception as e: print("voice err", e)

def cmd_goal(uid, arg):
    G = STATE.setdefault("goals", {}).setdefault(str(uid), [])
    a = (arg or "").strip(); low = a.lower()
    if not a or low == "list":
        if not G: return "goal nai. /goal set IELTS 7.5|2027-03-01 diye set koro"
        return "\n".join(str(i + 1) + ". " + ("✅ " if g.get("done") else "🎯 ") + g["goal"] + (" (target: " + g["target"] + ")" if g.get("target") else "") for i, g in enumerate(G))
    if low.startswith("set"):
        body = a[3:].strip()
        if not body: return "format: /goal set IELTS 7.5|2027-03-01"
        goal, _, target = body.partition("|")
        goal = goal.strip(); target = target.strip()
        if not goal: return "goal ta khali"
        G.append({"goal": goal, "target": target, "done": False, "t": now_local().strftime("%Y-%m-%d")}); save()
        return "goal set ✅: " + goal + (" (target: " + target + ")" if target else "") + "\nprati Monday raat 8 pm e progress jigges korbo"
    if low.startswith("done"):
        try:
            n = int(a[4:].strip())
            G[n - 1]["done"] = True; save()
            return "goal done ✅: " + G[n - 1]["goal"]
        except Exception: return "format: /goal done 1 (list e number dekho)"
    return "format: /goal set X|date, /goal list, /goal done n"

def v8_cmd(uid, cmd, arg):
    uid = str(uid)
    if cmd == "/proactive":
        P = STATE.setdefault("proactive", {})
        c = P.setdefault(uid, {"on": False})
        a = (arg or "").strip().lower()
        if a == "on":
            c["on"] = True; save()
            return "proactive on ✅ good morning (9am er age text na korle), streak warning (8pm), sunday mirror nudge, monday goal check-in. max 1 ta message per 4 ghonta"
        if a == "off":
            c["on"] = False; save(); return "proactive off"
        return "proactive ekhon: " + ("on" if c.get("on") else "off") + "\nformat: /proactive on | /proactive off"
    if cmd == "/voice_mode":
        a = (arg or "").strip().lower()
        V = STATE.setdefault("voice_mode", {})
        if a == "on":
            if not ELEVEN_KEY: return "elevenlabs key lagbe, free account koro"
            V[uid] = True; save(); return "voice mode on ✅ reply er sathe voice note o pabe"
        if a == "off":
            V[uid] = False; save(); return "voice mode off"
        return "format: /voice_mode on | /voice_mode off"
    if cmd == "/goal":
        return cmd_goal(uid, arg)
    if cmd == "/solve":
        if not arg: return "math/homework likho: /solve x^2 + 5x - 6 = 0 , othoba photo pathao caption e /solve diye"
        return "SOLVECMD:" + arg
    if cmd == "/groupmode":
        if uid != str(OWNER_ID): return "ei command shudhu owner er jonno"
        a = (arg or "").strip().lower()
        if a == "on":
            STATE["groupmode"] = True; save(); return "group mode on ✅ group e mention ba reply dile reply dibo"
        if a == "off":
            STATE["groupmode"] = False; save(); return "group mode off"
        return "group mode ekhon: " + ("on" if STATE.get("groupmode") else "off") + "\nformat: /groupmode on | /groupmode off"
    return None

def bot_username():
    if GROUP_BOT_UN[0] is not None: return GROUP_BOT_UN[0]
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getMe", timeout=15)
        GROUP_BOT_UN[0] = (r.json().get("result") or {}).get("username") or ""
    except Exception as e:
        print("getMe err", e); return ""
    return GROUP_BOT_UN[0]

def group_reply(cid, mid):
    try:
        G = STATE.get("groupmem", {}).get(str(cid), [])[-20:]
        convo = "\n".join(m["n"] + ": " + m["t"] for m in G)
        sysp = (persona_prompt() + " You are in a group chat with several friends. Reply to the latest message that addressed you (mention or reply to you). "
                "Short casual Banglish, one to three lines, use people's names, no lists. You only know this group chat; do not talk about any owner's private memory or personal life.")
        out = llm([{"role": "system", "content": sysp}, {"role": "user", "content": "group chat (latest messages):\n" + convo + "\n\nreply now as the bot."}], timeout=120, max_tokens=400)
        out = (out or "").strip() or "hmm, abar bolo"
        send_long(cid, out, mid)
        G2 = STATE.setdefault("groupmem", {}).setdefault(str(cid), [])
        G2.append({"n": "fuad ai", "t": out[:500]}); del G2[:-20]; save()
    except Exception as e:
        print("group reply err", e); send_long(cid, fmt_err(e), mid)

def group_handle(msg):
    chat = msg.get("chat") or {}
    cid = chat.get("id"); mid = msg.get("message_id")
    frm = msg.get("from") or {}
    name = frm.get("first_name") or frm.get("username") or "ekjon"
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text or cid is None: return
    if text.lower().startswith("/groupmode"):
        if str(frm.get("id")) != str(OWNER_ID): return
        parts = text.split(maxsplit=1)
        send_long(cid, v8_cmd(str(frm.get("id")), "/groupmode", parts[1] if len(parts) > 1 else ""), mid)
        return
    G = STATE.setdefault("groupmem", {}).setdefault(str(cid), [])
    G.append({"n": name, "t": text[:500]}); del G[:-20]
    save()
    if not STATE.get("groupmode"): return
    un = bot_username()
    mentioned = bool(un) and ("@" + un.lower()) in text.lower()
    rep = ((msg.get("reply_to_message") or {}).get("from") or {}).get("username", "") or ""
    replied = bool(un) and rep.lower() == un.lower()
    if mentioned or replied:
        threading.Thread(target=group_reply, args=(cid, mid), daemon=True).start()

def v8_tick():
    nm = now_local(); today = nm.strftime("%Y-%m-%d"); now = time.time()
    P = STATE.setdefault("proactive", {})
    if now - float(P.get("_last_sent", 0) or 0) < 14400: return
    cands = []
    nine = nm.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    for uid, c in list(P.items()):
        if not isinstance(c, dict) or not c.get("on"): continue
        if 9 <= nm.hour < 12 and c.get("greet") != today and STATE.get("last_seen", {}).get(uid, 0) < nine:
            cands.append((uid, "good morning 🌞 ajke ki ki korbi? todo ta dekhe ekta task start koro", "greet", today))
        if nm.hour >= 20 and c.get("streak") != today:
            yest = (nm - timedelta(days=1)).strftime("%Y-%m-%d")
            risk = [k for k, v in STATE.get("streaks", {}).get(uid, {}).items() if v.get("last") == yest and v.get("count", 0) > 0]
            if risk: cands.append((uid, "🔥 streak break hobe aj raate: " + ", ".join(risk) + ". ekbar kore fel, /streak <name> diye likhe rakho", "streak", today))
            else: c["streak"] = today
        if nm.weekday() == 6 and nm.hour >= 20 and c.get("mirror") != today:
            cands.append((uid, "🪞 sunday mirror time: ei hafta kemon gelo? /mirror diye ekta reflection likho", "mirror", today))
    if nm.weekday() == 0 and nm.hour >= 20:
        GC = STATE.setdefault("goal_checkin", {})
        for uid, gs in list(STATE.get("goals", {}).items()):
            act = [g["goal"] for g in gs if not g.get("done")]
            if act and GC.get(uid) != today:
                cands.append((uid, "🎯 weekly check-in: " + "; ".join(act) + ". ei hafta er moddhe ki korli? ekta line e bolo", "goal", today))
    if not cands: return
    uid, text, key, val = cands[0]
    send_long(int(uid), text)
    if key == "goal": STATE["goal_checkin"][uid] = val
    else: P[uid][key] = val
    P["_last_sent"] = now
    save()

def human_tick():
    # folk-style human check-in: once a day the brain reviews what it remembers about the user
    # and sends a personal message, sometimes with a self-made meme picture
    nm = now_local(); today = nm.strftime("%Y-%m-%d"); now = time.time()
    if not (14 <= nm.hour < 23): return
    H = STATE.setdefault("human_tick", {})
    OWNER = str(OWNER_ID)
    if not OWNER: return
    if int(H.get("count", 0) or 0) >= 5: H["sent"] = today; save(); return
    if now - float(H.get("last", 0) or 0) < 9000: return
    if not _once("htick-" + today): return
    rems = [r["text"].replace("reminder: ", "") for r in STATE.get("rems", []) if str(r.get("chat")) == OWNER]
    rems_txt = "\n".join("- " + t for t in rems) or "(kono reminder nai)"
    chats = STATE["chats"].get(OWNER, [])[-14:]
    convo = "\n".join((c.get("role", "?") + ": " + str(c.get("content", ""))[:150]) for c in chats)
    notes = mem_text(OWNER)
    prompt = ("You are Fuad AI, texting your owner " + ("Fuad" ) + " like a close friend (casual Banglish, roman letters only, plain text). "
        "Once a day you proactively check in, folk-style. Here is what you remember about him:\n" + (notes or "(kichu mone nai)") +
        "\n\nRecent chat:\n" + (convo or "(kotha hoi nai ajke)") +
        "\n\nWrite ONE short check-in message (2-3 lines max) that references ONE specific real thing you remember (exam, study, gym, Taqwa, his bot, a plan, anything real). "
        "Like a friend who remembered. No generic 'kemon acho' openers. End with a light question.\n"
        "HARD FACTS RULE: His ACTIVE reminders right now:\n" + rems_txt + "\n"
        "Only mention reminders that are in this exact list. NEVER say you set, deleted or changed a reminder unless it is visible in this list with that exact time. Never invent plans, streaks or facts.\n"
        "\n\nPICTURE RULE: attach a picture ONLY if the message becomes funnier or warmer with one AND the picture can be drawn as a scene that clearly matches what you are talking about "
        "(e.g. the actual thing from his life). The picture's subject must be THE SAME THING the message is about - same topic, same moment. If unsure, write NOMEME - a random or loosely-related picture is worse than no picture. "
        "If yes, on a NEW last line write exactly: MEME: <a detailed image-generation prompt describing the SCENE: subject, action, setting, style. Describe what is VISUALLY in the frame so the picture clearly connects to the message. Never ask for text, words, letters or captions inside the image - generated text comes out garbled. No real brand logos.>")
    has_material = bool(notes.strip() or convo.strip())
    fallback = "ki obosta? ajke kotha holo na e, bolo ki koros"
    if not has_material:
        send_long(int(OWNER), fallback)
        H["count"] = int(H.get("count", 0) or 0) + 1; H["last"] = now
        save(); return
    try:
        out = llm([{"role": "system", "content": prompt}], timeout=60)
    except Exception as e:
        print("human_tick llm err", e); out = ""
    if not out: out = fallback
    import re as _re
    real_words = set(w.lower() for w in _re.findall(r"[a-z]{4,}", (notes + " " + convo)))
    body_words = set(w.lower() for w in _re.findall(r"[a-z]{4,}", body))
    stop = {"kotha","bolo","valo","bhalo","koros","korcho","acho","ache","keno","ajke","kalke","bolechilam","mone","share","korish","recommend","next","weekend","start","korba","jonno","ekta","filmokhon","film","series","drama","binge"}
    overlap = body_words.intersection(real_words).difference(stop)
    if not overlap:
        body = fallback
        meme = ""
    if not body: return
    meme = ""
    body = out.strip()
    for line in body.split("\n"):
        if line.upper().startswith("MEME:"):
            meme = line[5:].strip(); body = body.replace(line, "").strip()
        elif line.strip().upper() == "NOMEME":
            body = body.replace(line, "").strip()
    if not body: return
    H["count"] = int(H.get("count", 0) or 0) + 1; H["last"] = now
    if False and meme:  # check-in images disabled: quality faltu + context mismatch - text only now
        try:
            scene = llm([{"role": "system", "content": "Turn this into ONE detailed image-generation prompt (max 60 words). Describe only what is visually in the frame: subject, action, setting, art style. The picture MUST literally show the main subject of the message below - if the message is about namaz/prayer, the frame must show a prayer-related scene (prayer mat, mosque, person praying); if about exam, exam scene; if about gym, gym scene. If the idea cannot honestly depict that subject, reply exactly SKIP. Context: " + body[:250] + " | idea: " + meme + ". Never request text, letters, words or captions inside the image. Reply with ONLY the prompt (or SKIP)."}], timeout=60) or ""
            if not scene.strip() or "SKIP" in scene.upper():
                pth = None
            else:
                pth = image_gen(scene.strip())
            if pth:
                with open(pth, "rb") as f:
                    requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendPhoto",
                        data={"chat_id": int(OWNER), "caption": body}, files={"photo": f}, timeout=120)
                save(); return
        except Exception as e:
            print("human_tick meme err", e)
    send_long(int(OWNER), body)
    save()

# Telegram command menu (the menu button in the chat) - registered once at startup
def register_menu():
    CMDS = [
        {"command": "menu", "description": "Menu - sob feature ek jaygay"},
        {"command": "remind", "description": "Reminder set (e.g. /remind 10m | text)"},
        {"command": "daily", "description": "Roj er auto text (e.g. /daily 07:00 | good morning)"},
        {"command": "todo", "description": "To-do list"},
        {"command": "streak", "description": "Habit streak"},
        {"command": "solve", "description": "Math/homework solve (photo pathate paro)"},
        {"command": "img", "description": "AI image banao"},
        {"command": "tts", "description": "Text theke voice"},
        {"command": "translate", "description": "Translate"},
        {"command": "quiz", "description": "Quiz banao"},
        {"command": "notes", "description": "Notes dekho"},
        {"command": "quran", "description": "Quran ayat"},
        {"command": "news", "description": "News"},
        {"command": "stats", "description": "Usage stats"},
        {"command": "brain", "description": "Brain/model switch"},
        {"command": "persona", "description": "Persona switch"},
        {"command": "proactive", "description": "Auto check-in on/off"},
        {"command": "settings", "description": "Settings"},
    ]
    try:
        tg("setMyCommands", commands=CMDS)
        print("command menu registered")
    except Exception as e:
        print("menu register err", e)

@app.route("/ping")
def ping(): return "pong"

@app.route("/sendtest")
def sendtest():
    # tests the bot's own tg() send path end to end
    try:
        tg("sendMessage", chat_id=int(OWNER_ID), text="tg-path test: ei message ta bot er nijer tg() function theke")
        return "tg() called ok, check the chat"
    except Exception as e:
        return "tg() FAIL: " + str(e)[:200]

@app.route("/selftest")
def selftest():
    # synchronous end-to-end: takes Fuad's message through the REAL brain and returns what happens
    import time as _t, traceback as _tb3
    t0 = _t.time()
    try:
        out = think(str(OWNER_ID), "selftest: ek line e bolo kemon acho")
        return "OK (" + str(round(_t.time()-t0,1)) + "s): " + str(out)[:300]
    except Exception as e:
        return "FAIL (" + str(round(_t.time()-t0,1)) + "s): " + str(e)[:300] + "\n" + _tb3.format_exc()[-600:]

VERSION = "v9.52-raw-qwen"

@app.route("/version")
def ver_dbg():
    return VERSION

@app.route("/state")
def state_dbg():
    ch = STATE.get("chats", {}).get(str(OWNER_ID), [])
    return "chats=" + str(len(ch)) + " last_brain=" + str(STATE.get("last_brain")) + " cfg_model=" + str(STATE["cfg"].get("model")) + " busy=" + str(len(BUSY))

@app.route("/braincheck")
def braincheck():
    import time as _t
    cb = (STATE["cfg"].get("base") or BRAINS["sage"]["base"], STATE["cfg"].get("key") or BRAINS["sage"]["key"], STATE["cfg"].get("model") or BRAINS["sage"]["model"])
    results = []
    for name, (b, k, m) in [("primary", cb), ("zylo", (BRAINS["zylo"]["base"], BRAINS["zylo"]["key"], BRAINS["zylo"]["model"])), ("groq", (BRAINS["groq"]["base"], BRAINS["groq"]["key"], BRAINS["groq"]["model"]))]:
        if not k: results.append(name + ": NO KEY"); continue
        t0 = _t.time()
        try:
            r = requests.post(b.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                json={"model": m, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}, timeout=30)
            results.append(name + ": HTTP " + str(r.status_code) + " " + r.text[:120] + " (" + str(round(_t.time()-t0,1)) + "s)")
        except Exception as e:
            results.append(name + ": FAIL " + str(e)[:120] + " (" + str(round(_t.time()-t0,1)) + "s)")
    return "\n".join(results)

@app.route("/debug")
def debug():
    try:
        out = "busy=" + str(len(BUSY)) + " q=" + str({k: len(v) for k, v in QUEUE.items()}) + "\n"
        try: out += open("/tmp/bot_errors.log").read()[-2000:]
        except Exception: out += "no errors logged"
        return out
    except Exception as e: return "dbg err " + str(e)

@app.route("/webhook", methods=["POST"])
def hook():
    global _rl
    if not _rl.is_alive():   # watchdog: restart reminder loop if it ever died
        _rl = threading.Thread(target=reminder_loop, daemon=True); _rl.start()
    upd = request.get_json(force=True, silent=True) or {}
    if not isinstance(upd, dict): return "bad update", 400
    # Claim an update before acknowledging so concurrent webhook retries are deduped.
    uid_upd = upd.get("update_id")
    if uid_upd is not None:
        with SEEN_LOCK:
            if uid_upd in SEEN: return "ok"
            SEEN.add(uid_upd)
            if len(SEEN) > 500: SEEN.clear(); SEEN.add(uid_upd)
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "ok"

# ---------- polling mode: webhook delivery was silently dropping real user messages ----------
def polling_loop():
    lockf = open("/tmp/bot_poller.lock", "w")
    try:
        import fcntl
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        print("poller: another worker already polling, standing down")
        return
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])},
                             timeout=70)
            j = r.json()
            if not j.get("ok"):
                ec = (j.get("error_code") or 0)
                if ec == 409:
                    # another poller won this round; back off generously, don't spam the log
                    time.sleep(15); continue
                _log_err("polling", "getUpdates not ok: " + str(j)[:200]); time.sleep(3); continue
            for upd in j.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
        except Exception as e:
            _log_err("polling", e); time.sleep(3)

def start_polling():
    try:
        requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook", params={"drop_pending_updates": "false"}, timeout=20)
        print("webhook deleted, switching to polling")
    except Exception as e:
        print("deleteWebhook err", e)
    threading.Thread(target=polling_loop, daemon=True).start()

register_menu()
_migrate_brain()
_purge_fabricated()
_rl = threading.Thread(target=reminder_loop, daemon=True)
_rl.start()
if TG_TOKEN:
    start_polling()
