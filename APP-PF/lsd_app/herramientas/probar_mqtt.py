"""La fase 0 contra un broker de verdad.

Es el gemelo de `probar_simulador`, pero por MQTT: mide el enlace real y dice
qué está mal. Para usarlo hacen falta dos terminales:

    1)  python -m lsd_app.herramientas.publicar_simulador
    2)  python -m lsd_app.herramientas.probar_mqtt

🔑 **Esto es lo que hay que correr en la red del aula, en semana 2, no en
diciembre.** `CLAUDE.md` avisa de que las redes institucionales aíslan clientes
entre sí y cierran el puerto 1883: esta herramienta lo detecta en 10 segundos.
"""

from __future__ import annotations

import argparse
import time

from ..config import ConfigBroker
from ..contrato import BAT_MINIMA_PARA_TRABAJAR, GUANTES
from ..fuentes.mqtt_guante import MqttGuante

FREC_MINIMA_HZ = 45
PERDIDA_MAXIMA_PCT = 2.0
LATENCIA_MAXIMA_MS = 100


def main() -> int:
    ap = argparse.ArgumentParser(description="Mide el enlace MQTT de los guantes.")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--usuario", default="")
    ap.add_argument("--contrasena", default="")
    ap.add_argument("--segundos", type=float, default=8.0)
    args = ap.parse_args()

    broker = ConfigBroker(
        host=args.host,
        puerto=args.puerto,
        usuario=args.usuario,
        contrasena=args.contrasena,
    )

    print(f"Broker: {broker.host}:{broker.puerto}")
    print(f"Midiendo {args.segundos:.0f} s...\n")

    with MqttGuante(broker=broker) as fuente:
        # Un par de segundos de cortesía para conectar y recibir los estados
        # retenidos antes de empezar a juzgar.
        espera = time.perf_counter() + 3.0
        while time.perf_counter() < espera and not fuente.conectado:
            fuente.leer(timeout=0.1)

        if not fuente.conectado:
            print("🔴 No conectó al broker.")
            for p in fuente.diagnostico():
                print(f"   └─ {p}")
            print("\n   Comprueba, en este orden:")
            print("   1. ¿Está Mosquitto corriendo?")
            print("   2. ¿El puerto 1883 está abierto en el firewall?")
            print("   3. ¿La red aísla clientes entre sí? (típico en aulas)")
            print("      Plan B permanente: hotspot del celular.")
            return 1

        print("✅ Conectado. Escuchando...\n")
        fin = time.perf_counter() + args.segundos
        while time.perf_counter() < fin:
            fuente.leer(timeout=0.2)

        # --- el informe ---
        print("FASE 0 — enlace")
        todo_bien = True
        for guante in GUANTES:
            s = fuente.stats[guante]
            if s.recibidos == 0:
                estado = f" (estado: {s.estado})" if s.estado else ""
                print(f"  {guante}: 🔴 sin paquetes{estado}")
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

            print(
                f"  {guante}: {'✅' if not fallos else '🔴'} {s.recibidos} paquetes · "
                f"{s.frecuencia_hz:.1f} Hz · pérdida {s.perdida_pct:.1f} % · "
                f"latencia {s.latencia_media_ms:.0f} ms · batería {s.bat:.2f} V · "
                f"estado {s.estado or '?'}"
            )
            for f in fallos:
                print(f"       └─ {f}")
            todo_bien &= not fallos

        print(f"\n  reconexiones: {fuente.reconexiones}")
        print(f"  guantes vivos: {', '.join(fuente.guantes_vivos()) or 'ninguno'}")

        problemas = fuente.diagnostico()
        if problemas:
            print("\nDIAGNÓSTICO")
            for p in problemas:
                print(f"  🔴 {p}")

        print(f"\nFase 0: {'✅ pasa' if todo_bien and not problemas else '🔴 no pasa'}")
        return 0 if todo_bien and not problemas else 1


if __name__ == "__main__":
    raise SystemExit(main())
