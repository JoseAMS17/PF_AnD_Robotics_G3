"""La fuente real: los guantes por MQTT sobre Mosquitto.

Misma interfaz `FuenteGuante` que el simulador, así que para el resto de la app
son intercambiables con un parámetro de arranque.

Lo que hace además de recibir paquetes, y que no es decorativo:

* **Mide el enlace.** Frecuencia, pérdida por saltos de `seq`, latencia y
  batería. Es lo que consume la fase 0 de la calibración obligatoria.
* **Rechaza con motivo.** Un paquete malo no tumba la app ni se traga en
  silencio: se cuenta y se guarda el motivo, porque la pantalla de calibración
  tiene que decir qué está mal con nombre y apellido.
* **Sigue el topic de estado.** Llega con *retain* y como *Last Will*, así que
  en cuanto la app conecta ya sabe qué guantes hay, y si un ESP32 se muere el
  broker publica `offline` solo.
* **Cuenta reconexiones.** 🔑 Importa para la defensa: `CLAUDE.md` avisa de que
  la red del aula puede cerrar el puerto 1883 o aislar clientes. Un contador de
  reconexiones que sube es el síntoma, y hay que poder verlo **en semana 2, no
  en diciembre**.

El cliente paho se puede inyectar (`cliente=`) para poder probar todo el manejo
de mensajes **sin levantar un broker**.
"""

from __future__ import annotations

import threading
from typing import Any

import paho.mqtt.client as mqtt

from ..config import ConfigBroker
from ..contrato import (
    ESTADO_OFFLINE,
    ESTADO_ONLINE,
    GUANTES,
    TOPIC_DATOS,
    TOPIC_ESTADO,
    Muestra,
    PaqueteInvalido,
)
from .base import FuenteGuante

# Un guante a 50 Hz que lleva medio segundo callado está muerto o perdido.
# No se usa el Last Will para esto: un ESP32 colgado no cierra la conexión.
SILENCIO_PARA_DARLO_POR_MUERTO_MS = 500


class MqttGuante(FuenteGuante):
    """Suscripción a `guante/+/datos` y `guante/+/estado`."""

    def __init__(
        self,
        broker: ConfigBroker | None = None,
        cliente: Any | None = None,
    ) -> None:
        super().__init__()
        self.broker = broker or ConfigBroker()
        self.conectado = False
        self.reconexiones = 0
        self.ultimo_motivo_desconexion = ""
        self._topicos_desconocidos = 0
        self._arrancado = False
        self._lock = threading.Lock()

        self.cliente = cliente if cliente is not None else self._crear_cliente()
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_recibir

    def _crear_cliente(self) -> mqtt.Client:
        # paho 2.x obliga a declarar la versión de callbacks. VERSION2 es la
        # actual; con VERSION1 las firmas de los callbacks son otras.
        c = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.broker.id_efectivo(),
        )
        if self.broker.usuario:
            c.username_pw_set(self.broker.usuario, self.broker.contrasena or None)
        # Reintento con espera creciente: la red del aula se cae a ratos y no
        # queremos un bucle de reconexión a toda velocidad.
        c.reconnect_delay_set(min_delay=1, max_delay=16)
        return c

    # -- ciclo de vida --

    def iniciar(self) -> None:
        if self._arrancado:
            return
        self._arrancado = True
        # connect_async + loop_start: la app no se queda bloqueada si el broker
        # no está. Es la diferencia entre "la app no abre" y "la app abre y
        # dice que no hay broker", y la segunda es la que se puede defender.
        self.cliente.connect_async(
            self.broker.host, self.broker.puerto, keepalive=10
        )
        self.cliente.loop_start()

    def detener(self) -> None:
        if not self._arrancado:
            return
        self._arrancado = False
        try:
            self.cliente.disconnect()
        finally:
            self.cliente.loop_stop()
        self.conectado = False

    # -- callbacks de paho (corren en el hilo de red, no en el de la interfaz) --

    def _al_conectar(self, cliente, _userdata, _flags, reason_code, _props=None):
        if getattr(reason_code, "is_failure", False):
            self.conectado = False
            self.ultimo_motivo_desconexion = f"el broker rechazó la conexión: {reason_code}"
            return

        self.conectado = True
        # Se suscribe a los dos topics de cada guante. El de estado lleva
        # retain, así que el estado actual llega de inmediato al suscribirse.
        for guante in GUANTES:
            cliente.subscribe(TOPIC_DATOS.format(id=guante), qos=0)
            cliente.subscribe(TOPIC_ESTADO.format(id=guante), qos=1)

    def _al_desconectar(self, _cliente, _userdata, _flags, reason_code, _props=None):
        if self.conectado:
            self.reconexiones += 1
        self.conectado = False
        self.ultimo_motivo_desconexion = str(reason_code)

    def _al_recibir(self, _cliente, _userdata, mensaje):
        topico = mensaje.topic
        for guante in GUANTES:
            if topico == TOPIC_ESTADO.format(id=guante):
                self._estado(guante, mensaje.payload)
                return
            if topico == TOPIC_DATOS.format(id=guante):
                self._datos(guante, mensaje.payload)
                return
        # Un topic que no es de nadie: se cuenta, no se ignora. Casi siempre es
        # un ESP32 publicando en un topic mal escrito.
        with self._lock:
            self._topicos_desconocidos += 1

    # -- manejo de mensajes --

    def _estado(self, guante: str, payload: bytes) -> None:
        texto = payload.decode("utf-8", errors="replace").strip()
        if texto not in (ESTADO_ONLINE, ESTADO_OFFLINE):
            self.stats[guante].anotar_invalido(
                f"estado desconocido en el topic de {guante}: {texto!r}"
            )
            return
        self.stats[guante].estado = texto

    def _datos(self, guante: str, payload: bytes) -> None:
        try:
            muestra = Muestra.desde_json(payload)
        except PaqueteInvalido as exc:
            self.stats[guante].anotar_invalido(str(exc))
            return

        if muestra.id != guante:
            # El `id` es redundante con el topic a propósito, y justo por eso
            # sirve de comprobación: si no cuadran, el firmware está mal.
            self.stats[guante].anotar_invalido(
                f"el topic dice {guante} y el payload dice {muestra.id}"
            )
            return

        self._entregar(muestra)

    # -- lo que consulta la fase 0 --

    @property
    def topicos_desconocidos(self) -> int:
        with self._lock:
            return self._topicos_desconocidos

    def guantes_vivos(self) -> list[str]:
        """Los que están publicando de verdad ahora mismo.

        No basta con `estado == "online"`: un ESP32 colgado sin cerrar la
        conexión sigue marcado online y no manda nada. Hace falta el silencio.
        """
        return [
            g
            for g in GUANTES
            if self.stats[g].recibidos > 0
            and self.stats[g].silencio_ms < SILENCIO_PARA_DARLO_POR_MUERTO_MS
        ]

    def diagnostico(self) -> list[str]:
        """Qué está mal, en frases que se pueden mostrar tal cual."""
        problemas: list[str] = []

        if not self.conectado:
            donde = f"{self.broker.host}:{self.broker.puerto}"
            motivo = (
                f" ({self.ultimo_motivo_desconexion})"
                if self.ultimo_motivo_desconexion
                else ""
            )
            problemas.append(f"no hay conexión con el broker en {donde}{motivo}")

        if self.reconexiones:
            problemas.append(
                f"{self.reconexiones} reconexiones: la red está inestable. "
                "Si es la del aula, probar el hotspot del celular"
            )

        for guante in GUANTES:
            s = self.stats[guante]
            if s.recibidos == 0:
                problemas.append(
                    f"el guante {guante} no ha publicado ni un paquete"
                    + (f" (su estado dice {s.estado})" if s.estado else "")
                )
            elif s.silencio_ms >= SILENCIO_PARA_DARLO_POR_MUERTO_MS:
                problemas.append(
                    f"el guante {guante} lleva {s.silencio_ms} ms callado"
                )
            if s.invalidos:
                problemas.append(
                    f"{s.invalidos} paquetes rechazados en {guante}; "
                    f"el último: {s.ultimo_error}"
                )

        if self.topicos_desconocidos:
            problemas.append(
                f"{self.topicos_desconocidos} mensajes en topics que no son del "
                "contrato: revisa cómo escribe el topic el firmware"
            )

        return problemas
