"""La interfaz `FuenteGuante` y las estadísticas de enlace.

Regla de oro de la app: **tiene que funcionar completa sin guantes.** Por eso
todo lo que consuma datos de guante habla con esta interfaz y nunca con MQTT
directamente. Hay dos implementaciones intercambiables:

    fuentes/simulador    el gemelo falso, y debe poder fallar a propósito
    fuentes/mqtt_guante  la real (parte 3)

Un parámetro de arranque decide cuál se usa, así que la app no distingue.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..contrato import GUANTES, Muestra, ahora_ms


@dataclass
class EstadisticasEnlace:
    """Lo que la fase 0 de la calibración necesita medir, por guante.

    La pérdida se calcula con los saltos de `seq`, que es exactamente para lo
    que `seq` está en el contrato: sin él no se puede medir.
    """

    guante: str
    recibidos: int = 0
    perdidos: int = 0
    invalidos: int = 0
    ultimo_seq: int | None = None
    bat: float = 0.0
    # El motivo del último paquete rechazado. La pantalla de calibración tiene
    # que decir qué está mal con nombre y apellido, no "error de datos".
    ultimo_error: str = ""
    # "online" / "offline" según el topic de estado, que llega con retain y
    # como Last Will: si el ESP32 se muere, el broker lo publica solo.
    estado: str = ""
    latencias_ms: list[int] = field(default_factory=list)
    _marcas_ms: list[int] = field(default_factory=list)

    def anotar(self, muestra: Muestra) -> None:
        if self.ultimo_seq is not None:
            salto = muestra.seq - self.ultimo_seq - 1
            if salto > 0:
                self.perdidos += salto
        self.ultimo_seq = muestra.seq
        self.recibidos += 1
        self.bat = muestra.bat

        self.latencias_ms.append(muestra.latencia_ms)
        self._marcas_ms.append(muestra.recibido_ms or ahora_ms())
        # Ventana de un par de segundos: la frecuencia instantánea interesa
        # más que la media desde que arrancó la app.
        if len(self._marcas_ms) > 128:
            del self._marcas_ms[:-128]
            del self.latencias_ms[:-128]

    def anotar_invalido(self, motivo: str = "") -> None:
        self.invalidos += 1
        if motivo:
            self.ultimo_error = motivo

    @property
    def silencio_ms(self) -> int:
        """Cuánto lleva este guante sin decir nada.

        Es la otra mitad de detectar un guante muerto: el Last Will avisa si el
        ESP32 cierra la conexión, pero si se queda colgado sin cerrarla, lo
        único que se nota es el silencio.
        """
        if not self._marcas_ms:
            return 0
        return max(0, ahora_ms() - self._marcas_ms[-1])

    @property
    def frecuencia_hz(self) -> float:
        if len(self._marcas_ms) < 2:
            return 0.0
        ventana_s = (self._marcas_ms[-1] - self._marcas_ms[0]) / 1000
        if ventana_s <= 0:
            return 0.0
        return (len(self._marcas_ms) - 1) / ventana_s

    @property
    def perdida_pct(self) -> float:
        esperados = self.recibidos + self.perdidos
        return 100 * self.perdidos / esperados if esperados else 0.0

    @property
    def latencia_media_ms(self) -> float:
        return sum(self.latencias_ms) / len(self.latencias_ms) if self.latencias_ms else 0.0


class FuenteGuante(ABC):
    """Produce `Muestra` por una cola, venga de donde venga."""

    def __init__(self, tamano_cola: int = 512) -> None:
        self.cola: queue.Queue[Muestra] = queue.Queue(maxsize=tamano_cola)
        self.stats: dict[str, EstadisticasEnlace] = {
            g: EstadisticasEnlace(guante=g) for g in GUANTES
        }

    @abstractmethod
    def iniciar(self) -> None:
        """Arranca la producción. No debe bloquear."""

    @abstractmethod
    def detener(self) -> None:
        """Para y suelta los recursos. Tiene que poder llamarse dos veces."""

    def leer(self, timeout: float | None = 0.1) -> Muestra | None:
        """Saca la siguiente muestra, o None si no hay en `timeout` segundos."""
        try:
            return self.cola.get(timeout=timeout)
        except queue.Empty:
            return None

    # -- para las implementaciones --

    def _entregar(self, muestra: Muestra) -> None:
        self.stats[muestra.id].anotar(muestra)
        try:
            self.cola.put_nowait(muestra)
        except queue.Full:
            # Si la cola se llena, el consumidor es más lento que el enlace.
            # Se tira la muestra más vieja: en tiempo real, el dato viejo
            # ya no sirve para nada.
            try:
                self.cola.get_nowait()
                self.cola.put_nowait(muestra)
            except (queue.Empty, queue.Full):
                pass

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, *_exc) -> None:
        self.detener()
