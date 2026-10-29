"""Punto de entrada de la app.

    python -m lsd_app                        # con lo que diga config.json
    python -m lsd_app --transporte simulador # fuerza el simulador
    python -m lsd_app --transporte mqtt --host 192.168.0.13

**Un parametro de arranque decide la fuente**, como manda la regla de oro: la
app funciona completa sin guantes. Lo que se pase por linea de comandos pisa a
`config.json` **solo en esta ejecucion**, sin guardarlo: asi se puede probar
otro transporte sin tocar la configuracion guardada.

Este es tambien el archivo que apuntara PyInstaller cuando se empaquete.
"""

from __future__ import annotations

import argparse
import sys


def aviso(texto: str) -> None:
    """`print` que no revienta dentro del ejecutable.

    🔴 En un build con `--windowed` **`sys.stdout` es `None`**, y un `print()`
    normal lanza `AttributeError: 'NoneType' object has no attribute 'write'`.
    La app moriria al arrancar, sin ventana y sin mensaje, porque tampoco hay
    consola donde verlo.
    """
    if sys.stdout is not None:
        try:
            print(texto)
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Traductor de Lengua de Senas Dominicana")
    ap.add_argument("--transporte", choices=[t.value for t in _transportes()])
    ap.add_argument("--host", help="host del broker MQTT")
    ap.add_argument("--puerto", type=int)
    ap.add_argument("--camara", type=int, help="indice de la webcam")
    ap.add_argument(
        "--autocerrar",
        type=float,
        default=0.0,
        help="se cierra tras N segundos; para comprobar que arranca sin quedarse abierta",
    )
    ap.add_argument(
        "--captura",
        help="guarda un PNG de la ventana y se cierra; para ver que la interfaz pinta",
    )
    ap.add_argument(
        "--pantalla",
        choices=("Start", "Configuracion", "Calibracion"),
        default="Start",
        help="con que pantalla abrir",
    )
    args = ap.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import Config, Transporte
    from .ui.ventana import VentanaPrincipal

    config = Config.cargar()
    if args.transporte:
        config.transporte = Transporte(args.transporte)
    if args.host:
        config.broker.host = args.host
    if args.puerto:
        config.broker.puerto = args.puerto
    if args.camara is not None:
        config.camara.indice = args.camara

    app = QApplication(sys.argv[:1])
    ventana = VentanaPrincipal(config=config)
    ventana.mostrar(args.pantalla)
    ventana.show()

    if args.captura:
        # Captura la ventana a un PNG y se cierra. Sirve para comprobar que la
        # interfaz de verdad pinta algo, no solo que el proceso arranca.
        def _capturar():
            ventana.grab().save(args.captura)
            aviso(f"captura guardada en {args.captura}")
            app.quit()

        QTimer.singleShot(int(max(args.autocerrar, 3.0) * 1000), _capturar)
    elif args.autocerrar > 0:
        aviso(f"arrancada; se cerrara en {args.autocerrar:.0f} s")
        QTimer.singleShot(int(args.autocerrar * 1000), app.quit)

    return app.exec()


def _transportes():
    from .config import Transporte

    return Transporte


if __name__ == "__main__":
    raise SystemExit(main())
