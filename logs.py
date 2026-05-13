#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
logs.py - Sistema de logging para Pk_Arena

Uso:
    py logs.py -l INFO -o console "Mensaje de prueba"
    py logs.py -l DEBUG -o file "Mensaje de debug"
    py logs.py -l ERROR -o voice "Error en apuesta"
    py logs.py -l INFO -o all -c [APUESTA] "Apuesta realizada"
    py logs.py -l INFO -o voice -v female "Mensaje con voz femenina"
"""

import argparse
import logging
import subprocess
import sys
import os
import io
from datetime import datetime

# Configurar UTF-8 para stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Constantes
VOICE_EXE = r"c:\Python\voice.exe"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "pk_arena.log")

# Colores ANSI para consola
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    # Colores de texto
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

# Niveles de log con iconos y colores
LOG_LEVELS = {
    "DEBUG": {
        "level": logging.DEBUG,
        "icon": "[D]",
        "color": Colors.CYAN,
        "prefix": "DEBUG"
    },
    "INFO": {
        "level": logging.INFO,
        "icon": "[I]",
        "color": Colors.GREEN,
        "prefix": "INFO"
    },
    "WARNING": {
        "level": logging.WARNING,
        "icon": "[!]",
        "color": Colors.YELLOW,
        "prefix": "WARN"
    },
    "ERROR": {
        "level": logging.ERROR,
        "icon": "[X]",
        "color": Colors.RED,
        "prefix": "ERROR"
    },
    "CRITICAL": {
        "level": logging.CRITICAL,
        "icon": "[#]",
        "color": Colors.MAGENTA,
        "prefix": "CRIT"
    },
}

# Tipos de salida
OUTPUT_TYPES = ["console", "file", "voice", "all"]

# Tipos de voz (solo femenina disponible)
VOICE_TYPES = ["default", "female"]

# Configurar logging básico
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log_message(level, category, message, output="all", voice_type="default"):
    """
    Registra un mensaje con el nivel y tipo de salida especificados.
    
    Args:
        level: Nivel de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        category: Categoría del mensaje (ej: [APUESTA], [FALTA])
        message: Mensaje a registrar
        output: Tipo de salida (console, file, voice, all)
        voice_type: Tipo de voz (default, male, female)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Obtener configuración del nivel
    level_config = LOG_LEVELS.get(level, LOG_LEVELS["INFO"])
    icon = level_config["icon"]
    color = level_config["color"]
    prefix = level_config["prefix"]
    
    # Formatear el mensaje completo
    if category:
        formatted_message = f"[{timestamp}] {category} {message}"
    else:
        formatted_message = f"[{timestamp}] {message}"
    
    # Mensaje con icono para consola
    console_message = f"{icon} {formatted_message}"
    
    # Salida por consola con colores
    if output in ["console", "all"]:
        colored_message = f"{color}{console_message}{Colors.RESET}"
        print(colored_message)
        sys.stdout.flush()
    
    # Salida a fiche
    if output in ["file", "all"]:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(formatted_message + "\n")
                f.flush()
        except Exception as e:
            print(f"[ERROR] Error escribiendo en fiche de log: {e}")
            sys.stdout.flush()
    
    # Salida por voz
    if output in ["voice", "all"]:
        try:
            # Construir comando de voz
            voice_cmd = [VOICE_EXE]
            
            # Por defecto, errores usar voz femenina (no hay masculina)
            if voice_type == "default":
                if level in ["ERROR", "CRITICAL"]:
                    voice_type = "female"  # Usar femenina por defecto para errores
            
            # Agregar opción de voz solo si hay voz disponible
            # Nota: No hay voces masculinas instaladas
            if voice_type == "female":
                voice_cmd.append("-f")
            # male no disponible, se ignora
            
            voice_cmd.append(message)
            
            subprocess.Popen(voice_cmd, shell=True)
        except Exception as e:
            print(f"[ERROR] Error con salida de voz: {e}")

def list_voices():
    """Lista las voces disponibles"""
    try:
        subprocess.Popen([VOICE_EXE, "-l"], shell=True)
    except Exception as e:
        print(f"[ERROR] Error listando voces: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Sistema de logging para Pk_Arena",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
    py logs.py -l INFO -o console "Mensaje de prueba"
    py logs.py -l DEBUG -o file "Mensaje de debug"
    py logs.py -l ERROR -o voice "Error en apuesta"
    py logs.py -l INFO -o all -c [APUESTA] "Apuesta realizada"
    py logs.py -l INFO -o voice -v female "Mensaje con voz femenina"
    py logs.py --list-voices
        """
    )
    parser.add_argument(
        "-l", "--level",
        default="INFO",
        choices=list(LOG_LEVELS.keys()),
        help="Nivel de log (default: INFO)"
    )
    parser.add_argument(
        "-o", "--output",
        default="all",
        choices=OUTPUT_TYPES,
        help="Tipo de salida (default: all)"
    )
    parser.add_argument(
        "-c", "--category",
        default="",
        help="Categoría del mensaje (ej: [APUESTA], [FALTA])"
    )
    parser.add_argument(
        "-v", "--voice",
        default="default",
        choices=VOICE_TYPES,
        help="Tipo de voz: default, female (default: default)"
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Lista las voces disponibles"
    )
    parser.add_argument(
        "message",
        nargs="*",
        help="Mensaje a registrar"
    )
    
    args = parser.parse_args()
    
    # Listar voces si se pide
    if args.list_voices:
        list_voices()
        sys.exit(0)
    
    # Verificar mensaje
    if not args.message:
        print("[ERROR] Se requiere un mensaje")
        parser.print_help()
        sys.exit(1)
    
    message = " ".join(args.message)
    category = args.category if args.category else ""
    
    log_message(args.level, category, message, args.output, args.voice)

if __name__ == "__main__":
    main()