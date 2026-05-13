"""
PNL DASHBOARD — LiveMonitor: conecta al websocket y emite eventos via queue.
"""
import asyncio
import threading
import queue
import json
import datetime as _dt

from pnl_config import C, LIVE_WS_URL, LIVE_WS_ORIGIN, FILTER_PARAMS
from pnl_data import calcular_rango


class LiveMonitor:
    """Conecta al websocket en tiempo real y emite eventos via queue."""

    def __init__(self, q: queue.Queue):
        self._q = q
        self._loop = None
        self._thread = None
        self._running = False
        self._ws = None

        # Estado de ronda
        self._vol_blue   = 0.0
        self._vol_red    = 0.0
        self._dif_t25    = 0.0
        self._dif_t33    = 0.0
        self._dif_act    = 0.0
        self._tick       = 0
        self._acel       = 0.0
        self._est        = 'ESTABLE'
        self._historial  = []
        self._wr_actual  = 50.0
        self._primera_ok = False

    def iniciar(self):
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def detener(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._conectar())

    async def _conectar(self):
        import websockets
        self._q.put({'ev': 'status', 'msg': 'CONECTANDO...', 'color': C['warn']})
        intentos = 0
        while self._running:
            try:
                async with websockets.connect(
                    LIVE_WS_URL, origin=LIVE_WS_ORIGIN,
                    ping_interval=30, ping_timeout=10
                ) as ws:
                    self._ws = ws
                    intentos = 0
                    self._q.put({'ev': 'status', 'msg': 'CONECTADO', 'color': C['accent2']})
                    await ws.send(json.dumps({"type": "bind", "uid": "9999"}))
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue
                        self._procesar(data)
            except Exception as e:
                if not self._running:
                    break
                intentos += 1
                self._q.put({'ev': 'status', 'msg': f'RECONECTANDO ({intentos})...', 'color': C['warn']})
                await asyncio.sleep(5)
        self._q.put({'ev': 'status', 'msg': 'DESCONECTADO', 'color': C['accent3']})

    def _procesar(self, data):
        t = data.get('type', '')

        if t in ('open_draw', 'start', 'game_start', 'game_init'):
            self._tick = 0
            self._dif_t25 = self._dif_t33 = self._dif_act = 0.0
            self._acel = 0.0
            self._est = 'ESTABLE'
            self._vol_blue = self._vol_red = 0.0
            issue = data.get('issue_num', data.get('issue', ''))
            self._q.put({'ev': 'ronda', 'issue': issue})
            return

        if t == 'total_bet':
            self._tick += 1
            blue = float(data.get('blue', 0))
            red  = float(data.get('red', 0))
            total = blue + red
            self._vol_blue = blue
            self._vol_red  = red
            if total > 0:
                p_b = (blue / total) * 100
                p_r = (red  / total) * 100
                self._dif_act = round(abs(p_b - p_r), 2)
                mayor = 'AZUL' if blue > red else 'ROJO'
                self._q.put({'ev': 'tick', 'dif': self._dif_act,
                             'p_b': round(p_b, 1), 'p_r': round(p_r, 1),
                             'mayor': mayor, 'tick_n': self._tick})
            if self._tick == 25:
                self._dif_t25 = self._dif_act
            if self._tick == 33:
                self._dif_t33 = self._dif_act
                self._acel = round(self._dif_t33 - self._dif_t25, 2)
                self._est  = 'ESTABLE' if abs(self._acel) < FILTER_PARAMS['acel_umbral'] else 'VOLATIL'
            return

        if t == 'drawed':
            if not self._primera_ok:
                self._primera_ok = True
                return
            result_raw = data.get('result', '').lower()
            winner = 'azul' if 'blue' in result_raw else 'rojo'
            mayor  = 'azul' if self._vol_blue > self._vol_red else 'rojo'
            acierto = (winner == mayor)
            self._historial.append(1 if acierto else 0)
            if len(self._historial) > 10:
                self._historial.pop(0)
            wr = sum(self._historial) / len(self._historial) * 100
            self._wr_actual = wr
            rango_dif = self._dif_t33 if self._dif_t33 > 0 else self._dif_act
            rango = calcular_rango(rango_dif)
            self._q.put({
                'ev':        'resultado',
                'winner':    winner,
                'mayor':     mayor,
                'acierto':   acierto,
                'wr':        round(wr, 1),
                'rango':     rango,
                'dif':       round(rango_dif, 1),
                'acel':      self._acel,
                'est':       self._est,
                'timestamp': _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
