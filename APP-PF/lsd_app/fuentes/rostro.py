"""La fuente de rostro: webcam + MediaPipe Face, en su propio hilo.

🔴 **Va en un hilo aparte porque MediaPipe bloquea ~20 ms por frame.** A 30 fps
solo hay 33 ms por frame: si esto corriera en el hilo de la interfaz, la app se
congelaría visiblemente. El hilo lee de la cámara, infiere, y deja **solo el
último resultado** disponible; nadie espera a nadie.

**Se tira el frame viejo a propósito.** Si la inferencia no da para 30 fps, la
respuesta correcta es perder frames, no acumular retraso: un landmark de hace
400 ms no sirve para sincronizar con un guante.

🔑 **MediaPipe NO trae los modelos dentro.** El `face_landmarker.task` es un
archivo del proyecto, en `modelos/`. Si falta, esta fuente lo dice con esas
palabras en vez de reventar con un error de MediaPipe.

Lo que entrega es la **línea base de blendshapes restada**, que es lo que pide
la fase 4 de la calibración: sin restar el rostro neutro, el modelo aprende
*quién* es la persona en vez de *qué* expresión hizo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..contrato import ahora_ms
from ..rutas import dir_modelos

# Los modelos viven en `modelos/`. La ruta la resuelve `rutas.dir_modelos()`,
# que sabe distinguir desarrollo de ejecutable: dentro de un `.exe` el paquete
# no esta en una carpeta normal sino en `sys._MEIPASS`, y una ruta calculada
# con `__file__` apuntaria a la nada.
DIR_MODELOS = dir_modelos()
MODELO_ROSTRO = DIR_MODELOS / "face_landmarker.task"
MODELO_MANOS = DIR_MODELOS / "hand_landmarker.task"

URL_MODELOS = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)


class ModeloNoEncontrado(FileNotFoundError):
    """Falta el `.task`. No es un fallo de MediaPipe: es un archivo que no está."""


def cargar_modelo(ruta: Path) -> bytes:
    """Lee el `.task` y devuelve sus bytes.

    🔴 **ESTO NO ES UN ADORNO: ES OBLIGATORIO EN ESTE PROYECTO.**

    MediaPipe en Windows **no puede abrir un archivo cuya ruta tenga caracteres
    no ASCII**. Y la carpeta de este proyecto los tiene de forma permanente:

        ...\\Instituto Tecnológico de Las Américas (ITLA)\\...
                        ↑ó                    ↑é

    Comprobado el 17 de octubre de 2026:

        model_asset_path con acentos  -> FileNotFoundError
        model_asset_path en ruta ASCII -> OK
        model_asset_buffer (bytes)     -> OK

    Y el error que da es de los peores posibles: `FileNotFoundError: Unable to
    open file at <ruta>` **sobre un archivo que existe**, porque Python sí lo
    encuentra y MediaPipe no. Se puede perder un día entero con eso.

    Por eso **nunca se usa `model_asset_path` en este proyecto**: se leen los
    bytes con Python, que maneja Unicode sin problema, y se pasan por
    `model_asset_buffer`.
    """
    if not ruta.is_file():
        raise ModeloNoEncontrado(
            f"falta el modelo {ruta.name} en modelos/. MediaPipe no lo trae "
            f"dentro: hay que descargarlo de {URL_MODELOS}"
        )
    return ruta.read_bytes()


class CamaraNoDisponible(RuntimeError):
    """No se pudo abrir la webcam."""


@dataclass
class MuestraRostro:
    """Un frame procesado."""

    t: int                                  # epoch ms, para alinear con los guantes
    caras_detectadas: int
    blendshapes: dict[str, float] = field(default_factory=dict)
    ms_inferencia: float = 0.0

    @property
    def una_sola_cara(self) -> bool:
        """La fase 0 lo exige: dos caras en el encuadre arruinan la etiqueta."""
        return self.caras_detectadas == 1


class FuenteRostro:
    """Webcam + MediaPipe Face en un hilo. Entrega siempre el último resultado."""

    def __init__(
        self,
        indice_camara: int = 0,
        modelo: Path | None = None,
        max_caras: int = 2,   # 2 para poder DETECTAR que hay dos, no solo una
    ) -> None:
        self.indice_camara = indice_camara
        self.modelo = modelo or MODELO_ROSTRO
        self.max_caras = max_caras

        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()
        self._lock = threading.Lock()
        self._ultima: MuestraRostro | None = None
        self._base: dict[str, float] = {}

        self.frames_procesados = 0
        self.frames_tirados = 0
        self.ultimo_error = ""

    # -- comprobaciones que se hacen ANTES de arrancar un hilo --

    def comprobar_modelo(self) -> None:
        if not self.modelo.is_file():
            raise ModeloNoEncontrado(
                f"falta el modelo {self.modelo.name} en modelos/. MediaPipe no lo "
                f"trae dentro: hay que descargarlo de {URL_MODELOS}"
            )

    # -- ciclo de vida --

    def iniciar(self) -> None:
        if self._hilo is not None:
            return
        self.comprobar_modelo()
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="rostro", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=3.0)
            self._hilo = None

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, *_exc) -> None:
        self.detener()

    # -- lo que consume el resto de la app --

    def leer(self) -> MuestraRostro | None:
        """El último resultado, sin bloquear. Puede ser None al arrancar."""
        with self._lock:
            return self._ultima

    def fijar_base_neutra(self) -> bool:
        """Fase 4: guarda el rostro neutro actual como línea base."""
        muestra = self.leer()
        if muestra is None or not muestra.blendshapes:
            return False
        with self._lock:
            self._base = dict(muestra.blendshapes)
        return True

    def blendshapes_relativos(self) -> dict[str, float]:
        """Los blendshapes con la base restada. Es lo que va al vector de features.

        Sin restar la base, el modelo aprende *quién* es la persona en vez de
        *qué* expresión hizo. Si no hay base fijada, devuelve los crudos.
        """
        muestra = self.leer()
        if muestra is None:
            return {}
        with self._lock:
            base = dict(self._base)
        if not base:
            return dict(muestra.blendshapes)
        return {k: v - base.get(k, 0.0) for k, v in muestra.blendshapes.items()}

    # -- el hilo --

    def _bucle(self) -> None:
        try:
            import cv2
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
            import mediapipe as mp
        except ImportError as exc:
            self.ultimo_error = f"falta una dependencia de la cámara: {exc}"
            return

        camara = cv2.VideoCapture(self.indice_camara, cv2.CAP_DSHOW)
        if not camara.isOpened():
            self.ultimo_error = (
                f"no se pudo abrir la cámara {self.indice_camara}. "
                "¿Está conectada? ¿La está usando otro programa?"
            )
            camara.release()
            return

        # Los BYTES, no la ruta: la carpeta del proyecto tiene acentos y
        # MediaPipe no puede abrirla. Ver `cargar_modelo`.
        opciones = vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_buffer=cargar_modelo(self.modelo)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=self.max_caras,
            output_face_blendshapes=True,
        )

        try:
            with vision.FaceLandmarker.create_from_options(opciones) as detector:
                while not self._parar.is_set():
                    ok, frame = camara.read()
                    if not ok:
                        self.frames_tirados += 1
                        continue

                    inicio = time.perf_counter()
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    resultado = detector.detect_for_video(
                        imagen, int(time.perf_counter() * 1000)
                    )
                    ms = (time.perf_counter() - inicio) * 1000

                    blendshapes: dict[str, float] = {}
                    if resultado.face_blendshapes:
                        blendshapes = {
                            c.category_name: c.score
                            for c in resultado.face_blendshapes[0]
                        }

                    muestra = MuestraRostro(
                        t=ahora_ms(),
                        caras_detectadas=len(resultado.face_landmarks),
                        blendshapes=blendshapes,
                        ms_inferencia=ms,
                    )
                    with self._lock:
                        # Se sobrescribe sin cola: el frame viejo no sirve.
                        self._ultima = muestra
                    self.frames_procesados += 1
        except Exception as exc:  # noqa: BLE001 - el hilo no debe tumbar la app
            self.ultimo_error = f"la cámara falló: {exc}"
        finally:
            camara.release()

    # -- diagnóstico, en frases mostrables --

    def diagnostico(self) -> list[str]:
        problemas: list[str] = []

        if not self.modelo.is_file():
            problemas.append(
                f"falta {self.modelo.name} en modelos/ — MediaPipe no trae los "
                "modelos dentro, hay que descargarlo"
            )
        if self.ultimo_error:
            problemas.append(self.ultimo_error)

        muestra = self.leer()
        if muestra is None:
            if not problemas:
                problemas.append("la cámara no ha entregado ningún frame todavía")
        elif muestra.caras_detectadas == 0:
            problemas.append("no se detecta ninguna cara en el encuadre")
        elif muestra.caras_detectadas > 1:
            problemas.append(
                f"se detectan {muestra.caras_detectadas} caras: la fase 0 exige una "
                "sola, porque dos en el encuadre arruinan la etiqueta"
            )
        elif not self._base:
            problemas.append(
                "no se ha fijado el rostro neutro (fase 4): sin restar la base, el "
                "modelo aprende quién es la persona en vez de qué expresión hizo"
            )

        return problemas
