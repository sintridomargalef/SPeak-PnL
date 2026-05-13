import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

def conectar_excel():
    """Conecta con el libro Pk_Arena completo (usado por panel.py)."""
    creds = Credentials.from_service_account_file("credenciales.json", 
                                                 scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    return client.open("Pk_Arena")

def conectar_hojas():
    """Conecta con Pk_Arena y devuelve las pestañas Lector y Variables."""
    spreadsheet = conectar_excel()
    return spreadsheet.worksheet("Lector"), spreadsheet.worksheet("Variables")

def obtener_parametros(sheet_vars):
    """Obtiene APUESTA, SALDO_INIC y FILTRO_FIAB."""
    def get_v(tag):
        celda = sheet_vars.find(tag)
        return float(sheet_vars.cell(celda.row, celda.col + 1).value.replace(',', '.'))
    
    return {
        "apuesta": get_v("APUESTA"),
        "saldo_ini": get_v("SALDO_INIC"),
        "filtro_fiab": get_v("FILTRO_FIAB")
    }

def procesar_datos(sheet_lector):
    """Procesa datos desde la Fila 9 (según IA_MEMORY). Incluye Pico y Salto."""
    values = sheet_lector.get_all_values()
    if len(values) < 9: return pd.DataFrame()
    
    df = pd.DataFrame(values[8:]) # Fila 9 es índice 8
    # Mapeo de columnas: 1:B, 5:F, 8:I, 11:L, 12:M
    # Aseguramos que el DF tenga suficientes columnas
    columnas_necesarias = {1: 'Estado', 5: 'Diferencia', 8: 'Resultado', 11: 'Pico_Dif', 12: 'Inyectado'}
    df = df.rename(columns=columnas_necesarias)
    
    # Limpieza de Diferencia
    df['Diferencia'] = pd.to_numeric(df['Diferencia'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)
    
    # Nueva limpieza: Pico_Dif
    if 'Pico_Dif' in df.columns:
        df['Pico_Dif'] = pd.to_numeric(df['Pico_Dif'].astype(str).str.replace(',', '.'), errors='coerce').fillna(df['Diferencia'])
    
    df = df[df['Diferencia'] < 99].copy()
    
    df['Resultado'] = df['Resultado'].astype(str).str.strip().str.upper()
    df['Estado'] = df['Estado'].astype(str).str.strip().str.lower()
    
    # Si la columna Inyectado no existe (partidas viejas), la ponemos como "No"
    if 'Inyectado' not in df.columns:
        df['Inyectado'] = "No"
    
    return df