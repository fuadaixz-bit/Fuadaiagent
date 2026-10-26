import os, json, time, threading, requests
from flask import Flask, request
from datetime import datetime

app = Flask(__name__)

TG_TOKEN = os.environ.get("TG_TOKEN", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
PERSONA = os.environ.get("PERSONA", "You are Fuad AI, Fuad's personal AI friend. Casual, warm, honest, a little funny. Reply in the language he texts you in (Banglish/Bangla/English). Keep replies short like a friend texting, not an assistant.")

DATA = "data.json"
STATE = {"chats": {}, "mem": {}, "cfg": {}, "rems": []}
CHAT_LIMIT = 40
MEM_LIMIT = 100

def save():
    try: json.dump(STATE, open(DATA, "w"))
    except Exception: pass

def load():
    global STATE
    try: STATE.update(json.load(open(DATA)))
    except Exception: pass

load()

def tg(method, **kw):
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=kw, timeout=30)
    except Exception as e: print("tg err", e)

def llm(messages):
    cfg = STATE["cfg"]
    base = cfg.get("base", OPENAI_BASE)
    key = cfg.get("key", OPENAI_KEY)
    model = cfg.get("model", MODEL)
    r = requests.post(base.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": model, "messages": messages}, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def think(uid, text):
    hist = STATE["chats"].get(str(uid), [])
    notes = "\n".join(STATE["mem"].get(str(uid), []))
    msgs = [{"role": "system", "content": PERSONA + ("\n\nThings you remember about the user:\n" + notes if notes else "")}]
    msgs += [{"role": r["role"], "content": r["content"]} for r in hist]
    msgs.append({"role": "user", "content": text})
    out = llm(msgs)
    hist.append({"role": "user", "content": text})
    hist.append({"role": "assistant", "content": out})
    STATE["chats"][str(uid)] = hist[-CHAT_LIMIT:]
    save()
    return out

def remember(uid, text):
    lst = STATE["mem"].setdefault(str(uid), [])
    if len(lst) < MEM_LIMIT: lst.append(f"{datetime.now().strftime('%Y-%m-%d')}: {text}")
    save()

def reminder_loop():
    while True:
        try:
            now = time.time()
            due = [r for r in STATE.get("rems", []) if r["at"] <= now]
            for r in due:
                tg("sendMessage", chat_id=r["chat"], text=r["text"])
                STATE["rems"].remove(r)
            if due: save()
        except Exception as e: print("rem err", e)
        time.sleep(30)

def do_cmd(uid, text):
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "/setprovider":
        try:
            base, key, model = arg.split("|")
            STATE["cfg"].update({"base": base.strip(), "key": key.strip(), "model": model.strip()})
            save()
            return "provider set: " + model.strip()
        except Exception: return "format: /setprovider base_url|api_key|model_name"
    if cmd == "/settings":
        c = STATE["cfg"]
        return f"base: {c.get('base', OPENAI_BASE)}\nmodel: {c.get('model', MODEL)}"
    if cmd == "/remember":
        remember(uid, arg); return "mone rekhlam: " + arg
    if cmd == "/memory":
        return "\n".join(STATE["mem"].get(str(uid), [])) or "kiso nai"
    if cmd == "/forget":
        STATE["mem"][str(uid)] = [m for m in STATE["mem"].get(str(uid), []) if arg.lower() not in m.lower()]
        save(); return "forget done"
    if cmd == "/reset":
        STATE["chats"][str(uid)] = []; save(); return "chat history clear"
    if cmd == "/remind":
        try:
            amt, txt = arg.split("|", 1)
            amt = amt.strip().lower(); mult = 60 if amt.endswith("m") else 3600 if amt.endswith("h") else 86400
            at = time.time() + float(amt[:-1]) * mult
            STATE.setdefault("rems", []).append({"at": at, "chat": uid, "text": "reminder: " + txt.strip()})
            save()
            return "reminder set: " + txt.strip()
        except Exception: return "format: /remind 10m | ki mone koraite jabi"
    if cmd == "/help":
        return ("commands:\n/setprovider base|key|model - brain switch\n/settings - current brain\n"
                "/remember kotha - important jinish mone rakho\n/memory - sob memory dekho\n"
                "/forget word - memory theke muche dao\n/remind 10m | text - reminder\n/reset - chat clear")
    return None

@app.route("/")
def home(): return "bot is running"

@app.route("/webhook", methods=["POST"])
def hook():
    upd = request.get_json(force=True, silent=True) or {}
    try:
        msg = (upd.get("message") or {})
        uid = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if uid and text:
            if OWNER_ID and uid != OWNER_ID:
                tg("sendMessage", chat_id=uid, text="eta private bot"); return "ok"
            if text.startswith("/"):
                out = do_cmd(uid, text)
            else:
                out = think(uid, text)
            tg("sendMessage", chat_id=uid, text=out, reply_to_message_id=msg.get("message_id"))
    except Exception as e:
        print("hook err", e)
        try: tg("sendMessage", chat_id=upd["message"]["chat"]["id"], text="ekkta problem hoise, abr try koro")
        except Exception: pass
    return "ok"

threading.Thread(target=reminder_loop, daemon=True).start()
