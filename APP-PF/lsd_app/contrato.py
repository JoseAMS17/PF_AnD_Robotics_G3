"""El contrato de datos de los guantes, como código.

Esta es la única definición del formato que viajan los guantes. Si cambia aquí,
cambia en firmware y en la app a la vez. El contrato en prosa vive en
`CLAUDE.md`, sección *Contrato de datos*; este archivo es su versión ejecutable.

Dos reglas que no se negocian, y la razón de cada una:

1. **Los flex viajan CRUDOS, no como estados.** Los tres estados que interesan
   (extendido, doblado, retraído) los deriva la app, no el ESP32, porque el
   umbral es *por persona* y sale de la fase 2 de calibración. Un ESP32 no
   conoce la mano de quien se puso el guante. Y sobre todo: la semana del 2 al
   8 de noviembre no se repite. Si se graba el estado y el umbral resulta
   equivocado, el dataset se perdió; si se graba el crudo, el umbral se puede
   recalcular siempre. Ver `nucleo/estados.py`, que hace la conversión.

2. **La batería viaja en voltios, no en porcentaje.** El porcentaje depende de
   la curva de la celda, y el modelo de batería todavía no está decidido.
   De voltios a porcentaje se puede; al revés, no.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

# --- Topics MQTT -------------------------------------------------------------

GUANTES = ("izq", "der")

TOPIC_DATOS = "guante/{id}/datos"      # QoS 0, sin retain
TOPIC_ESTADO = "guante/{id}/estado"    # con retain y como Last Will

ESTADO_ONLINE = "online"
ESTADO_OFFLINE = "offline"


# --- Dedos y contactos -------------------------------------------------------

DEDOS = ("pulgar", "indice", "medio", "anular", "menique")
N_FLEX = len(DEDOS)

# --- Los contactos: cuatro huecos, no diez pares -----------------------------
#
# Las tiras de cobre van en los COSTADOS de los dedos. Esa sola decisión
# mecánica fija todo lo demás:
#
# * Solo se pueden tocar dedos **adyacentes**. Dos tiras que miran a lados
#   opuestos de la mano no se encuentran nunca.
# * Cinco dedos en fila dejan **cuatro huecos**. Cada hueco es un contacto, y
#   cada contacto es un pin: cuando los dos dedos del hueco se cierran, su pin
#   lee 1.
# * Hacen falta **ocho tiras**: una a cada lado de los cuatro huecos.
#   Es decir, 2 tiras por dedo interior y 1 en el pulgar y en el meñique.
#
# 🔑 Esto explica la contradicción que llevaba abierta desde septiembre: el
# `FIGURAS/Boceto montaje guante.png` dibuja **8 almohadillas** y tenía razón
# en el número de tiras. Lo que estaba mal era el **28 pares**: ocho tiras en
# los costados no dan 28 combinaciones, dan **cuatro huecos**.
#
# El orden es fijo: el bit i de `touch` es PARES_CONTACTO[i].
PARES_CONTACTO: tuple[tuple[str, str], ...] = (
    ("pulgar", "indice"),
    ("indice", "medio"),
    ("medio", "anular"),
    ("anular", "menique"),
)
N_BITS_TOUCH = len(PARES_CONTACTO)
N_TIRAS_COBRE = 2 * N_BITS_TOUCH  # las ocho del boceto


def pares_en_contacto(touch: int) -> tuple[tuple[str, str], ...]:
    """Traduce la máscara a la lista de pares de dedos que se están tocando."""
    return tuple(par for i, par in enumerate(PARES_CONTACTO) if touch >> i & 1)


def bit_del_par(dedo_a: str, dedo_b: str) -> int:
    """El número de bit del hueco entre dos dedos, en cualquier orden.

    Lanza `ValueError` si no son adyacentes: con tiras en los costados, un par
    no adyacente no es un dato que falte, es un dato que no existe.
    """
    par = (dedo_a, dedo_b)
    for i, p in enumerate(PARES_CONTACTO):
        if par == p or par == (p[1], p[0]):
            return i
    raise ValueError(
        f"{dedo_a} y {dedo_b} no son dedos adyacentes: no hay tira de cobre entre ellos"
    )


def mascara_de_pares(pares: list[tuple[str, str]]) -> int:
    """El camino inverso: útil para el simulador y para las pruebas."""
    mascara = 0
    for dedo_a, dedo_b in pares:
        mascara |= 1 << bit_del_par(dedo_a, dedo_b)
    return mascara


# --- Límites de validación ---------------------------------------------------

# El ADS7128 es de 12 bits. Lo que llegue fuera de rango es un paquete roto,
# no una mano rara.
ADC_MIN, ADC_MAX = 0, 4095

# Tensión de una celda LiPo. Por debajo de 3.0 V la celda está en problemas y
# por encima de 4.25 V el dato no es creíble.
BAT_MIN, BAT_MAX = 2.5, 4.3

# Debajo de esto la calibración obligatoria no deja pasar (fase 0).
BAT_MINIMA_PARA_TRABAJAR = 3.6

FRECUENCIA_GUANTE_HZ = 50     # lo que publica el ESP32
FRECUENCIA_CAMARA_HZ = 30     # lo que da la webcam
FRECUENCIA_COMUN_HZ = 30      # a lo que remuestrea el sincronizador


class PaqueteInvalido(ValueError):
    """El JSON llegó, pero no respeta el contrato."""


# --- La muestra --------------------------------------------------------------


@dataclass(slots=True)
class Muestra:
    """Un paquete de `guante/<id>/datos`.

    Los nombres de los campos son los del contrato y van tal cual al JSON:
    cambiarlos aquí rompe el firmware.
    """

    id: str                      # "izq" | "der"
    seq: int                     # contador incremental, para medir pérdida
    t: int                       # epoch en milisegundos (los ESP32 van por NTP)
    flex: list[int]              # 5 enteros CRUDOS del ADC, uno por dedo
    quat: list[float]            # cuaternión del BNO055 (no Euler: cardán)
    cal: list[int]               # estado de calibración que reporta el BNO055
    touch: int                   # máscara de N_BITS_TOUCH bits
    bat: float                   # voltios

    # No viaja: lo pone la app al recibir, para medir latencia.
    recibido_ms: int = field(default=0, compare=False)

    # -- serialización --

    def a_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "seq": self.seq,
                "t": self.t,
                "flex": self.flex,
                "quat": self.quat,
                "cal": self.cal,
                "touch": self.touch,
                "bat": round(self.bat, 3),
            },
            separators=(",", ":"),
        )

    @classmethod
    def desde_json(cls, bruto: str | bytes) -> "Muestra":
        """Parsea y valida. Lanza `PaqueteInvalido` con el motivo concreto.

        El motivo importa: la pantalla de calibración tiene que poder decir
        qué componente está malo, no "error de datos".
        """
        try:
            d = json.loads(bruto)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PaqueteInvalido(f"no es JSON válido: {exc}") from exc

        if not isinstance(d, dict):
            raise PaqueteInvalido("el payload no es un objeto JSON")

        faltan = {"id", "seq", "t", "flex", "quat", "cal", "touch", "bat"} - d.keys()
        if faltan:
            raise PaqueteInvalido(f"faltan campos: {sorted(faltan)}")

        muestra = cls(
            id=d["id"],
            seq=d["seq"],
            t=d["t"],
            flex=list(d["flex"]),
            quat=[float(v) for v in d["quat"]],
            cal=list(d["cal"]),
            touch=int(d["touch"]),
            bat=float(d["bat"]),
            recibido_ms=ahora_ms(),
        )
        muestra.validar()
        return muestra

    # -- validación --

    def validar(self) -> None:
        if self.id not in GUANTES:
            raise PaqueteInvalido(f"id desconocido: {self.id!r}; se esperaba izq o der")

        if len(self.flex) != N_FLEX:
            raise PaqueteInvalido(
                f"flex trae {len(self.flex)} valores y el contrato pide {N_FLEX}"
            )
        for dedo, valor in zip(DEDOS, self.flex):
            if not isinstance(valor, int) or not ADC_MIN <= valor <= ADC_MAX:
                raise PaqueteInvalido(
                    f"flex del {dedo} fuera de rango del ADC: {valor!r}"
                )

        if len(self.quat) != 4:
            raise PaqueteInvalido(f"quat trae {len(self.quat)} valores y deben ser 4")

        if len(self.cal) != 4:
            raise PaqueteInvalido(f"cal trae {len(self.cal)} valores y deben ser 4")
        if any(not 0 <= c <= 3 for c in self.cal):
            raise PaqueteInvalido(f"cal fuera de 0..3: {self.cal}")

        if not 0 <= self.touch < (1 << N_BITS_TOUCH):
            raise PaqueteInvalido(
                f"touch={self.touch} no cabe en los {N_BITS_TOUCH} bits "
                f"de los {N_BITS_TOUCH} huecos entre dedos"
            )

        if not BAT_MIN <= self.bat <= BAT_MAX:
            raise PaqueteInvalido(f"bat fuera de rango de una celda LiPo: {self.bat} V")

    # -- utilidades --

    @property
    def bno_calibrado(self) -> bool:
        """El BNO055 solo es de fiar cuando los cuatro llegan a 3."""
        return all(c == 3 for c in self.cal)

    @property
    def latencia_ms(self) -> int:
        """Milisegundos entre que el guante marcó el dato y la app lo recibió."""
        return max(0, self.recibido_ms - self.t)


def ahora_ms() -> int:
    return time.time_ns() // 1_000_000
