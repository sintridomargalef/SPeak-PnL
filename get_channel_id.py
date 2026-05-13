import os
import sys
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    sys.exit("Falta TELEGRAM_BOT_TOKEN. Defínelo antes de ejecutar este script.")

url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
data = requests.get(url, timeout=10).json()

if not data.get("ok"):
    sys.exit(f"Error de Telegram: {data}")

seen = {}
for update in data.get("result", []):
    for key in ("message", "channel_post", "edited_channel_post", "my_chat_member"):
        obj = update.get(key)
        if not obj:
            continue
        chat = obj.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen[cid] = {
            "title": chat.get("title") or chat.get("username") or chat.get("first_name"),
            "type": chat.get("type"),
        }

if not seen:
    print("No se han encontrado chats. Publica un mensaje en el canal con el bot como admin y vuelve a ejecutar.")
else:
    print("Chats detectados:")
    for cid, info in seen.items():
        print(f"  {cid}  [{info['type']}]  {info['title']}")
