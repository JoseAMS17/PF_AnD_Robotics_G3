"""Fuentes de datos de guante, y la fábrica que elige una.

`crear_fuente()` es el único sitio de la app que sabe qué transportes existen.
Todo lo demás recibe una `FuenteGuante` y no pregunta de dónde viene: eso es lo
que permite que la app funcione completa sin guantes.
"""

from __future__ import annotations

from ..config import TRANSPORTES_LISTOS, Config, Transporte
from .base import EstadisticasEnlace, FuenteGuante


class TransporteNoImplementado(NotImplementedError):
    """El transporte existe en la configuración pero todavía no está escrito."""


def crear_fuente(config: Config | None = None) -> FuenteGuante:
    """Devuelve la fuente que pide la configuración.

    Importa las implementaciones de forma diferida a propósito: así la app
    arranca en modo simulador **sin que paho-mqtt tenga que estar instalado**,
    y un fallo al importar el cliente MQTT no impide abrir la app.
    """
    config = config or Config()

    if config.transporte is Transporte.SIMULADOR:
        from .simulador import SimuladorGuante

        return SimuladorGuante()

    if config.transporte is Transporte.MQTT:
        from .mqtt_guante import MqttGuante

        return MqttGuante(broker=config.broker)

    pendientes = ", ".join(
        str(t) for t in Transporte if t not in TRANSPORTES_LISTOS
    )
    raise TransporteNoImplementado(
        f"el transporte {config.transporte} todavía no está implementado "
        f"(pendientes: {pendientes}). Usa simulador o mqtt."
    )


__all__ = [
    "EstadisticasEnlace",
    "FuenteGuante",
    "TransporteNoImplementado",
    "crear_fuente",
]
