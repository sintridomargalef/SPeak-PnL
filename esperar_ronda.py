"""
Espera a que termine una ronda completa en el websocket y sale (exit 0).
El dashboard lo lanza al conectar y bloquea el procesamiento hasta que
este proceso termina — así siempre empieza en una ronda limpia.
"""
import asyncio
import websockets
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pnl_config import LIVE_WS_URL, LIVE_WS_ORIGIN

TIMEOUT_SEG = 600   # 10 min máximo antes de salir igualmente


async def _esperar():
    async with websockets.connect(
        LIVE_WS_URL, origin=LIVE_WS_ORIGIN,
        ping_interval=30, ping_timeout=10
    ) as ws:
        await ws.send(json.dumps({"type": "bind", "uid": "9999"}))
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get('type') == 'drawed':
                return   # primera ronda terminada → salir


try:
    asyncio.run(asyncio.wait_for(_esperar(), timeout=TIMEOUT_SEG))
except Exception:
    pass   # timeout o error de conexión → salir igualmente

sys.exit(0)
