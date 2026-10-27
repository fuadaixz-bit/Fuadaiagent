import os, json, time, threading, requests, random, re
from flask import Flask, request
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

TG_TOKEN = os.environ.get("TG_TOKEN", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE") or "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENAI_KEY = os.environ.get("OPENAI_KEY") or ""
MODEL = os.environ.get("MODEL") or "gemini-2.5-flash"
BACKUP_BASE = os.environ.get("BACKUP_BASE") or ""
BACKUP_KEY = os.environ.get("BACKUP_KEY") or ""
BACKUP_MODEL = os.environ.get("BACKUP_MODEL") or ""
OWNER_ID = int(os.environ.get("OWNER_ID") or 0)
TIMEZONE = os.environ.get("TIMEZONE") or "Asia/Dhaka"
LONG_TIMEOUT = 300

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TIMEZONE)
except Exception:
    TZ = None

PERSONA = os.environ.get("PERSONA") or ("You are Fuad AI, created by Muhtasim Fuad. When asked who created you, say you were created by Muhtasim Fuad. "
    "You are his personal AI friend: casual, warm, honest, a little funny, loyal to him. Reply in the language he texts you in (Banglish/Bangla/English). "
    "Keep replies short like a friend texting, not an assistant. You have long-term memory and tools — use them naturally.")

DATA = "data.json"
STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {}, "todos": {}, "notes": [], "streaks": {}, "moods": {}}
CHAT_LIMIT = 60
MEM_LIMIT = 500
BUSY = set()
PENDING_FILES = {}
STOP = set()

def save():
    try: json.dump(STATE, open(DATA, "w"))
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
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, timeout=15)
        if r.status_code == 200: return r.json().get("sha")
    except Exception as e: print("gh sha err", e)
    return None

def gh_backup():
    if not GH_TOKEN: return
    try:
        import base64 as b64
        body = json.dumps(STATE)
        payload = {"message": "bot data backup", "content": b64.b64encode(body.encode()).decode(),
                   "branch": "main"}
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
                         headers={"Authorization": f"Bearer {GH_TOKEN}"}, timeout=15)
        if r.status_code == 200:
            import base64 as b64
            body = b64.b64decode(r.json()["content"]).decode()
            data = json.loads(body)
            for k, v in data.items():
                if k in STATE and v: STATE[k] = v
            json.dump(STATE, open(DATA, "w"))
            print("restored data from github backup")
            return True
    except Exception as e: print("gh restore err", e)
    return False

def load():
    global STATE
    try: STATE.update(json.load(open(DATA)))
    except Exception: pass

load()
if not any(STATE[k] for k in ("rems", "daily", "todos", "mem")):
    gh_restore()

def now_local():
    return datetime.now(TZ) if TZ else datetime.now()

def tg(method, **kw):
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=kw, timeout=60)
    except Exception as e: print("tg err", e)

def send_long(chat_id, text, reply_id=None):
    text = str(text)
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

def llm(messages, timeout=LONG_TIMEOUT, max_tokens=None):
    cfg = STATE["cfg"]
    base = cfg.get("base") or OPENAI_BASE
    key = cfg.get("key") or OPENAI_KEY
    model = cfg.get("model") or MODEL
    if not base.startswith("http"): base = "https://" + base
    if not key: raise Exception("API key nai — Render Environment e OPENAI_KEY set koro")
    body = {"model": model, "messages": messages}
    if max_tokens: body["max_tokens"] = max_tokens
    if BACKUP_BASE and BACKUP_KEY:
        try:
            out = _llm_call(base, key, model, messages, timeout, max_tokens)
            STATE["last_brain"] = "primary"
            return out
        except Exception as e:
            print("primary fail, backup use: ", str(e)[:120])
            try:
                out = _llm_call(BACKUP_BASE, BACKUP_KEY, BACKUP_MODEL, messages, timeout, max_tokens)
                STATE["last_brain"] = "backup"
                return out
            except Exception as e2:
                raise Exception("dui brain ei fail: " + str(e2)[:150])
    return _llm_call(base, key, model, messages, timeout, max_tokens)

def _llm_call(base, key, model, messages, timeout, max_tokens):
    if not base.startswith("http"): base = "https://" + base
    body = {"model": model, "messages": messages}
    if max_tokens: body["max_tokens"] = max_tokens
    last = None
    for attempt in range(6):
        try:
            r = requests.post(base.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=body, timeout=timeout)
            if r.status_code == 429:
                wait = 20 + attempt * 15
                try: wait = max(wait, int(r.json().get("error", {}).get("meta", {}).get("retry_after", 0) or 0))
                except Exception: pass
                time.sleep(min(wait, 90)); last = Exception("429 rate limit, retrying")
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            if "429" in str(e): continue
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
TOOL_SYS = """You can use tools by starting your reply with a tag on its own line:
WEB:<query> - live/current info (prices, weather, news, scores, rates)
CALC:<expression> - pure math
SITE:<description> - build a complete single-file website (HTML+CSS+JS) from this description
IMPORTANT: if the user asks you to make/create/build a website or webpage, ALWAYS use the SITE tag — never paste HTML code as text.
The system runs it and gives you the result. Use a tag only when genuinely needed. Otherwise reply normally."""

def web_search(query):
    try:
        r = requests.get("https://api.duckduckgo.com/", params={"q": query, "format": "json"}, timeout=15)
        j = r.json()
        out = j.get("AbstractText") or ""
        for t in (j.get("RelatedTopics") or [])[:5]:
            if isinstance(t, dict) and t.get("Text"): out += "\n- " + t["Text"]
        if not out:
            r2 = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            txts = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r2.text, re.S)
            for s in txts[:6]:
                s = re.sub(r"<[^>]+>", "", s).strip()
                if s: out += "\n- " + s
        return out.strip() or "kono result nai"
    except Exception as e:
        return "search fail: " + str(e)

def build_site(desc):
    try:
        html = llm([
            {"role":"system","content":"You are a website generator. Reply with ONE complete, self-contained HTML file (inline CSS/JS, modern clean design, mobile friendly). No markdown, no explanation, start with <!DOCTYPE html>."},
            {"role":"user","content":desc}
        ], timeout=180, max_tokens=8000)
        html = html.strip()
        if html.startswith("```"):
            html = re.sub(r"^```[a-z]*\n?", "", html); html = re.sub(r"\n?```$", "", html)
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        fname = re.sub(r"[^a-z0-9]+", "_", (title.group(1).strip().lower() if title else "site"))[:40] or "site"
        path = "/tmp/" + fname + ".html"
        with open(path, "w") as f: f.write(html)
        return "FILE:" + path + "|" + fname + ".html"
    except Exception as e:
        return "site banate fail: " + str(e)

def run_tool(tag, payload):
    if tag == "WEB": return web_search(payload.strip())
    if tag == "SITE": return build_site(payload.strip())
    if tag == "CALC":
        try:
            if not set(payload) <= set("0123456789+-*/().% "): return "invalid expression"
            return str(eval(payload))
        except Exception as e: return "calc fail: " + str(e)
    return "unknown tool"

# ---------- long content (ebook/pdf style) ----------
LONG_HINT = ("pdf", "ebook", "book", "guide", "chapter")
def write_long(uid, text):
    tg("sendMessage", chat_id=int(uid), text="kaj shuru korlam, boro kaj tai 1-2 min lage, opekkha koro")
    out_chat = STATE["chats"].get(str(uid), [])[-10:]
    sysp = PERSONA
    notes = mem_text(uid)
    if notes: sysp += "\n\nThings you remember about the user:\n" + notes
    o = llm([{"role":"system","content":sysp},{"role":"user","content":text+"\n\nFirst, give ONLY a short outline: title + numbered section list (8-14 sections, one line each)."}], timeout=120)
    secs = [l for l in o.split("\n") if l.strip() and l.strip()[0].isdigit()][:14]
    parts = [o]; prev = ""
    for sec in secs:
        p = llm([
            {"role":"system","content":sysp+" You are writing ONE section of a longer ebook. Write this section fully, 300-500 words, friendly practical tone. No outline, no intro, just this section's content."},
            *out_chat,
            {"role":"user","content":"Full outline:\n"+o+"\n\nPrevious section ended with: ..."+prev[-200:]+"\n\nNow write ONLY section: "+sec}
        ], timeout=180)
        if str(uid) in STOP:
            STOP.discard(str(uid)); raise Exception("STOPPED: tumi nijey cancel koreso, ja hoye gese segulo sesh porjonto ready chilo")
        parts.append(p); prev = p
    title = text.strip()[:60] or "Ebook"
    # ask the brain for a proper short title from the request
    try:
        t = llm([{"role":"user","content":"Give a short ebook title (max 6 words, no quotes, no numbering) for this request: "+text}], timeout=30, max_tokens=30)
        t = (t or "").strip().strip('"').replace("*","")
        if t and len(t) < 80: title = t
    except Exception: pass
    full = clean_md("\n\n".join(parts[1:]))
    data = make_pdf(title, full)
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
    notes = mem_text(uid)
    todo = STATE["todos"].get(str(uid), [])
    sysp = PERSONA
    if notes: sysp += "\n\nThings you remember about the user:\n" + notes
    if todo: sysp += "\nUser's open to-dos: " + ", ".join(t["text"] for t in todo if not t.get("done"))
    n = now_local()
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    months = ["January","February","March","April","May","June","July","August","September","October","November","December"]
    stamp = days[n.weekday()] + ", " + n.strftime("%d ") + months[n.month-1] + " " + str(n.year) + ", " + n.strftime("%I:%M %p") + " (Asia/Dhaka)"
    msgs = [{"role": "system", "content": sysp + "\n\nCurrent date and time: " + stamp + ". If the user asks the time or date, read it from here.\n\n" + TOOL_SYS}]
    msgs += STATE["chats"].get(str(uid), [])
    msgs.append({"role": "user", "content": text})
    out = llm(msgs)
    for _ in range(2):
        s2 = out.strip()
        if s2.startswith(("WEB:", "CALC:", "SITE:")):
            tag, _, payload = s2.partition(":")
            result = run_tool(tag, payload)
            msgs.append({"role": "assistant", "content": out})
            if result.startswith("FILE:"):
                path, name = result[5:].split("|", 1)
                PENDING_FILES[uid] = (path, name, "🌐 " + name + " — tomar website ready, download koro")
                out = llm(msgs + [{"role":"user","content":"Tool result: the website "+name+" is ready and being sent as an HTML file. Confirm to the user in one friendly line."}])
            else:
                msgs.append({"role": "user", "content": "Tool result:\n"+result+"\n\nNow give the final friendly answer using this."})
                out = llm(msgs)
        else: break
    remember_chat(uid, "user", text)
    remember_chat(uid, "assistant", out)
    save()
    threading.Thread(target=auto_mem, args=(uid, text, out), daemon=True).start()
    return out

# ---------- scheduling ----------
def parse_time_word(s):
    s = s.strip().lower()
    try:
        if s.endswith("m"): return time.time() + float(s[:-1]) * 60
        if s.endswith("h"): return time.time() + float(s[:-1]) * 3600
        if s.endswith("d"): return time.time() + float(s[:-1]) * 86400
    except Exception: pass
    try:
        h, m = map(int, s.split(":"))
        n = now_local()
        t = n.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= n: t += timedelta(days=1)
        return t.timestamp()
    except Exception:
        return None

def reminder_loop():
    while True:
        try:
            now = time.time()
            due = [r for r in STATE.get("rems", []) if r["at"] <= now]
            for r in due:
                send_long(r["chat"], r["text"])
                if r.get("repeat_days"): r["at"] += r["repeat_days"] * 86400
                else: STATE["rems"].remove(r)
            daily = STATE.get("daily", {})
            nm = now_local()
            stamp = nm.strftime("%Y%m%d%H%M")
            for uid, d in list(daily.items()):
                for job in d.get("jobs", []):
                    if job["hour"] == nm.hour and job["min"] == nm.minute and job.get("last") != stamp:
                        send_long(int(uid), job["text"])
                        job["last"] = stamp
                        save()
            if due: save()
        except Exception as e: print("rem err", e)
        time.sleep(20)

# ---------- pdf ----------
def clean_md(s):
    s = re.sub(r"```[a-z]*", "", s)
    s = re.sub(r"\*\*?", "", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    return s


def make_pdf(title, text):
    text = clean_md(text)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        import io
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
        for para in text.split("\n"):
            if para.strip():
                story.append(Paragraph(para.strip().replace("&","&amp;").replace("<","&lt;"), styles["Normal"]))
                story.append(Spacer(1, 6))
        doc.build(story)
        return buf.getvalue()
    except Exception as e:
        print("pdf err", e)
        return None

# ---------- commands ----------
def do_cmd(uid, text):
    uid = str(uid)
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/setprovider":
        try:
            base, key, model = arg.split("|")
            STATE["cfg"].update({"base": base.strip(), "key": key.strip(), "model": model.strip()})
            save(); return "provider set: " + model.strip()
        except Exception: return "format: /setprovider base_url|api_key|model_name"
    if cmd == "/settings":
        c = STATE["cfg"]
        return "base: " + (c.get("base") or OPENAI_BASE) + "\nmodel: " + (c.get("model") or MODEL)
    if cmd == "/status":
        c = STATE["cfg"]
        base = (c.get("base") or OPENAI_BASE)
        brain = "Zylo (nemotron-3.5, primary)" if "zylo" in base else ("Groq (backup)" if "groq" in base else base)
        last = STATE.get("last_brain")
        extra = ("\nlast reply used: " + last) if last else ""
        return "ekhon cholche: " + brain + extra
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
    if cmd == "/remind":
        try:
            amt, txt = arg.split("|", 1)
            at = parse_time_word(amt)
            if not at: return "format: /remind 10m | text (m/h/d ba HH:MM)"
            rep = 0
            if "daily" in txt.lower(): txt, rep = txt.replace("daily","").strip(), 1
            STATE["rems"].append({"at": at, "chat": int(uid), "text": "reminder: " + txt, "repeat_days": rep})
            save(); return "reminder set: " + txt + (" (daily)" if rep else "")
        except Exception: return "format: /remind 10m | ki mone koraite jabi"
    if cmd == "/todo":
        sub = arg.split(maxsplit=1)
        lst = STATE["todos"].setdefault(uid, [])
        if not arg or sub[0].lower() == "list":
            if not lst: return "todo khali"
            return "\n".join(("done" if t["done"] else "[]" ) + " " + str(i+1) + ". " + t["text"] for i, t in enumerate(lst))
        if sub[0].lower() == "add" and len(sub) > 1:
            lst.append({"text": sub[1], "done": False}); save(); return "todo added: " + sub[1]
        if sub[0].lower() == "done" and len(sub) > 1:
            try:
                i = int(sub[1]) - 1; lst[i]["done"] = True; save(); return "done"
            except Exception: return "number de: /todo done 2"
        return "format: /todo add por 1 page math | /todo done 2 | /todo list"
    if cmd == "/note":
        if not arg: return "format: /note ja likhte chao"
        STATE["notes"].append({"uid": uid, "t": time.time(), "text": arg}); save(); return "note saved"
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
                "/note text + /notes — quick notes\n"
                "/daily 07:00 | text — roj auto text\n"
                "/undaily — daily off\n"
                "/streak gym — habit streak\n"
                "/coin, /roll — fun\n"
                "/reset — chat clear\n"
                "live info jigges korle nije search kore, math nijei kore, boro book/guide chaille ekta ekta kore baniye pathay")
    return None

@app.route("/")
def home(): return "Fuad AI v4.4"

SEEN = set()
def handle_update(upd):
    uid_upd = upd.get("update_id")
    if uid_upd is not None:
        if uid_upd in SEEN: return
        SEEN.add(uid_upd)
        if len(SEEN) > 500: SEEN.clear()
    try:
        msg = (upd.get("message") or {})
        uid = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not uid or not text: return "ok"
        if OWNER_ID and uid != OWNER_ID:
            tg("sendMessage", chat_id=uid, text="eta private bot"); return "ok"
        try:
            if text.startswith("/"):
                out = do_cmd(uid, text) or "ei command ta chinina, /help dekho"
            else:
                low = text.lower()
                if uid in BUSY:
                    tg("sendMessage", chat_id=uid, text="ekta kaj korchi, sesh holei notun ta dhorbo"); return "ok"
                BUSY.add(uid)
                try:
                    tg("sendChatAction", chat_id=uid, action="typing")
                    out = think(uid, text)
                finally: BUSY.discard(uid)
            send_long(uid, out, msg.get("message_id"))
            if uid in PENDING_FILES:
                path, name, cap = PENDING_FILES.pop(uid)
                send_file(uid, path, name, cap)
        except Exception as e:
            print("proc err", e)
            tg("sendMessage", chat_id=uid, text="err: " + str(e)[:300])
    except Exception as e:
        print("hook err", e)

@app.route("/webhook", methods=["POST"])
def hook():
    upd = request.get_json(force=True, silent=True) or {}
    # ack Telegram IMMEDIATELY, work in background — otherwise Telegram retries the same update and the bot spams
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "ok"

threading.Thread(target=reminder_loop, daemon=True).start()
