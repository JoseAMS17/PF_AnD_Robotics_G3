"""El gemelo falso de los guantes: publica el mismo JSON a 50 Hz.

Genera exactamente el contrato de `contrato.py`, así que la app no distingue si
viene de aquí o de un ESP32. **Y debe poder fallar a propósito**, porque un
simulador que solo funciona bien no sirve para probar nada: el día de la
defensa el enlace sí va a fallar.

Fallos que sabe hacer, por `Averias`:
    perdida_pct      tira paquetes (salta `seq`, que es lo que mide la app)
    retardo_ms       los entrega tarde
    un_solo_guante   el otro guante no publica nunca
    bateria_baja     arranca por debajo del mínimo de la fase 0
    bno_sin_calibrar cal nunca llega a 3
    congelar_tras_s  deja de publicar pasados N segundos

Se corre solo:
    python -m lsd_app.herramientas.probar_simulador
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

from ..contrato import (
    ADC_MAX,
    DEDOS,
    FRECUENCIA_GUANTE_HZ,
    GUANTES,
    N_BITS_TOUCH,
    N_TIRAS_COBRE,
    PARES_CONTACTO,
    Muestra,
    ahora_ms,
)
from .base import FuenteGuante

# Rango que de verdad recorre un sensor flex con el divisor de 100 kΩ.
# No es 0..4095: un flex nunca llega a los extremos del ADC, y la app tiene
# que verse obligada a normalizar por usuario (fase 2) en vez de asumir.
FLEX_EXTENDIDO = 1500
FLEX_PUNO = 2900


@dataclass
class Averias:
    """Lo que se le puede pedir al simulador que haga mal."""

    perdida_pct: float = 0.0
    retardo_ms: int = 0
    un_solo_guante: str | None = None   # "izq" o "der": el que SÍ publica
    bateria_baja: bool = False
    bno_sin_calibrar: bool = False
    congelar_tras_s: float | None = None


class SimuladorGuante(FuenteGuante):
    """Dos guantes falsos publicando a 50 Hz desde un hilo."""

    def __init__(
        self,
        averias: Averias | None = None,
        frecuencia_hz: int = FRECUENCIA_GUANTE_HZ,
        semilla: int | None = None,
    ) -> None:
        super().__init__()
        self.averias = averias or Averias()
        self.frecuencia_hz = frecuencia_hz
        self._rng = random.Random(semilla)
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()
        self._seq = {g: 0 for g in GUANTES}
        # Una batería que de verdad baja mientras corre la demo: si no baja,
        # el aviso de batería nunca se prueba.
        self._bat = {g: 3.55 if self.averias.bateria_baja else 4.05 for g in GUANTES}

    # -- ciclo de vida --

    def iniciar(self) -> None:
        if self._hilo is not None:
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="simulador", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None

    # -- el bucle --

    def _bucle(self) -> None:
        periodo = 1.0 / self.frecuencia_hz
        arranque = time.perf_counter()
        siguiente = arranque

        while not self._parar.is_set():
            ahora = time.perf_counter()
            transcurrido = ahora - arranque

            if (
                self.averias.congelar_tras_s is not None
                and transcurrido >= self.averias.congelar_tras_s
            ):
                return  # el guante se murió: la app tiene que darse cuenta sola

            for guante in GUANTES:
                if (
                    self.averias.un_solo_guante is not None
                    and guante != self.averias.un_solo_guante
                ):
                    continue

                # `seq` avanza SIEMPRE, incluso en el paquete que se tira:
                # así el salto se ve en el receptor, que es la idea.
                self._seq[guante] += 1
                if self._rng.random() * 100 < self.averias.perdida_pct:
                    continue

                self._entregar(self._muestra(guante, transcurrido))

            siguiente += periodo
            espera = siguiente - time.perf_counter()
            if espera > 0:
                self._parar.wait(espera)
            else:
                siguiente = time.perf_counter()  # se atrasó: no acumular deuda

    # -- generación --

    def _muestra(self, guante: str, t_s: float) -> Muestra:
        # Una mano que abre y cierra despacio, para que los tres estados
        # aparezcan solos y se vea si el umbral de la app los separa bien.
        fase = 0.5 * (1 - math.cos(2 * math.pi * t_s / 4.0))

        flex = []
        for i, _dedo in enumerate(DEDOS):
            # Cada dedo va un poco desfasado: una mano real no cierra a la vez.
            f = 0.5 * (1 - math.cos(2 * math.pi * (t_s / 4.0) - i * 0.25))
            valor = FLEX_EXTENDIDO + (FLEX_PUNO - FLEX_EXTENDIDO) * f
            valor += self._rng.gauss(0, 8)  # ruido del ADC
            flex.append(int(min(ADC_MAX, max(0, valor))))

        # Los huecos se cierran cuando la mano se cierra. Cada hueco va por su
        # cuenta: en una mano real los dedos no se juntan todos a la vez, y el
        # bit de un hueco puede parpadear con el contacto justo al límite.
        touch = 0
        if fase > 0.6:
            for bit in range(N_BITS_TOUCH):
                if self._rng.random() < 0.55 + 0.4 * fase:
                    touch |= 1 << bit

        # Cuaternión de una muñeca girando despacio sobre un eje.
        angulo = 0.6 * math.sin(2 * math.pi * t_s / 7.0)
        quat = [
            round(math.cos(angulo / 2), 4),
            round(math.sin(angulo / 2), 4),
            0.0,
            0.0,
        ]

        cal = [1, 2, 1, 2] if self.averias.bno_sin_calibrar else [3, 3, 3, 3]

        # La celda cae ~1 mV/s: en una demo de 10 minutos se nota.
        self._bat[guante] = max(3.0, self._bat[guante] - 0.001 / self.frecuencia_hz)

        marca = ahora_ms() - self.averias.retardo_ms
        return Muestra(
            id=guante,
            seq=self._seq[guante],
            t=marca,
            flex=flex,
            quat=quat,
            cal=cal,
            touch=touch,
            bat=round(self._bat[guante], 3),
            recibido_ms=ahora_ms(),
        )


def describir_hardware_simulado() -> str:
    """Una línea para la pantalla de configuración: qué está simulando."""
    return (
        f"{len(GUANTES)} guantes · {len(DEDOS)} flex por mano · "
        f"{N_TIRAS_COBRE} tiras de cobre en {N_BITS_TOUCH} huecos "
        f"({', '.join('-'.join(p) for p in PARES_CONTACTO)}) · "
        f"{FRECUENCIA_GUANTE_HZ} Hz"
    )
