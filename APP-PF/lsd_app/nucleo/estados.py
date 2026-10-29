"""De flex crudo a los tres estados del dedo: extendido, doblado, retraído.

Esto es lo que José Alejandro pidió que marcara el ESP32, y vive aquí a
propósito. La razón no es de estilo:

* **El umbral es por persona.** Sale de la fase 2 de calibración (mano abierta,
  puño, relajada, 3 s cada una), que es obligatoria por usuario. El ESP32 no
  sabe quién se puso el guante.
* **La semana del 2 al 8 de noviembre no se repite.** Grabando el crudo, el
  umbral se puede recalcular en diciembre tantas veces como haga falta.
  Grabando el estado, un umbral mal puesto se lleva el dataset entero.
* **Cuesta cero.** El estado se obtiene aquí con una comparación, y el crudo
  cabe en el mismo paquete que ya estaba congelado en el contrato.

El guante no pierde nada: la app entrega los tres estados igual.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from ..contrato import DEDOS, N_FLEX, Muestra


class EstadoDedo(Enum):
    """Los tres estados que el sistema necesita distinguir."""

    EXTENDIDO = "extendido"
    DOBLADO = "doblado"
    RETRAIDO = "retraido"

    def __str__(self) -> str:
        return self.value


@dataclass
class RangoDedo:
    """Lo que la fase 2 mide para un dedo de una persona concreta."""

    abierto: int    # valor crudo con la mano abierta
    cerrado: int    # valor crudo con el puño cerrado

    @property
    def recorrido(self) -> int:
        return abs(self.cerrado - self.abierto)


# Un flex que varía menos que esto entre mano abierta y puño está mal soldado
# o está roto. El número viene del ejemplo que ya estaba escrito en CLAUDE.md
# ("solo varía 40 unidades — revisa la soldadura").
RECORRIDO_MINIMO_SANO = 150

# Dónde se cortan los tres estados, en fracción del recorrido de cada persona.
# Se dejan como constantes y no como números sueltos porque son lo primero que
# habrá que ajustar cuando se mire el dataset real.
CORTE_EXTENDIDO = 0.33
CORTE_DOBLADO = 0.75


@dataclass
class PerfilFlex:
    """El rango de los cinco dedos de una mano, para una persona.

    Se guarda dentro de `perfil_usuario.json` junto con el resto de las fases.
    """

    guante: str
    rangos: list[RangoDedo]

    def __post_init__(self) -> None:
        if len(self.rangos) != N_FLEX:
            raise ValueError(
                f"el perfil trae {len(self.rangos)} dedos y hacen falta {N_FLEX}"
            )

    # -- la conversión --

    def normalizar(self, crudo: int, i: int) -> float:
        """Crudo -> 0.0 (extendido del todo) a 1.0 (cerrado del todo).

        Se recorta a 0..1: pasarse del rango medido no es un error, es que la
        persona apretó más fuerte que durante la calibración.
        """
        rango = self.rangos[i]
        if rango.recorrido == 0:
            return 0.0
        f = (crudo - rango.abierto) / (rango.cerrado - rango.abierto)
        return min(1.0, max(0.0, f))

    def estado(self, crudo: int, i: int) -> EstadoDedo:
        f = self.normalizar(crudo, i)
        if f < CORTE_EXTENDIDO:
            return EstadoDedo.EXTENDIDO
        if f < CORTE_DOBLADO:
            return EstadoDedo.DOBLADO
        return EstadoDedo.RETRAIDO

    def estados(self, muestra: Muestra) -> dict[str, EstadoDedo]:
        """Los cinco estados de una muestra, por nombre de dedo."""
        return {
            dedo: self.estado(muestra.flex[i], i) for i, dedo in enumerate(DEDOS)
        }

    def normalizados(self, muestra: Muestra) -> list[float]:
        """Los cinco flex en 0..1, que es lo que consume `nucleo/features`."""
        return [self.normalizar(v, i) for i, v in enumerate(muestra.flex)]

    # -- diagnóstico, que es para lo que existe la pantalla de calibración --

    def dedos_sospechosos(self) -> list[str]:
        """Qué dedos tienen tan poco recorrido que hay que mirar la soldadura.

        La pantalla de calibración tiene que decir el componente con nombre y
        apellido, no "error de calibración".
        """
        return [
            dedo
            for dedo, rango in zip(DEDOS, self.rangos)
            if rango.recorrido < RECORRIDO_MINIMO_SANO
        ]

    def diagnostico(self) -> list[str]:
        return [
            f"el flex del {dedo} solo varía {self.rangos[DEDOS.index(dedo)].recorrido} "
            f"unidades entre mano abierta y puño — revisa la soldadura"
            for dedo in self.dedos_sospechosos()
        ]

    # -- persistencia --

    def a_dict(self) -> dict:
        return {"guante": self.guante, "rangos": [asdict(r) for r in self.rangos]}

    @classmethod
    def desde_dict(cls, d: dict) -> "PerfilFlex":
        return cls(
            guante=d["guante"],
            rangos=[RangoDedo(**r) for r in d["rangos"]],
        )

    def guardar(self, ruta: Path) -> None:
        ruta.write_text(
            json.dumps(self.a_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def cargar(cls, ruta: Path) -> "PerfilFlex":
        return cls.desde_dict(json.loads(ruta.read_text(encoding="utf-8")))
