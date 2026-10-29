"""Punto de entrada para PyInstaller.

🔴 **Este archivo existe por una razon concreta, no es un duplicado.**

A PyInstaller **no se le puede apuntar a `lsd_app\\__main__.py`**. Si se hace,
lo ejecuta como un script suelto llamado `__main__`, fuera del paquete, y todos
los imports relativos de dentro revientan:

    ImportError: attempted relative import with no known parent package

Paso el 19 de octubre de 2026: el `.exe` se construyo sin un solo error, pesaba
305 MB, los modelos estaban dentro... y moria al arrancar con codigo 1. Y como
se construyo con `--windowed`, **no habia consola donde ver el fallo**: hubo que
rehacerlo con `--console` para leer el traceback.

La solucion es este lanzador: importa el paquete **por su nombre**, que es lo
que hace que los imports relativos de dentro funcionen igual que con
`python -m lsd_app`.

En desarrollo se sigue usando `python -m lsd_app`; esto es solo para el build.
"""

from lsd_app.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
