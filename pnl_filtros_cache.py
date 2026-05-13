"""
Cache compartida de stats de filtros.
Actualizada desde pnl_dashboard tras cada ronda completada.
Cualquier módulo del programa puede importarlo y llamar get_stats().
"""
import json
from pathlib import Path

_CACHE_FILE = Path(__file__).parent / 'pnl_filtros_stats.json'
_cache: list = []   # lista de dicts, uno por filtro, en orden de índice


def actualizar(filas: list):
    """Reemplaza la cache en memoria y persiste en JSON.

    filas: lista de dicts con claves:
        idx, nombre, color, ops, ac, wr, pnl, ratio, explicacion
    """
    global _cache
    _cache = list(filas)
    try:
        _CACHE_FILE.write_text(
            json.dumps(filas, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"[FiltrosCache] Error guardando: {e}")


def get_stats() -> list:
    """Devuelve la lista completa de stats de todos los filtros.
    Si la cache está vacía intenta cargar desde el JSON en disco.
    """
    if not _cache:
        _cargar_desde_archivo()
    return list(_cache)


def get_filtro(idx: int) -> dict:
    """Stats del filtro con índice idx. Devuelve {} si no existe."""
    for f in get_stats():
        if f.get('idx') == idx:
            return dict(f)
    return {}


def get_mejor() -> dict:
    """Filtro con mejor PNL/op entre los que tienen ops > 0.
    Excluye BAL.FILTRO (no es un filtro teórico comparable).
    Devuelve {} si no hay datos.
    """
    candidatos = [
        f for f in get_stats()
        if (f.get('ops') or 0) > 0 and f.get('nombre') != 'BAL.FILTRO'
    ]
    if not candidatos:
        return {}
    return dict(max(candidatos, key=lambda f: f.get('ratio') or 0.0))


def _cargar_desde_archivo():
    """Carga la cache desde el JSON en disco. Llamado automáticamente si la cache está vacía."""
    global _cache
    try:
        _cache = json.loads(_CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        _cache = []
