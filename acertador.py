import asyncio
import websockets
import json
import random
import logging
import sys
import subprocess
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
from threading import Thread
import tkinter as tk
from tkinter import ttk, font
from collections import deque
from historial_widget import HistorialWidget
from ep_core import ep_evaluar, ep_mult, ep_simular_combinado as ep_simular, EP_UMBRAL_ESTADO, EP_VENTANA, EP_MIN_OPS
from umbral_core import umbral_validar_rango
import time
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ============================================================
# CONFIGURACIÓN
# ============================================================

@dataclass
class Config:
    ARCHIVO_CREDENCIALES: str = "credenciales.json"
    ARCHIVO_BALANCE: str = "balance.json"
    NOMBRE_HOJA: str = "Pk_Arena"
    NOMBRE_PESTANA: str = "Lector"
    FILA_INICIO: int = 9
    UMBRAL_DIRECTO: float = 60.0
    UMBRAL_INVERSO: float = 40.0
    VENTANA_RACHA: int = 10
    PNL_ACIERTO: float = 0.90
    PNL_FALLO: float = -1.00
    WS_URL: str = "wss://www.ff2016.vip/game"
    WS_ORIGIN: str = "https://www.ff2016.vip"
    WS_RECONNECT_DELAY: int = 5
    WS_MAX_RETRIES: int = 10
    WS_PING_INTERVAL: int = 30
    LOG_DIR: str = "logs"
    LOG_FILE: str = "logs/acertador_backup.log"
    LOG_LEVEL: int = logging.INFO
    CONF_UMBRAL: float = 60.0
    MULT_MAXIMO: int = 4


class ModoApuesta(Enum):
    DIRECTO = "DIRECTO"
    INVERSO = "INVERSO"
    SKIP    = "SKIP"


# ============================================================
# COLORES TEMA FUTURISTA
# ============================================================

C = {
    'bg':       '#050A14',
    'panel':    '#0A1628',
    'border':   '#0D2137',
    'accent':   '#00D4FF',
    'accent2':  '#00FF88',
    'accent3':  '#FF3366',
    'warn':     '#FFB800',
    'text':     '#C8D8E8',
    'muted':    '#4A6080',
    'blue':     '#2B7FFF',
    'red':      '#FF3366',
    'white':    '#E8F4FF',
    'green':    '#00FF88',
    'skip':     '#888899',
    'ganada':   '#00FF88',
    'perdida':  '#FF3366',
}

FONT_MONO  = ('Consolas', 11)
FONT_MONO_B= ('Consolas', 11, 'bold')
FONT_BIG   = ('Consolas', 24, 'bold')
FONT_MED   = ('Consolas', 15, 'bold')
FONT_SM    = ('Consolas', 10)
FONT_TITLE = ('Consolas', 13, 'bold')

# ============================================================
# MOTOR DE ANÁLISIS
# ============================================================

@dataclass
class ResultadoAnalisis:
    rango: str
    dif: float
    racha: float
    modo: ModoApuesta
    pnl: float
    acierto: bool
    prevision: str
    mayor_bando: str = ""
    forced: bool = False


@dataclass
class DatosTick29:
    p_azul: float
    p_rojo: float
    v_azul: float
    v_rojo: float

    def validar(self) -> bool:
        try:
            return all(isinstance(v, (int, float)) and v >= 0
                       for v in [self.p_azul, self.p_rojo, self.v_azul, self.v_rojo])
        except:
            return False


class Analizador3Fases:
    def __init__(self, config: Config):
        self.config = config
        self.historial_mayor_gana: List[int] = []
        self.historial_todo: List[dict] = []
        self.balance_acumulado: float = 0.0
        self.stats_rangos: Dict[str, Dict[str, Dict[str, int]]] = {}
        self.ventana_rangos: Dict[str, Dict[str, deque]] = {}
        self.mejor_modo_rangos: Dict[str, tuple] = {}  # {rango: (modo, wr)}

        # 🚀 OPTIMIZACIÓN: Cache TOP5 + timestamp
        self._top5_cache = None
        self._top5_ops_cache = []
        self._top5_timestamp = 0
        self._top5_cache_ttl = 5  # Invalidar cada 5 segundos

        self._cargar_balance()
        self._cargar_historial_desde_archivo()
        self._cargar_stats_rangos_desde_archivo()
        self._cargar_ventana_rangos()
        self._calcular_mejor_modo_por_rango()

    def _obtener_top5_cached(self):
        """🚀 OPTIMIZACIÓN: Obtiene TOP5 con cache (5 seg TTL)."""
        ahora = time.time()
        if self._top5_cache is not None and (ahora - self._top5_timestamp) < self._top5_cache_ttl:
            return self._top5_cache, self._top5_ops_cache

        # Recargar cache
        try:
            from umbral_core import cargar_ops_desde_archivo, obtener_top5_rangos
            ops_para_top5 = cargar_ops_desde_archivo()
            if not ops_para_top5:
                from umbral_core import cargar_ops_desde_reconstructor
                ops_para_top5 = cargar_ops_desde_reconstructor()

            top5 = obtener_top5_rangos(ops_para_top5) if ops_para_top5 else None
            self._top5_cache = top5
            self._top5_ops_cache = ops_para_top5
            self._top5_timestamp = ahora
            return top5, ops_para_top5
        except Exception as e:
            return None, []

    @property
    def ops_history(self) -> list:
        """Retorna historial en formato compatible con umbral_core."""
        resultado = []
        for entry in self.historial_todo:
            modo = entry.get('modo', 'SKIP')
            if modo in ('SKIP', None):
                continue
            rango = entry.get('rango', 'desconocido')
            acierto = entry.get('acierto')
            pnl = entry.get('pnl', 0)
            ganada = acierto if acierto is not None else (pnl > 0 if pnl != 0 else False)
            resultado.append({
                'rango': rango,
                'modo': modo,
                'ganada': ganada,
                'pnl_real': pnl,
                'mult_real': entry.get('mult', 1),
                'racha': entry.get('racha', 50.0),
            })
        return resultado

    def _guardar_ronda_historial(self, entry: dict):
        try:
            from historial_rondas import agregar_entrada
            ep_val = 'ON' if getattr(self, 'ep_activa', False) else 'OFF'
            agregar_entrada(
                ronda=entry.get('ronda', '?'),
                rango=entry.get('rango', '?'),
                modo=entry.get('modo', '?'),
                pnl=entry.get('pnl', 0),
                acierto=entry.get('acierto', False),
                dif=entry.get('dif', 0),
                prevision=entry.get('prevision', 'N/A'),
                winner=entry.get('ganador', '---'),
                balance=entry.get('balance', 0),
                estrategia=entry.get('estrategia', '---'),
                mult=entry.get('mult'),
                ep=ep_val,
            )
        except Exception as e:
            print(f"Error guardando historial: {e}")

    def _cargar_stats_rangos_desde_archivo(self):
        ruta = Path(__file__).parent / "reconstructor_data_AI.txt"
        if not ruta.exists():
            return
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                lineas = f.readlines()
            import re
            stats = {}
            for linea in lineas:
                linea = linea.strip()
                if not linea:
                    continue

                rango = modo = None
                ganada = False

                # Formato nuevo: RANGO: 5-10 | MODO: DIRECTO | ACIERTO: True
                if 'RANGO:' in linea and 'MODO:' in linea and 'ACIERTO:' in linea:
                    m_r = re.search(r'RANGO:\s*([0-9+\-]+)', linea)
                    m_m = re.search(r'MODO:\s*(DIRECTO|INVERSO)', linea)
                    m_a = re.search(r'ACIERTO:\s*(True|False)', linea)
                    if m_r and m_m:
                        rango = m_r.group(1)
                        modo = m_m.group(1)
                        ganada = m_a.group(1) == 'True' if m_a else False
                # Formato antiguo: [*] RESULTADO: ... | Rango: ... | Modo: ...
                elif 'RESULTADO:' in linea:
                    m_rango = re.search(r'Rango:\s*([0-9+\-]+)', linea)
                    if m_rango:
                        rango = m_rango.group(1)
                    else:
                        continue
                    if 'Modo: DIRECTO' in linea:
                        modo = 'DIRECTO'
                    elif 'Modo: INVERSO' in linea:
                        modo = 'INVERSO'
                    ganada = 'MayorGana: True' in linea
                else:
                    continue

                if not rango:
                    continue

                if rango not in stats:
                    stats[rango] = {'DIRECTO':{'ops':0,'ganadas':0,'perdidas':0},
                                    'INVERSO':{'ops':0,'ganadas':0,'perdidas':0},
                                    'SKIP': 0}

                if modo not in ('DIRECTO', 'INVERSO'):
                    stats[rango]['SKIP'] += 1
                    continue

                stats[rango][modo]['ops'] += 1
                if ganada:
                    stats[rango][modo]['ganadas'] += 1
                else:
                    stats[rango][modo]['perdidas'] += 1

            if stats:
                self.stats_rangos.clear()
                self.stats_rangos.update(stats)
        except:
            pass

    def _cargar_ventana_rangos(self, ventana: int = 50):
        import re
        ruta = Path(__file__).parent / "reconstructor_data_AI.txt"
        if not ruta.exists():
            return
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                lineas = f.readlines()
            todas = []
            for linea in lineas:
                linea = linea.strip()
                if not linea:
                    continue

                rango = modo = None
                ganada = 0

                # Formato nuevo
                if 'RANGO:' in linea and 'MODO:' in linea and 'ACIERTO:' in linea:
                    m_r = re.search(r'RANGO:\s*([0-9+\-]+)', linea)
                    m_m = re.search(r'MODO:\s*(DIRECTO|INVERSO)', linea)
                    m_a = re.search(r'ACIERTO:\s*(True|False)', linea)
                    if m_r and m_m:
                        rango = m_r.group(1)
                        modo = m_m.group(1)
                        ganada = 1 if (m_a and m_a.group(1) == 'True') else 0
                # Formato antiguo
                elif 'RESULTADO:' in linea:
                    m_rango = re.search(r'Rango:\s*([0-9+\-]+)', linea)
                    if m_rango:
                        rango = m_rango.group(1)
                    else:
                        continue
                    if 'Modo: DIRECTO' in linea:
                        modo = 'DIRECTO'
                    elif 'Modo: INVERSO' in linea:
                        modo = 'INVERSO'
                    else:
                        continue
                    ganada = 1 if 'MayorGana: True' in linea else 0
                else:
                    continue

                if rango and modo:
                    todas.append((rango, modo, ganada))
            self.ventana_rangos = {}
            for rango, modo, ganada in todas:
                if rango not in self.ventana_rangos:
                    self.ventana_rangos[rango] = {}
                if modo not in self.ventana_rangos[rango]:
                    self.ventana_rangos[rango][modo] = deque(maxlen=ventana)
                self.ventana_rangos[rango][modo].append(ganada)
        except:
            pass

    def actualizar_ventana(self, rango: str, modo: str, ganada: bool, ventana: int = 50):
        if rango not in self.ventana_rangos:
            self.ventana_rangos[rango] = {}
        if modo not in self.ventana_rangos[rango]:
            self.ventana_rangos[rango][modo] = deque(maxlen=ventana)
        self.ventana_rangos[rango][modo].append(1 if ganada else 0)
        self._calcular_mejor_modo_por_rango()

    def _calcular_mejor_modo_por_rango(self):
        """Para cada rango determina qué modo gana más históricamente."""
        MIN_OPS_UMBRAL = 10
        self.mejor_modo_rangos = {}
        for rango, modos in self.stats_rangos.items():
            d = modos.get('DIRECTO', {})
            i = modos.get('INVERSO', {})
            d_ops = d.get('ops', 0) if isinstance(d, dict) else 0
            i_ops = i.get('ops', 0) if isinstance(i, dict) else 0
            d_wr  = d['ganadas'] / d_ops * 100 if d_ops >= MIN_OPS_UMBRAL else 0
            i_wr  = i['ganadas'] / i_ops * 100 if i_ops >= MIN_OPS_UMBRAL else 0
            mejor_wr   = max(d_wr, i_wr)
            mejor_modo = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
            if mejor_wr >= EP_UMBRAL_ESTADO:
                self.mejor_modo_rangos[rango] = (mejor_modo, mejor_wr)

    def _cargar_historial_desde_archivo(self):
        ruta = Path("reconstructor_data_AI.txt")
        if not ruta.exists():
            return
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                lineas = f.readlines()
            historial_temp = []
            for linea in lineas:
                linea = linea.strip()
                if '*] RESULTADO:' in linea:
                    if 'MayorGana: True' in linea:
                        historial_temp.append(1)
                    elif 'MayorGana: False' in linea:
                        historial_temp.append(0)
            self.historial_mayor_gana = historial_temp[-self.config.VENTANA_RACHA:]
        except:
            pass

    def _cargar_balance(self):
        ruta = Path(self.config.ARCHIVO_BALANCE)
        if ruta.exists():
            try:
                with open(ruta, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.balance_acumulado = float(data.get('balance', 0.0))
                    h = data.get('historial', [])
                    if isinstance(h, list):
                        self.historial_mayor_gana = h[-self.config.VENTANA_RACHA:]
                    self.historial_todo = data.get('historial_todo', [])
            except:
                pass

    def _guardar_balance(self):
        try:
            with open(self.config.ARCHIVO_BALANCE, 'w', encoding='utf-8') as f:
                json.dump({'balance': round(self.balance_acumulado, 2),
                         'historial': self.historial_mayor_gana,
                         'historial_todo': self.historial_todo,
                         'timestamp': datetime.now().isoformat()}, f, indent=2)
        except:
            pass

    def _calcular_racha(self) -> float:
        if self.historial_mayor_gana:
            return (sum(self.historial_mayor_gana) / len(self.historial_mayor_gana)) * 100
        return 50.0

    def actualizar_historial(self, mayor_gana: bool):
        self.historial_mayor_gana.append(1 if mayor_gana else 0)
        if len(self.historial_mayor_gana) > self.config.VENTANA_RACHA:
            self.historial_mayor_gana.pop(0)

    def agregar_historial_todo(self, ronda, rango, modo, pnl, acierto,
                               dif=0.0, prevision='N/A', winner='---', balance=0.0, estrategia='---', mult=None):
        entrada = {
            'ronda': ronda,
            'rango': rango,
            'modo': modo,
            'pnl': round(pnl, 2),
            'acierto': acierto,
            'dif': dif,
            'prevision': prevision,
            'ganador': winner,
            'balance': round(balance, 2),
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'estrategia': estrategia,
            'mult': mult,
        }
        self.historial_todo.append(entrada)

    def ejecutar(self, datos: DatosTick29, ganador_real: str) -> ResultadoAnalisis:
        if not datos.validar():
            raise ValueError("Datos inválidos")

        dif = round(abs(datos.p_azul - datos.p_rojo), 2)

        if   dif <  5:  rango_str = "0-5"
        elif dif < 10:  rango_str = "5-10"
        elif dif < 15:  rango_str = "10-15"
        elif dif < 20:  rango_str = "15-20"
        elif dif < 25:  rango_str = "20-25"
        elif dif < 30:  rango_str = "25-30"
        elif dif < 35:  rango_str = "30-35"
        elif dif < 40:  rango_str = "35-40"
        elif dif < 45:  rango_str = "40-45"
        elif dif < 50:  rango_str = "45-50"
        else:           rango_str = "+50"

        if datos.v_azul > datos.v_rojo:
            bando_mayor = "azul"
        elif datos.v_rojo > datos.v_azul:
            bando_mayor = "rojo"
        else:
            bando_mayor = "azul" if datos.p_azul > datos.p_rojo else "rojo"

        racha_10r = self._calcular_racha()

        # Lógica normal (DIRECTO/INVERSO/SKIP)
        # Rangos excluidos por datos historicos: 35-40 en DIRECTO pierde, 40-45 en INVERSO pierde
        RANGOS_EXCLUIR_DIRECTO = {"35-40"}
        RANGOS_EXCLUIR_INVERSO = {"40-45"}

        modo = ModoApuesta.SKIP
        acierto = False
        pnl = 0.0
        forced = False

        # Modo por defecto DIRECTO — el reconstructor decide DIR/INV en _calcular_y_apostar
        modo = ModoApuesta.DIRECTO
        if ganador_real:
            acierto = (bando_mayor == ganador_real)
            pnl = self.config.PNL_ACIERTO if acierto else self.config.PNL_FALLO

        if modo == ModoApuesta.DIRECTO:
            prevision = bando_mayor.capitalize()
        elif modo == ModoApuesta.INVERSO:
            prevision = "Rojo" if bando_mayor == "azul" else "Azul"
        else:
            prevision = "N/A"

        return ResultadoAnalisis(
            rango=rango_str, dif=dif, racha=round(racha_10r, 1),
            modo=modo, pnl=round(pnl, 2), acierto=acierto,
            prevision=prevision, mayor_bando=bando_mayor,
            forced=forced
        )


# ============================================================
# GOOGLE SHEETS
# ============================================================

class GestorGoogleSheets:
    def __init__(self, config: Config, logger):
        self.config = config
        self.logger = logger
        self.sheet = None
        self._conectar()

    def _conectar(self):
        try:
            scope = ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self.config.ARCHIVO_CREDENCIALES, scope)
            cliente = gspread.authorize(creds)
            self.sheet = cliente.open(self.config.NOMBRE_HOJA).worksheet(
                self.config.NOMBRE_PESTANA)
            self.logger.info("✅ Google Sheets conectado")
        except Exception as e:
            self.logger.error(f"❌ Sheets: {e}")
            self.sheet = None

    def esta_conectado(self): return self.sheet is not None

    def buscar_fila_libre(self) -> int:
        if not self.sheet: return self.config.FILA_INICIO
        try:
            col_c = self.sheet.col_values(3)
            row = self.config.FILA_INICIO
            while row <= len(col_c) + 1:
                if row > len(col_c): return row
                if str(col_c[row-1]).strip() == "": return row
                row += 1
            return row
        except:
            return self.config.FILA_INICIO

    def registrar_ronda(self, session_id, r_id, resultado, datos, ts,
                        balance_acumulado=0.0, winner_real="", winrate=0,
                        mult=None, confianza='---'):
        if not self.sheet or not resultado: return None
        if resultado.modo == ModoApuesta.SKIP:       res_txt = "SKIP"
        elif resultado.acierto:                       res_txt = "GANADA"
        else:                                         res_txt = "PERDIDA"
        prev_e = "⚪" if resultado.modo == ModoApuesta.SKIP else (
                 "🔵" if resultado.prevision.lower() in ("azul","blue") else "🔴")
        gan_e  = "🔵" if winner_real.lower() in ("azul","blue") else "🔴"
        umbral = (self.config.UMBRAL_DIRECTO if resultado.modo == ModoApuesta.DIRECTO else
                  self.config.UMBRAL_INVERSO if resultado.modo == ModoApuesta.INVERSO else "SKIP")
        mult_txt = f"x{mult}" if mult is not None else ""

        try:
            target = self.buscar_fila_libre()
            fila_completa = [
                ts, session_id, r_id, resultado.rango,
                f"{datos.p_azul}%", f"{datos.p_rojo}%",
                resultado.dif, prev_e, gan_e, umbral,
                round(balance_acumulado, 2), resultado.modo.value,
                res_txt, f"{winrate}%", f"{resultado.racha:.1f}%",
                mult_txt, confianza,
            ]
            self.sheet.update(range_name=f"A{target}:Q{target}", values=[fila_completa])
            return target
        except Exception as e:
            self.logger.error(f"❌ Sheets write: {e}")
            return None

    def leer_variables(self):
        try:
            ws = self.sheet.spreadsheet.worksheet("Variables")
            return {str(f[0]).strip().upper(): str(f[1]).strip()
                    for f in ws.get_all_values() if len(f) >= 2}
        except:
            return {}

    def escribir_variable(self, clave: str, valor: str):
        try:
            ws = self.sheet.spreadsheet.worksheet("Variables")
            datos = ws.get_all_values()
            for i, fila in enumerate(datos, start=1):
                if len(fila) >= 1 and str(fila[0]).strip().upper() == clave.upper():
                    ws.update(range_name=f"B{i}", values=[[valor]])
                    return
        except Exception as e:
            print(f"[Sheets] Error escribiendo variable {clave}: {e}")

    def obtener_multiplicador(self, rango: str) -> int:
        try:
            ws = self.sheet.spreadsheet.worksheet("Apuestas")
            for f in ws.get_all_values():
                if len(f) >= 2 and str(f[0]).strip().upper() == rango.upper():
                    return int(f[1])
        except:
            pass
        return 1

    def guardar_rangos(self, datos: list):
        """
        Guarda análisis de rangos en pestaña RANGOS.
        Columnas: A=inicio, B=fin, C=modo, D=pnl, E=ratio, F=wr, G=ops
        """
        try:
            # Verificar si la pestaña existe, si no crearla
            try:
                ws = self.sheet.spreadsheet.worksheet("RANGOS")
            except:
                ws = self.sheet.spreadsheet.add_worksheet("RANGOS", 100, 10)
                self.logger.info("📊 Creada pestaña RANGOS")
            
            ws.clear()
            headers = [["INICIO", "FIN", "MODO", "PNL", "RATIO", "WR%", "OPS"]]
            ws.update('A1:G1', headers)
            if datos:
                ws.update('A2:G' + str(len(datos) + 1), datos)
            self.logger.info(f"✅ Rangos guardados en Sheets: {len(datos)} filas")
        except Exception as e:
            self.logger.error(f"❌ Error guardando rangos: {e}")

    def leer_mult_rangos(self) -> list:
        """Lee de la pestaña Apuestas los rangos de confianza → multiplicador.
        Formato esperado en la hoja: col A = umbral mínimo (número), col B = multiplicador.
        Ejemplo:  60 | 1 / 70 | 2 / 80 | 3 / 90 | 4
        Devuelve lista [(umbral, mult), ...] ordenada de mayor a menor umbral.
        """
        try:
            ws = self.sheet.spreadsheet.worksheet("Apuestas")
            resultado = []
            for f in ws.get_all_values():
                if len(f) >= 2:
                    try:
                        umbral = float(str(f[0]).strip().replace(',', '.'))
                        mult   = int(str(f[1]).strip())
                        resultado.append((umbral, mult))
                    except (ValueError, TypeError):
                        pass  # fila no numérica (cabecera, rango, etc.)
            if resultado:
                resultado.sort(key=lambda x: x[0], reverse=True)
                return resultado
        except Exception:
            pass
        # Fallback hardcoded
        return [(90, 7), (85, 6), (80, 5), (75, 4), (70, 3), (65, 2), (60, 1)]


# ============================================================
# WEBSOCKET CLIENT
# ============================================================

class WebSocketClient:
    def __init__(self, config: Config, logger):
        self.config = config
        self.logger = logger
        self.ws = None

    async def conectar(self) -> bool:
        for i in range(self.config.WS_MAX_RETRIES):
            try:
                self.logger.info(f"🔗 Conectando (intento {i+1})...")
                self.ws = await websockets.connect(
                    self.config.WS_URL, origin=self.config.WS_ORIGIN,
                    ping_interval=self.config.WS_PING_INTERVAL, ping_timeout=10)
                uid = str(random.randint(1000, 9999))
                await self.ws.send(json.dumps({'type': 'bind', 'uid': uid}))
                self.logger.info(f"📡 Conectado. UID: {uid}")
                return True
            except Exception as e:
                self.logger.warning(f"⚠️ Conexión fallida: {e}")
                await asyncio.sleep(self.config.WS_RECONNECT_DELAY)
        return False

    async def desconectar(self):
        if self.ws:
            try: await self.ws.close()
            except: pass
            finally: self.ws = None

    async def recibir(self):
        if not self.ws: return None
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
            return json.loads(msg)
        except asyncio.TimeoutError: return None
        except: return None

    async def enviar(self, data):
        if not self.ws: return False
        try:
            await self.ws.send(json.dumps(data))
            return True
        except: return False


# ============================================================
# DASHBOARD TKINTER — FUTURISTA (COMPLETO)
# ============================================================

class DashboardFuturista:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ACERTADOR SENIOR PRO — DASHBOARD")
        self.root.configure(bg=C['bg'])
        self.root.geometry("1400x860")
        self.root.resizable(True, True)

        self.estado = {
            'ronda': '---',
            'balance': 0.0,
            'racha': 50.0,
            'modo': 'ESPERANDO',
            'dif': 0.0,
            'rango': '---',
            'umbral': '---',
            'p_azul': 0.0,
            'p_rojo': 0.0,
            'v_azul': 0.0,
            'v_rojo': 0.0,
            'prevision': '---',
            'ganador': '---',
            'tiempo_total': 50,
            'tiempo_restante': 0,
            'tiempo_activo': False,
            'sheets': False,
            'ws': False,
            'hacer_apuesta': False,
            'invertir_apuesta': False,
            'aciertos': 0,
            'fallos': 0,
            'skips': 0,
            'inicio_sesion': None,
            '_balance': 0.0,
            'inicio_programa': datetime.now(),
            'stats_umbrales': {},
        }
        self.historico = None  # se asigna en _panel_historico tras crear HistorialWidget
        self._on_reset_balance = None
        self._cond_vars = {
            'skip_sin_datos':    tk.BooleanVar(value=True),
            'modo_reconstructor':tk.BooleanVar(value=True),
            'skip_conf_baja':    tk.BooleanVar(value=True),
            'explorar_inverso':  tk.BooleanVar(value=True),
        }
        self._log_counter = 0
        self._rango_activo = None
        self.ep_activa = False
        self.mult_activo = False
        self.log_colores = False
        self.rangos_activo = True  # Exportar rangos a Sheets por defecto ON
        self.explorar_inverso = False
        self.explorar_cada = 5  # cada N apuestas fuerza una en INVERSO
        self._heat_fig = None
        self._heat_resaltar = None   # (rango, modo) a resaltar tras drawed
        self._heat_resaltar_acierto = None
        self.stats_rangos = {}
        rangos_posibles = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]
        for r in rangos_posibles:
            self.stats_rangos[r] = {'DIRECTO':{'ops':0,'ganadas':0,'perdidas':0},
                                    'INVERSO':{'ops':0,'ganadas':0,'perdidas':0},
                                    'SKIP':0}
        self._rangos_json_ts = 0.0
        self._stats_conf = {}
        self._stats_conf_ts = 0.0
        self._wr_alarm_50_disparada = False
        self._cargar_config_ventana()
        self._cargar_stats_umbrales_desde_archivo()
        self._construir_ui()
        self.hist_widget.cargar_desde_archivo()
        self._cargar_stats_desde_historial()
        self._pulso_activo = False
        self._actualizar_loop()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._cargar_stats_rangos_desde_archivo()
        self._cargar_stats_conf_desde_archivo()
        self.actualizar_rangos()
        self._actualizar_umbrales()
        self._actualizar_prob()
        self._actualizar_conf()
        self.root.after(30_000, self._loop_conf)
        
        # Exportar rangos a Sheets al inicio si está activado
        if self.rangos_activo:
            self.root.after(0, self._exportar_rangos_a_sheets)

    def _on_closing(self):
        self._guardar_config_ventana()
        self._guardar_log_sesion()
        self.root.destroy()
        sys.exit(0)

    def _cargar_stats_desde_historial(self):
        """Inicializa aciertos/fallos/skips desde el historial cargado y anuncia por voz."""
        ac = fa = sk = 0
        ultimo_balance = None
        for entry in self.hist_widget.historico:
            modo = entry.get('modo', 'SKIP')
            acierto = entry.get('acierto', None)
            if modo == 'SKIP' or acierto is None:
                sk += 1
            elif acierto:
                ac += 1
            else:
                fa += 1

        # Balance más reciente: primer entry del deque (índice 0 = más nuevo)
        for entry in self.hist_widget.historico:
            bal = entry.get('balance')
            if isinstance(bal, (int, float)):
                ultimo_balance = bal
                break

        self.estado['aciertos'] = ac
        self.estado['fallos']   = fa
        self.estado['skips']    = sk

        # Actualizar labels si ya están creados
        if hasattr(self, '_st_aciertos'):
            self._st_aciertos.config(text=str(ac))
            self._st_fallos.config(text=str(fa))
            self._st_skips.config(text=str(sk))

        total = ac + fa
        # WR ajustado: gana 0.9x, pierde 1x → break-even en 52.63% → aquí break-even = 50%
        wr = round(ac * 0.9 / (ac * 0.9 + fa) * 100, 1) if total > 0 else 0.0
        bal_txt = f"{ultimo_balance:+.2f} euros" if ultimo_balance is not None else "sin datos"
        wr_txt  = f"winrate ajustado {wr} por ciento"

        def _anunciar():
            import subprocess
            for msg in [
                f"{ac} aciertos",
                f"{fa} fallos",
                f"{sk} skips",
                wr_txt,
                f"balance {bal_txt}",
            ]:
                subprocess.run([r'c:\Python\voice.exe', msg], capture_output=True, timeout=8)

        import threading
        threading.Thread(target=_anunciar, daemon=True).start()

    def _guardar_log_sesion(self):
        try:
            contenido = self._log_text.get('1.0', 'end').strip()
            if not contenido:
                return
            carpeta = Path(__file__).parent / "logs"
            carpeta.mkdir(exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            ruta = carpeta / f"log_sesion_{ts}.txt"
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write(contenido)
        except Exception as e:
            print(f"Error guardando log: {e}")

    def _abrir_analizador(self):
        archivo = "reconstructor_data_AI.txt"
        try:
            subprocess.Popen(
                ['py', 'analizador_graficas.py', archivo],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        except Exception as e:
            self.log(f"⚠️ No se pudo abrir analizador: {e}", 'warn')

    def _toggle_explorar(self):
        self.explorar_inverso = not self.explorar_inverso
        if self.explorar_inverso:
            self._btn_explorar.config(text=f"EXPLORAR: 1/{self.explorar_cada}", bg='#1A001A', fg='#FF88FF')
        else:
            self._btn_explorar.config(text="EXPLORAR: OFF", bg='#1A0A1A', fg=C['muted'])

    def _toggle_log_colores(self):
        self.log_colores = not self.log_colores
        if self.log_colores:
            self._btn_log_colores.config(text="LOG COLOR: ON", bg='#0A2218', fg=C['accent2'])
        else:
            self._btn_log_colores.config(text="LOG COLOR: OFF", bg='#1A0A1A', fg=C['muted'])

    def _toggle_rangos_export(self):
        self.rangos_activo = not self.rangos_activo
        if self.rangos_activo:
            self._btn_rangos.config(text="RANGOS: ON", bg='#0A2218', fg=C['accent2'])
            self.log("📊 RANGOS: Activado", 'ok')
            self._exportar_rangos_a_sheets()
        else:
            self._btn_rangos.config(text="RANGOS: OFF", bg='#1A0A1A', fg=C['muted'])
            self.log("📊 RANGOS: Desactivado", 'dim')

    def _exportar_rangos_a_sheets(self):
        try:
            from umbral_core import umbral_obtener_datos_rangos
            
            # Verificar que acer exista
            if not hasattr(self, 'acer') or not self.acer:
                self.log("⚠️ Acertador no conectado - reintentando...", 'warn')
                self.root.after(2000, self._exportar_rangos_a_sheets)
                return
            
            # Obtener operaciones
            if not hasattr(self.acer.analizador, 'ops_history'):
                self.log("⚠️ Analizador sin ops_history - reintentando...", 'warn')
                self.root.after(2000, self._exportar_rangos_a_sheets)
                return
            
            ops = self.acer.analizador.ops_history
            self.log(f"📊 Analizando {len(ops)} operaciones...", 'dim')
            
            if not ops:
                self.log("⚠️ Sin operaciones para analizar", 'warn')
                return
            
            datos = umbral_obtener_datos_rangos(ops)
            self.log(f"📊 Generados {len(datos)} rangos para Sheets", 'dim')
            
            if self.acer.sheets.esta_conectado():
                self.acer.sheets.guardar_rangos(datos)
                self.log(f"✅ Rangos exportados a Sheets: {len(datos)} filas", 'ok')
            else:
                self.log("⚠️ Sheets no conectado", 'warn')
        except Exception as e:
            import traceback
            self.log(f"⚠️ Error exportar rangos: {e}", 'err')
            self.log(f"   Trace: {traceback.format_exc()[:200]}", 'err')

    def _toggle_mult(self):
        self.mult_activo = not self.mult_activo
        if self.mult_activo:
            self._btn_mult.config(text="MULT: ON", bg='#0A2218', fg=C['accent2'])
            subprocess.Popen([r'c:\Python\voice.exe', 'Multiplicador activado'])
        else:
            self._btn_mult.config(text="MULT: OFF", bg='#1A0A1A', fg=C['muted'])
            subprocess.Popen([r'c:\Python\voice.exe', 'Multiplicador desactivado'])

    def _toggle_ep(self):
        self.ep_activa = not self.ep_activa
        if self.ep_activa:
            self._btn_ep_toggle.config(text="EP: ON", bg='#0A2218', fg=C['accent2'])
            subprocess.Popen([r'c:\Python\voice.exe', 'EP activado'])
        else:
            self._btn_ep_toggle.config(text="EP: OFF", bg='#1A1500', fg=C['muted'])
            subprocess.Popen([r'c:\Python\voice.exe', 'EP desactivado'])

    def _toggle_invertir(self):
        self.estado['invertir_apuesta'] = not self.estado['invertir_apuesta']
        if self.estado['invertir_apuesta']:
            self._btn_invertir.config(text="🔄 INVERTIR: ON", bg='#2A0000', fg=C['accent3'])
            subprocess.Popen([r'c:\Python\voice.exe', 'Invertir activado'])
        else:
            self._btn_invertir.config(text="🔄 INVERTIR: OFF", bg='#1A0A0A', fg=C['muted'])
            subprocess.Popen([r'c:\Python\voice.exe', 'Invertir desactivado'])

    def _toggle_hacer_apuesta(self):
        self.estado['hacer_apuesta'] = not self.estado['hacer_apuesta']
        activo = self.estado['hacer_apuesta']
        if activo:
            self._btn_apuesta_toggle.config(text="APUESTAS: SI", bg='#0A2218', fg=C['accent2'])
        else:
            self._btn_apuesta_toggle.config(text="APUESTAS: NO", bg='#2A0808', fg=C['accent3'])
        # Escribir en Sheets si hay referencia
        _sheets = getattr(self, '_sheets_ref', None)
        if _sheets:
            import threading as _thr
            _thr.Thread(target=_sheets.escribir_variable,
                        args=('HACER_APUESTA', 'SI' if activo else 'NO'),
                        daemon=True).start()
        # Voz
        texto = 'apuestas activadas' if activo else 'apuestas desactivadas'
        try:
            subprocess.Popen([r'c:\Python\voice.exe', texto])
        except Exception:
            pass

    def _ventana_condiciones(self):
        win = tk.Toplevel(self.root)
        win.title("CONDICIONES PRE-APUESTA")
        win.configure(bg=C['bg'])
        win.geometry("420x220")
        win.resizable(False, False)

        tk.Label(win, text="CONDICIONES PRE-APUESTA (RECONSTRUCTOR)", font=('Consolas', 11, 'bold'),
                 bg=C['bg'], fg=C['accent']).pack(pady=(10, 5))

        tk.Frame(win, bg=C['warn'], height=1).pack(fill='x', padx=10, pady=(5, 2))

        conds = [
            ('skip_sin_datos',     'SKIP si rango < 3 ops en reconstructor'),
            ('modo_reconstructor', 'Elegir DIR/INV segun reconstructor'),
            ('skip_conf_baja',     'SKIP si confianza < UMBRAL'),
            ('explorar_inverso',   'Forzar INVERSO cada N apuestas'),
        ]
        for key, desc in conds:
            tk.Checkbutton(win, text=desc, variable=self._cond_vars[key],
                           font=('Consolas', 9), bg=C['bg'], fg=C['text'],
                           selectcolor=C['panel'], activebackground=C['bg'],
                           activeforeground=C['text']).pack(anchor='w', padx=20, pady=2)

    def _abrir_estrategia_perfecta(self):
        try:
            subprocess.Popen(
                ['py', 'estrategia_perfecta.py'],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        except Exception as e:
            self.log(f"⚠️ No se pudo abrir estrategia_perfecta: {e}", 'warn')

    def _abrir_mejor_rango(self):
        import threading
        def _ejecutar():
            try:
                import sys as _sys
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "analizador_mejor_rango", "analizador_mejor_rango.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                resultado = mod.parsear_archivo("reconstructor_data_AI.txt")
                if resultado.lineas_procesadas == 0:
                    self.root.after(0, lambda: self._mostrar_popup_rango(
                        "Sin datos", "No se encontraron registros en reconstructor_data_AI.txt", ""))
                    return

                mejor_dir  = resultado.mejor_rango_directo()
                mejor_inv  = resultado.mejor_rango_inverso()
                mejor_gl   = resultado.mejor_rango_global()
                ranking    = resultado.ranking_rentabilidad()
                tot        = resultado.totales()

                # Construir texto del popup
                lineas = []
                lineas.append(f"📊  {resultado.lineas_procesadas} registros analizados\n")

                lineas.append("─" * 42)
                lineas.append("  RANKING DE RANGOS (mejor saldo)")
                lineas.append("─" * 42)
                lineas.append(f"  {'POS':<4} {'RANGO':<8} {'MODO':<10} {'OPS':>5} {'WR':>7} {'SALDO':>8}")
                for pos, s in enumerate(ranking[:8], 1):
                    modo = s.mejor_modo
                    if modo == "DIRECTO":
                        ops, wr, sal = s.directo_ops, s.directo_winrate, s.directo_saldo
                    else:
                        ops, wr, sal = s.inverso_ops, s.inverso_winrate, s.inverso_saldo
                    marker = " ◄" if pos == 1 else ""
                    lineas.append(f"  {pos:<4} {s.rango:<8} {modo:<10} {ops:>5} {wr:>6.1f}% {sal:>+7.2f}€{marker}")

                lineas.append("")
                lineas.append("─" * 42)
                lineas.append("  RECOMENDACIÓN FINAL")
                lineas.append("─" * 42)
                if mejor_dir:
                    lineas.append(f"  MEJOR DIRECTO : [{mejor_dir.rango}]  "
                                  f"WR {mejor_dir.directo_winrate:.1f}%  "
                                  f"Saldo {mejor_dir.directo_saldo:+.2f}€")
                if mejor_inv:
                    lineas.append(f"  MEJOR INVERSO : [{mejor_inv.rango}]  "
                                  f"WR {mejor_inv.inverso_winrate:.1f}%  "
                                  f"Saldo {mejor_inv.inverso_saldo:+.2f}€")
                if mejor_gl:
                    lineas.append("")
                    lineas.append(f"  ★ MEJOR GLOBAL: [{mejor_gl.rango}] en {mejor_gl.mejor_modo}")
                    lineas.append(f"    Saldo: {mejor_gl.mejor_saldo:+.2f}€  "
                                  f"Rentabilidad/op: {mejor_gl.rentabilidad_pct:+.1f}%")
                    if mejor_gl.excluido():
                        lineas.append(f"    ⚠ {mejor_gl.excluido()}")

                lineas.append("")
                lineas.append("─" * 42)
                lineas.append("  TOTALES GLOBALES")
                lineas.append("─" * 42)
                d, i = tot["directo"], tot["inverso"]
                lineas.append(f"  DIRECTO  {d['ops']:>5} ops  "
                              f"{d['ganadas']:>4}G / {d['perdidas']:>4}P  {d['saldo']:>+8.2f}€")
                lineas.append(f"  INVERSO  {i['ops']:>5} ops  "
                              f"{i['ganadas']:>4}G / {i['perdidas']:>4}P  {i['saldo']:>+8.2f}€")
                lineas.append(f"  SKIPS    {tot['skips']:>5}")
                saldo_total = d['saldo'] + i['saldo']
                lineas.append(f"\n  SALDO COMBINADO: {saldo_total:>+.2f}€")

                texto = "\n".join(lineas)

                # Texto de voz
                voz = ""
                if mejor_gl:
                    voz = (f"Análisis completado. "
                           f"El mejor rango global es {mejor_gl.rango}, "
                           f"en modo {mejor_gl.mejor_modo}, "
                           f"con un saldo de {mejor_gl.mejor_saldo:.2f} euros "
                           f"y una rentabilidad de {mejor_gl.rentabilidad_pct:.1f} por ciento por operación.")
                    if mejor_dir:
                        voz += f" Mejor directo: rango {mejor_dir.rango}."
                    if mejor_inv:
                        voz += f" Mejor inverso: rango {mejor_inv.rango}."

                titulo = f"🏆 Mejor Rango — {resultado.lineas_procesadas} registros"
                self.root.after(0, lambda t=titulo, tx=texto, v=voz:
                    self._mostrar_popup_rango(t, tx, v))

            except Exception as e:
                self.root.after(0, lambda err=str(e): self._mostrar_popup_rango(
                    "Error", f"No se pudo ejecutar el análisis:\n{err}", ""))

        threading.Thread(target=_ejecutar, daemon=True).start()

    def _mostrar_popup_rango(self, titulo, texto, voz):
        popup = tk.Toplevel(self.root)
        popup.title(titulo)
        popup.configure(bg=C['bg'])
        popup.resizable(True, True)
        popup.geometry("560x540")
        popup.grab_set()  # modal

        # Header
        hf = tk.Frame(popup, bg='#020810')
        hf.pack(fill='x')
        tk.Frame(hf, bg=C['warn'], height=2).pack(fill='x')
        tk.Label(hf, text=f"  🏆 {titulo}", font=('Consolas', 11, 'bold'),
                 bg='#020810', fg=C['warn']).pack(anchor='w', padx=8, pady=8)

        # Texto con scrollbar
        frame_txt = tk.Frame(popup, bg=C['bg'])
        frame_txt.pack(fill='both', expand=True, padx=10, pady=6)
        sb = tk.Scrollbar(frame_txt)
        sb.pack(side='right', fill='y')
        txt = tk.Text(frame_txt, font=('Consolas', 11), bg=C['panel'], fg=C['text'],
                      relief='flat', bd=0, padx=12, pady=10,
                      yscrollcommand=sb.set, cursor='arrow')
        txt.insert('1.0', texto)
        txt.config(state='disabled')
        txt.pack(fill='both', expand=True)
        sb.config(command=txt.yview)

        # Botones
        bf = tk.Frame(popup, bg=C['bg'])
        bf.pack(fill='x', padx=10, pady=10)

        if voz:
            import re as _re
            limpio = _re.sub(r'[★◄─📊⚠☆▸►🏆]', '', voz).strip()

            def _hablar_voz():
                import threading
                def _run():
                    try:
                        subprocess.run([r'c:\Python\voice.exe', limpio],
                                       capture_output=True, timeout=60)
                    except Exception:
                        pass
                threading.Thread(target=_run, daemon=True).start()

            tk.Button(bf, text="🔊 ESCUCHAR", font=('Consolas', 10, 'bold'),
                      bg=C['panel'], fg=C['warn'], relief='raised', bd=1,
                      command=_hablar_voz).pack(side='left', padx=4)

            # Reproducir automaticamente al abrir
            popup.after(300, _hablar_voz)

        tk.Button(bf, text="✓ OK", font=('Consolas', 10, 'bold'),
                  bg=C['accent2'], fg=C['bg'], relief='raised', bd=1, width=10,
                  command=popup.destroy).pack(side='right', padx=4)

    def _cargar_config_ventana(self):
        config_file = "acertador_geometry.txt"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    geom = f.read().strip()
                    self.root.geometry(geom)
            except:
                pass

    def _guardar_config_ventana(self):
        config_file = "acertador_geometry.txt"
        with open(config_file, 'w') as f:
            f.write(self.root.geometry())

    def _cargar_stats_umbrales_desde_archivo(self):
        import json as _json
        ruta = Path(__file__).parent / "historial_rondas.txt"
        if not ruta.exists():
            return
        self.estado['stats_umbrales'] = {}
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                for linea in f:
                    if '{' not in linea:
                        continue
                    try:
                        datos = _json.loads(linea[linea.index('{'):])
                        modo = datos.get('modo', 'SKIP')
                        if not modo or modo == 'SKIP':
                            continue
                        rango = datos.get('rango', 'desconocido')
                        acierto = datos.get('acierto')
                        if acierto is None:
                            continue
                        if rango not in self.estado['stats_umbrales']:
                            self.estado['stats_umbrales'][rango] = {'ops': 0, 'ganadas': 0, 'perdidas': 0}
                        self.estado['stats_umbrales'][rango]['ops'] += 1
                        if acierto:
                            self.estado['stats_umbrales'][rango]['ganadas'] += 1
                        else:
                            self.estado['stats_umbrales'][rango]['perdidas'] += 1
                    except:
                        pass
        except:
            pass

    def _cargar_stats_rangos_desde_archivo(self):
        ruta = Path(__file__).parent / "stats_rangos.json"
        if not ruta.exists():
            return
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            if stats:
                self.stats_rangos.clear()
                self.stats_rangos.update(stats)
        except:
            pass

    def _cargar_stats_conf_desde_archivo(self):
        ruta = Path(__file__).parent / "stats_conf.json"
        if not ruta.exists():
            return
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                self._stats_conf = json.load(f)
        except:
            pass


    def _construir_ui(self):
        self._header()
        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # Sidebar izquierda con botones
        self._panel_sidebar(body)

        left = tk.Frame(body, bg=C['bg'])
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))

        self._panel_ronda(left)
        self._panel_barras(left)
        self._panel_barra_tiempo(left)
        self._panel_proyeccion(left)
        self._panel_grafica(left)
        self._panel_historico(left)

        right = tk.Frame(body, bg=C['bg'])
        right.pack(side='right', fill='both', padx=(6, 0), pady=0)
        right.configure(width=420)
        right.pack_propagate(False)

        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=0)
        right.grid_rowconfigure(3, weight=0)
        right.grid_rowconfigure(4, weight=0)
        right.grid_rowconfigure(5, weight=1)
        right.grid_columnconfigure(0, weight=1)

        p_stats = self._panel_stats(right)
        p_stats.grid(row=0, column=0, sticky='ew', pady=(0, 4))

        p_rangos = self._panel_rangos(right)
        p_rangos.grid(row=1, column=0, sticky='ew', pady=(0, 4))

        p_umbr = self._panel_umbr(right)
        p_umbr.grid(row=2, column=0, sticky='ew', pady=(0, 4))

        p_prob = self._panel_prob(right)
        p_prob.grid(row=3, column=0, sticky='ew', pady=(0, 4))

        p_conf = self._panel_conf(right)
        p_conf.grid(row=4, column=0, sticky='ew', pady=(0, 4))

        p_log = self._panel_log(right)
        p_log.grid(row=5, column=0, sticky='nsew')

    def _header(self):
        hf = tk.Frame(self.root, bg='#020810', height=42)
        hf.pack(fill='x')
        hf.pack_propagate(False)

        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x', side='top')

        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=16)

        tk.Label(inner, text="◈ ACERTADOR SENIOR PRO", font=('Consolas', 14, 'bold'),
                 bg='#020810', fg=C['accent']).pack(side='left', pady=8, padx=4)

        self._lbl_ws = tk.Label(inner, text="● WS", font=FONT_MONO_B,
                                bg='#020810', fg=C['muted'])
        self._lbl_ws.pack(side='right', padx=8, pady=8)
        self._lbl_historico = tk.Label(inner, text="● HIST", font=FONT_MONO_B,
                                       bg='#020810', fg=C['muted'])
        self._lbl_historico.pack(side='right', padx=4, pady=8)
        self._lbl_sheets = tk.Label(inner, text="● SHEETS", font=FONT_MONO_B,
                                    bg='#020810', fg=C['muted'])
        self._lbl_sheets.pack(side='right', padx=8, pady=8)
        self._lbl_apuesta = tk.Label(inner, text="● APUESTA", font=FONT_MONO_B,
                                     bg='#020810', fg=C['muted'])
        self._lbl_apuesta.pack(side='right', padx=8, pady=8)

        self._lbl_clock = tk.Label(inner, text="00:00:00", font=('Consolas', 11, 'bold'),
                                   bg='#020810', fg=C['muted'])
        self._lbl_clock.pack(side='right', padx=20, pady=8)

    def _panel_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=C['panel'], width=145,
                           highlightbackground=C['border'], highlightthickness=1)
        sidebar.pack(side='left', fill='y', padx=(0, 6))
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="CONTROLES", font=('Consolas', 9, 'bold'),
                 bg=C['panel'], fg=C['accent']).pack(pady=(8, 6))
        tk.Frame(sidebar, bg=C['border'], height=1).pack(fill='x', padx=4)

        bstyle = dict(font=('Consolas', 9, 'bold'), relief='raised', bd=1,
                      width=14, cursor='hand2')

        # ── Herramientas ──
        tk.Button(sidebar, text="▲ PEAK", bg=C['panel'], fg=C['accent'], **bstyle,
                  command=lambda: __import__('subprocess').run(["py", "winpos.py", "--move"])
                  ).pack(pady=(8, 2), padx=4)

        tk.Button(sidebar, text="GRAFICAS", bg=C['panel'], fg=C['accent2'], **bstyle,
                  command=self._abrir_analizador).pack(pady=2, padx=4)

        tk.Button(sidebar, text="MEJOR RANGO", bg=C['panel'], fg=C['warn'], **bstyle,
                  command=self._abrir_mejor_rango).pack(pady=2, padx=4)

        tk.Button(sidebar, text="ESTRATEGIA", bg=C['panel'], fg='#00D4FF', **bstyle,
                  command=self._abrir_estrategia_perfecta).pack(pady=2, padx=4)

        tk.Frame(sidebar, bg=C['border'], height=1).pack(fill='x', padx=4, pady=6)
        tk.Label(sidebar, text="TOGGLES", font=('Consolas', 8, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack()

        # ── Toggles ──
        self._btn_apuesta_toggle = tk.Button(sidebar, text="APUESTAS: NO",
                  bg='#2A0808', fg=C['accent3'], **bstyle,
                  command=self._toggle_hacer_apuesta)
        self._btn_apuesta_toggle.pack(pady=(4, 2), padx=4)

        self._btn_mult = tk.Button(sidebar, text="MULT: OFF",
                  bg='#1A0A1A', fg=C['muted'], **bstyle,
                  command=self._toggle_mult)
        self._btn_mult.pack(pady=2, padx=4)

        self._btn_ep_toggle = tk.Button(sidebar, text="EP: OFF",
                  bg='#1A1500', fg=C['muted'], **bstyle,
                  command=self._toggle_ep)
        self._btn_ep_toggle.pack(pady=2, padx=4)

        self._btn_invertir = tk.Button(sidebar, text="INVERTIR: OFF",
                  bg='#1A0A0A', fg=C['muted'], **bstyle,
                  command=self._toggle_invertir)
        self._btn_invertir.pack(pady=2, padx=4)

        self._btn_explorar = tk.Button(sidebar, text="EXPLORAR: OFF",
                  bg='#1A0A1A', fg=C['muted'], **bstyle,
                  command=self._toggle_explorar)
        self._btn_explorar.pack(pady=2, padx=4)

        self._btn_log_colores = tk.Button(sidebar, text="LOG COLOR: OFF",
                  bg='#1A0A1A', fg=C['muted'], **bstyle,
                  command=self._toggle_log_colores)
        self._btn_log_colores.pack(pady=2, padx=4)

        self._btn_rangos = tk.Button(sidebar, text="RANGOS: ON",
                  bg='#0A2218', fg=C['accent2'], **bstyle,
                  command=self._toggle_rangos_export)
        self._btn_rangos.pack(pady=(8, 2), padx=4)

        tk.Button(sidebar, text="COND", bg='#1A1A00', fg=C['warn'], **bstyle,
                  command=self._ventana_condiciones).pack(pady=2, padx=4)

    def _mk_panel(self, parent, titulo=None, pady=4, padx=0):
        f = tk.Frame(parent, bg=C['panel'],
                     highlightbackground=C['border'], highlightthickness=1)
        f.pack(fill='x', pady=pady, padx=padx)
        if titulo:
            tf = tk.Frame(f, bg=C['border'])
            tf.pack(fill='x')
            tk.Label(tf, text=f"  {titulo}", font=FONT_TITLE,
                     bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        inner = tk.Frame(f, bg=C['panel'])
        inner.pack(fill='both', expand=True, padx=8, pady=6)
        return inner

    def _panel_ronda(self, parent):
        inner = self._mk_panel(parent, "◈ RONDA EN CURSO")

        row1 = tk.Frame(inner, bg=C['panel'])
        row1.pack(fill='x', pady=2)

        col1 = tk.Frame(row1, bg=C['panel'])
        col1.pack(side='left', expand=True, fill='x')
        tk.Label(col1, text="RONDA", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_ronda = tk.Label(col1, text="---", font=('Consolas', 13, 'bold'),
                                   bg=C['panel'], fg=C['accent'])
        self._lbl_ronda.pack(anchor='w')

        col2 = tk.Frame(row1, bg=C['panel'])
        col2.pack(side='left', expand=True, fill='x')
        bal_header = tk.Frame(col2, bg=C['panel'])
        bal_header.pack(anchor='w', fill='x')
        tk.Label(bal_header, text="BALANCE", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(side='left')
        tk.Button(bal_header, text="RST", font=('Consolas', 7, 'bold'),
                  bg='#1A0A0A', fg='#FF4444', activebackground='#2A1A1A', activeforeground='#FF6666',
                  bd=0, padx=4, pady=0, cursor='hand2',
                  command=self._resetear_balance).pack(side='left', padx=(6, 0))
        self._lbl_balance = tk.Label(col2, text="0.00€", font=FONT_BIG,
                                     bg=C['panel'], fg=C['accent2'])
        self._lbl_balance.pack(anchor='w')

        col3 = tk.Frame(row1, bg=C['panel'])
        col3.pack(side='left', expand=True, fill='x')
        tk.Label(col3, text="MODO", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_modo = tk.Label(col3, text="ESPERANDO", font=('Consolas', 14, 'bold'),
                                  bg=C['panel'], fg=C['warn'])
        self._lbl_modo.pack(anchor='w')

        self._lbl_sesion = tk.Label(col3, text="Sesión: 00:00:00", font=FONT_SM,
                                    bg=C['panel'], fg=C['accent'])
        self._lbl_sesion.pack(anchor='w')

        col_mult = tk.Frame(row1, bg=C['panel'])
        col_mult.pack(side='left', expand=True, fill='x')
        tk.Label(col_mult, text="MULT", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_mult = tk.Label(col_mult, text="x1", font=('Consolas', 14, 'bold'),
                                  bg=C['panel'], fg=C['accent2'])
        self._lbl_mult.pack(anchor='w')

        # Columna UMBRAL a la derecha
        col4 = tk.Frame(row1, bg=C['panel'], padx=20)
        col4.pack(side='right', padx=(20, 0))
        tk.Label(col4, text="UMBRAL", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_umbral_apostar = tk.Label(col4, text="53.2%", font=('Consolas', 16, 'bold'),
                                            bg=C['panel'], fg=C['warn'])
        self._lbl_umbral_apostar.pack(anchor='w')

        row2 = tk.Frame(inner, bg=C['panel'])
        row2.pack(fill='x', pady=(6, 2))

        for attr, label in [('_lbl_dif', 'DIF %'), ('_lbl_rango', 'RANGO'),
                             ('_lbl_racha2', 'RACHA'), ('_lbl_umbral2', 'UMBRAL')]:
            c = tk.Frame(row2, bg=C['panel'])
            c.pack(side='left', expand=True, fill='x')
            tk.Label(c, text=label, font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
            lbl = tk.Label(c, text="---", font=FONT_MONO_B, bg=C['panel'], fg=C['text'])
            lbl.pack(anchor='w')
            setattr(self, attr, lbl)

        row3 = tk.Frame(inner, bg=C['panel'])
        row3.pack(fill='x', pady=(6, 2))

        for attr, label in [('_lbl_prev2', 'PREVISIÓN'), ('_lbl_gan2', 'GANADOR')]:
            c = tk.Frame(row3, bg=C['panel'])
            c.pack(side='left', expand=True, fill='x')
            tk.Label(c, text=label, font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
            lbl = tk.Label(c, text="---", font=('Consolas', 12, 'bold'),
                           bg=C['panel'], fg=C['text'])
            lbl.pack(anchor='w')
            setattr(self, attr, lbl)

        c = tk.Frame(row3, bg=C['panel'])
        c.pack(side='left', expand=True, fill='x')
        tk.Label(c, text="VOLÚMENES", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_vols = tk.Label(c, text="B:--- R:---", font=FONT_MONO_B,
                                  bg=C['panel'], fg=C['text'])
        self._lbl_vols.pack(anchor='w')

        c2 = tk.Frame(row3, bg=C['panel'])
        c2.pack(side='left', expand=True, fill='x')
        tk.Label(c2, text="RESULTADO", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_resultado = tk.Label(c2, text="---", font=('Consolas', 12, 'bold'),
                                       bg=C['panel'], fg=C['text'])
        self._lbl_resultado.pack(anchor='w')

    def _resetear_balance(self):
        self.estado['balance'] = 1.0
        self.estado['_balance'] = 1.0
        self._lbl_balance.config(text="+1.00€", fg=C['green'])
        self.log("BALANCE reseteado a 1.00€", 'warn')
        if self._on_reset_balance:
            self._on_reset_balance()

    def _panel_barras(self, parent):
        inner = self._mk_panel(parent, "◈ DISTRIBUCIÓN DE VOLÚMENES")

        row_b = tk.Frame(inner, bg=C['panel'])
        row_b.pack(fill='x', pady=2)
        tk.Label(row_b, text="🔵 BLUE", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['blue'], width=9, anchor='w').pack(side='left')
        self._bar_blue_frame = tk.Frame(row_b, bg=C['border'], height=16)
        self._bar_blue_frame.pack(side='left', fill='x', expand=True, padx=6)
        self._bar_blue = tk.Frame(self._bar_blue_frame, bg=C['blue'], height=16)
        self._bar_blue.place(x=0, y=0, relheight=1, relwidth=0.5)
        self._lbl_pct_blue = tk.Label(row_b, text="50.0%", font=FONT_MONO_B,
                                      bg=C['panel'], fg=C['blue'], width=7)
        self._lbl_pct_blue.pack(side='left')

        row_r = tk.Frame(inner, bg=C['panel'])
        row_r.pack(fill='x', pady=2)
        tk.Label(row_r, text="🔴 RED", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['red'], width=9, anchor='w').pack(side='left')
        self._bar_red_frame = tk.Frame(row_r, bg=C['border'], height=16)
        self._bar_red_frame.pack(side='left', fill='x', expand=True, padx=6)
        self._bar_red = tk.Frame(self._bar_red_frame, bg=C['red'], height=16)
        self._bar_red.place(x=0, y=0, relheight=1, relwidth=0.5)
        self._lbl_pct_red = tk.Label(row_r, text="50.0%", font=FONT_MONO_B,
                                     bg=C['panel'], fg=C['red'], width=7)
        self._lbl_pct_red.pack(side='left')

        row_ra = tk.Frame(inner, bg=C['panel'])
        row_ra.pack(fill='x', pady=(6, 2))
        tk.Label(row_ra, text="⚡ RACHA", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['warn'], width=9, anchor='w').pack(side='left')
        self._bar_racha_frame = tk.Frame(row_ra, bg=C['border'], height=16)
        self._bar_racha_frame.pack(side='left', fill='x', expand=True, padx=6)
        self._bar_racha = tk.Frame(self._bar_racha_frame, bg=C['warn'], height=16)
        self._bar_racha.place(x=0, y=0, relheight=1, relwidth=0.5)
        self._lbl_racha = tk.Label(row_ra, text="50.0%", font=FONT_MONO_B,
                                   bg=C['panel'], fg=C['warn'], width=7)
        self._lbl_racha.pack(side='left')

        tk.Label(inner, text=f"  UMBRAL DIR ≥{self.estado.get('umbral_d',70)}%  ·  UMBRAL INV ≤{self.estado.get('umbral_i',30)}%",
                 font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(anchor='w', pady=2)

    def _panel_barra_tiempo(self, parent):
        inner = self._mk_panel(parent, "◈ CONTADOR DE TIEMPO")

        row = tk.Frame(inner, bg=C['panel'])
        row.pack(fill='x', pady=4)

        self._lbl_tiempo_num = tk.Label(row, text="--s", font=('Consolas', 18, 'bold'),
                                        bg=C['panel'], fg=C['accent'], width=6)
        self._lbl_tiempo_num.pack(side='left')

        bar_outer = tk.Frame(row, bg=C['border'], height=22)
        bar_outer.pack(side='left', fill='x', expand=True, padx=8)
        bar_outer.pack_propagate(False)
        self._bar_tiempo_outer = bar_outer
        self._bar_tiempo = tk.Frame(bar_outer, bg=C['accent'], height=22)
        self._bar_tiempo.place(x=0, y=0, relheight=1, relwidth=0)

        self._lbl_tiempo_pct = tk.Label(row, text="0%", font=FONT_MONO_B,
                                        bg=C['panel'], fg=C['accent'], width=5)
        self._lbl_tiempo_pct.pack(side='left')

        self._lbl_tiempo_estado = tk.Label(inner, text="ESPERANDO NUEVA RONDA...",
                                           font=FONT_MONO_B, bg=C['panel'], fg=C['muted'])
        self._lbl_tiempo_estado.pack(anchor='w', pady=2)

    def _panel_proyeccion(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)

        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ PROYECCIÓN DE GANANCIAS", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        grid = tk.Frame(f, bg=C['panel'])
        grid.pack(fill='x', pady=4)

        projections = [
            ('_proj_1h', '1 HORA', '0.00€'),
            ('_proj_2h', '2 HORAS', '0.00€'),
            ('_proj_4h', '4 HORAS', '0.00€'),
            ('_proj_8h', '8 HORAS', '0.00€'),
            ('_proj_dia', 'POR DÍA', '0.00€'),
            ('_proj_semana', 'POR SEMANA', '0.00€'),
            ('_proj_mes', 'POR MES', '0.00€'),
        ]
        for i, (attr, lbl, val) in enumerate(projections):
            c = tk.Frame(grid, bg=C['border'], padx=6, pady=4)
            c.grid(row=0, column=i, padx=2, sticky='ew')
            grid.columnconfigure(i, weight=1)
            tk.Label(c, text=lbl, font=FONT_SM, bg=C['border'], fg=C['muted']).pack()
            l = tk.Label(c, text=val, font=('Consolas', 10, 'bold'),
                         bg=C['border'], fg=C['accent2'])
            l.pack()
            setattr(self, attr, l)

    def _panel_grafica(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)

        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ GRÁFICA DE BENEFICIOS", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        self._grafica_oculto = tk.BooleanVar(value=False)
        self._grafica_toggle_btn = tk.Checkbutton(
            tf, text="△", variable=self._grafica_oculto,
            font=FONT_TITLE, bg=C['border'], fg=C['accent'],
            selectcolor=C['border'], indicatoron=False,
            command=self._toggle_grafica)
        self._grafica_toggle_btn.pack(side='right', padx=4)

        self._grafica_frame = tk.Frame(f, bg=C['bg'])
        self._grafica_frame.pack(fill='both', expand=True, padx=4, pady=4)

        # Columna izquierda: gráfica
        self._grafica_img_frame = tk.Frame(self._grafica_frame, bg=C['bg'])
        self._grafica_img_frame.pack(side='left', fill='both', expand=True)

        # Columna derecha: análisis de tendencia
        self._grafica_analisis_frame = tk.Frame(
            self._grafica_frame, bg=C['panel'], width=190)
        self._grafica_analisis_frame.pack(side='right', fill='y', padx=(4, 0))
        self._grafica_analisis_frame.pack_propagate(False)
        self._construir_panel_analisis(self._grafica_analisis_frame)

        self._img_ref = None

    def _construir_panel_analisis(self, parent):
        """Construye los labels del panel de análisis de tendencia."""
        def sep(txt=''):
            tk.Label(parent, text=txt, font=('Consolas', 6), bg=C['panel'],
                     fg='#1A3A5C').pack(fill='x')

        def titulo(txt):
            tk.Label(parent, text=txt, font=('Consolas', 8, 'bold'),
                     bg=C['panel'], fg=C['accent'], anchor='w').pack(fill='x', padx=6, pady=(6, 1))
            tk.Frame(parent, bg='#0D2137', height=1).pack(fill='x', padx=6)

        def fila(attr, label, val='---', color=None):
            row = tk.Frame(parent, bg=C['panel'])
            row.pack(fill='x', padx=6, pady=1)
            tk.Label(row, text=label, font=('Consolas', 7), bg=C['panel'],
                     fg=C['muted'], width=10, anchor='w').pack(side='left')
            lbl = tk.Label(row, text=val, font=('Consolas', 8, 'bold'),
                           bg=C['panel'], fg=color or C['text'], anchor='e')
            lbl.pack(side='right')
            setattr(self, attr, lbl)

        titulo("◈ TENDENCIA")
        fila('_ta_dir',    'Dirección')
        fila('_ta_slope',  'Pendiente')
        sep()
        titulo("◈ BALANCE")
        fila('_ta_actual', 'Actual')
        fila('_ta_max',    'Máximo')
        fila('_ta_min',    'Mínimo')
        fila('_ta_dd',     'DrawDown')
        sep()
        titulo("◈ RACHAS")
        fila('_ta_mejor',  'Mejor')
        fila('_ta_peor',   'Peor')
        fila('_ta_actual_racha', 'Actual')
        sep()
        titulo("◈ MOMENTUM")
        fila('_ta_mom10',  'Últ.10')
        fila('_ta_media',  'Media')
        fila('_ta_estado', 'Estado')

    def _toggle_grafica(self):
        if self._grafica_oculto.get():
            self._grafica_frame.pack_forget()
        else:
            self._grafica_frame.pack(fill='both', expand=True, padx=4, pady=4)

    def _historico_a_ops(self):
        """Convierte self.historico a formato ops para simulacion EP."""
        ops = []
        for entry in reversed(list(self.historico)):
            modo = entry.get('modo', 'SKIP')
            acierto = entry.get('acierto')
            rango = entry.get('rango', '')
            if modo in ('SKIP', None, '---') or acierto is None or not rango:
                continue
            modo_norm = ('DIRECTO' if 'DIRECTO' in str(modo).upper() else
                         'INVERSO' if 'INVERSO' in str(modo).upper() else None)
            if not modo_norm:
                continue
            ops.append({
                'rango': rango, 'modo': modo_norm, 'ganada': bool(acierto),
                'pnl_real': entry.get('pnl'), 'mult_real': entry.get('mult'),
            })
        return ops

    def _actualizar_grafica(self):
        if not hasattr(self, '_grafica_img_frame'):
            return
        for w in self._grafica_img_frame.winfo_children():
            w.destroy()

        balances = []
        pnls = []
        for entry in self.historico:
            bal = entry.get('balance', 0)
            if bal is not None and bal != 0:
                balances.insert(0, bal)
            p = entry.get('pnl')
            if isinstance(p, (int, float)):
                pnls.insert(0, p)

        self._actualizar_analisis_tendencia(balances, pnls)

        # Intentar simulacion EP desde historial
        ops = self._historico_a_ops()
        bal_real = None
        bal_ep = None

        if len(ops) >= 2:
            try:
                res = ep_simular(ops)
                bal_real = res['bal_real']
                bal_ep = res['bal_ep']
            except Exception:
                bal_real = None
                bal_ep = None

        # Fallback: solo curva real desde balances del historico
        if not bal_real or len(bal_real) < 2:
            bal_real = balances
            bal_ep = None

        if not bal_real or len(bal_real) < 2:
            tk.Label(self._grafica_img_frame, text="Sin suficientes datos", font=FONT_SM,
                     bg=C['bg'], fg=C['muted']).pack()
            return

        fig, ax = plt.subplots(figsize=(8, 4), dpi=80)
        fig.patch.set_facecolor('#050A14')
        ax.set_facecolor('#0A1628')

        x_real = list(range(len(bal_real)))
        ax.plot(x_real, bal_real, color='#4A8ECC', linewidth=1.2,
                linestyle='--', alpha=0.8, label='Real')

        if bal_ep and len(bal_ep) >= 2:
            x_ep = list(range(len(bal_ep)))
            ax.plot(x_ep, bal_ep, color='#00FF88', linewidth=2,
                    alpha=0.9, label='Simulada EP')
            ax.fill_between(x_ep, bal_ep, 0,
                            where=[v >= 0 for v in bal_ep],
                            color='#00FF88', alpha=0.08)
            ax.fill_between(x_ep, bal_ep, 0,
                            where=[v < 0 for v in bal_ep],
                            color='#FF3366', alpha=0.08)
            titulo = f"Real: {bal_real[-1]:+.1f}  Sim EP: {bal_ep[-1]:+.1f}"
        else:
            ax.fill_between(x_real, bal_real, 0,
                            where=[b >= 0 for b in bal_real],
                            color='#00FF88', alpha=0.15)
            ax.fill_between(x_real, bal_real, 0,
                            where=[b < 0 for b in bal_real],
                            color='#FF3366', alpha=0.15)
            titulo = f"Balance: {bal_real[-1]:+.1f}"

        ax.axhline(y=0, color='#333344', linewidth=0.8)
        ax.legend(facecolor='#0A1628', edgecolor='#0D2137',
                  labelcolor='#C8D8E8', fontsize=7)
        ax.set_title(titulo, color='#C8D8E8', fontsize=8,
                     fontfamily='monospace', pad=6)
        ax.set_xlabel('Operacion #', fontsize=8, color='#4A6080')
        ax.set_ylabel('Balance', fontsize=8, color='#4A6080')
        ax.tick_params(colors='#4A6080', labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor('#1A2A3A')
        ax.grid(True, color='#1A2A3A', linewidth=0.4)

        fig.tight_layout()
        tmp = '_temp_grafica.png'
        fig.savefig(tmp, dpi=80, bbox_inches='tight',
                    facecolor='#050A14', edgecolor='none')
        plt.close(fig)

        if not os.path.exists(tmp):
            return
        self._img_ref = tk.PhotoImage(file=tmp)
        img_w = self._img_ref.width()
        img_h = self._img_ref.height()
        canvas = tk.Canvas(self._grafica_img_frame, bg=C['bg'],
                           width=img_w, height=img_h,
                           highlightthickness=0)
        canvas.pack(fill='x')
        canvas.create_image(0, 0, anchor='nw', image=self._img_ref)

        try:
            os.remove(tmp)
        except:
            pass

    def _actualizar_analisis_tendencia(self, balances, pnls):
        """Calcula y actualiza el panel de análisis de tendencia."""
        if not hasattr(self, '_ta_dir'):
            return
        if len(balances) < 2:
            return

        # --- Tendencia por regresión lineal ---
        n = len(balances)
        x = list(range(n))
        mean_x = sum(x) / n
        mean_y = sum(balances) / n
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, balances))
        den = sum((xi - mean_x) ** 2 for xi in x) or 1
        slope = num / den  # €/ronda

        if slope > 0.05:
            dir_txt, dir_col = '↗ SUBIENDO', '#00FF88'
        elif slope < -0.05:
            dir_txt, dir_col = '↘ BAJANDO', '#FF3366'
        else:
            dir_txt, dir_col = '→ LATERAL', '#FFD700'

        self._ta_dir.config(text=dir_txt, fg=dir_col)
        self._ta_slope.config(text=f'{slope:+.3f}€/r',
                              fg='#00FF88' if slope >= 0 else '#FF3366')

        # --- Balance stats ---
        actual = balances[-1]
        maximo = max(balances)
        minimo = min(balances)

        # Max drawdown: mayor caída desde un pico previo
        pico = balances[0]
        max_dd = 0.0
        for b in balances:
            if b > pico:
                pico = b
            dd = pico - b
            if dd > max_dd:
                max_dd = dd

        self._ta_actual.config(text=f'{actual:+.2f}€',
                               fg='#00FF88' if actual >= 0 else '#FF3366')
        self._ta_max.config(text=f'{maximo:+.2f}€', fg='#00FF88')
        self._ta_min.config(text=f'{minimo:+.2f}€', fg='#FF3366')
        self._ta_dd.config(text=f'-{max_dd:.2f}€',
                           fg='#FF9944' if max_dd > 0 else C['muted'])

        # --- Rachas (basado en pnl si disponible, sino diffs de balance) ---
        serie = pnls if len(pnls) >= 2 else [balances[i] - balances[i-1] for i in range(1, len(balances))]
        mejor_racha = peor_racha = racha_actual = 0
        cur_pos = cur_neg = 0
        for v in serie:
            if v > 0:
                cur_pos += 1
                cur_neg = 0
            elif v < 0:
                cur_neg += 1
                cur_pos = 0
            else:
                cur_pos = cur_neg = 0
            mejor_racha = max(mejor_racha, cur_pos)
            peor_racha  = max(peor_racha, cur_neg)
        racha_actual = cur_pos if cur_pos > 0 else -cur_neg

        self._ta_mejor.config(text=f'+{mejor_racha} wins', fg='#00FF88')
        self._ta_peor.config(text=f'-{peor_racha} loss', fg='#FF3366')
        ra_txt = (f'+{racha_actual} wins' if racha_actual > 0
                  else f'{racha_actual} loss' if racha_actual < 0 else '—')
        ra_col = '#00FF88' if racha_actual > 0 else '#FF5555' if racha_actual < 0 else C['muted']
        self._ta_actual_racha.config(text=ra_txt, fg=ra_col)

        # --- Momentum últimas 10 rondas ---
        media_global = sum(serie) / len(serie) if serie else 0
        ult10 = serie[-10:] if len(serie) >= 10 else serie
        media_10 = sum(ult10) / len(ult10) if ult10 else 0
        mom_col = '#00FF88' if media_10 >= media_global else '#FF3366'
        estado = 'ACELERANDO' if media_10 > media_global + 0.05 else \
                 'FRENANDO'   if media_10 < media_global - 0.05 else 'ESTABLE'
        est_col = '#00FF88' if estado == 'ACELERANDO' else \
                  '#FF3366'  if estado == 'FRENANDO'  else '#FFD700'

        self._ta_mom10.config(text=f'{media_10:+.3f}€/r', fg=mom_col)
        self._ta_media.config(text=f'{media_global:+.3f}€/r',
                              fg='#00FF88' if media_global >= 0 else '#FF3366')
        self._ta_estado.config(text=estado, fg=est_col)

    def _panel_historico(self, parent):
        f = tk.Frame(parent, bg=C['panel'],
                     highlightbackground=C['border'], highlightthickness=1)
        f.pack(fill='both', expand=True, pady=4)

        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ HISTÓRICO DE RONDAS", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        # Contenedor horizontal: tabla izquierda + heatmap derecha
        body = tk.Frame(f, bg=C['panel'])
        body.pack(fill='both', expand=True)

        # --- TREEVIEW EXCEL (delegado a HistorialWidget) ---
        tabla_frame = tk.Frame(body, bg=C['panel'])
        tabla_frame.pack(side='left', fill='both', expand=True)

        self.hist_widget = HistorialWidget(tabla_frame, self.root,
                                           on_log=self.log, colores=C)
        self.historico = self.hist_widget.historico

        # --- HEATMAP ---
        heat_frame = tk.Frame(body, bg=C['panel'], width=320,
                              highlightbackground=C['border'], highlightthickness=1)
        heat_frame.pack(side='right', fill='y', padx=(2, 4), pady=2)
        heat_frame.pack_propagate(False)
        self._heat_canvas_frame = heat_frame

    def _panel_stats(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ ESTADÍSTICAS DE SESIÓN", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        grid = tk.Frame(f, bg=C['panel'])
        grid.pack(fill='x', pady=4)

        stats = [
            ('_st_aciertos', 'ACIERTOS', '0', C['accent2']),
            ('_st_fallos',   'FALLOS',   '0', C['accent3']),
            ('_st_skips',    'SKIPS',    '0', C['skip']),
            ('_st_wr',       'WIN RATE', '0%', C['warn']),
        ]
        for i, (attr, lbl, val, col) in enumerate(stats):
            c = tk.Frame(grid, bg=C['border'], padx=8, pady=6)
            c.grid(row=0, column=i, padx=3, sticky='ew')
            grid.columnconfigure(i, weight=1)
            tk.Label(c, text=lbl, font=FONT_SM, bg=C['border'], fg=C['muted']).pack()
            l = tk.Label(c, text=val, font=('Consolas', 14, 'bold'),
                         bg=C['border'], fg=col)
            l.pack()
            setattr(self, attr, l)
        return f

    def _panel_rangos(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')

        left_tf = tk.Frame(tf, bg=C['border'])
        left_tf.pack(side='left')
        tk.Label(left_tf, text="  ◈ ANÁLISIS POR RANGOS", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        right_tf = tk.Frame(tf, bg=C['border'])
        right_tf.pack(side='right', padx=4)
        self._rangos_oculto = tk.BooleanVar(value=True)
        btn = tk.Checkbutton(right_tf, text="◁", variable=self._rangos_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_rangos)
        btn.pack(side='right')

        self._rangos_frame = tk.Frame(f, bg=C['panel'])
        return f

    def _toggle_rangos(self):
        if self._rangos_oculto.get():
            self._rangos_frame.pack_forget()
        else:
            self._rangos_frame.pack(fill='x', padx=4, pady=2)

    def _panel_umbr(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')

        left_tf = tk.Frame(tf, bg=C['border'])
        left_tf.pack(side='left')
        tk.Label(left_tf, text="  ◈ ANÁLISIS POR UMBRALES", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        right_tf = tk.Frame(tf, bg=C['border'])
        right_tf.pack(side='right', padx=4)
        self._umbr_oculto = tk.BooleanVar(value=True)
        btn = tk.Checkbutton(right_tf, text="◁", variable=self._umbr_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_umbr)
        btn.pack(side='right')

        self._umbr_cab = tk.Frame(f, bg='#060E1C')
        for txt, w in [("RANGO",10),("OPS",6),("GANADAS",9),("PERDIDAS",9),("SALDO",8)]:
            tk.Label(self._umbr_cab, text=txt, font=FONT_SM, bg='#060E1C',
                     fg=C['accent'], width=w, anchor='w').pack(side='left', padx=1)

        self._umbr_frame = tk.Frame(f, bg=C['panel'])
        return f

    def _toggle_umbr(self):
        if self._umbr_oculto.get():
            self._umbr_cab.pack_forget()
            self._umbr_frame.pack_forget()
        else:
            self._umbr_cab.pack(fill='x', padx=4, pady=(4, 0))
            self._umbr_frame.pack(fill='x', padx=4, pady=2)

    def _panel_prob(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')

        left_tf = tk.Frame(tf, bg=C['border'])
        left_tf.pack(side='left')
        tk.Label(left_tf, text="  ◈ ANÁLISIS DE RENTABILIDAD", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        right_tf = tk.Frame(tf, bg=C['border'])
        right_tf.pack(side='right', padx=4)
        self._prob_oculto = tk.BooleanVar(value=True)
        btn = tk.Checkbutton(right_tf, text="◁", variable=self._prob_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_prob)
        btn.pack(side='right')

        self._prob_cab = tk.Frame(f, bg='#060E1C')
        for txt, w in [("RANGO",10),("OPS",6),("GANADAS",9),("PERDIDAS",9),("%RENT",8),("SALDO",8)]:
            tk.Label(self._prob_cab, text=txt, font=FONT_SM, bg='#060E1C',
                     fg=C['accent'], width=w, anchor='w').pack(side='left', padx=1)

        self._prob_frame = tk.Frame(f, bg=C['panel'])
        return f

    def _toggle_prob(self):
        if self._prob_oculto.get():
            self._prob_cab.pack_forget()
            self._prob_frame.pack_forget()
        else:
            self._prob_cab.pack(fill='x', padx=4, pady=(4, 0))
            self._prob_frame.pack(fill='x', padx=4, pady=2)

    def _actualizar_prob(self):
        if not hasattr(self, '_prob_frame'):
            return
        for w in self._prob_frame.winfo_children():
            w.destroy()

        # Usar stats_umbrales (cargado exclusivamente desde reconstructor_data_AI.txt)
        fuente = self.estado.get('stats_umbrales', {})
        if not fuente:
            return

        def sort_key(x):
            s = x[1]
            return s['ganadas'] * 0.9 - s['perdidas']

        ORDEN_RANGOS = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]

        for rango, s in sorted(fuente.items(), key=sort_key, reverse=True):
            if s['ops'] == 0:
                continue
            saldo = s['ganadas'] * 0.9 - s['perdidas']
            rent_pct = (saldo / s['ops']) * 100 if s['ops'] > 0 else 0.0
            col_s   = C['accent2'] if saldo > 0 else (C['accent3'] if saldo < 0 else C['muted'])
            col_r   = C['accent2'] if rent_pct > 0 else C['accent3']

            row = tk.Frame(self._prob_frame, bg=C['panel'])
            row.pack(fill='x', pady=1)
            for val, w, col in [
                (rango,                  10, C['accent']),
                (str(s['ops']),           6, C['text']),
                (str(s['ganadas']),       9, C['accent2']),
                (str(s['perdidas']),      9, C['accent3']),
                (f"{rent_pct:+.1f}%",    8, col_r),
                (f"{saldo:+.1f}€",       8, col_s),
            ]:
                tk.Label(row, text=val, font=FONT_SM, bg=C['panel'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

        total_ops = sum(s['ops'] for s in fuente.values())
        total_gan = sum(s['ganadas'] for s in fuente.values())
        total_per = sum(s['perdidas'] for s in fuente.values())
        total_sal = total_gan * 0.9 - total_per
        total_rent = (total_sal / total_ops * 100) if total_ops > 0 else 0.0
        col_total  = C['accent2'] if total_sal > 0 else (C['accent3'] if total_sal < 0 else C['muted'])
        col_rt     = C['accent2'] if total_rent > 0 else C['accent3']

        if total_ops > 0:
            row_tot = tk.Frame(self._prob_frame, bg=C['border'])
            row_tot.pack(fill='x', pady=(4, 0))
            for val, w, col in [
                ('TOTAL',              10, C['white']),
                (str(total_ops),        6, C['text']),
                (str(total_gan),        9, C['accent2']),
                (str(total_per),        9, C['accent3']),
                (f"{total_rent:+.1f}%", 8, col_rt),
                (f"{total_sal:+.1f}€",  8, col_total),
            ]:
                tk.Label(row_tot, text=val, font=FONT_SM, bg=C['border'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

    def _panel_conf(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')

        left_tf = tk.Frame(tf, bg=C['border'])
        left_tf.pack(side='left')
        tk.Label(left_tf, text="  ◈ ANÁLISIS DE CONFIANZA", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        # Desplegable modo de cálculo
        self._conf_modo = tk.StringVar(value='RECONSTRUCTOR')
        menu = tk.OptionMenu(left_tf, self._conf_modo,
                             'RECONSTRUCTOR', 'HISTÓRICO',
                             command=lambda _: (self._cargar_stats_conf_desde_archivo(), self._actualizar_conf()))
        menu.config(font=FONT_SM, bg=C['border'], fg=C['accent'],
                    activebackground=C['panel'], activeforeground=C['accent'],
                    highlightthickness=0, bd=0, relief='flat',
                    indicatoron=False)
        menu['menu'].config(font=FONT_SM, bg=C['panel'], fg=C['accent'],
                            activebackground=C['border'], activeforeground=C['accent'])
        menu.pack(side='left', padx=6)

        right_tf = tk.Frame(tf, bg=C['border'])
        right_tf.pack(side='right', padx=4)

        self._filtro_hist_activo = tk.BooleanVar(value=False)
        tk.Checkbutton(right_tf, text="FILTRO HIST", variable=self._filtro_hist_activo,
                       font=FONT_SM, bg=C['border'], fg=C['warn'],
                       selectcolor='#1A2800', indicatoron=False,
                       command=self._on_filtro_hist_toggle).pack(side='right', padx=(0, 8))

        self._invertir_x_rango = tk.BooleanVar(value=False)
        tk.Checkbutton(right_tf, text="INVERTIR X RANGO", variable=self._invertir_x_rango,
                       font=FONT_SM, bg=C['border'], fg=C['accent3'],
                       selectcolor='#2A0A00', indicatoron=False).pack(side='right', padx=(8, 0))

        self._conf_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right_tf, text="◁", variable=self._conf_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_conf)
        btn.pack(side='right')

        self._conf_cab = tk.Frame(f, bg='#060E1C')
        for txt, w in [("RANGO", 10), ("OPS", 6), ("G", 5), ("P", 5), ("%DIR", 8), ("%INV", 8), ("MEJOR", 8), ("BENEF", 8)]:
            tk.Label(self._conf_cab, text=txt, font=FONT_SM, bg='#060E1C',
                     fg=C['accent'], width=w, anchor='w').pack(side='left', padx=1)

        self._conf_frame = tk.Frame(f, bg=C['panel'])
        return f

    def _on_filtro_hist_toggle(self):
        activo = self._filtro_hist_activo.get()
        estado = "ACTIVADO" if activo else "DESACTIVADO"
        self.log(f"🔎 Filtro Histórico {estado}", 'warn' if activo else 'dim')

    def _toggle_conf(self):
        if self._conf_oculto.get():
            self._conf_cab.pack_forget()
            self._conf_frame.pack_forget()
        else:
            self._conf_cab.pack(fill='x', padx=4, pady=(4, 0))
            self._conf_frame.pack(fill='x', padx=4, pady=2)
            self._actualizar_conf()

    def _parsear_reconstructor_txt(self):
        ruta = Path(__file__).parent / "reconstructor_data_AI.txt"
        if not ruta.exists():
            return {}
        stats = {}
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                for linea in f:
                    if 'RANGO:' not in linea or 'ACIERTO:' not in linea:
                        continue
                    m_rango = re.search(r'RANGO:\s*([0-9+\-]+)', linea)
                    m_ac = re.search(r'ACIERTO:\s*(True|False)', linea)
                    if not m_rango or not m_ac:
                        continue
                    rango = m_rango.group(1)
                    acierto = m_ac.group(1) == 'True'
                    if rango not in stats:
                        stats[rango] = {'ops': 0, 'ganadas': 0, 'perdidas': 0}
                    stats[rango]['ops'] += 1
                    if acierto:
                        stats[rango]['ganadas'] += 1
                    else:
                        stats[rango]['perdidas'] += 1
        except Exception:
            pass
        return stats

    def _actualizar_conf(self, stats_rangos: dict = None):
        if not hasattr(self, '_conf_frame'):
            return
        for w in self._conf_frame.winfo_children():
            w.destroy()

        fuente = self._parsear_reconstructor_txt()
        if not fuente:
            return

        def _wr_dir(gan, per):
            """((G-P)/P)*100 cap 100. None si G<=P o ops<3."""
            if gan + per < 3 or gan <= per:
                return None
            if per == 0:
                return 100.0
            return round(min(((gan - per) / per) * 100, 100.0), 1)

        def _wr_inv(gan, per):
            """INV: perdidas de DIR son ganadas de INV. ((P-G)/G)*100 cap 100."""
            if gan + per < 3 or per <= gan:
                return None
            if gan == 0:
                return 100.0
            return round(min(((per - gan) / gan) * 100, 100.0), 1)

        def _col(v):
            if v is None:  return C['muted']
            if v > 10:     return C['accent2']
            if v >= 0:     return C['warn']
            return C['accent3']

        def _str(v):
            return f"{v:+.0f}%" if v is not None else '---'

        ORDEN_RANGOS = ['0-5','5-10','10-15','15-20','20-25','25-30','30-35','35-40','40-45','45-50','+50']

        def _sort(item):
            r, _ = item
            return ORDEN_RANGOS.index(r) if r in ORDEN_RANGOS else 99

        tot_ops = tot_gan = tot_per = tot_benef = 0

        for rango, s in sorted(fuente.items(), key=_sort):
            ops = s['ops']
            gan = s['ganadas']
            per = s['perdidas']
            if ops == 0:
                continue

            d_wr = _wr_dir(gan, per)
            i_wr = _wr_inv(gan, per)

            if d_wr is not None and i_wr is not None:
                mejor = 'DIR' if d_wr >= i_wr else 'INV'
                mejor_wr = max(d_wr, i_wr)
            elif d_wr is not None:
                mejor, mejor_wr = 'DIR', d_wr
            elif i_wr is not None:
                mejor, mejor_wr = 'INV', i_wr
            else:
                mejor, mejor_wr = '---', 0

            col_mejor = _col(mejor_wr)

            if mejor == 'DIR':
                benef = gan * 0.9 - per * 1.0
            elif mejor == 'INV':
                benef = per * 0.9 - gan * 1.0
            else:
                benef = 0
            benef_txt = f"{benef:+.1f}"
            benef_col = C['accent2'] if benef >= 0 else C['accent3']

            tot_ops   += ops
            tot_gan   += gan
            tot_per   += per
            tot_benef += benef

            row = tk.Frame(self._conf_frame, bg=C['panel'])
            row.pack(fill='x', pady=1)
            for val, w, col in [
                (rango,          10, C['accent']),
                (str(ops),        6, C['text']),
                (str(gan),        5, C['accent2']),
                (str(per),        5, C['accent3']),
                (_str(d_wr),      8, _col(d_wr)),
                (_str(i_wr),      8, _col(i_wr)),
                (mejor,           8, col_mejor),
                (benef_txt,       8, benef_col),
            ]:
                tk.Label(row, text=val, font=FONT_SM, bg=C['panel'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

        # Fila de totales
        if tot_ops > 0:
            tk.Frame(self._conf_frame, bg=C['border'], height=1).pack(fill='x', pady=(4, 1))
            tot_row = tk.Frame(self._conf_frame, bg='#060E1C')
            tot_row.pack(fill='x', pady=1)
            tot_benef_col = C['accent2'] if tot_benef >= 0 else C['accent3']
            for val, w, col in [
                ('TOTAL',             10, C['accent']),
                (str(tot_ops),         6, C['text']),
                (str(tot_gan),         5, C['accent2']),
                (str(tot_per),         5, C['accent3']),
                ('',                    8, C['muted']),
                ('',                    8, C['muted']),
                ('',                    8, C['muted']),
                (f"{tot_benef:+.1f}",   8, tot_benef_col),
            ]:
                tk.Label(tot_row, text=val, font=('Consolas', 8, 'bold'), bg='#060E1C',
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

    def _calcular_stats_historico(self) -> dict:
        """Devuelve stats históricos desde stats_conf.json (calculado por calc_conf.py)."""
        return self._stats_conf.get('historico', {})

    def _loop_conf(self):
        """Refresca análisis de confianza cada 30 segundos."""
        if not self._conf_oculto.get():
            self._actualizar_conf()
        self.root.after(30_000, self._loop_conf)

    def _panel_log(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        self._log_panel_f = f
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ LOG DEL SISTEMA", font=FONT_TITLE,
                 bg=C['border'], fg=C['accent'], pady=4).pack(side='left')

        right_tf = tk.Frame(tf, bg=C['border'])
        right_tf.pack(side='right', padx=4)
        self._log_oculto = tk.BooleanVar(value=False)
        tk.Checkbutton(right_tf, text="◁", variable=self._log_oculto,
                       font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                       selectcolor=C['border'], indicatoron=False,
                       command=self._toggle_log).pack(side='right')

        self._log_scroll_frame = tk.Frame(f, bg=C['panel'])
        self._log_scroll_frame.pack(fill='both', expand=True, padx=4, pady=4)
        scroll_frame = self._log_scroll_frame
        
        sb = tk.Scrollbar(scroll_frame, bg=C['bg'])
        sb.pack(side='right', fill='y')
        
        self._log_text = tk.Text(scroll_frame, bg='#020810', fg=C['text'],
                                font=('Consolas', 11), state='disabled',
                                wrap='word', height=15, relief='flat',
                                insertbackground=C['accent'],
                                yscrollcommand=sb.set)
        self._log_text.pack(side='left', fill='both', expand=True)
        sb.config(command=self._log_text.yview)
        
        self._log_text.tag_config('ok',    foreground=C['accent2'])
        self._log_text.tag_config('err',   foreground=C['accent3'])
        self._log_text.tag_config('warn',  foreground=C['warn'])
        self._log_text.tag_config('info',  foreground=C['text'])
        self._log_text.tag_config('dim',   foreground=C['muted'])
        self._log_text.tag_config('tick_azul', foreground='#00BFFF')
        self._log_text.tag_config('tick_rojo', foreground='#FF4444')
        return f

    def _toggle_log(self):
        if self._log_oculto.get():
            h = self._log_panel_f.winfo_height()
            w = self._log_panel_f.winfo_width()
            if h > 1 and w > 1:
                self._log_panel_f.config(height=h, width=w)
            self._log_panel_f.pack_propagate(False)
            self._log_scroll_frame.pack_forget()
        else:
            self._log_panel_f.pack_propagate(True)
            self._log_scroll_frame.pack(fill='both', expand=True, padx=4, pady=4)

    def log(self, msg: str, tipo: str = 'info'):
        self._log_counter += 1
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state='normal')
        self._log_text.insert('end', f"{self._log_counter:04d} {ts} {msg}\n", tipo)
        self._log_text.see('end')
        self._log_text.config(state='disabled')

    def _actualizar_loop(self):
        self._actualizar_ui()
        if self.estado.get('inicio_programa'):
            segs = (datetime.now() - self.estado['inicio_programa']).total_seconds()
            h = int(segs // 3600)
            m = int((segs % 3600) // 60)
            s = int(segs % 60)
            self._lbl_sesion.config(text=f"Sesión: {h:02d}:{m:02d}:{s:02d}")
        import time as _time
        ahora = _time.time()
        if ahora - self._rangos_json_ts >= 60:
            self._cargar_stats_rangos_desde_archivo()
            self._rangos_json_ts = ahora
        if ahora - self._stats_conf_ts >= 60:
            self._cargar_stats_conf_desde_archivo()
            self._stats_conf_ts = ahora
        self.root.after(250, self._actualizar_loop)

    def _actualizar_ui(self):
        e = self.estado
        ts = datetime.now().strftime("%H:%M:%S")
        self._lbl_clock.config(text=ts)

        self._lbl_ws.config(fg=C['accent2'] if e['ws'] else C['accent3'])
        self._lbl_sheets.config(fg=C['accent2'] if e['sheets'] else C['accent3'])
        self._lbl_apuesta.config(fg=C['accent2'] if e['hacer_apuesta'] else C['muted'])
        self._lbl_historico.config(fg=C['accent2'] if e.get('historico') else C['muted'])

        self._lbl_ronda.config(text=str(e['ronda'])[-10:])
        bal = e['balance']
        self._lbl_balance.config(text=f"{bal:+.2f}€",
                                  fg=C['accent2'] if bal >= 0 else C['accent3'])

        modo = e['modo']
        col_modo = {'DIRECTO': C['accent2'], 'INVERSO': C['accent3'],
                    'SKIP': C['skip'], 'ESPERANDO': C['warn']}.get(modo, C['text'])
        self._lbl_modo.config(text=modo, fg=col_modo)

        mult = e.get('mult', 1) or 1
        self._lbl_mult.config(text=f"x{mult}", fg=C['accent2'] if mult > 1 else C['text'])

        self._lbl_dif.config(text=f"{e['dif']:.2f}%")
        self._lbl_rango.config(text=e['rango'])
        self._lbl_racha2.config(text=f"{e['racha']:.1f}%")
        self._lbl_umbral2.config(text=str(e['umbral']))

        prev = e['prevision']
        col_prev = C['blue'] if 'azul' in prev.lower() or 'blue' in prev.lower() else (
                   C['red'] if 'rojo' in prev.lower() or 'red' in prev.lower() else C['skip'])
        self._lbl_prev2.config(text=prev, fg=col_prev)

        gan = e['ganador']
        gan_disp = 'Azul' if 'azul' in gan.lower() or 'blue' in gan.lower() else (
                  'Rojo' if 'rojo' in gan.lower() or 'red' in gan.lower() else '---')
        col_gan = C['blue'] if ('azul' in gan.lower() or 'blue' in gan.lower()) else (
                  C['red'] if ('rojo' in gan.lower() or 'red' in gan.lower()) else C['muted'])
        self._lbl_gan2.config(text=gan_disp, fg=col_gan)

        self._lbl_vols.config(text=f"B:{e['v_azul']:.0f}  R:{e['v_rojo']:.0f}")

        total_v = e['v_azul'] + e['v_rojo']
        r_b = e['p_azul'] / 100 if total_v > 0 else 0.5
        r_r = e['p_rojo'] / 100 if total_v > 0 else 0.5
        self._bar_blue.place(relwidth=r_b)
        self._bar_red.place(relwidth=r_r)
        self._lbl_pct_blue.config(text=f"{e['p_azul']:.1f}%")
        self._lbl_pct_red.config(text=f"{e['p_rojo']:.1f}%")

        r_ra = e['racha'] / 100
        col_ra = C['accent2'] if e['racha'] >= 70 else (C['accent3'] if e['racha'] <= 30 else C['warn'])
        self._bar_racha.config(bg=col_ra)
        self._bar_racha.place(relwidth=r_ra)
        self._lbl_racha.config(text=f"{e['racha']:.1f}%", fg=col_ra)

        if e['tiempo_activo'] and e['tiempo_total'] > 0:
            t_rest = e['tiempo_restante']
            t_tot  = e['tiempo_total']
            pct = max(0, min(1, t_rest / t_tot))
            col_t = C['accent2'] if pct > 0.5 else C['warn'] if pct > 0.2 else C['accent3']
            self._bar_tiempo.config(bg=col_t)
            self._bar_tiempo.place(relwidth=pct)
            self._lbl_tiempo_num.config(text=f"{t_rest}s", fg=col_t)
            self._lbl_tiempo_pct.config(text=f"{int(pct*100)}%", fg=col_t)
            self._lbl_tiempo_estado.config(
                text=f"⏱ RONDA {str(e['ronda'])[-8:]} — {'APOSTANDO' if e['hacer_apuesta'] else 'OBSERVANDO'}",
                fg=col_t)
        else:
            self._bar_tiempo.place(relwidth=0)
            self._lbl_tiempo_num.config(text="--s", fg=C['muted'])
            self._lbl_tiempo_pct.config(text="0%", fg=C['muted'])
            self._lbl_tiempo_estado.config(text="ESPERANDO NUEVA RONDA...", fg=C['muted'])

        ac = e['aciertos']
        fa = e['fallos']
        total_op = ac + fa
        # WR ajustado: gana 0.9x, pierde 1x → break-even en 52.63%
        if total_op > 0:
            wr_val = ac * 0.9 / (ac * 0.9 + fa) * 100
            wr = f"{wr_val:.1f}%"
        else:
            wr_val = 0.0
            wr = "0%"
        self._st_aciertos.config(text=str(ac))
        self._st_fallos.config(text=str(fa))
        self._st_skips.config(text=str(e['skips']))
        self._st_wr.config(text=wr,
                           fg=C['accent2'] if wr_val >= 50.0 else C['accent3'])

        # Alarma al cruzar el 50% de WR
        if wr_val >= 50.0 and not self._wr_alarm_50_disparada and total_op >= 3:
            self._wr_alarm_50_disparada = True
            def _alarma_wr():
                import winsound
                for _ in range(3):
                    winsound.Beep(1200, 200)
                    winsound.Beep(900, 100)
            import threading as _thr
            _thr.Thread(target=_alarma_wr, daemon=True).start()
        elif wr_val < 50.0:
            self._wr_alarm_50_disparada = False

        if e.get('inicio_sesion') and e['aciertos'] + e['fallos'] > 0:
            inicio = e['inicio_sesion']
            ahora = datetime.now()
            segs = max(1, (ahora - inicio).total_seconds())
            horas = segs / 3600
            balance = self.estado.get('_balance', 0)
            por_hora = balance / horas if horas > 0 else 0

            self._proj_1h.config(text=f"{por_hora:+.2f}€")
            self._proj_2h.config(text=f"{por_hora*2:+.2f}€")
            self._proj_4h.config(text=f"{por_hora*4:+.2f}€")
            self._proj_8h.config(text=f"{por_hora*8:+.2f}€")
            self._proj_dia.config(text=f"{por_hora*24:+.2f}€")
            self._proj_semana.config(text=f"{por_hora*24*7:+.2f}€")
            self._proj_mes.config(text=f"{por_hora*24*30:+.2f}€")
        else:
            self._proj_1h.config(text="0.00€")
            self._proj_2h.config(text="0.00€")
            self._proj_4h.config(text="0.00€")
            self._proj_8h.config(text="0.00€")
            self._proj_dia.config(text="0.00€")
            self._proj_semana.config(text="0.00€")
            self._proj_mes.config(text="0.00€")

    def actualizar_rangos(self):
        self._cargar_stats_rangos_desde_archivo()
        self._actualizar_rangos(self._rango_activo)

    def _actualizar_todos_analisis(self):
        self._cargar_stats_rangos_desde_archivo()
        self._cargar_stats_conf_desde_archivo()
        self._actualizar_rangos(self._rango_activo)
        self._cargar_stats_umbrales_desde_archivo()
        self._actualizar_umbrales()
        self._actualizar_conf()
        # Señalar hilo para recálculo inmediato y refrescar conf cuando termine
        ev = getattr(self, '_recalc_event', None)
        if ev:
            ev.set()
            self.root.after(3000, self._recargar_conf_tras_recalc)

    def _recargar_conf_tras_recalc(self):
        self._cargar_stats_rangos_desde_archivo()
        self._cargar_stats_conf_desde_archivo()
        self._actualizar_rangos(self._rango_activo)
        self._actualizar_conf()

    def _actualizar_rangos(self, rango_activo: str = None):
        for w in self._rangos_frame.winfo_children():
            w.destroy()
        self._filas_rangos = {}

        def _cabecera_bloque(parent, titulo, color):
            hf = tk.Frame(parent, bg=color)
            hf.pack(fill='x', pady=(6, 1))
            tk.Label(hf, text=f"  {titulo}", font=FONT_MONO_B,
                     bg=color, fg='#050A14', pady=2).pack(side='left')
            cab = tk.Frame(parent, bg='#060E1C')
            cab.pack(fill='x')
            for txt, w in [("RANGO",10),("OPS",5),("GANADAS",8),("PERDIDAS",9),("SKIPS",6),("SALDO",8)]:
                tk.Label(cab, text=txt, font=FONT_SM, bg='#060E1C',
                         fg=color, width=w, anchor='w').pack(side='left', padx=1)

        def _fila_rango(parent, rango, s, skips, es_activo, modo):
            saldo = s['ganadas'] * 0.9 - s['perdidas']
            col_s = C['accent2'] if saldo > 0 else (C['accent3'] if saldo < 0 else C['muted'])
            row = tk.Frame(parent, bg=C['panel'])
            row.pack(fill='x', pady=1)
            if rango not in self._filas_rangos:
                self._filas_rangos[rango] = []
            self._filas_rangos[rango].append(row)
            for val, w, col in [
                (rango,             10, C['text']),
                (str(s['ops']),      5, C['text']),
                (str(s['ganadas']),  8, C['accent2']),
                (str(s['perdidas']), 9, C['accent3']),
                (str(skips),         6, C['skip']),
                (f"{saldo:+.1f}€",   8, col_s),
            ]:
                tk.Label(row, text=val, font=FONT_SM,
                         bg=C['panel'], fg=col, width=w, anchor='w').pack(side='left', padx=1)

        def _total_bloque(parent, modo):
            ops = sum(d[modo]['ops'] for d in self.stats_rangos.values())
            gan = sum(d[modo]['ganadas'] for d in self.stats_rangos.values())
            per = sum(d[modo]['perdidas'] for d in self.stats_rangos.values())
            skp = sum(d['SKIP'] for d in self.stats_rangos.values())
            sal = gan * 0.9 - per
            col_s = C['accent2'] if sal > 0 else (C['accent3'] if sal < 0 else C['muted'])
            row = tk.Frame(parent, bg=C['border'])
            row.pack(fill='x', pady=(2, 4))
            for val, w, col in [
                ('TOTAL', 10, C['white']),
                (str(ops), 5, C['text']),
                (str(gan), 8, C['accent2']),
                (str(per), 9, C['accent3']),
                (str(skp), 6, C['skip']),
                (f"{sal:+.1f}€", 8, col_s),
            ]:
                tk.Label(row, text=val, font=FONT_SM, bg=C['border'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)
        
        def sort_key(x):
            rango, datos = x
            modo_dir = datos['DIRECTO']
            modo_inv = datos['INVERSO']
            saldo_dir = modo_dir['ganadas'] * 0.9 - modo_dir['perdidas']
            saldo_inv = modo_inv['ganadas'] * 0.9 - modo_inv['perdidas']
            return max(saldo_dir, saldo_inv)
        
        _cabecera_bloque(self._rangos_frame, "▲ DIRECTO", C['accent2'])
        hay_dir = False
        for rango, datos in sorted(self.stats_rangos.items(), key=lambda x: sort_key(x), reverse=True):
            s = datos['DIRECTO']
            if s['ops'] == 0: continue
            hay_dir = True
            _fila_rango(self._rangos_frame, rango, s, datos['SKIP'],
                        rango == rango_activo, 'DIRECTO')
        if not hay_dir:
            tk.Label(self._rangos_frame, text="  Sin datos", font=FONT_SM,
                     bg=C['panel'], fg=C['muted']).pack(anchor='w')
        _total_bloque(self._rangos_frame, 'DIRECTO')
        
        _cabecera_bloque(self._rangos_frame, "▼ INVERSO", C['accent3'])
        hay_inv = False
        for rango, datos in sorted(self.stats_rangos.items(), key=lambda x: sort_key(x), reverse=True):
            s = datos['INVERSO']
            if s['ops'] == 0: continue
            hay_inv = True
            _fila_rango(self._rangos_frame, rango, s, datos['SKIP'],
                        rango == rango_activo, 'INVERSO')
        if not hay_inv:
            tk.Label(self._rangos_frame, text="  Sin datos", font=FONT_SM,
                     bg=C['panel'], fg=C['muted']).pack(anchor='w')
        _total_bloque(self._rangos_frame, 'INVERSO')
        # Heatmap desactivado
        # rec = self._parsear_reconstructor_txt()
        # heat_data = {}
        # for rango, d in rec.items():
        #     g, p = d['ganadas'], d['perdidas']
        #     ops = d['ops']
        #     heat_data[rango] = {
        #         'DIRECTO':  {'ops': ops, 'ganadas': g, 'perdidas': p},
        #         'INVERSO':  {'ops': ops, 'ganadas': p, 'perdidas': g},
        #     }
        # self._actualizar_heatmap(heat_data, 'RECONSTRUCTOR')

    def _actualizar_umbrales(self):
        for w in self._umbr_frame.winfo_children():
            w.destroy()
        
        def sort_key(x):
            rango, s = x
            return s['ganadas'] * 0.9 - s['perdidas']
        
        for rango, s in sorted(self.estado['stats_umbrales'].items(), key=sort_key, reverse=True):
            saldo = s['ganadas'] * 0.9 - s['perdidas']
            col_s = C['accent2'] if saldo > 0 else (C['accent3'] if saldo < 0 else C['muted'])
            row = tk.Frame(self._umbr_frame, bg=C['panel'])
            row.pack(fill='x', pady=1)
            for val, w, col in [
                (rango, 10, C['accent']),
                (str(s['ops']), 6, C['text']),
                (str(s['ganadas']), 9, C['accent2']),
                (str(s['perdidas']), 9, C['accent3']),
                (f"{saldo:+.1f}€", 8, col_s),
            ]:
                tk.Label(row, text=val, font=FONT_SM, bg=C['panel'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

        total_ops = sum(s['ops'] for s in self.estado['stats_umbrales'].values())
        total_gan = sum(s['ganadas'] for s in self.estado['stats_umbrales'].values())
        total_per = sum(s['perdidas'] for s in self.estado['stats_umbrales'].values())
        total_sal = total_gan * 0.9 - total_per
        col_total = C['accent2'] if total_sal > 0 else (C['accent3'] if total_sal < 0 else C['muted'])

        if total_ops > 0:
            row_tot = tk.Frame(self._umbr_frame, bg=C['border'])
            row_tot.pack(fill='x', pady=(4, 0))
            for val, w, col in [
                ('TOTAL', 10, C['white']),
                (str(total_ops), 6, C['text']),
                (str(total_gan), 9, C['accent2']),
                (str(total_per), 9, C['accent3']),
                (f"{total_sal:+.1f}€", 8, col_total),
            ]:
                tk.Label(row_tot, text=val, font=FONT_SM, bg=C['border'],
                         fg=col, width=w, anchor='w').pack(side='left', padx=1)

    def guardar_entrada_historial(self, entry: dict):
        self.hist_widget.guardar(entry)

    def agregar_historico(self, entry: dict):
        self.hist_widget.agregar(entry)
        self.root.after(0, self._actualizar_grafica)

    def _actualizar_heatmap(self, stats_rangos: dict, fuente_txt: str = 'RECONSTRUCTOR'):
        if not hasattr(self, '_heat_canvas_frame'):
            return
        import numpy as np
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        rangos = [r for r in ['0-5','5-10','10-15','15-20','20-25','25-30','30-35','35-40','40-45','45-50','+50']
                  if r in stats_rangos]
        modos  = ['DIRECTO', 'INVERSO']

        # Crear figura persistente la primera vez
        if not hasattr(self, '_heat_fig') or self._heat_fig is None:
            for w in self._heat_canvas_frame.winfo_children():
                w.destroy()
            self._heat_lbl_analisis = None
            self._heat_lbl_fuente = None
            tk.Label(self._heat_canvas_frame, text="HEATMAP WIN%", font=FONT_SM,
                     bg=C['panel'], fg=C['accent']).pack(pady=(4, 0))
            self._heat_lbl_fuente = tk.Label(self._heat_canvas_frame, text='',
                     font=FONT_SM, bg=C['panel'], fg=C['warn'])
            self._heat_lbl_fuente.pack()
            self._heat_fig, self._heat_ax = plt.subplots(figsize=(5.5, 2.8), dpi=90)
            self._heat_fig.patch.set_facecolor('#050A14')
            self._heat_canvas_widget = FigureCanvasTkAgg(self._heat_fig, master=self._heat_canvas_frame)
            self._heat_canvas_widget.get_tk_widget().pack(pady=2)
            self._heat_lbl_analisis = tk.Label(self._heat_canvas_frame,
                text='', font=FONT_SM, bg=C['panel'], fg=C['muted'],
                wraplength=300, justify='left', anchor='nw')
            self._heat_lbl_analisis.pack(fill='x', padx=4, pady=(2, 4))

        # Actualizar datos sin destruir el widget
        ax = self._heat_ax
        ax.cla()
        ax.set_facecolor('#0A1628')

        if not rangos:
            ax.text(0.5, 0.5, 'Sin datos', ha='center', va='center',
                    color='#888888', transform=ax.transAxes)
        else:
            data = np.full((2, len(rangos)), np.nan)
            for j, rng in enumerate(rangos):
                s = stats_rangos.get(rng, {})
                for i, m in enumerate(modos):
                    bloque = s.get(m, {})
                    if isinstance(bloque, dict) and bloque.get('ops', 0) >= 3:
                        data[i][j] = bloque['ganadas'] / bloque['ops'] * 100

            ax.imshow(data, aspect='auto', cmap='RdYlGn', vmin=30, vmax=70,
                      interpolation='nearest', origin='upper')
            ax.set_xlim(-0.5, len(rangos) - 0.5)
            ax.set_ylim(1.5, -0.5)   # fuerza DIRECTO(0) arriba, INVERSO(1) abajo
            ax.set_xticks(range(len(rangos)))
            ax.set_yticks([0, 1])
            ax.set_xticklabels(rangos, rotation=45, ha='right', fontsize=7, color='#CCCCCC')
            ax.set_yticklabels(modos, fontsize=8, color='#CCCCCC')
            ax.tick_params(colors='#CCCCCC', length=2)
            for spine in ax.spines.values():
                spine.set_edgecolor('#333333')
            for i in range(2):
                for j in range(len(rangos)):
                    if not np.isnan(data[i][j]):
                        ax.text(j, i, f'{data[i][j]:.0f}%', ha='center', va='center',
                                fontsize=8, color='black' if 35 < data[i][j] < 65 else 'white')

            # Resaltar rango activo durante total_bet (columna entera en cyan)
            rango_vivo = getattr(self, '_heat_rango_activo', None)
            if rango_vivo and rango_vivo in rangos:
                from matplotlib.patches import Rectangle
                rj = rangos.index(rango_vivo)
                ax.add_patch(Rectangle((rj - 0.5, -0.5), 1, 2,
                                       linewidth=2, edgecolor='#00BFFF',
                                       facecolor='none', linestyle='--', zorder=4))

            # Resaltar celda ganadora tras drawed
            resaltar = getattr(self, '_heat_resaltar', None)
            if resaltar:
                r_rango, r_modo = resaltar
                if r_rango in rangos and r_modo in modos:
                    rj = rangos.index(r_rango)
                    ri = modos.index(r_modo)
                    acierto_res = getattr(self, '_heat_resaltar_acierto', None)
                    color_borde = '#00FF88' if acierto_res else '#FF3366'
                    from matplotlib.patches import Rectangle
                    ax.add_patch(Rectangle((rj - 0.5, ri - 0.5), 1, 1,
                                           linewidth=3, edgecolor=color_borde,
                                           facecolor='none', zorder=5))

        self._heat_fig.tight_layout(pad=0.3)
        self._heat_canvas_widget.draw()

        # Actualizar etiqueta de fuente
        if hasattr(self, '_heat_lbl_fuente') and self._heat_lbl_fuente:
            col_f = C['warn'] if fuente_txt == 'HISTÓRICO' else C['muted']
            self._heat_lbl_fuente.config(text=f"[ {fuente_txt} ]", fg=col_f)

        # Actualizar texto de análisis
        rango_activo = getattr(self, '_rango_activo', None)
        analisis_txt, analisis_col = self._generar_analisis_heatmap(stats_rangos, rango_activo)
        if self._heat_lbl_analisis:
            self._heat_lbl_analisis.config(text=analisis_txt, fg=analisis_col)

        self._heat_ultimo_rango = rango_activo

    def _generar_analisis_heatmap(self, stats_rangos: dict, rango_activo: str = None) -> tuple:
        if not stats_rangos:
            return "Sin datos suficientes.", C['muted']

        lineas = []

        # Analizar rango activo primero
        if rango_activo and rango_activo in stats_rangos:
            s = stats_rangos[rango_activo]
            lineas.append(f"► RANGO ACTUAL: {rango_activo}")
            for modo in ('DIRECTO', 'INVERSO'):
                ops = s.get(modo, {}).get('ops', 0)
                gan = s.get(modo, {}).get('ganadas', 0)
                if ops >= 3:
                    wr = gan / ops * 100
                    lineas.append(f"  {modo}: {wr:.0f}% ({gan}/{ops})")
                else:
                    lineas.append(f"  {modo}: sin datos")

            # Recomendación
            d_ops = s.get('DIRECTO', {}).get('ops', 0)
            i_ops = s.get('INVERSO', {}).get('ops', 0)
            d_wr  = s['DIRECTO']['ganadas'] / d_ops * 100 if d_ops >= 3 else 0
            i_wr  = s['INVERSO']['ganadas'] / i_ops * 100 if i_ops >= 3 else 0

            if d_wr >= 60 and d_wr > i_wr:
                lineas.append(f"\n✅ RECOMENDADO: DIRECTO")
                lineas.append(f"   Confianza: {d_wr:.0f}%")
                color = C['accent2']
            elif i_wr >= 60 and i_wr > d_wr:
                lineas.append(f"\n✅ RECOMENDADO: INVERSO")
                lineas.append(f"   Confianza: {i_wr:.0f}%")
                color = C['warn']
            elif max(d_wr, i_wr) >= 50:
                mejor = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
                lineas.append(f"\n⚠️ INCIERTO: {mejor}")
                lineas.append(f"   WR bajo ({max(d_wr,i_wr):.0f}%)")
                color = C['muted']
            else:
                lineas.append(f"\n🚫 SKIP recomendado")
                lineas.append(f"   WR insuficiente")
                color = C['accent3']
        else:
            lineas.append("Esperando rango...")
            color = C['muted']

        # Mejor rango global
        mejor_rng, mejor_wr, mejor_modo = None, 0, ''
        for rng, s in stats_rangos.items():
            for modo in ('DIRECTO', 'INVERSO'):
                ops = s.get(modo, {}).get('ops', 0)
                gan = s.get(modo, {}).get('ganadas', 0)
                if ops >= 5:
                    wr = gan / ops * 100
                    if wr > mejor_wr:
                        mejor_wr, mejor_rng, mejor_modo = wr, rng, modo

        if mejor_rng:
            lineas.append(f"\n🏆 MEJOR GLOBAL:")
            lineas.append(f"   {mejor_rng} {mejor_modo}")
            lineas.append(f"   {mejor_wr:.0f}% win rate")

        return '\n'.join(lineas), color

    def _generar_voz_heatmap(self, stats_rangos: dict, rango_activo: str, mayor_bando: str = '') -> str:
        if not rango_activo or rango_activo not in stats_rangos:
            return ''
        s = stats_rangos[rango_activo]
        d_ops = s.get('DIRECTO', {}).get('ops', 0)
        i_ops = s.get('INVERSO', {}).get('ops', 0)
        d_wr  = s['DIRECTO']['ganadas'] / d_ops * 100 if d_ops >= 3 else 0
        i_wr  = s['INVERSO']['ganadas'] / i_ops * 100 if i_ops >= 3 else 0

        def _mult(wr):
            for umbral, m in [(90,7),(85,6),(80,5),(75,4),(70,3),(65,2),(60,1)]:
                if wr >= umbral: return m
            return 1

        # Colores según mayor_bando
        col_dir = ('Azul' if mayor_bando.lower() == 'azul' else 'Rojo') if mayor_bando else ''
        col_inv = ('Rojo' if mayor_bando.lower() == 'azul' else 'Azul') if mayor_bando else ''

        return ''


# ============================================================
# BOT PRINCIPAL
# ============================================================

class AcertadorConDashboard:
    def __init__(self, dashboard: DashboardFuturista):
        self.dash = dashboard
        self.dash.acer = self  # Referencia para que el dashboard pueda acceder
        self.config = Config()
        self.logger = self._setup_logger()
        self.analizador = Analizador3Fases(self.config)
        self.dash.stats_rangos = self.analizador.stats_rangos  # misma referencia desde el inicio
        self.dash._on_reset_balance = self._reset_balance_analizador
        self.sheets = GestorGoogleSheets(self.config, self.logger)
        self.ws_client = WebSocketClient(self.config, self.logger)

        self.session_id = datetime.now().strftime("%d/%m %H:%M")
        self.r_id_actual = None
        self.r_id_barra = None
        self.vol_blue = 0.0
        self.vol_red = 0.0
        self.datos_tick29 = None
        self.datos_tick29_fallback = None
        self.tiempo_espera = 50
        self.hacer_apuesta = True
        self.apuesta_ejecutada = False
        self._pending_entry = None
        self._tarea_barra = None
        self.preparar_activa = False
        self._primera_ronda_procesada = True
        self._primera_ronda_skip = True  # La primera ronda es incompleta, no contar
        self.config.MODO_HISTORICO = False
        self._barra_activa = False
        self._ultimo_tick = time.time()

        self.dash.estado['sheets'] = self.sheets.esta_conectado()
        self.dash.estado['balance'] = self.analizador.balance_acumulado
        self.dash.estado['umbral'] = f"D≥{self.config.UMBRAL_DIRECTO}%"
        self.dash.estado['umbral_d'] = self.config.UMBRAL_DIRECTO
        self.dash.estado['umbral_i'] = self.config.UMBRAL_INVERSO
        self.dash.estado['historico'] = self.config.MODO_HISTORICO

        # Rangos de confianza → multiplicador (cargados desde Apuestas)
        self._mult_rangos = [(90, 7), (85, 6), (80, 5), (75, 4), (70, 3), (65, 2), (60, 1)]
        self._hist_stats_cache = {}
        self._hist_stats_ts = 0.0
        self._explorar_contador = 0
        self._piano_proceso = None
        self._piano_rango_actual = None
        self._invertir_x_rango = None  # Se inicializa en _panel_conf del dash
        self.umbral_apostar = EP_UMBRAL_ESTADO

        # ── EP ADAPTATIVO (Opción B: WR rolling de resultados EP) ────────────
        # Registra si cada apuesta EP fue acierto (1) o fallo (0)
        # para calcular el WR rolling y decidir si cambiar a modo INVERSO
        self._ep_hist_resultados: deque = deque(maxlen=15)  # ventana k=15 apuestas
        self._ep_modo_activo: str = 'EP'      # 'EP' o 'INVERSO'
        self._ep_wr_inv_umbral: float = 48.0  # WR por debajo → cambiar a INVERSO
        self._ep_wr_ep_umbral: float = EP_UMBRAL_ESTADO  # WR por encima → volver a EP
        # ─────────────────────────────────────────────────────────────────────

        self._leer_variables(es_inicio=True)

    def _reset_balance_analizador(self):
        self.analizador.balance_acumulado = 1.0
        self.analizador._guardar_balance()

    def _setup_logger(self):
        import os
        log_dir = Path(__file__).parent / self.config.LOG_DIR
        log_dir.mkdir(exist_ok=True)
        
        logger = logging.getLogger("AcertadorDash")
        logger.setLevel(logging.INFO)
        
        fh = logging.FileHandler(log_dir / "acertador_backup.log", encoding='utf-8', errors='replace')
        fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
        logger.addHandler(fh)
        
        fh_consola = logging.FileHandler(log_dir / "consola.log", encoding='utf-8', errors='replace')
        fh_consola.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%d %H:%M:%S'))
        logger.addHandler(fh_consola)
        
        return logger

    def log(self, msg, tipo='info'):
        self.dash.log(msg, tipo)
        self.logger.info(f"{self.dash._log_counter:04d} {msg}")

    def _leer_variables(self, es_inicio=False):
        if not self.sheets.esta_conectado():
            return
        try:
            vars_dict = self.sheets.leer_variables()
            ha = vars_dict.get('HACER_APUESTA', 'SI')
            self.hacer_apuesta = ha.upper() == 'SI'
            t = vars_dict.get('TIEMPO', '50')
            try: self.tiempo_espera = int(t)
            except: self.tiempo_espera = 50
            preparar_val = vars_dict.get('PREPARAR', 'NO')
            self.preparar_activa = preparar_val.upper() == 'SI'
            umbral_dir = vars_dict.get('UMBRAL_DIRECTO') or vars_dict.get('UMBRAL DIRECTO')
            if umbral_dir:
                try: self.config.UMBRAL_DIRECTO = float(str(umbral_dir).replace(',', '.'))
                except: pass
            umbral_inv = vars_dict.get('UMBRAL_INVERSO') or vars_dict.get('UMBRAL INVERSO')
            if umbral_inv:
                try: self.config.UMBRAL_INVERSO = float(str(umbral_inv).replace(',', '.'))
                except: pass
            historico_val = vars_dict.get('HISTORICO', 'NO')
            self.config.MODO_HISTORICO = historico_val.upper() == 'SI'
            try: self.config.CONF_UMBRAL = float(str(vars_dict.get('CONF_UMBRAL', '60')).replace(',', '.'))
            except: self.config.CONF_UMBRAL = 60.0
            try: self.config.MULT_MAXIMO = int(vars_dict.get('MULT_MAXIMO', '4'))
            except: self.config.MULT_MAXIMO = 4
            try: self.umbral_apostar = float(str(vars_dict.get('UMBRAL_APOSTAR', str(EP_UMBRAL_ESTADO))).replace(',', '.'))
            except: self.umbral_apostar = EP_UMBRAL_ESTADO
            self.dash._lbl_umbral_apostar.config(text=f"{self.umbral_apostar:.1f}%")
            self.dash.estado['umbral_d'] = self.config.UMBRAL_DIRECTO
            self.dash.estado['umbral_i'] = self.config.UMBRAL_INVERSO
            self.dash.estado['hacer_apuesta'] = self.hacer_apuesta
            self.dash.estado['historico'] = self.config.MODO_HISTORICO
            # Recargar rangos multiplicador desde pestaña Apuestas
            rangos = self.sheets.leer_mult_rangos()
            if rangos:
                self._mult_rangos = rangos
            if not es_inicio:
                self.log(f"📋 Variables: HACER_APUESTA={ha} TIEMPO={self.tiempo_espera}s PREPARAR={'SI' if self.preparar_activa else 'NO'} HISTORICO={'SI' if self.config.MODO_HISTORICO else 'NO'} UMBRAL_APOSTAR={self.umbral_apostar:.1f}%")
                self.log(f"📊 Mult.rangos: {' | '.join(f'≥{u:.0f}%→x{m}' for u,m in self._mult_rangos)}", 'dim')
        except Exception as e:
            self.log(f"⚠️ Variables: {e}", 'warn')

    def _ejecutar_preparar(self):
        if not self.preparar_activa:
            self.log("ℹ️ PREPARAR=NO, omitiendo preparar.py", 'dim')
            return
        try:
            self.log("▶ Ejecutando preparar.py (background)...")
            subprocess.Popen(
                ['py', 'preparar.py'],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            self.log("✅ preparar.py lanzado", 'ok')
        except Exception as e:
            self.log(f"⚠️ preparar.py error: {e}", 'warn')

    async def _correr_barra_tiempo(self):
        duracion = self.tiempo_espera
        self.dash.estado['tiempo_total'] = duracion
        self.dash.estado['tiempo_restante'] = duracion
        self.dash.estado['tiempo_activo'] = True
        self._barra_activa = True
        self._ultimo_tick = time.time()
        # self.log(f"⏱ INICIANDO BARRA: {duracion}s", 'dim')
        try:
            for i in range(duracion, 0, -1):
                self.dash.estado['tiempo_restante'] = i
                self._ultimo_tick = time.time()
                # Forzar actualización de UI
                self.dash.root.after(0, self.dash._actualizar_ui)
                await asyncio.sleep(1)
            self.dash.estado['tiempo_restante'] = 0
            self.dash.root.after(0, self.dash._actualizar_ui)
            self.log(f"⏰ FIN DE TIEMPO — Ronda: {self.r_id_barra}")
            # Sonido suave al finalizar la barra
            try:
                import winsound
                winsound.Beep(880, 120)   # La5, 120ms — tono suave y corto
            except Exception:
                pass
            # ── PROCESO EP: decisión de apuesta con modo adaptativo ──────────
            try:
                if self.vol_blue + self.vol_red > 0:
                    color_mayor  = "AZUL" if self.vol_blue > self.vol_red else "ROJO"
                    color_menor  = "ROJO" if color_mayor == "AZUL" else "AZUL"
                    rango_act    = self.dash.estado.get('rango', '---')

                    self.log(f"━━ INICIO PROCESO EP ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", 'dim')
                    self.log(
                        f"  Ronda={self.r_id_barra or self.r_id_actual} | "
                        f"Rango={rango_act} | "
                        f"Vol Azul={self.vol_blue:.0f} Rojo={self.vol_red:.0f} | "
                        f"Color mayor={color_mayor}", 'dim')
                    self.log(
                        f"  EP={self.dash.ep_activa} | "
                        f"Mult={self.dash.mult_activo} | "
                        f"Hacer apuesta={self.dash.estado.get('hacer_apuesta', self.hacer_apuesta)}", 'dim')

                    # ── 1. EP ACTIVO: evaluar modo + adaptativo ───────────────
                    if self.dash.ep_activa:
                        self.log(f"  [EP ON] → llamando _ep_evaluar_modo({rango_act})", 'dim')
                        modo_ep, wr_ep, skip_ep = self._ep_evaluar_modo(rango_act)
                        self.log(
                            f"  [EP ON] ← resultado: modo={modo_ep} | "
                            f"WR={wr_ep:.1f}% | skip={skip_ep}", 'dim')

                        if skip_ep:
                            self.log(
                                f"  [EP ON] ⏭ SKIP — WR={wr_ep:.1f}% "
                                f"< umbral {EP_UMBRAL_ESTADO:.1f}% o sin datos", 'dim')
                            self.log(f"  [EP ON] pending=SKIP — sin apuesta esta ronda", 'dim')
                            subprocess.Popen([r'c:\Python\voice.exe', 'skip'])
                            self._pending_entry = {
                                'timestamp': datetime.now().strftime('%H:%M:%S'),
                                'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
                                'rango':     rango_act,
                                'dif':       self.dash.estado.get('dif', 0),
                                'prevision': 'N/A',
                                'modo':      'SKIP',
                                'estrategia': '---',
                                'confianza': '---',
                                'mult':      None,
                                'ep':        'ON',
                                'ganador':   None,
                                'pnl':       None,
                                'balance':   None,
                                'acierto':   None,
                            }
                            self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
                            self.dash.agregar_historico(self._pending_entry)
                        else:
                            # Modo EP decide el color a apostar
                            if modo_ep == 'DIRECTO':
                                color_apuesta = color_mayor
                                prevision     = "Azul" if color_mayor == "AZUL" else "Rojo"
                                estrategia    = 'DIRECTA'
                            else:  # INVERSO
                                color_apuesta = color_menor
                                prevision     = "Azul" if color_menor == "AZUL" else "Rojo"
                                estrategia    = 'INVERSA'

                            self.log(
                                f"  [EP ON] Modo={modo_ep} | color mayor={color_mayor} → "
                                f"apostando {color_apuesta} ({estrategia})", 'dim')

                            # ── 2. Multiplicador según WR EP ─────────────────
                            if self.dash.mult_activo:
                                mult = self._mult_por_confianza(wr_ep)
                                self.log(
                                    f"  [EP ON] Mult: WR={wr_ep:.1f}% → "
                                    f"_mult_por_confianza → x{mult}", 'dim')
                            else:
                                mult = 1
                                self.log(f"  [EP ON] Mult desactivado → x1", 'dim')

                            self.log(
                                f"  [EP ON] ★ DECISIÓN FINAL: {color_apuesta} x{mult} | "
                                f"modo={modo_ep} | WR={wr_ep:.1f}% | "
                                f"adaptativo=[{self._ep_modo_activo}]", 'ok')

                            # === VALIDACIÓN UMBRAL_CORE (solo TOP5) — 🚀 CON CACHE ===
                            rango_en_top5 = True
                            if self.dash.rangos_activo:
                                # 🚀 OPTIMIZACIÓN: Obtener TOP5 desde cache (5 seg TTL)
                                top5_debug, ops_para_umbral = self._obtener_top5_cached()
                                self.log(f"  [UMBRAL] {len(ops_para_umbral)} ops (cache)", 'dim')

                                if len(ops_para_umbral) == 0:
                                    self.log(f"  [UMBRAL] ⚠️ NO HAY DATOS - SKIP forzado", 'warn')
                                    subprocess.Popen([r'c:\Python\voice.exe', 'skip'])
                                    self._pending_entry = {
                                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                                        'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
                                        'rango':     rango_act,
                                        'dif':       self.dash.estado.get('dif', 0),
                                        'prevision': prevision,
                                        'modo':      'SKIP',
                                        'estrategia': '---',
                                        'confianza': '---',
                                        'mult':      None,
                                        'ep':        'ON',
                                        'ganador':   None,
                                        'pnl':       None,
                                        'balance':   None,
                                        'acierto':   None,
                                    }
                                    self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
                                    self.dash.agregar_historico(self._pending_entry)
                                    skip_ep = True
                                elif top5_debug and not skip_ep:
                                    top5_str = ", ".join([f"{r['rango']}({r['modo']}:{r['ratio']:+.2f})" for r in top5_debug])
                                    self.log(f"  [UMBRAL] TOP5: {top5_str}", 'dim')

                                    from umbral_core import umbral_validar_rango
                                    val_umbral = umbral_validar_rango(ops_para_umbral, rango_act, modo_ep, ratio_minimo=0.15, top5_only=True)
                                    self.log(f"  [UMBRAL] {val_umbral['razon']}", 'dim')
                                    rango_en_top5 = val_umbral['validar']
                                    if not val_umbral['validar']:
                                        self.log(f"  [UMBRAL] ⏭ SKIP — {rango_act}|{modo_ep}: {val_umbral['razon']}", 'warn')
                                        subprocess.Popen([r'c:\Python\voice.exe', 'skip'])
                                        self._pending_entry = {
                                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                                            'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
                                            'rango':     rango_act,
                                            'dif':       self.dash.estado.get('dif', 0),
                                            'prevision': prevision,
                                            'modo':      'SKIP',
                                            'estrategia': '---',
                                            'confianza': '---',
                                            'mult':      None,
                                            'ep':        'ON',
                                            'ganador':   None,
                                            'pnl':       None,
                                            'balance':   None,
                                            'acierto':   None,
                                        }
                                        self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
                                        self.dash.agregar_historico(self._pending_entry)
                                        skip_ep = True

                            if not skip_ep:
                                # Voz con información de TOP5
                                voz_top5 = f"Top5 {rango_act}" if rango_en_top5 else f"Fuera de top5 {rango_act}"
                                subprocess.Popen([
                                    r'c:\Python\voice.exe',
                                    f'{"inverso" if modo_ep == "INVERSO" else ""} '
                                    f'Color {color_apuesta} por {mult} {voz_top5}'.strip()
                                ])

                                self._pending_entry = {
                                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                                    'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
                                    'rango':     rango_act,
                                    'dif':       self.dash.estado.get('dif', 0),
                                    'prevision': prevision,
                                    'modo':      modo_ep,
                                    'estrategia': estrategia,
                                    'confianza': f"{wr_ep:.0f}%",
                                    'mult':      mult,
                                    'ep':        'ON',
                                    'ganador':   None,
                                    'pnl':       None,
                                    'balance':   None,
                                    'acierto':   None,
                                }
                                self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
                                self.dash.agregar_historico(self._pending_entry)
                                self.dash.estado['prevision'] = prevision
                                self.dash.estado['modo']      = modo_ep
                                self.dash.estado['mult']      = mult

                                self.log(f"  [EP ON] Pending guardado → lanzando apuesta.py...", 'dim')
                                self.log(f"🎯 APUESTA EP: {color_apuesta} x{mult} [{estrategia}]", 'ok')
                                if self.dash.estado.get('hacer_apuesta', self.hacer_apuesta):
                                    if self.apuesta_ejecutada:
                                        self.log(f"  [EP ON] ⏭ Apuesta ya ejecutada, omitir", 'dim')
                                    else:
                                        asyncio.ensure_future(self._ejecutar_apuesta(color_apuesta, mult))
                                else:
                                    self.log(f"👁 OBSERVANDO EP — ({color_apuesta} x{mult})", 'warn')

                    # ── EP DESACTIVADO: apuesta directa clásica ───────────────
                    else:
                        self.log(f"  [EP OFF] Modo clásico DIRECTO — apostando color mayor", 'dim')
                        prevision = "Azul" if color_mayor == "AZUL" else "Rojo"
                        mult = 1
                        if self.dash.mult_activo:
                            rec   = self.dash._parsear_reconstructor_txt()
                            rd    = rec.get(rango_act, {})
                            ops_r = rd.get('ops', 0)
                            gan_r = rd.get('ganadas', 0)
                            self.log(
                                f"  [EP OFF] Reconstructor {rango_act}: "
                                f"ops={ops_r} gan={gan_r}", 'dim')
                            if ops_r >= 3:
                                wr_r = gan_r / ops_r * 100
                                mult = ep_mult(wr_r)
                                self.log(f"  [EP OFF] ★ MULT EP: WR={wr_r:.1f}% → x{mult}", 'ok')
                            else:
                                self.log(f"  [EP OFF] ★ MULT EP: sin datos ({ops_r} ops) → x1", 'dim')
                        else:
                            self.log(f"  [EP OFF] Mult desactivado → x1", 'dim')

                        self.log(
                            f"  [EP OFF] ★ DECISIÓN FINAL: {color_mayor} x{mult} | modo=DIRECTO", 'ok')

                        subprocess.Popen([r'c:\Python\voice.exe', f'Color {color_mayor} por {mult}'])
                        self._pending_entry = {
                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                            'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
                            'rango':     rango_act,
                            'dif':       self.dash.estado.get('dif', 0),
                            'prevision': prevision,
                            'modo':      'DIRECTO',
                            'estrategia':'DIRECTA',
                            'confianza': '---',
                            'mult':      mult,
                            'ep':        'OFF',
                            'ganador':   None,
                            'pnl':       None,
                            'balance':   None,
                            'acierto':   None,
                        }
                        self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
                        self.dash.agregar_historico(self._pending_entry)

                        self.log(f"🎯 APUESTA DIRECTA: {color_mayor} x{mult}", 'ok')
                        
                        # === VALIDACIÓN UMBRAL_CORE (solo TOP5) para EP OFF — 🚀 CON CACHE ===
                        skip_ep_off = False
                        if self.dash.rangos_activo:
                            # 🚀 OPTIMIZACIÓN: Obtener TOP5 desde cache (5 seg TTL)
                            top5_off, ops_para_umbral_off = self._obtener_top5_cached()

                            if not ops_para_umbral_off:
                                self.log(f"  [UMBRAL-OFF] ⚠️ NO HAY DATOS - SKIP forzado", 'warn')
                                skip_ep_off = True
                                self._pending_entry['modo'] = 'SKIP'
                            elif top5_off:
                                from umbral_core import umbral_validar_rango
                                val_umbral_off = umbral_validar_rango(ops_para_umbral_off, rango_act, 'DIRECTO', ratio_minimo=0.15, top5_only=True)
                                self.log(f"  [UMBRAL-OFF] {val_umbral_off['razon']}", 'dim')
                                if not val_umbral_off['validar']:
                                    self.log(f"  [UMBRAL-OFF] ⏭ SKIP — {rango_act}|DIRECTO: {val_umbral_off['razon']}", 'warn')
                                    skip_ep_off = True
                                    self._pending_entry['modo'] = 'SKIP'
                        
                        if self.dash.estado.get('hacer_apuesta', self.hacer_apuesta) and not skip_ep_off:
                            if self.apuesta_ejecutada:
                                self.log(f"  ⏭ Apuesta ya ejecutada, omitir", 'dim')
                            else:
                                asyncio.ensure_future(self._ejecutar_apuesta(color_mayor, mult))
                        elif skip_ep_off:
                            self.log(f"  [UMBRAL-OFF] ⏭ APuesta cancelada por TOP5", 'warn')
                        else:
                            self.log(f"👁 OBSERVANDO — ({color_mayor} x{mult})", 'warn')

                else:
                    self.log(
                        f"  ⚠️ Sin volumen — "
                        f"vol_blue={self.vol_blue} vol_red={self.vol_red} — sin apuesta", 'warn')

            except Exception as e:
                self.log(f"❌ Error proceso EP: {e}", 'err')
            self.log(f"━━ FIN PROCESO EP ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", 'dim')
            # ── FIN PROCESO EP ────────────────────────────────────────────────
            # Detener el piano al final de la barra
            try:
                if self._piano_proceso:
                    self._piano_proceso.terminate()
                    time.sleep(0.1)
                    if self._piano_proceso.poll() is None:
                        self._piano_proceso.kill()
                    self._piano_proceso = None
            except:
                pass
            # Voz heatmap después del análisis final (usa el rango actualizado)
            try:
                rango_act = getattr(self.dash, '_rango_activo', None)
                if rango_act:
                    mayor_bando_voz = getattr(self, '_ultimo_mayor_bando', '')
                    voz = self.dash._generar_voz_heatmap(self.analizador.stats_rangos, rango_act, mayor_bando_voz)
                    if voz:
                        # Si el botón INVERTIR está activo, intercambiar Azul/Rojo en el mensaje de voz
                        if self.dash.estado.get('invertir_apuesta', False):
                            voz = voz.replace('Color Azul', 'Color __ROJO__').replace('Color Rojo', 'Color __AZUL__')
                            voz = voz.replace('__ROJO__', 'Rojo').replace('__AZUL__', 'Azul')
                            voz = voz.replace('Apuesta directa', 'Apuesta directa INVERTIDA').replace('Apuesta inversa', 'Apuesta inversa INVERTIDA')
                        subprocess.Popen([r'c:\Python\voice.exe', voz])
            except:
                pass

            # Ejecutar apuesta si no es SKIP — tarea independiente para no ser cancelada por drawed
            pending = getattr(self, '_pending_entry', None)
            if pending and pending.get('modo') not in ('SKIP', None):
                prevision = pending.get('prevision', '')
                color = 'AZUL' if 'azul' in prevision.lower() else 'ROJO'

                # 🔧 Si el modo es INVERSO, invertir el color automáticamente
                modo_pending = pending.get('modo', 'DIRECTO')
                if modo_pending == 'INVERSO':
                    color = 'ROJO' if color == 'AZUL' else 'AZUL'
                    self.log(f"🔄 INVERSO activo: {prevision} → {color}", 'warn')

                # Usar mult del pending si es válido (>= 1), si no usar 1
                _mult_raw = pending.get('mult')
                mult = int(_mult_raw) if (_mult_raw is not None and int(_mult_raw) >= 1) else 1
                # Aplicar inversión manual si el usuario la ha activado (lee del reconstructor)
                if self.dash.estado.get('invertir_apuesta', False):
                    _rango_inv = getattr(self.dash, '_rango_activo', None) or self.dash.estado.get('rango', '')
                    _rec = self.dash._parsear_reconstructor_txt()
                    _rd = _rec.get(_rango_inv, {})
                    _r_g, _r_p = _rd.get('ganadas', 0), _rd.get('perdidas', 0)
                    if _r_p > _r_g and _r_g + _r_p >= 3:
                        color_invertido = 'ROJO' if color == 'AZUL' else 'AZUL'
                        self.log(f"🔄 INVERSIÓN REC: {color} → {color_invertido} | G={_r_g} P={_r_p} | {_rango_inv}", 'warn')
                        color = color_invertido
                        pending['prevision'] = 'Azul' if color == 'AZUL' else 'Rojo'
                        self.dash.estado['modo'] = 'INVERSO'
                        self.dash.estado['prevision'] = pending['prevision']
                        self.dash.root.after(0, self.dash._actualizar_ui)
                    else:
                        self.log(f"🔄 INVERSIÓN NO APLICADA: G={_r_g} P={_r_p} | {_rango_inv}", 'dim')
                modo_filtro = "ESTRATEGIA PERFECTA" if self.dash.ep_activa else "FILTRO CLASICO"
                
                # Validación TOP5 para Filtro Clásico (solo si RANGOS ON)
                rango_act = getattr(self.dash, '_rango_activo', None) or self.dash.estado.get('rango', '')
                skip_top5_clasico = False
                if self.dash.rangos_activo:
                    try:
                        from umbral_core import umbral_validar_rango, cargar_ops_desde_archivo, cargar_ops_desde_reconstructor
                        ops_top5 = cargar_ops_desde_archivo()
                        if not ops_top5:
                            ops_top5 = cargar_ops_desde_reconstructor()
                        if ops_top5:
                            modo_validar = pending.get('modo', 'DIRECTO')
                            val_top5 = umbral_validar_rango(ops_top5, rango_act, modo_validar, ratio_minimo=0.15, top5_only=True)
                            self.log(f"  [TOP5-CL] {val_top5['razon']}", 'dim')
                            if not val_top5['validar']:
                                skip_top5_clasico = True
                    except Exception as e:
                        self.log(f"  [TOP5-CL] Error validación: {e}", 'warn')
                
                self.log(f"★ {modo_filtro} | {color} x{mult}", 'ok')
                if skip_top5_clasico:
                    self.log(f"⏭ SKIP — {rango_act} no está en TOP5 (Filtro Clásico)", 'warn')
                elif self.dash.estado.get('hacer_apuesta', self.hacer_apuesta):
                    if self.apuesta_ejecutada:
                        self.log(f"⏭ Apuesta ya ejecutada en esta ronda, omitir", 'dim')
                    else:
                        self.log(f"▶ Lanzando apuesta.py {color} x{mult}...", 'dim')
                        asyncio.ensure_future(self._ejecutar_apuesta(color, mult))
                else:
                    self.log(f"👁 OBSERVANDO — apuesta desactivada en Sheets ({color} x{mult})", 'warn')
        except asyncio.CancelledError:
            self.log(f"⏹ Barra cancelada — Ronda: {self.r_id_barra}", 'dim')
        except Exception as e:
            self.log(f"❌ Error en contador: {e}", 'err')
        finally:
            self.dash.estado['tiempo_activo'] = False
            self._barra_activa = False

    def _ep_evaluar_modo(self, rango: str, MIN_OPS: int = 10) -> tuple:
        """
        Decide qué modo usar para este rango aplicando la lógica EP completa:

        1. Consulta ventana_rangos (rolling 50 ops) → WR DIRECTO y WR INVERSO
        2. Determina el modo base con mejor WR (si supera EP_UMBRAL_ESTADO)
        3. Aplica lógica adaptativa Opción B:
              Si WR rolling de resultados EP recientes < _ep_wr_inv_umbral → MODO INVERSO
              Si en modo INVERSO ese WR vuelve >= _ep_wr_ep_umbral       → vuelve a EP
        4. Devuelve (modo_final, wr_final, skip)
              modo_final : 'DIRECTO' | 'INVERSO'
              wr_final   : float  — WR del modo elegido en ventana rolling
              skip       : bool   — True si no hay datos suficientes o WR < umbral
        """
        self.log(f"┌─ EP EVALUAR MODO [{rango}] ─────────────────────────────", 'dim')

        # ── PASO 1: Leer ventana rolling por rango ───────────────────────────
        vent = self.analizador.ventana_rangos.get(rango, {})
        dq_d = vent.get('DIRECTO', deque())
        dq_i = vent.get('INVERSO', deque())
        n_d, n_i = len(dq_d), len(dq_i)

        wr_d = sum(dq_d) / n_d * 100 if n_d >= MIN_OPS else None
        wr_i = sum(dq_i) / n_i * 100 if n_i >= MIN_OPS else None

        self.log(
            f"│  PASO 1 · Ventana rolling (min {MIN_OPS} ops): "
            f"DIR={n_d} ops / WR={'--' if wr_d is None else f'{wr_d:.1f}%'} | "
            f"INV={n_i} ops / WR={'--' if wr_i is None else f'{wr_i:.1f}%'}", 'dim')

        # Sin datos suficientes → SKIP
        if wr_d is None and wr_i is None:
            self.log(f"│  PASO 1 · ⏭ SKIP — sin datos suficientes en ventana (min {MIN_OPS} ops)", 'dim')
            self.log(f"└─────────────────────────────────────────────────────────", 'dim')
            return ('DIRECTO', 0.0, True)

        # ── PASO 2: Elegir modo base por WR más alto ─────────────────────────
        wr_d = wr_d or 0.0
        wr_i = wr_i or 0.0
        if wr_d >= wr_i:
            modo_base, wr_base = 'DIRECTO', wr_d
        else:
            modo_base, wr_base = 'INVERSO', wr_i

        self.log(
            f"│  PASO 2 · Modo base elegido: {modo_base} "
            f"(WR={wr_base:.1f}% | umbral EP={EP_UMBRAL_ESTADO:.1f}%)", 'dim')

        # WR insuficiente → SKIP
        if wr_base < EP_UMBRAL_ESTADO:
            self.log(
                f"│  PASO 2 · ⏭ SKIP — WR {wr_base:.1f}% < umbral {EP_UMBRAL_ESTADO:.1f}%", 'dim')
            self.log(f"└─────────────────────────────────────────────────────────", 'dim')
            return (modo_base, wr_base, True)

        # ── PASO 3: Lógica adaptativa Opción B ──────────────────────────────
        n_hist = len(self._ep_hist_resultados)
        wr_ep_roll = sum(self._ep_hist_resultados) / n_hist * 100 if n_hist > 0 else 0.0

        self.log(
            f"│  PASO 3 · Adaptativo [{self._ep_modo_activo}]: "
            f"historial={n_hist} muestras | WR roll={wr_ep_roll:.1f}% "
            f"(umbral INV<{self._ep_wr_inv_umbral:.1f}% | umbral EP≥{self._ep_wr_ep_umbral:.1f}%)", 'dim')

        if n_hist >= MIN_OPS:
            if self._ep_modo_activo == 'EP' and wr_ep_roll < self._ep_wr_inv_umbral:
                self._ep_modo_activo = 'INVERSO'
                self.log(
                    f"│  PASO 3 · 🔀 CAMBIO → MODO INVERSO "
                    f"(WR roll {wr_ep_roll:.1f}% < umbral {self._ep_wr_inv_umbral:.1f}%)", 'warn')
                subprocess.Popen([r'c:\Python\voice.exe', 'modo inverso adaptativo'])
            elif self._ep_modo_activo == 'INVERSO' and wr_ep_roll >= self._ep_wr_ep_umbral:
                self._ep_modo_activo = 'EP'
                self.log(
                    f"│  PASO 3 · 🔀 CAMBIO → MODO EP recuperado "
                    f"(WR roll {wr_ep_roll:.1f}% ≥ umbral {self._ep_wr_ep_umbral:.1f}%)", 'ok')
                subprocess.Popen([r'c:\Python\voice.exe', 'modo e pe recuperado'])
            else:
                self.log(
                    f"│  PASO 3 · Sin cambio de modo — "
                    f"sigue en [{self._ep_modo_activo}]", 'dim')
        else:
            self.log(
                f"│  PASO 3 · Adaptativo inactivo — "
                f"faltan {MIN_OPS - n_hist} muestras para activar", 'dim')

        # ── PASO 4: Aplicar inversión adaptativa al modo base ────────────────
        if self._ep_modo_activo == 'INVERSO':
            modo_final = 'INVERSO' if modo_base == 'DIRECTO' else 'DIRECTO'
            self.log(
                f"│  PASO 4 · Modo base {modo_base} → invertido por adaptativo → {modo_final}", 'dim')
        else:
            modo_final = modo_base
            self.log(f"│  PASO 4 · Modo final = {modo_final} (sin inversión adaptativa)", 'dim')

        self.log(
            f"└─ RESULTADO: modo={modo_final} | WR={wr_base:.1f}% | skip=False", 'dim')

        return (modo_final, wr_base, False)

    def _calcular_confianza(self, rango: str, modo_value: str) -> float:
        s = self.analizador.stats_rangos.get(rango, {})
        if modo_value == 'SKIP':
            # SKIP no tiene stats propios; devolver la mejor confianza entre DIR e INV
            best = 0.0
            for mk in ('DIRECTO', 'INVERSO'):
                bloque = s.get(mk, {})
                if isinstance(bloque, dict) and bloque.get('ops', 0) >= 3:
                    best = max(best, round(bloque['ganadas'] / bloque['ops'] * 100, 1))
            return best
        modo_key = modo_value
        bloque = s.get(modo_key, {})
        if not isinstance(bloque, dict):
            return 0.0
        ops = bloque.get('ops', 0)
        gan = bloque.get('ganadas', 0)
        return round(gan / ops * 100, 1) if ops >= 3 else 0.0

    async def _calcular_y_apostar(self):
        if not self.datos_tick29_fallback:
            self.log("⚠️ Sin datos para apuesta", 'warn')
            return

        # Leer mejorconf.json al inicio de ronda
        mejor_conf_json = None
        try:
            with open(BASE / 'mejorconf.json', 'r', encoding='utf-8') as f:
                mejor_conf_json = json.load(f)
        except:
            pass

        # Usar siempre el rango y stats del HeatMap — no recargar ni recalcular
        analisis = self.analizador.ejecutar(self.datos_tick29_fallback, '')
        if self.dash._rango_activo:
            analisis.rango = self.dash._rango_activo  # el rango es el del heatmap, no el del tick actual
        self.log(f"📐 Rango HeatMap: {analisis.rango} | Mayor bando: {analisis.mayor_bando}", 'dim')

        # ── UMBRAL_APOSTAR — filtro primario, pasa por delante de todo ───────────
        _conf_modo_val = getattr(self.dash, '_conf_modo', None)
        _fuente_ua = _conf_modo_val.get() if _conf_modo_val else 'RECONSTRUCTOR'
        if _fuente_ua == 'HISTÓRICO':
            _src_ua = self.dash._stats_conf.get('historico', {})
        else:
            _src_ua = self.dash._stats_conf.get('reconstructor', {}) or self.dash.stats_rangos
        _d_ua = _src_ua.get(analisis.rango, {}).get('DIRECTO', {})
        _i_ua = _src_ua.get(analisis.rango, {}).get('INVERSO', {})
        _d_wr_ua = _d_ua.get('ganadas', 0) / _d_ua.get('ops', 0) * 100 if _d_ua.get('ops', 0) >= 10 else 0
        _i_wr_ua = _i_ua.get('ganadas', 0) / _i_ua.get('ops', 0) * 100 if _i_ua.get('ops', 0) >= 10 else 0
        _best_wr_ua = max(_d_wr_ua, _i_wr_ua)
        # if _best_wr_ua < self.umbral_apostar:
        #     self.log(f"⏭ SKIP — WR {_best_wr_ua:.1f}% < UMBRAL_APOSTAR {self.umbral_apostar:.1f}% [{_fuente_ua}]", 'dim')
        #     return
        # ─────────────────────────────────────────────────────────────────────────

        # Guardar mayor_bando para la voz del heatmap
        self._ultimo_mayor_bando = analisis.mayor_bando

        # ── DECISIÓN POR RECONSTRUCTOR ─────────────────────────────────────
        rec = self.dash._parsear_reconstructor_txt()
        r_data = rec.get(analisis.rango, {})
        r_ops = r_data.get('ops', 0)
        r_gan = r_data.get('ganadas', 0)
        r_per = r_data.get('perdidas', 0)

        # conf inicializada a 0 — se actualiza si hay datos en reconstructor
        conf = 0.0

        # 1. SKIP si no hay datos suficientes
        if self.dash._cond_vars['skip_sin_datos'].get() and r_ops < 3:
            self.log(f"⏭ SKIP — {analisis.rango} sin datos reconstructor ({r_ops} ops)", 'dim')
            return

        # 2. Elegir DIRECTO o INVERSO segun reconstructor
        if self.dash._cond_vars['modo_reconstructor'].get() and r_ops >= 3:
            if r_gan > r_per:
                analisis.modo = ModoApuesta.DIRECTO
                conf = min(((r_gan - r_per) / r_per) * 100, 100.0) if r_per > 0 else 100.0
            elif r_per > r_gan:
                analisis.modo = ModoApuesta.INVERSO
                conf = min(((r_per - r_gan) / r_gan) * 100, 100.0) if r_gan > 0 else 100.0
            else:
                self.log(f"⏭ SKIP — {analisis.rango} empate G={r_gan} P={r_per}", 'dim')
                return
            self.log(f"📊 REC {analisis.rango}: G={r_gan} P={r_per} → {analisis.modo.value} ({conf:.0f}%)", 'dim')

        # 3. SKIP si confianza baja
        if self.dash._cond_vars['skip_conf_baja'].get() and conf < self.config.CONF_UMBRAL:
            self.log(f"⏭ SKIP — {analisis.rango} confianza {conf:.0f}% < umbral {self.config.CONF_UMBRAL:.0f}%", 'dim')
            return

        # 4. Exploración inverso (contador)
        forzado_inverso = False
        if self.dash._cond_vars['explorar_inverso'].get() and self.dash.explorar_inverso:
            self._explorar_contador += 1
            cada = self.dash.explorar_cada
            if self._explorar_contador >= cada:
                self._explorar_contador = 0
                analisis.modo = ModoApuesta.INVERSO
                forzado_inverso = True
                self.log(f"🔬 EXPLORACIÓN INVERSO forzada (cada {cada} apuestas)", 'warn')
                subprocess.Popen([r'c:\Python\voice.exe', 'exploracion inverso'])
            else:
                restante = cada - self._explorar_contador
                self.dash.root.after(0, lambda r=restante, c=cada:
                    self.dash._btn_explorar.config(text=f"EXPLORAR: {c-r+1}/{c}"))

        # Asignar color segun modo
        if analisis.modo == ModoApuesta.INVERSO:
            color = "ROJO" if analisis.mayor_bando.lower() == "azul" else "AZUL"
            analisis.prevision = "Rojo" if analisis.mayor_bando.lower() == "azul" else "Azul"
        else:
            color = "AZUL" if analisis.mayor_bando.lower() == "azul" else "ROJO"
            analisis.prevision = "Azul" if analisis.mayor_bando.lower() == "azul" else "Rojo"

        # Solo aplicar multiplicador si el boton MULT esta activo
        if getattr(self.dash, 'mult_activo', False):
            mult = self._mult_por_confianza(conf) if conf > 0 else 1
        else:
            mult = 1

        self._guardar_pending_entry(analisis, analisis.modo, mult=mult, conf=conf)
        self.log(f"✔ Modo: {analisis.modo.value} | Prev: {analisis.prevision} | Conf: {conf:.0f}% | x{mult}", 'ok')
        self.log(f"🎯 APUESTA: {color} x{mult}", 'ok')
        if mult > 4:
            def _sonido_mult_alto():
                import winsound
                for f, d in [(600,80),(900,80),(1200,80),(1500,150)]:
                    winsound.Beep(f, d)
            import threading as _thr
            _thr.Thread(target=_sonido_mult_alto, daemon=True).start()
        if analisis.modo == ModoApuesta.INVERSO:
            subprocess.Popen([r'c:\Python\voice.exe', 'modo inverso'])

    async def _ejecutar_apuesta(self, color: str, mult: int):
        # Doble verificación para evitar doble ejecución
        if self.apuesta_ejecutada:
            self.log(f"⚠️ BLOQUEO DOBLE EJECUCIÓN - Ronda: {self.r_id_barra}", 'warn')
            return

        # GUARDIA FINAL: verificar TOP5 antes de ejecutar click (solo si RANGOS ON)
        if self.dash.rangos_activo:
            try:
                from umbral_core import umbral_validar_rango, cargar_ops_desde_archivo, cargar_ops_desde_reconstructor
                rango_guard = self.dash.estado.get('rango', '---')
                modo_guard = self._pending_entry.get('modo', 'DIRECTO') if self._pending_entry else 'DIRECTO'
                ops_guard = cargar_ops_desde_archivo()
                if not ops_guard:
                    ops_guard = cargar_ops_desde_reconstructor()
                if not ops_guard:
                    self.log(f"🛑 GUARDIA FINAL: SIN DATOS — apuesta bloqueada", 'warn')
                    if self._pending_entry:
                        self._pending_entry['modo'] = 'SKIP'
                    return
                val_guard = umbral_validar_rango(ops_guard, rango_guard, modo_guard, top5_only=True)
                if not val_guard['validar']:
                    self.log(f"🛑 GUARDIA FINAL: {rango_guard}|{modo_guard} bloqueado — {val_guard['razon']}", 'warn')
                    if self._pending_entry:
                        self._pending_entry['modo'] = 'SKIP'
                    return
                self.log(f"✅ GUARDIA FINAL: {rango_guard}|{modo_guard} OK — {val_guard['razon']}", 'dim')
            except Exception as e:
                self.log(f"🛑 GUARDIA FINAL: error={e} — apuesta bloqueada por seguridad", 'warn')
                if self._pending_entry:
                    self._pending_entry['modo'] = 'SKIP'
                return

        self.apuesta_ejecutada = True  # Marcar inmediatamente para evitar doble ejecución
        self.log(f"▶▶ Ejecutando apuesta: {color} x{mult} (ronda: {self.r_id_barra})", 'ok')
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                ['py', 'apuesta.py', color, str(mult)],
                capture_output=True, text=True, timeout=30))
            for line in result.stdout.splitlines():
                self.log(f"  apuesta▸ {line}", 'dim')
            if result.returncode == 0:
                self.log("✅ apuesta.py OK", 'ok')
            else:
                self.log(f"⚠️ apuesta.py rc={result.returncode}: {result.stderr[:120]}", 'warn')
        except Exception as e:
            self.log(f"⚠️ apuesta.py excepción: {e}", 'warn')

    def _get_stats_historico_cached(self) -> dict:
        """Stats reales del historial, recalculados cada 5 minutos."""
        ahora = time.time()
        if ahora - self._hist_stats_ts < 300 and self._hist_stats_cache:
            return self._hist_stats_cache
        self._hist_stats_cache = self.dash._calcular_stats_historico()
        self._hist_stats_ts = ahora
        return self._hist_stats_cache

    def _mult_por_confianza(self, conf: float) -> int:
        rangos = getattr(self, '_mult_rangos', [(90, 7), (85, 6), (80, 5), (75, 4), (70, 3), (65, 2), (60, 1)])
        for umbral, mult in rangos:  # ordenados de mayor a menor
            if conf >= umbral:
                return mult
        return 1

    def _guardar_pending_entry(self, analisis, modo, mult=None, conf=None):
        _modo_key = modo.value if modo.value not in ('SKIP',) else 'DIRECTO'
        if _modo_key == 'SKIP':
            _modo_key = 'DIRECTO'
        if conf is not None:
            _conf = f"{conf:.0f}%"
        else:
            _s = self.analizador.stats_rangos.get(analisis.rango, {})
            _bloque = _s.get(_modo_key, {})
            _ops = _bloque.get('ops', 0) if isinstance(_bloque, dict) else 0
            _gan = _bloque.get('ganadas', 0) if isinstance(_bloque, dict) else 0
            _conf = f"{_gan/_ops*100:.0f}%" if _ops >= 3 else '---'
        est = 'INVERSO' if modo.value == 'INVERSO' else ('DIRECTO' if modo.value == 'DIRECTO' else '---')

        _mult = (mult if mult is not None else self.config.MULT_MAXIMO) if modo.value != 'SKIP' else None
        # Leer estado del botón EP directamente
        btn_text = self._btn_ep_toggle.cget('text')
        ep_val = 'ON' if 'ON' in btn_text else 'OFF'
        self._pending_entry = {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'ronda':     self.r_id_barra or self.r_id_actual or 'unknown',
            'rango':     analisis.rango,
            'dif':       analisis.dif,
            'prevision': analisis.prevision,
            'modo':      modo.value,
            'estrategia': est,
            'confianza': _conf,
            'mult':      _mult,
            'ep':        ep_val,
            'ganador':   None,
            'pnl':       None,
            'balance':   None,
            'acierto':   None,
        }
        # Pre-asignar iid para que completar() pueda encontrar la fila antes de que root.after la inserte
        self._pending_entry['_tree_iid'] = str(id(self._pending_entry))
        self.dash.agregar_historico(self._pending_entry)
        self.dash.estado['prevision'] = analisis.prevision
        self.dash.estado['modo'] = modo.value
        mult_val = self._pending_entry.get('mult', 1) or 1
        self.dash.estado['mult'] = mult_val

    async def _procesar_game_init(self, data):
        self.r_id_actual = data.get('issue_num') or data.get('issue') or data.get('id') or data.get('round') or 'unknown'
        self.vol_blue = 0.0
        self.vol_red = 0.0
        self.datos_tick29 = None
        self.datos_tick29_fallback = None
        self.dash.estado['ronda'] = self.r_id_actual
        self.dash.estado['modo'] = 'ESPERANDO'
        self.dash.estado['prevision'] = '---'
        self.dash.estado['ganador'] = '---'
        self.dash.estado['mult'] = 1
        self.log(f"🔔 NUEVA RONDA: {self.r_id_actual}")
        # Copiar reconstructor_data_AI.txt desde Z:\Python\Peak al directorio local
        try:
            src = Path(r"Z:\Python\Peak\reconstructor_data_AI.txt")
            dst = Path(__file__).parent / "reconstructor_data_AI.txt"
            if src.exists():
                import shutil
                shutil.copy2(src, dst)
                self.log("📋 reconstructor_data_AI.txt actualizado desde Z:", 'dim')
        except Exception as e:
            self.log(f"⚠️ Error copiando reconstructor_data: {e}", 'warn')
        self.dash.root.after(0, self.dash._actualizar_todos_analisis)
        self._leer_variables()
        self._ejecutar_preparar()
        
        # Cancelar barra anterior correctamente
        if self._tarea_barra and not self._tarea_barra.done():
            self._tarea_barra.cancel()
            try:
                await self._tarea_barra
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.log(f"⚠️ Error cancelando barra: {e}", 'warn')
        
        self.log(f"⏱ TIEMPO INICIADO: {self.tiempo_espera}s | Apuesta: {'SI' if self.hacer_apuesta else 'NO'} | PREPARAR: {'SI' if self.preparar_activa else 'NO'}")
        self.r_id_barra = self.r_id_actual
        self.apuesta_ejecutada = False
        self._tarea_barra = asyncio.create_task(self._correr_barra_tiempo())
        await asyncio.sleep(0)

    async def _procesar_total_bet(self, data):
        issue = data.get('issue') or data.get('issue_num')
        if issue and issue != self.r_id_actual:
            if self.r_id_actual:
                self.log(f"⏰ FIN DE TIEMPO — Ronda: {self.r_id_actual}")
            self.r_id_actual = issue
            self.vol_blue = 0.0
            self.vol_red = 0.0
            self.datos_tick29 = None
            self.datos_tick29_fallback = None
            self.dash._heat_rango_activo = None
            self.dash.estado['ronda'] = self.r_id_actual
            self.dash.estado['modo'] = 'ESPERANDO'
            self.apuesta_ejecutada = False  # Reset para nueva ronda
            self.log(f"🔔 NUEVA RONDA detectada: {self.r_id_actual}")
            # Copiar reconstructor_data_AI.txt desde Z:\Python\Peak al directorio local
            try:
                src = Path(r"Z:\Python\Peak\reconstructor_data_AI.txt")
                dst = Path(__file__).parent / "reconstructor_data_AI.txt"
                if src.exists():
                    import shutil
                    shutil.copy2(src, dst)
                    self.log("📋 reconstructor_data_AI.txt actualizado desde Z:", 'dim')
            except Exception as e:
                self.log(f"⚠️ Error copiando reconstructor_data: {e}", 'warn')
            self.dash.root.after(0, self.dash._actualizar_todos_analisis)
            self._leer_variables()
            self._ejecutar_preparar()
            
            # Cancelar barra anterior correctamente
            if self._tarea_barra and not self._tarea_barra.done():
                self._tarea_barra.cancel()
                try:
                    await self._tarea_barra
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self.log(f"⚠️ Error cancelando barra: {e}", 'warn')
            
            self.log(f"⏱ TIEMPO: {self.tiempo_espera}s | Apuesta: {'SI' if self.hacer_apuesta else 'NO'} | PREPARAR: {'SI' if self.preparar_activa else 'NO'}")
            self.r_id_barra = self.r_id_actual
            self.apuesta_ejecutada = False
            self._tarea_barra = asyncio.create_task(self._correr_barra_tiempo())
            await asyncio.sleep(0)

        if not getattr(self, '_primera_ronda_procesada', False):
            return

        self.vol_blue = float(data.get('blue', 0.0))
        self.vol_red  = float(data.get('red', 0.0))
        total = self.vol_blue + self.vol_red
        p_b = round((self.vol_blue / total) * 100, 2) if total > 0 else 50.0
        p_r = round((self.vol_red  / total) * 100, 2) if total > 0 else 50.0
        if self._barra_activa:
            color_tick = "AZUL" if self.vol_blue > self.vol_red else "ROJO"
            prevision_tick = "Azul" if color_tick == "AZUL" else "Rojo"
            if self.dash.log_colores:
                tag_tick = 'tick_azul' if color_tick == "AZUL" else 'tick_rojo'
                self.log(f"● B:{p_b:.1f}% R:{p_r:.1f}% → {color_tick}", tag_tick)
            self.dash.estado['prevision'] = prevision_tick
            col_prev = '#00BFFF' if color_tick == "AZUL" else '#FF4444'
            self.dash.root.after(0, lambda p=prevision_tick, c=col_prev:
                self.dash._lbl_prev2.config(text=p, fg=c))
            # Actualizar pending en cada tick para que drawed tenga datos si llega antes
            # No sobreescribir si ya hay un pending con ronda asignada (viene de la barra)
            if not self._pending_entry or not self._pending_entry.get('ronda'):
                self._pending_entry = {
                    'modo': 'DIRECTO',
                    'prevision': prevision_tick,
                    'mult': 1,
                    'conf': 0,
                    'rango': self.dash.estado.get('rango', '---'),
                    'ep': 'ON' if self.dash.ep_activa else 'OFF',
                    'ronda': self.r_id_actual or 'unknown',
                }

        self.datos_tick29_fallback = DatosTick29(p_b, p_r, self.vol_blue, self.vol_red)
        try:
            analisis = self.analizador.ejecutar(self.datos_tick29_fallback, '', )
            self.dash.estado['dif'] = analisis.dif
            self.dash.estado['rango'] = analisis.rango if analisis.rango else "---"
            if analisis.rango and hasattr(self.dash, '_rangos_frame'):
                self.dash._rango_activo = analisis.rango
                self.dash._heat_rango_activo = analisis.rango
                self.dash._actualizar_rangos(analisis.rango)

                # Reproducir nota de piano según el rango (misma frecuencia toda la ronda)
                try:
                    tiempo_r = self.dash.estado.get('tiempo_restante', 0)
                    if tiempo_r > 5:
                        # Solo generar nueva frecuencia si cambia el rango
                        if analisis.rango != self._piano_rango_actual:
                            self._piano_rango_actual = analisis.rango
                            import os
                            if self._piano_proceso and self._piano_proceso.poll() is None:
                                self._piano_proceso.terminate()
                            ruta_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'piano_rangos.py')
                            self._piano_proceso = subprocess.Popen(
                                ['py', ruta_script, analisis.rango],
                                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                            )
                except:
                    pass

                # Verificar si la confianza del rango activo es la mayor de todas
                stats = self.analizador.stats_rangos
                if stats and analisis.rango in stats:
                    rango_data = stats[analisis.rango]
                    modo_elegido = getattr(self.dash, '_conf_modo', None)
                    fuente = modo_elegido.get() if modo_elegido else 'RECONSTRUCTOR'
                    if fuente == 'HISTÓRICO':
                        src_stats = self.dash._stats_conf.get('historico', {})
                    else:
                        src_stats = self.dash._stats_conf.get('reconstructor', {}) or stats
                    rng_src = src_stats.get(analisis.rango, {})
                    mejor_conf = 0
                    for m in ('DIRECTO', 'INVERSO'):
                        blo = rng_src.get(m, {})
                        ops = blo.get('ops', 0)
                        if ops >= 3:
                            gan = blo.get('ganadas', 0)
                            conf = gan / ops * 100
                            mejor_conf = max(mejor_conf, conf)
                    # Comparar con todos los rangos
                    es_maxima = True
                    for rng, dat in src_stats.items():
                        if rng == analisis.rango:
                            continue
                        for m in ('DIRECTO', 'INVERSO'):
                            blo = dat.get(m, {})
                            ops = blo.get('ops', 0)
                            if ops >= 3:
                                gan = blo.get('ganadas', 0)
                                conf = gan / ops * 100
                                if conf > mejor_conf:
                                    es_maxima = False
                                    break
                        if not es_maxima:
                            break
                    if es_maxima and mejor_conf > 0:
                        # self.log(f"🔥 UFF — Rango {analisis.rango} tiene la mejor confianza: {mejor_conf:.0f}%", 'ok')
                        subprocess.Popen([r'c:\Python\voice.exe', 'uff'])

            self.dash.estado['modo'] = 'ANALIZANDO'
        except:
            pass

        self.dash.estado.update({
            'p_azul': p_b, 'p_rojo': p_r,
            'v_azul': self.vol_blue, 'v_rojo': self.vol_red,
            'racha': self.analizador._calcular_racha(),
        })

    async def _procesar_drawed(self, data):
        if self._tarea_barra and not self._tarea_barra.done():
            self._tarea_barra.cancel()

        self._primera_ronda_procesada = True

        if self._primera_ronda_skip:
            self._primera_ronda_skip = False
            self.log("ℹ️ Primera ronda incompleta, no se cuenta", 'dim')
            self._pending_entry = None
            self.r_id_actual = None
            return

        issue = data.get('issue') or self.r_id_actual or 'unknown'
        result_raw = data.get('result', '').lower()
        winner = 'azul' if 'blue' in result_raw else ('rojo' if 'red' in result_raw else None)

        if winner:
            datos = self.datos_tick29 or self.datos_tick29_fallback
            if datos and datos.validar():
                analisis = self.analizador.ejecutar(datos, winner)

                mayor_gana = analisis.mayor_bando == winner
                self.analizador.actualizar_historial(mayor_gana)

                # Determinar si el bot realmente apostó o fue SKIP
                _pending_now = getattr(self, '_pending_entry', None)
                fue_skip_real = _pending_now is None or _pending_now.get('modo') == 'SKIP'

                # Usar modo y mult del pending (tiempo de apuesta), no del analisis recalculado
                if fue_skip_real:
                    acierto = False
                    pnl = 0.0
                    modo_real = ModoApuesta.SKIP
                else:
                    modo_str = _pending_now.get('modo', 'DIRECTO')
                    modo_real = ModoApuesta.DIRECTO if modo_str == 'DIRECTO' else ModoApuesta.INVERSO
                    # Comparar prevision (color apostado) con el ganador real — es lo más fiable
                    prev_color = _pending_now.get('prevision', '').lower()
                    if 'azul' in prev_color or 'blue' in prev_color:
                        bet_color = 'azul'
                    elif 'rojo' in prev_color or 'red' in prev_color:
                        bet_color = 'rojo'
                    else:
                        bet_color = None
                    acierto = (bet_color == winner) if bet_color else False
                    mult = _pending_now.get('mult') or 1
                    pnl = (self.config.PNL_ACIERTO if acierto else self.config.PNL_FALLO) * mult

                # Usar rango del momento de la apuesta (pending), no del drawed
                rango_apuesta = (_pending_now.get('rango') if _pending_now else None) or analisis.rango

                if modo_real != ModoApuesta.SKIP:
                    self.analizador.balance_acumulado += pnl
                    # Actualizar ventana EP con el resultado real
                    self.analizador.actualizar_ventana(rango_apuesta, modo_real.value, acierto)
                    # ── Registrar resultado en historial EP adaptativo ────────
                    # Solo si EP estaba activo en esta apuesta
                    if _pending_now and _pending_now.get('ep') == 'ON':
                        self._ep_hist_resultados.append(1 if acierto else 0)
                        n_h = len(self._ep_hist_resultados)
                        wr_r = sum(self._ep_hist_resultados) / n_h * 100 if n_h > 0 else 0
                        self.log(
                            f"📈 EP adaptativo [{self._ep_modo_activo}]: "
                            f"WR roll={wr_r:.1f}% ({n_h} muestras)", 'dim')
                    # ─────────────────────────────────────────────────────────

                _prev_pending = (_pending_now.get('prevision') if _pending_now else None) or analisis.prevision
                mult_guardado = _pending_now.get('mult') if _pending_now else None
                self.analizador.agregar_historial_todo(
                    ronda=issue,
                    rango=rango_apuesta,
                    modo=modo_real.value,
                    pnl=pnl if not fue_skip_real else 0.0,
                    acierto=acierto if not fue_skip_real else False,
                    dif=analisis.dif,
                    prevision=_prev_pending,
                    winner='BLUE' if winner == 'azul' else 'RED',
                    balance=self.analizador.balance_acumulado,
                    estrategia='DIRECTA' if modo_real.value == 'DIRECTO' else ('INVERSA' if modo_real.value == 'INVERSO' else '---'),
                    mult=mult_guardado,
                )

                self.analizador._guardar_balance()
                analisis.pnl = round(pnl, 2)
                analisis.acierto = acierto
                analisis.racha = self.analizador._calcular_racha()

                res_txt = 'SKIP' if modo_real == ModoApuesta.SKIP else ('✅ GANADA' if acierto else '❌ PERDIDA')
                col_res = C['accent2'] if acierto else (C['skip'] if modo_real == ModoApuesta.SKIP else C['accent3'])
                if hasattr(self.dash, '_lbl_resultado'):
                    rt, rc = res_txt, col_res
                    self.dash.root.after(0, lambda t=rt, c=rc: self.dash._lbl_resultado.config(text=t, fg=c))

                _prev_display = _prev_pending
                self.dash.estado.update({
                    'balance': self.analizador.balance_acumulado,
                    '_balance': self.analizador.balance_acumulado,
                    'modo': modo_real.value,
                    'dif': analisis.dif,
                    'rango': analisis.rango,
                    'racha': analisis.racha,
                    'umbral': f"D≥{self.config.UMBRAL_DIRECTO}%" if analisis.modo == ModoApuesta.DIRECTO else (
                              f"I≤{self.config.UMBRAL_INVERSO}%" if analisis.modo == ModoApuesta.INVERSO else "SKIP"),
                    'prevision': _prev_display,
                    'ganador': 'BLUE' if winner == 'azul' else 'RED',
                })

                if modo_real != ModoApuesta.SKIP:
                    if acierto:
                        self.dash.estado['aciertos'] += 1
                        # Ejecutar animación de victoria sin bloquear
                        try:
                            import subprocess
                            subprocess.Popen(['py', 'victoria.py'], cwd=str(Path(__file__).parent))
                        except:
                            pass
                    else:
                        self.dash.estado['fallos'] += 1
                else:
                    self.dash.estado['skips'] += 1

                # Completar la fila pendiente con el resultado
                pending = getattr(self, '_pending_entry', None)
                if pending and str(pending.get('ronda', '')) == str(issue):
                    pending.update({
                        'ganador':  'BLUE' if winner == 'azul' else 'RED',
                        'pnl':      None if fue_skip_real else round(pnl, 2),
                        'balance':  None if fue_skip_real else round(self.analizador.balance_acumulado, 2),
                        'acierto':  None if fue_skip_real else acierto,
                    })
                    self._sheets_mult     = pending.get('mult')
                    self._sheets_confianza = pending.get('confianza', '---')
                    self.dash.guardar_entrada_historial(pending)
                    p = dict(pending)
                    iid_str = p.get('_tree_iid', str(id(pending)))
                    self.dash.hist_widget.completar(p, iid_str)
                    self._pending_entry = None
                else:
                    # Fallback: insertar entrada completa si no hay pending
                    fb = {
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                        'ronda':    issue,
                        'rango':    analisis.rango,
                        'dif':      analisis.dif,
                        'prevision':analisis.prevision,
                        'ganador':  'BLUE' if winner == 'azul' else 'RED',
                        'modo':     modo_real.value,
                        'pnl':      None if fue_skip_real else analisis.pnl,
                        'balance':  None if fue_skip_real else self.analizador.balance_acumulado,
                        'acierto':  None if fue_skip_real else acierto,
                        'estrategia':'DIRECTA' if modo_real.value == 'DIRECTO' else ('INVERSA' if modo_real.value == 'INVERSO' else '---'),
                        'confianza':'---',
                        'mult':     None if fue_skip_real else (mult_guardado or 1),
                        'ep':       'ON' if self.dash.ep_activa else 'OFF',
                    }
                    self.dash.guardar_entrada_historial(fb)
                    self.dash.agregar_historico(fb)
                rango_fin = analisis.rango
                self.dash.root.after(0, self.dash._actualizar_grafica)
                if hasattr(self.dash, '_rango_activo'):
                    self.dash._rango_activo = rango_fin
                    if modo_real != ModoApuesta.SKIP:
                        self.dash._heat_resaltar = (rango_fin, modo_real.value)
                        self.dash._heat_resaltar_acierto = acierto
                    else:
                        self.dash._heat_resaltar = None
                    self.dash.root.after(0, lambda r=rango_fin: self.dash._actualizar_rangos(r))

                if modo_real != ModoApuesta.SKIP:
                    tipo_log = 'ok' if acierto else 'err'
                    self.log(f"{'✅' if acierto else '❌'} "
                             f"Ronda {issue} | {analisis.rango} | {analisis.dif:.1f}% | "
                             f"Prev:{_prev_pending} Real:{'BLUE' if winner=='azul' else 'RED'} | "
                             f"Bal:{self.analizador.balance_acumulado:+.2f}€", tipo_log)

                # Actualizar ganador en panel de ronda
                gan_txt = 'Azul' if winner == 'azul' else 'Rojo'
                gan_col = '#00BFFF' if winner == 'azul' else '#FF4444'
                self.dash.root.after(0, lambda t=gan_txt, c=gan_col:
                    self.dash._lbl_gan2.config(text=t, fg=c))

                # Voz: color ganador + resultado
                try:
                    if modo_real != ModoApuesta.SKIP:
                        resultado_voz = "ganada" if acierto else "perdida"
                        subprocess.Popen([r'c:\Python\voice.exe', f'Gana {gan_txt}, {resultado_voz}'])
                    else:
                        subprocess.Popen([r'c:\Python\voice.exe', f'Gana {gan_txt}'])
                except:
                    pass

                if self.sheets.esta_conectado():
                    try:
                        winrate = self._calcular_confianza(analisis.rango, analisis.modo.value)
                        self.sheets.registrar_ronda(
                            self.session_id, issue, analisis, datos,
                            self._timestamp(),
                            balance_acumulado=self.analizador.balance_acumulado,
                            winner_real=winner,
                            winrate=winrate,
                            mult=getattr(self, '_sheets_mult', None),
                            confianza=getattr(self, '_sheets_confianza', '---'))
                    except Exception as e:
                        self.log(f"⚠️ Sheets: {e}", 'warn')

                self.log("━━ FIN DE BLOQUE ━━", 'dim')

        self.r_id_actual = None
        self.vol_blue = 0.0
        self.vol_red = 0.0
        self.datos_tick29 = None
        self.datos_tick29_fallback = None

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    async def ciclo_eventos(self):
        self.log("📡 Escuchando eventos del WebSocket...")
        while True:
            # Watchdog para cancelar el contador si el servidor deja de mandar ticks
            if self._barra_activa and (time.time() - self._ultimo_tick) > 7:
                self.log("⏰ Watchdog: servidor sin ticks, cancelando barra y esperando nueva ronda...", 'warn')
                if self._tarea_barra and not self._tarea_barra.done():
                    self._tarea_barra.cancel()
                    try:
                        await self._tarea_barra
                    except:
                        pass
                self._barra_activa = False
                self._ultimo_tick = time.time()  # reset para no disparar de nuevo
                await asyncio.sleep(0)

            try:
                data = await self.ws_client.recibir()
                if data is None:
                    continue
                m = data.get('type')
                if m in ('game_init', 'start', 'open_draw', 'game_start'):
                    await self._procesar_game_init(data)
                elif m == 'total_bet':
                    await self._procesar_total_bet(data)
                elif m == 'drawed':
                    await self._procesar_drawed(data)
                elif m == 'ping':
                    await self.ws_client.enviar({'type': 'pong'})
                elif m in ('init', 'bind_success', 'add_bet'):
                    pass
                elif m == 'error':
                    self.log(f"⚠️ Server error: {data.get('message')}", 'warn')
            except Exception as e:
                self.log(f"❌ Error ciclo: {e}", 'err')
                await asyncio.sleep(2)


async def engine_async(bot: AcertadorConDashboard):
    while True:
        try:
            if not await bot.ws_client.conectar():
                bot.log("❌ No se pudo conectar. Reintentando en 10s...", 'err')
                await asyncio.sleep(10)
                continue
            bot.dash.estado['ws'] = True
            bot.dash.estado['inicio_sesion'] = datetime.now()
            bot.log("✅ WebSocket conectado", 'ok')
            await bot.ciclo_eventos()
        except KeyboardInterrupt:
            break
        except asyncio.CancelledError:
            bot.log("🔄 Reconectando en 5s...", 'warn')
            await asyncio.sleep(5)
        except Exception as e:
            bot.log(f"❌ Error fatal: {e}", 'err')
            await asyncio.sleep(10)
        finally:
            bot.dash.estado['ws'] = False
            await bot.ws_client.desconectar()


def run_bot(bot: AcertadorConDashboard):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(engine_async(bot))
    finally:
        loop.close()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import importlib.util as _ilu

    def _cargar_modulo(nombre):
        spec = _ilu.spec_from_file_location(nombre, Path(__file__).parent / f"{nombre}.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    import threading as _threading
    _recalc_event = _threading.Event()

    def _hilo_calculos():
        import time as _t
        try:
            mod_rangos = _cargar_modulo("calc_rangos")
            mod_conf   = _cargar_modulo("calc_conf")
        except Exception as e:
            print(f"[calculos] Error cargando módulos: {e}")
            return
        while True:
            try: mod_rangos.calcular_y_guardar()
            except Exception as e: print(f"[calc_rangos] {e}")
            try: mod_conf.calcular_y_guardar()
            except Exception as e: print(f"[calc_conf] {e}")
            _recalc_event.wait(timeout=60)
            _recalc_event.clear()

    Thread(target=_hilo_calculos, daemon=True).start()

    root = tk.Tk()
    dash = DashboardFuturista(root)
    dash._recalc_event = _recalc_event
    bot = AcertadorConDashboard(dash)
    dash._sheets_ref = bot.sheets

    t = Thread(target=run_bot, args=(bot,), daemon=True)
    t.start()

    root.mainloop()