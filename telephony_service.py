# telephony_service.py
import os, sys, json, time, shlex, asyncio, signal, logging, subprocess, configparser
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

def load_env():
    for fp in ("/etc/neurosfera/neurosfera.env", "/root/telephony.env", "./api.env"):
        p = Path(fp)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                if k not in os.environ:
                    os.environ[k] = v
load_env()

INI_LOCAL = Path(__file__).with_name("telephony.ini")
INI_FALLBACK = Path("/etc/neurosfera/telephony.ini")
INI = str(INI_LOCAL if INI_LOCAL.exists() else INI_FALLBACK)
cfg = configparser.ConfigParser()
cfg.read(INI, encoding="utf-8")

BASE_PATH = cfg.get("telephony", "base_path", fallback=os.getenv("TELEPHONY_BASE", "/bee"))
PORT = int(cfg.get("telephony", "port", fallback=os.getenv("TELEPHONY_PORT", "8087")))
AUDIO_DIR = Path(cfg.get("telephony", "audio_dir", fallback="/opt/neurosfera/audio"))
LOG_DIR = Path(cfg.get("telephony", "log_dir", fallback="/opt/neurosfera/logs"))

TTS_ENGINE = cfg.get("tts", "engine", fallback="openai").lower()   # openai|gemini
STUB_TEXT = cfg.get("tts", "stub_text", fallback="Здравствуйте. Телефония временно дорабатывается")

STT_ENGINE = cfg.get("stt", "engine", fallback="whisper").lower()  # whisper|gemini|yandex|tinkoff
RECORD_SECONDS = int(cfg.get("recording", "record_seconds", fallback=os.getenv("RECORD_SECONDS", "30")))

SIP_DOMAIN = os.getenv("SIP_DOMAIN", "ip.beeline.ru")
SIP_USER = os.getenv("SIP_USER", "SIP030FQU0451O")
SIP_AUTH_USER = os.getenv("SIP_AUTH_USER", f"{SIP_USER}@{SIP_DOMAIN}")
SIP_PASS = os.getenv("SIP_PASS", "")

BEELINE_API_TOKEN = os.getenv("BEELINE_API_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "@NeurosferaTech")

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "telephony_service.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("telephony")
log.info("Loaded config from %s", INI)

async def tg_send(text: str, parse_mode: str = "HTML"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True})

async def tg_send_document(path: Path, caption: str = ""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    files = {"document": (path.name, path.open("rb"))}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=None) as client:
        await client.post(url, data=data, files=files)

BARESIP_HOME = Path.home() / ".baresip"
BARESIP_ACCOUNTS = BARESIP_HOME / "accounts"
BARESIP_CONFIG = BARESIP_HOME / "config"
BARESIP_FIFO = Path("/tmp/baresip_cmd")
baresip_proc: Optional[subprocess.Popen] = None

def prepare_baresip():
    # accounts создаётся отдельным юнитом/скриптом из ENV; здесь просто убеждаемся в наличии каталога и config
    BARESIP_HOME.mkdir(parents=True, exist_ok=True)
    if not BARESIP_CONFIG.exists() or "module fifo" not in BARESIP_CONFIG.read_text(encoding="utf-8"):
        with BARESIP_CONFIG.open("a", encoding="utf-8") as f:
            f.write("\n# --- Neurosfera telephony additions ---\n")
            f.write("module                  fifo\n")
            f.write("fifo_path               /tmp/baresip_cmd\n")
            f.write("call_autoanswer         yes\n")
            f.write("audio_player            aufile,alsa,pipewire,portaudio\n")
            f.write("auplay_srate            8000\n")
            f.write("auplay_channels         1\n")

def start_baresip():
    # baresip поднимаем отдельным сервисом; здесь оставляем возможность локально стартануть
    if baresip_proc: return
    if not Path("/usr/bin/baresip").exists(): return
    baresip_proc = subprocess.Popen(
        ["/usr/bin/baresip", "-f", str(BARESIP_HOME)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True
    )

async def wait_registered(timeout=25) -> bool:
    if not baresip_proc or not baresip_proc.stdout: return False
    start = time.time()
    while time.time() - start < timeout:
        line = baresip_proc.stdout.readline()
        if not line: await asyncio.sleep(0.1); continue
        log.info("[baresip] %s", line.strip())
        if "registered" in line.lower(): return True
    return False

def auplay(wav: Path):
    if not BARESIP_FIFO.exists(): raise RuntimeError("baresip FIFO missing")
    with BARESIP_FIFO.open("w") as f:
        f.write(f"auplay {wav}\n")

async def tts_to_wav(text: str, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if TTS_ENGINE == "gemini" and GEMINI_API_KEY:
        # TODO: добавить Gemini TTS при необходимости
        pass
    if not OPENAI_API_KEY: raise RuntimeError("OPENAI_API_KEY missing for TTS")
    url = "https://api.openai.com/v1/audio/speech"  # OpenAI Audio TTS (gpt-4o-mini-tts)
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {"model": "gpt-4o-mini-tts", "voice": "alloy", "input": text, "format": "wav", "sample_rate": 8000}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers); r.raise_for_status()
        out_path.write_bytes(r.content)

def start_record(out_wav: Path, secs: int) -> subprocess.Popen:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg","-y","-f","alsa","-i","default","-ac","1","-ar","8000","-t",str(secs),str(out_wav)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def stt_from_wav(wav: Path) -> str:
    if STT_ENGINE == "gemini" and GEMINI_API_KEY:
        # TODO: добавить Gemini STT при необходимости
        pass
    if not OPENAI_API_KEY: return "(no OPENAI_API_KEY; skip STT)"
    url = "https://api.openai.com/v1/audio/transcriptions"  # Whisper API
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    data = {"model": "whisper-1"}
    files = {"file": (wav.name, wav.open("rb"), "audio/wav")}
    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(url, headers=headers, data=data, files=files); r.raise_for_status()
        return (r.json().get("text") or "").strip() or "(пусто)"

app = FastAPI(title="Neurosfera Telephony")

@app.on_event("startup")
async def startup():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    prepare_baresip()
    # Если baresip поднят отдельным юнитом — FIFO уже будет. Локальный автозапуск — опционально:
    # start_baresip()
    # ok = await wait_registered()
    await tg_send("✅ <b>Телефония-бот поднят</b> (сервис FastAPI).")

@app.on_event("shutdown")
async def shutdown():
    global baresip_proc
    if baresip_proc and baresip_proc.poll() is None:
        baresip_proc.send_signal(signal.SIGTERM)
        try: baresip_proc.wait(timeout=5)
        except subprocess.TimeoutExpired: baresip_proc.kill()

@app.get(f"{BASE_PATH}/health")
async def health():
    return {"status":"ok","sip_user":SIP_USER,"domain":SIP_DOMAIN,"tts":TTS_ENGINE,"stt":STT_ENGINE}

@app.post(f"{BASE_PATH}/beeline/webhook")
async def beeline_webhook(request: Request, x_api_token: Optional[str] = Header(None)):
    if BEELINE_API_TOKEN and x_api_token != BEELINE_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    try: payload = await request.json()
    except Exception: payload = {}

    tts_wav = AUDIO_DIR / "stub_hello.wav"
    await tts_to_wav(STUB_TEXT, tts_wav)
    try: auplay(tts_wav)
    except Exception as e: logging.exception("auplay failed: %s", e)

    rec = AUDIO_DIR / f"call_{int(time.time())}.wav"
    p = start_record(rec, RECORD_SECONDS)

    caller = payload.get("caller") or payload.get("from") or payload.get("ani")
    callee = payload.get("callee") or payload.get("to") or payload.get("dnis")
    ts = payload.get("ts") or payload.get("timestamp")
    summary = (
        f"<b>Входящий вызов</b>\n"
        f"<b>От:</b> <code>{caller}</code>\n"
        f"<b>Кому:</b> <code>{callee}</code>\n"
        f"<b>Время:</b> <code>{ts or time.strftime('%Y-%m-%d %H:%M:%S')}</code>"
    )
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    await tg_send(f"{summary}\n\n<pre>{raw}</pre>")

    try: p.wait(timeout=RECORD_SECONDS+5)
    except subprocess.TimeoutExpired: p.kill()
    text = await stt_from_wav(rec)
    await tg_send(f"📝 <b>Транскрипт абонента</b>:\n<pre>{text}</pre>")
    if rec.exists(): await tg_send_document(rec, caption="🎧 Запись разговора (MVP)")

    return JSONResponse({"ok": True})
