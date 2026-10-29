"""ESP32 falsos: publica el simulador a un broker MQTT real.

Con esto se prueba **todo el camino real** —broker, red, suscripción, parseo,
estadísticas— sin tener un solo guante construido. Es la pieza que hace que la
semana de pruebas de red se pueda hacer en octubre y no en diciembre.

🔑 **Y sirve de especificación del firmware.** Lo que hace aquí es exactamente
lo que tiene que hacer cada ESP32:

    publica  guante/<id>/datos    QoS 0, sin retain, a 50 Hz
    publica  guante/<id>/estado   "online",  QoS 1, CON retain, al conectar
    declara  guante/<id>/estado   "offline", QoS 1, CON retain, como Last Will

El Last Will es la parte que se olvida: sin él, si el ESP32 se muere de golpe,
el topic de estado se queda diciendo "online" para siempre.

Uso, con Mosquitto corriendo en esta PC:

    python -m lsd_app.herramientas.publicar_simulador
    python -m lsd_app.herramientas.publicar_simulador --host 192.168.1.50
    python -m lsd_app.herramientas.publicar_simulador --perdida 5 --un-guante izq
"""

from __future__ import annotations

import argparse
import time

import paho.mqtt.client as mqtt

from ..contrato import (
    ESTADO_OFFLINE,
    ESTADO_ONLINE,
    GUANTES,
    TOPIC_DATOS,
    TOPIC_ESTADO,
)
from ..fuentes.simulador import Averias, SimuladorGuante


def crear_esp32_falso(guante: str, host: str, puerto: int) -> mqtt.Client:
    """Un cliente que se comporta como debe comportarse el firmware."""
    cliente = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"guante-{guante}",
    )
    # El Last Will se declara ANTES de conectar: es el broker quien lo publica
    # si la conexión se corta sin un disconnect limpio.
    cliente.will_set(
        TOPIC_ESTADO.format(id=guante), ESTADO_OFFLINE, qos=1, retain=True
    )
    cliente.connect(host, puerto, keepalive=10)
    cliente.loop_start()
    cliente.publish(
        TOPIC_ESTADO.format(id=guante), ESTADO_ONLINE, qos=1, retain=True
    )
    return cliente


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Publica el simulador de guantes a un broker MQTT."
    )
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--segundos", type=float, default=0.0, help="0 = hasta Ctrl+C")
    ap.add_argument("--perdida", type=float, default=0.0, help="%% de paquetes a tirar")
    ap.add_argument("--retardo", type=int, default=0, help="ms de retardo")
    ap.add_argument("--un-guante", choices=GUANTES, help="solo publica este")
    ap.add_argument("--bateria-baja", action="store_true")
    args = ap.parse_args()

    averias = Averias(
        perdida_pct=args.perdida,
        retardo_ms=args.retardo,
        un_solo_guante=args.un_guante,
        bateria_baja=args.bateria_baja,
    )

    activos = [args.un_guante] if args.un_guante else list(GUANTES)

    print(f"Conectando a {args.host}:{args.puerto} ...")
    clientes: dict[str, mqtt.Client] = {}
    try:
        for guante in activos:
            clientes[guante] = crear_esp32_falso(guante, args.host, args.puerto)
    except OSError as exc:
        print(f"🔴 No se pudo conectar al broker: {exc}")
        print("   ¿Está Mosquitto corriendo? ¿El puerto 1883 está abierto?")
        print("   En la red del aula puede estar cerrado: probar el hotspot del celular.")
        for c in clientes.values():
            c.loop_stop()
        return 1

    print(f"Publicando {', '.join(activos)} a 50 Hz. Ctrl+C para parar.")
    enviados = 0
    ultimo_aviso = time.perf_counter()

    fuente = SimuladorGuante(averias=averias)
    fuente.iniciar()
    fin = time.perf_counter() + args.segundos if args.segundos else None

    try:
        while fin is None or time.perf_counter() < fin:
            muestra = fuente.leer(timeout=0.2)
            if muestra is None:
                continue
            cliente = clientes.get(muestra.id)
            if cliente is None:
                continue
            # QoS 0 y sin retain, como manda el contrato: un dato de 50 Hz
            # retenido no sirve de nada, y QoS 1 metería reintentos que
            # arruinan la medida de latencia.
            cliente.publish(
                TOPIC_DATOS.format(id=muestra.id),
                muestra.a_json(),
                qos=0,
                retain=False,
            )
            enviados += 1

            ahora = time.perf_counter()
            if ahora - ultimo_aviso >= 2.0:
                print(f"  {enviados} paquetes enviados")
                ultimo_aviso = ahora
    except KeyboardInterrupt:
        print("\nParando...")
    finally:
        fuente.detener()
        # Un disconnect limpio publica "offline" a mano: el Last Will solo
        # salta si la conexión se corta de golpe.
        for guante, cliente in clientes.items():
            cliente.publish(
                TOPIC_ESTADO.format(id=guante), ESTADO_OFFLINE, qos=1, retain=True
            )
            cliente.disconnect()
            cliente.loop_stop()

    print(f"Total: {enviados} paquetes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
