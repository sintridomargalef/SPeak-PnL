import os
from pathlib import Path
import requests


_ENV_FILE = Path(__file__).parent / 'telegram.env'


def _read_env_file():
    """Lee telegram.env (KEY=VALUE por línea, ignora # y vacías). Cache simple."""
    if not _ENV_FILE.exists():
        return {}
    out = {}
    try:
        for linea in _ENV_FILE.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if not linea or linea.startswith('#'):
                continue
            if '=' not in linea:
                continue
            k, v = linea.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _bot_token():
    v = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if v:
        return v
    return _read_env_file().get("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_ids():
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        raw = _read_env_file().get("TELEGRAM_CHAT_ID", "").strip()
    return [c.strip() for c in raw.split(",") if c.strip()]


# Compatibilidad: el código existente importa BOT_TOKEN / CHAT_IDS desde este módulo.
# Se mantienen como snapshot al import, pero las funciones siempre releen el entorno.
BOT_TOKEN = _bot_token()
CHAT_IDS  = _chat_ids()


def _require_token():
    token = _bot_token()
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en el entorno.")
    return token


def get_chat_id():
    token = _require_token()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    data = requests.get(url, timeout=10).json()
    if data.get("ok") and data["result"]:
        return data["result"][-1]["message"]["chat"]["id"]
    return None


def send_message(text, chat_id=None):
    token = _require_token()
    if chat_id is not None:
        targets = chat_id if isinstance(chat_id, (list, tuple)) else [chat_id]
    else:
        targets = _chat_ids()
    if not targets:
        raise RuntimeError("Falta chat_id (TELEGRAM_CHAT_ID o argumento).")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    results = []
    for cid in targets:
        try:
            resp = requests.post(url, json={"chat_id": cid, "text": text}, timeout=10)
            data = resp.json()
            results.append({"chat_id": cid, "response": data})
        except Exception as e:
            results.append({"chat_id": cid, "response": {"ok": False, "error": str(e)}})
    return results if len(results) > 1 else results[0]["response"]


if __name__ == "__main__":
    cids = _chat_ids()
    if cids:
        print(f"Enviando a CHAT_IDS={cids}…")
        print("Respuesta:", send_message("✅ SPeak conectado a Telegram"))
    else:
        print("Buscando chat_id…")
        cid = get_chat_id()
        if cid:
            print(f"Chat ID encontrado: {cid}")
            print("Respuesta:", send_message("✅ SPeak conectado a Telegram", chat_id=cid))
        else:
            print("No se encontró ningún chat. Envía un mensaje al bot en Telegram primero.")
