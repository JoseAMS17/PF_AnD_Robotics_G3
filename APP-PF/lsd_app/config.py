"""La configuración de la app, y el selector de transporte.

El transporte **no es arquitectura, es configuración**. El contrato de datos es
idéntico por MQTT, por ESP-NOW con equipo central y por BLE: lo único que
cambia es la implementación detrás de `FuenteGuante`. Por eso vive aquí y no
repartido por el código.

🔴 **El archivo de configuración NO va en la carpeta del proyecto.** El proyecto
se sincroniza entre dos PCs por OneDrive, y la IP del broker, la cámara y el
puerto COM **son distintos en cada máquina**. Si se sincronizara, cada PC
pisaría la configuración de la otra. Va en `%LOCALAPPDATA%\\lsd-app\\config.json`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Transporte(str, Enum):
    """De dónde salen los datos de los guantes."""

    SIMULADOR = "simulador"   # el gemelo falso: la app completa sin hardware
    MQTT = "mqtt"             # WiFi + Mosquitto. El que está congelado
    ESP_NOW = "esp_now"       # ESP-NOW + equipo central que repite a la PC
    BLE = "ble"               # Bluetooth directo a la PC

    def __str__(self) -> str:
        return self.value


# Lo que todavía no está implementado. Se declara aquí para que la pantalla de
# configuración los pueda mostrar en gris en vez de dejar que fallen al elegirlos.
TRANSPORTES_LISTOS = (Transporte.SIMULADOR, Transporte.MQTT)


@dataclass
class ConfigBroker:
    host: str = "localhost"
    puerto: int = 1883
    usuario: str = ""
    # Se guarda en claro: es un broker de laboratorio en una red local, no una
    # cuenta de nada. Si algún día sale a internet, esto hay que cambiarlo.
    contrasena: str = ""
    # El id con el que la app se presenta al broker. Si dos copias de la app
    # usan el mismo, Mosquitto expulsa a la primera: por eso lleva el PID.
    client_id: str = ""

    def id_efectivo(self) -> str:
        return self.client_id or f"lsd-app-{os.getpid()}"


@dataclass
class ConfigCamara:
    indice: int = 0           # qué webcam, si hay varias

    # 🔴 **Apagada de fábrica, a propósito.** Una app que enciende la webcam
    # sola al abrirse es intrusiva, y además la cámara es el componente más
    # lento de arrancar: si falla, no debe impedir que la app abra. Se
    # enciende desde Configuración, o con `--camara-activa`.
    activa: bool = False


@dataclass
class Config:
    transporte: Transporte = Transporte.SIMULADOR
    broker: ConfigBroker = field(default_factory=ConfigBroker)
    camara: ConfigCamara = field(default_factory=ConfigCamara)

    # -- persistencia --

    def a_dict(self) -> dict:
        d = asdict(self)
        d["transporte"] = str(self.transporte)
        return d

    @classmethod
    def desde_dict(cls, d: dict) -> "Config":
        return cls(
            transporte=Transporte(d.get("transporte", Transporte.SIMULADOR.value)),
            broker=ConfigBroker(**d.get("broker", {})),
            camara=ConfigCamara(**d.get("camara", {})),
        )

    def guardar(self, ruta: Path | None = None) -> Path:
        ruta = ruta or ruta_config()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(self.a_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return ruta

    @classmethod
    def cargar(cls, ruta: Path | None = None) -> "Config":
        """Carga la configuración, o devuelve la de fábrica si no hay o está rota.

        Una configuración corrupta **no debe impedir arrancar la app**: se cae a
        los valores de fábrica, que son el simulador, y la app abre igual.
        """
        ruta = ruta or ruta_config()
        try:
            return cls.desde_dict(json.loads(ruta.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            return cls()


def ruta_config() -> Path:
    """Dónde vive `config.json`, fuera de la carpeta sincronizada.

    Se resuelve en tiempo de ejecución a propósito: las dos PCs tienen usuarios
    distintos, así que ninguna ruta absoluta escrita a mano sirve en las dos.

    En el ejecutable mira primero **al lado del `.exe`** —eso es lo que hace que
    una copia portable se lleve su propia configuración— y cae a
    `%LOCALAPPDATA%` si esa carpeta es de solo lectura. Ver `rutas.dir_datos`.
    """
    from .rutas import dir_datos

    return dir_datos() / "config.json"
