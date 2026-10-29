"""Donde estan los archivos, tanto en desarrollo como dentro del ejecutable.

🔴 **Este modulo existe por una razon concreta: dentro de un `.exe` las rutas
cambian.** PyInstaller no deja el paquete en una carpeta normal; lo descomprime
en `sys._MEIPASS`. Una ruta calculada con `Path(__file__).parent` funciona
perfecto en desarrollo y **apunta a la nada dentro del `.exe`**.

Es exactamente el fallo de *"funciona en desarrollo y el .exe muere al abrir la
camara"*, y por eso todo lo que busque un archivo pasa por aqui.

Dos clases de archivo, con reglas distintas:

| | Que es | Donde va |
|---|---|---|
| **Recurso** | Solo lectura; viaja dentro del programa (los modelos de MediaPipe) | `_MEIPASS` en el `.exe`, la carpeta del paquete en desarrollo |
| **Dato** | Se escribe; es del usuario y de esta PC (`config.json`) | Al lado del `.exe` si se puede escribir ahi, si no en `%LOCALAPPDATA%` |

**Por que el dato mira primero al lado del `.exe`:** eso es lo que hace que sea
*portable de verdad*. Una copia en un USB se lleva su propia configuracion. Si
la carpeta es de solo lectura (por ejemplo en `Archivos de programa`), cae a
`%LOCALAPPDATA%`, que es como se comporta un programa instalado.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

NOMBRE_APP = "lsd-app"


def empaquetado() -> bool:
    """True si corremos dentro de un ejecutable de PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def dir_recursos() -> Path:
    """La carpeta de los archivos de solo lectura que viajan con el programa.

    En el `.exe` es `sys._MEIPASS`, donde PyInstaller pone lo que se le paso
    con `--add-data`. En desarrollo, la carpeta que contiene al paquete.
    """
    if empaquetado():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def dir_modelos() -> Path:
    """Donde estan los `.task` de MediaPipe.

    ⚠️ Recordatorio: MediaPipe **no los trae dentro**, son archivos del
    proyecto. Y nunca se le pasa la ruta, se le pasan los bytes — ver
    `fuentes/rostro.cargar_modelo`.
    """
    return dir_recursos() / "modelos"


def dir_ejecutable() -> Path:
    """La carpeta donde esta el `.exe`, o la raiz del proyecto en desarrollo."""
    if empaquetado():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _se_puede_escribir(carpeta: Path) -> bool:
    prueba = carpeta / ".lsd-escritura-prueba"
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        prueba.write_text("x", encoding="utf-8")
        prueba.unlink()
        return True
    except OSError:
        return False


def dir_datos() -> Path:
    """Donde se guarda lo que el programa escribe (`config.json`, perfiles).

    Primero al lado del ejecutable, para que una copia portable se lleve su
    configuracion. Si ahi no se puede escribir, `%LOCALAPPDATA%`.
    """
    if empaquetado():
        junto = dir_ejecutable() / "datos"
        if _se_puede_escribir(junto):
            return junto

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    raiz = Path(base) if base else Path.home() / ".config"
    return raiz / NOMBRE_APP


def describir() -> list[str]:
    """Para la pantalla de Configuracion y para depurar el `.exe`."""
    return [
        f"modo: {'ejecutable' if empaquetado() else 'desarrollo'}",
        f"recursos: {dir_recursos()}",
        f"modelos:  {dir_modelos()}",
        f"datos:    {dir_datos()}",
    ]
