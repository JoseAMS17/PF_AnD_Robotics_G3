"""Prueba de humo de la parte 1: corre el simulador y mide el enlace.

No necesita guantes, ni broker, ni cámara. Es la demostración de que la regla
de oro se cumple: la app arranca y mide sin hardware.

    python -m lsd_app.herramientas.probar_simulador
    python -m lsd_app.herramientas.probar_simulador --averiado
"""

from __future__ import annotations

import argparse
import time

from ..contrato import (
    BAT_MINIMA_PARA_TRABAJAR,
    DEDOS,
    FRECUENCIA_GUANTE_HZ,
    GUANTES,
    pares_en_contacto,
)
from ..fuentes.simulador import Averias, SimuladorGuante, describir_hardware_simulado
from ..nucleo.estados import PerfilFlex, RangoDedo

# Umbrales de la fase 0 de la calibración obligatoria.
FREC_MINIMA_HZ = 45
PERDIDA_MAXIMA_PCT = 2.0
LATENCIA_MAXIMA_MS = 100


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba el simulador de guantes.")
    ap.add_argument("--segundos", type=float, default=5.0)
    ap.add_argument(
        "--averiado",
        action="store_true",
        help="mete pérdida, retardo y batería baja, para ver si la fase 0 los caza",
    )
    args = ap.parse_args()

    averias = (
        Averias(perdida_pct=6.0, retardo_ms=180, bateria_baja=True)
        if args.averiado
        else Averias()
    )

    print(f"Hardware simulado: {describir_hardware_simulado()}")
    print(f"Modo: {'AVERIADO a propósito' if args.averiado else 'sano'}")
    print(f"Midiendo {args.segundos:.0f} s...\n")

    ultima: dict[str, object] = {}
    with SimuladorGuante(averias=averias, semilla=7) as fuente:
        fin = time.perf_counter() + args.segundos
        while time.perf_counter() < fin:
            muestra = fuente.leer(timeout=0.2)
            if muestra is not None:
                ultima[muestra.id] = muestra

        # --- fase 0: enlace ---
        print("FASE 0 — enlace")
        todo_bien = True
        for guante in GUANTES:
            s = fuente.stats[guante]
            if s.recibidos == 0:
                print(f"  {guante}: 🔴 no publicó ni un paquete")
                todo_bien = False
                continue

            fallos = []
            if s.frecuencia_hz < FREC_MINIMA_HZ:
                fallos.append(f"frecuencia {s.frecuencia_hz:.1f} Hz < {FREC_MINIMA_HZ}")
            if s.perdida_pct > PERDIDA_MAXIMA_PCT:
                fallos.append(f"pérdida {s.perdida_pct:.1f} % > {PERDIDA_MAXIMA_PCT}")
            if s.latencia_media_ms > LATENCIA_MAXIMA_MS:
                fallos.append(
                    f"latencia {s.latencia_media_ms:.0f} ms > {LATENCIA_MAXIMA_MS}"
                )
            if s.bat < BAT_MINIMA_PARA_TRABAJAR:
                fallos.append(f"batería {s.bat:.2f} V < {BAT_MINIMA_PARA_TRABAJAR}")

            marca = "✅" if not fallos else "🔴"
            print(
                f"  {guante}: {marca} {s.recibidos} paquetes · "
                f"{s.frecuencia_hz:.1f} Hz · pérdida {s.perdida_pct:.1f} % · "
                f"latencia {s.latencia_media_ms:.0f} ms · batería {s.bat:.2f} V"
            )
            for f in fallos:
                print(f"       └─ {f}")
            todo_bien &= not fallos

    # --- crudo -> tres estados ---
    print("\nDE CRUDO A LOS TRES ESTADOS (lo que deriva la app, no el ESP32)")
    # Un perfil de ejemplo; en la app real sale de la fase 2 por usuario.
    perfil = PerfilFlex(
        guante="izq",
        rangos=[RangoDedo(abierto=1500, cerrado=2900) for _ in DEDOS],
    )
    for guante, muestra in sorted(ultima.items()):
        estados = perfil.estados(muestra)  # type: ignore[arg-type]
        print(f"  {guante}  seq={muestra.seq}")  # type: ignore[attr-defined]
        for dedo in DEDOS:
            i = DEDOS.index(dedo)
            crudo = muestra.flex[i]  # type: ignore[attr-defined]
            norm = perfil.normalizar(crudo, i)
            print(f"       {dedo:>8}: crudo {crudo:>4}  →  {norm:4.2f}  →  {estados[dedo]}")
        contactos = pares_en_contacto(muestra.touch)  # type: ignore[attr-defined]
        print(
            "       contacto: "
            + (", ".join("-".join(p) for p in contactos) if contactos else "ninguno")
        )

    print(f"\nFase 0: {'✅ pasa' if todo_bien else '🔴 no pasa'}")
    if args.averiado and todo_bien:
        print("🔴 MAL: se pidió modo averiado y la fase 0 no lo detectó.")
        return 1
    return 0 if (todo_bien or args.averiado) else 1


if __name__ == "__main__":
    raise SystemExit(main())
