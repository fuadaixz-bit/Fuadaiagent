import os, json, time, threading, requests, random
from flask import Flask, request
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

TG_TOKEN = os.environ.get("TG_TOKEN", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Dhaka")
LONG_TIMEOUT = 300

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TIMEZONE)
except Exception:
    TZ = None

PERSONA_DEFAULT = ("You are Fuad AI, created by Muhtasim Fuad. When asked who created you, say you were created by Muhtasim Fuad. "
    "You are his personal AI friend: casual, warm, honest, a little funny. Reply in the language he texts you in (Banglish/Bangla/English). "
    "Keep replies short like a friend texting, not an assistant. Use your tools/commands when they help.")

DATA = "data.json"
STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {}, "todos": {}, "notes": [], "streaks": {}, "facts": {}}
CHAT_LIMIT = 60
MEM_LIMIT = 500

def save():
    try: json.dump(STATE, open(DATA, "w"))
    except Exception as e: print("save err", e)

def load():
    global STATE
    try: STATE.update(json.load(open(DATA)))
    except Exception: pass

load()

def now_local():
    return datetime.now(TZ) if TZ else datetime.now()

def tg(method, **kw):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=kw, timeout=60)
    except Exception as e: print("tg err", e)

def send_long(chat_id, text, reply_id=None):
    text = str(text)
    for i in range(0, len(text), 3900):
        kw = {"chat_id": chat_id, "text": text[i:i+3900]}
        if i == 0 and reply_id: kw["reply_to_message_id"] = reply_id
        tg("sendMessage", **kw)

def llm(messages, timeout=LONG_TIMEOUT, max_tokens=None):
    cfg = STATE["cfg"]
    base = cfg.get("base", OPENAI_BASE)
    key = cfg.get("key", OPENAI_KEY)
    model = cfg.get("model", MODEL)
    body = {"model": model, "messages": messages, "timeout": timeout}
    if max_tokens: body["max_tokens"] = max_tokens
    last = None
    for attempt in range(3):
        try:
            r = requests.post(base.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json={"model": model, "messages": messages, **({"max_tokens": max_tokens} if max_tokens else {})},
                timeout=timeout)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            time.sleep(2 + attempt * 3)
    raise last

# ---------- memory helpers ----------
def remember(uid, text, tag="note"):
    lst = STATE["mem"].setdefault(str(uid), [])
    lst.append({"t": time.time(), "tag": tag, "text": text})
    STATE["mem"][str(uid)] = lst[-MEM_LIMIT:]
    save()

def mem_text(uid):
    return "\n".join(f"- {m['text']}" for m in STATE["mem"].get(str(uid), []))

def remember_chat(uid, role, text):
    hist = STATE["chats"].get(str(uid), [])
    hist.append({"role": role, "content": text})
    STATE["chats"][str(uid)] = hist[-CHAT_LIMIT:]
    save()

# ---------- brain with tools ----------
TOOL_SYS = """You are Fuad AI. You can use these commands by starting your reply with a tag on its own line:
WEB:<query>  - when the user asks about live/current stuff (prices, weather, news, scores, live rates)
CALC:<expression>  - pure math like 45*3.2
The system runs it and gives you the result. Use a tag only when genuinely needed. Otherwise reply normally."""

def web_search(query):
    try:
        r = requests.get("https://api.duckduckgo.com/", params={"q": query, "format": "json"}, timeout=15)
        j = r.json()
        out = j.get("AbstractText") or ""
        for t in (j.get("RelatedTopics") or [])[:5]:
            if isinstance(t, dict) and t.get("Text"): out += "\n- " + t["Text"]
        if not out:
            r2 = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=15,
                              headers={"User-Agent": "Mozilla/5.0"})
            import re as _re
            txts = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r2.text, _re.S)
            for s in txts[:6]:
                s = _re.sub(r"<[^>]+>", "", s).strip()
                if s: out += "\n- " + s
        return out.strip() or "kono result nai"
    except Exception as e:
        return "search fail: " + str(e)

def run_tool(tag, payload):
    if tag == "WEB": return web_search(payload.strip())
    if tag == "CALC":
        try:
            allowed = set("0123456789+-*/().% ")
            if not set(payload) <= allowed: return "invalid expression"
            return str(eval(payload))
        except Exception as e: return "calc fail: " + str(e)
    return "unknown tool"

def think(uid, text):
    notes = mem_text(uid)
    todo = STATE["todos"].get(str(uid), [])
    sysp = PERSONA_DEFAULT
    if notes: sysp += "\n\nThings you remember about the user:\n" + notes
    if todo: sysp += "\nUser's open to-dos: " + ", ".join(t["text"] for t in todo if not t.get("done"))
    msgs = [{"role": "system", "content": sysp + "\n\n" + TOOL_SYS}]
    msgs += STATE["chats"].get(str(uid), [])
    msgs.append({"role": "user", "content": text})
    out = llm(msgs)
    # tool loop (one round)
    for _ in range(2):
        if out.strip().startswith("WEB:") or out.strip().startswith("CALC:"):
            tag, _, payload = out.strip().partition(":")
            result = run_tool(tag, payload)
            remember_chat(uid, "user", text)
            remember_chat(uid, "assistant", f"[used {tag} tool for: {payload.strip()}]")
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user", "content": f"Tool result:\n{result}\n\nNow give the final friendly answer using this."})
            out = llm(msgs)
        else:
            break
    remember_chat(uid, "user", text)
    remember_chat(uid, "assistant", out)
    save()
    return out

# ---------- scheduling engine ----------
def parse_time_word(s):
    s = s.strip().lower()
    try:
        if s.endswith("m"): return time.time() + float(s[:-1]) * 60
        if s.endswith("h"): return time.time() + float(s[:-1]) * 3600
        if s.endswith("d"): return time.time() + float(s[:-1]) * 86400
    except Exception: pass
    try:  # HH:MM -> next occurrence
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
                if r.get("repeat_days"):
                    r["at"] += r["repeat_days"] * 86400
                else:
                    STATE["rems"].remove(r)
            daily = STATE.get("daily", {})
            for uid, d in list(daily.items()):
                for job in d.get("jobs", []):
                    if job["hour"] == now_local().hour and job["min"] == now_local().minute and job.get("last") != now_local().strftime("%Y%m%d%H%M"):
                        send_long(int(uid), job["text"])
                        job["last"] = now_local().strftime("%Y%m%d%H%M")
                        save()
            if due: save()
        except Exception as e: print("rem err", e)
        time.sleep(20)

# ---------- pdf ----------
def make_pdf(title, text):
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
                story.append(Paragraph(para.strip().replace("&", "&amp;").replace("<", "&lt;"), styles["Normal"]))
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
        return f"base: {c.get('base', OPENAI_BASE)}\nmodel: {c.get('model', MODEL)}"
    if cmd == "/remember":
        if not arg: return "ki mone rakhbo? /remember gym roj 6 tay"
        tag = "note"
        if arg.lower().startswith("imp:"): tag, arg = "important", arg[4:].strip()
        remember(uid, arg, tag); return "mone rekhlam: " + arg
    if cmd == "/memory":
        lst = STATE["mem"].get(uid, [])
        if not lst: return "kiso nai"
        return "\n".join(f"[{m['tag']}] {m['text']}" for m in lst[-30:])
    if cmd == "/forget":
        n0 = len(STATE["mem"].get(uid, []))
        STATE["mem"][uid] = [m for m in STATE["mem"].get(uid, []) if arg.lower() not in m["text"].lower()]
        save(); return f"{n0 - len(STATE['mem'][uid])} ta memory muche dilam"
    if cmd == "/remind":
        try:
            amt, txt = arg.split("|", 1)
            at = parse_time_word(amt)
            if not at: return "format: /remind 10m | text  (m/h/d ba HH:MM)"
            rep = 0
            if "daily" in txt.lower(): txt, rep = txt.replace("daily", "").strip(), 1
            STATE["rems"].append({"at": at, "chat": int(uid), "text": "reminder: " + txt, "repeat_days": rep})
            save()
            return "reminder set: " + txt + (" (daily)" if rep else "")
        except Exception: return "format: /remind 10m | ki mone koraite jabi"
    if cmd == "/todo":
        if not arg: return "format: /todo add por 1 page math  ba /todo done 2  ba /todo list"
        sub = arg.split(maxsplit=1)
        lst = STATE["todos"].setdefault(uid, [])
        if sub[0].lower() == "add" and len(sub) > 1:
            lst.append({"text": sub[1], "done": False}); save(); return "todo added: " + sub[1]
        if sub[0].lower() == "done" and len(sub) > 1:
            try:
                i = int(sub[1]) - 1; lst[i]["done"] = True; save(); return "done ✅"
            except Exception: return "number de: /todo done 2"
        if sub[0].lower() == "list" or True:
            if not lst: return "todo khali"
            return "\n".join(f"{'✅' if t['done'] else '⬜'} {i+1}. {t['text']}" for i, t in enumerate(lst))
    if cmd == "/note":
        if not arg: return "format: /note ja likhte chao"
        STATE.setdefault("notes", []).append({"uid": uid, "t": time.time(), "text": arg}); save()
        return "note saved"
    if cmd == "/notes":
        ns = [n for n in STATE.get("notes", []) if n["uid"] == uid]
        return "\n\n".join(n["text"] for n in ns[-10:]) or "kiso nai"
    if cmd == "/daily":
        try:
            hhmm, txt = arg.split("|", 1)
            h, m = map(int, hhmm.strip().split(":"))
            d = STATE.setdefault("daily", {}).setdefault(uid, {"jobs": []})
            d["jobs"].append({"hour": h, "min": m, "text": txt.strip()}); save()
            return f"daily text set: roj {hhmm.strip()} e — {txt.strip()}"
        except Exception: return "format: /daily 07:00 | good morning fuad"
    if cmd == "/undaily":
        d = STATE.get("daily", {}).get(uid, {"jobs": []})
        d["jobs"] = []; save(); return "daily text gula off"
    if cmd == "/streak":
        today = now_local().strftime("%Y-%m-%d")
        s = STATE.setdefault("streaks", {}).setdefault(uid, {})
        if not arg: 
            return "\n".join(f"{k}: {v['count']} din (last {v['last']})" for k, v in s.items()) or "kono streak nai. /streak gym"
        name = arg.strip()
        st = s.setdefault(name, {"count": 0, "last": ""})
        if st["last"] == today: return f"{name}: aj to already done ✅ (streak {st['count']})"
        yest = (now_local() - timedelta(days=1)).strftime("%Y-%m-%d")
        st["count"] = st["count"] + 1 if st["last"] in ("", yest) else 1
        st["last"] = today; save()
        return f"{name}: {st['count']} din streak 🔥"
    if cmd == "/ask":
        return llm([{"role": "system", "content": PERSONA_DEFAULT}, {"role": "user", "content": arg or "hi"}]) if arg else "ki jigges korbo?"
    if cmd == "/coin":
        return random.choice(["Head", "Tail"])
    if cmd == "/roll":
        return str(random.randint(1, 6))
    if cmd == "/reset":
        STATE["chats"][uid] = []; save(); return "chat history clear"
    if cmd == "/help":
        return ("commands:\n"
                "/setprovider base|key|model — brain switch (any OpenAI-compatible provider)\n"
                "/settings — current brain\n"
                "/remember kotha — mone rakho (imp: dile important)\n"
                "/memory — memory dekho, /forget word — muche dao\n"
                "/remind 10m | text — reminder (m/h/d ba HH:MM, 'daily' dile roj)\n"
                "/todo add|done|list — task list\n"
                "/note text + /notes — quick notes\n"
                "/daily 07:00 | text — roj auto text\n"
                "/undaily — daily off\n"
                "/streak gym — habit streak\n"
                "/ask question — fresh memory-chara answer\n"
                "/coin, /roll — fun\n"
                "/reset — chat clear\n"
                "plus: live info jigges korle nije search kore (WEB tool), math CALC tool")
    return None

@app.route("/")
def home(): return "Fuad AI is running"

@app.route("/webhook", methods=["POST"])
def hook():
    upd = request.get_json(force=True, silent=True) or {}
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
                out = think(uid, text)
            send_long(uid, out, msg.get("message_id"))
        except Exception as e:
            print("proc err", e)
            tg("sendMessage", chat_id=uid, text="ekkta problem hoise, abr try koro")
    except Exception as e:
        print("hook err", e)
    return "ok"

threading.Thread(target=reminder_loop, daemon=True).start()
