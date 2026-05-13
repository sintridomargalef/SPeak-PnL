"""
Script one-shot: añade filas para las columnas nuevas del panel HISTORICO
('filtro_idx' y 'filtro_nombre') a la pestaña COLUMNAS del spreadsheet Pk_Arena.
Idempotente: si las filas ya existen, no hace nada.
"""
from pathlib import Path

import gspread
from oauth2client.service_account import ServiceAccountCredentials


SCOPE = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
CRED_PATH = str(Path(__file__).parent / 'credenciales.json')
SPREADSHEET_NAME = "Pk_Arena"
WORKSHEET_NAME = "COLUMNAS"

NUEVAS = [
    ("HISTORICO", "filtro_idx",    "40"),
    ("HISTORICO", "filtro_nombre", "200"),
    ("HISTORICO", "analisis",     "360"),
]


def main():
    creds   = ServiceAccountCredentials.from_json_keyfile_name(CRED_PATH, SCOPE)
    cliente = gspread.authorize(creds)
    ss = cliente.open(SPREADSHEET_NAME)
    ws = ss.worksheet(WORKSHEET_NAME)

    filas = ws.get_all_values()
    existentes = {
        (str(f[0]).strip().upper(), str(f[1]).strip())
        for f in filas if len(f) >= 2
    }

    anadidas, ya = [], []
    for seccion, key, ancho in NUEVAS:
        clave = (seccion.upper(), key)
        if clave in existentes:
            ya.append(key)
        else:
            ws.append_row([seccion, key, ancho], value_input_option='USER_ENTERED')
            anadidas.append(key)

    print(f"[COLUMNAS] añadidas={anadidas}  ya_existian={ya}")


if __name__ == '__main__':
    main()
