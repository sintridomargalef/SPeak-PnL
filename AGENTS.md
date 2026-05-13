# SPeak — PnL Dashboard

## Entrypoint

- `pnl_dashboard.py` — GUI principal tkinter. Ejecutar: `py pnl_dashboard.py`
- Otros entrypoints: `estrategia_perfecta.py`, `backtest_ep.py`, `monitor_decisiones.py`, `acertador.py`

## Plataforma

- **Solo Windows** (`winsound`, `ctypes.windll`, PowerShell TTS vía `System.Speech`)
- Python 3.14+ con venv (`venv/` o `ven/`)

## Arquitectura

### Módulos principales

| Archivo | Rol |
|---------|-----|
| `pnl_dashboard.py` | Ventana principal, layout, sincronización Sheets, auto-selección de filtro |
| `pnl_panels.py` | `PanelFiltros`, `PanelLive`, `HistoricoApuestasPanel` |
| `pnl_decision_panel.py` | `DecisionTable`, `DecisionHistoryWindow`, COL_DEFS |
| `pnl_curvas_panel.py` | Ventana de curvas de filtros |
| `pnl_data.py` | Parseo de datos (`parsear`, `parsear_websocket`), curvas PnL |
| `pnl_live.py` | `LiveMonitor` — cliente WebSocket asyncio, emite eventos vía queue |
| `pnl_config.py` | Constantes, colores, `FILTROS_CURVA` (18 filtros), `FILTER_PARAMS` |
| `pnl_filtros_cache.py` | Caché de resultados de `_get_ops` por filtro con timestamps |
| `telegram_notifier.py` | `send_message()`, `_chat_ids()`, `_bot_token()` — lee `telegram.env` |

### Archivos de datos

- `pnl_decision_history.json` — todas las decisiones (pre-apuesta + resultado)
- `pnl_filtros_long.jsonl` — 1 línea por filtro por ronda: `{delta, saldo, filtro_idx, ...}`
- `pnl_dashboard_cfg.json` — geometría ventana, `balance_historico`, `solo_base`
- `pnl_dashboard_geometry.txt` — posición de ventana de respaldo
- `cooldown_filtros.json` — timestamps de cooldown de filtros (30 min, persistente entre reinicios)

## Sistema de filtros

- 18 filtros definidos en `FILTROS_CURVA` como tuplas `(nombre, color, lambda, contrarian, raw)`
- Las lambdas referencian `FILTER_PARAMS` **por nombre en tiempo de ejecución** (no por closure), así las actualizaciones de Sheets toman efecto inmediato sin reiniciar
- `FILTER_PARAMS` claves: `wr_70`, `wr_80`, `wr_40`, `acel_umbral`, `vuelta_base`
- Selección de filtro: auto-selector corre cada ronda; la selección manual se respeta (flag `_user_overrode_filter`)
- Cooldown: cuando el auto-selector descarta un filtro O el anti-bucle de respaldo se activa, ese filtro entra en cooldown 30 min (persistido). La selección manual salta el cooldown.
- `VUELTA_BASE`: si el filtro Base gana consecutivamente, el auto-selector no puede salir de Base. Si un no-Base pierde N rondas consecutivas, fuerza Base + cooldown.

## Integración Sheets (Google Sheets "Pk_Arena")

- **Auth**: `credenciales.json` (service account) vía `gspread` + `oauth2client`
- **Worksheet "Variables"**: se lee cada ronda vía `_leer_variables_sheets()` (hilo background)
  - `DIR_WR_70`, `DIR_WR_80`, `WR_PERDEDORA` → `FILTER_PARAMS`
  - `ACEL_UMBRAL`, `VOLATIL_UMBRAL` → `FILTER_PARAMS['acel_umbral']`
  - `VUELTA_BASE` / `VUELTA` → `FILTER_PARAMS['vuelta_base']`
  - `SONIDO_SUAVE` → `winsound.Beep(600, 100)` al final de ronda
  - `BOTS_USO` → `'1'`/`'2'`/`'1-2'`
  - `TELEGRAM` → `ON/OFF`
- **Worksheet "COLUMNAS"**: anchos de columnas para DecisionTable y otras tablas
- **Worksheet "Filtros"**: exportación de estadísticas de filtros con explicaciones
- **Balances**: `_balance_historico_inicio` (para columna histórica) y `_balance_filtro_inicio` (por filtro) ajustables desde botón BALANCES, persistidos en `pnl_dashboard_cfg.json`

## Telegram

- `telegram.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (separado por comas para múltiples chats)
- `BOTS_USO` controla qué chat IDs reciben mensajes: `'1'` = solo primero, `'2'` = solo segundo, `'1-2'` = ambos
- Predicción enviada antes del resultado de la ronda, resultado enviado después de conocer ganador
- Segunda línea del mensaje de resultado muestra saldo histórico: `Saldo: +N.NN`

## Flujo de datos

1. `LiveMonitor` conecta a `wss://www.ff2016.vip/game`, parsea ticks
2. Al final de ronda, emite evento → `PanelLive._on_resultado_ev(ev)`
3. Decisión registrada en `self._decisiones` y persistida en `pnl_decision_history.json`
4. `_completar_decision_resultado(ev)` → envía Telegram, actualiza balance_filtro
5. Dashboard `_on_resultado()` → refresca histórico, ejecuta auto-selector, exporta a Sheets
6. `FILTER_PARAMS` actualizado desde Sheets cada ronda antes del auto-selector

## Voz

- `hablar(texto)` lanza PowerShell oculto con `System.Speech.SpeechSynthesizer`
- Anuncio por voz cada 5 rondas: `"Saldo final +N.NN"`
- Desactivado poniendo `HABLAR_ACTIVADO = False` en `pnl_config.py`

## Scripts de backfill / recuperación

- `backfill_pnl_filtros.py` — recalcula delta `pnl_filtros` para todas las decisiones históricas; ejecutar después de corregir `_delta_teorico`
- `regenerar_filtros_long.py` — reconstruye `pnl_filtros_long.jsonl` desde el historial de decisiones; ejecutar después de backfill
- `rebuild_decision_history.py` — reconstruye historial de decisiones desde datos crudos
- Archivos `backup`: `pre_backfill_pnl_filtros_*.json`, `pre_regen_filtros_long_*.jsonl`

## Convenciones clave

- `balance_filtro` usa delta teórico del filtro (`pnl_filtros[active_idx]`), NO el PnL real (`d['pnl']`) — evita que los filtros absorban ganancias/pérdidas de rondas que no apostaron
- `filtro_idx` y `filtro_nombre` guardados en cada registro de decisión para identificación histórica; fallback por nombre para datos legacy
- `_analizar_ronda()` se calcula en tiempo de visualización, no se guarda en JSONL
- `_curva_desde_long()` lee `delta` almacenado del long file en lugar de recalcular
- `HistoricoApuestasPanel` muestra solo las decisiones de la sesión actual (desde `_session_decision_start`) y solo rondas APOSTADA con delta ≠ 0
- Orden del layout (izquierda→derecha): estado(210) | center(500) | right(460) | live(640) | historico(530). Ventana por defecto: 3100x1000

## Gotchas

- `FILTER_PARAMS` es mutado in-place por el lector de Sheets — las lambdas lo referencian por clave, así que ven valores actualizados
- El flag `_user_overrode_filter` evita que REFRESCAR sobreescriba el filtro seleccionado manualmente
- `_vuelta_base_bloqueo` bloquea el auto-selector cuando Base está ganando; se resetea solo después de que Base pierde N rondas consecutivas
- Rango vacío `_session_decision_start` al arrancar → histórico se muestra vacío pero hereda `_balance_historico_inicio` global
- `winsound` import envuelto en try/except para compatibilidad Linux (no usado en Linux)
