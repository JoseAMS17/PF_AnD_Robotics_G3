"""Mide cuánto tarda MediaPipe por frame. Cierra la decisión abierta nº 6.

`CLAUDE.md` tiene esto abierto desde el 15 de septiembre:

> ¿MediaPipe Hands además de Face? **Medir primero**: Face ~20 ms + Hands ~20 ms
> = 40 ms, y a 30 fps solo hay 33 ms. **No cabe en un hilo.**

Los ~20 ms son una estimación de internet, no una medida en esta máquina. Esto
lo mide de verdad, en la PC que va a correr la demo.

    python -m lsd_app.herramientas.medir_rostro              # imagen en blanco
    python -m lsd_app.herramientas.medir_rostro --camara      # con tu cara

⚠️ **Sin `--camara` el número es un SUELO, no la medida real.** Sobre una imagen
en blanco el detector no encuentra cara y se corta antes de inferir los
landmarks, así que sale más rápido de lo que será. **Para decidir de verdad hace
falta `--camara` con una cara delante.**
"""

from __future__ import annotations

import argparse
import statistics
import time

from ..fuentes.rostro import MODELO_MANOS, MODELO_ROSTRO, cargar_modelo

PRESUPUESTO_MS = 1000 / 30  # 33,3 ms por frame a 30 fps


def _imagen_en_blanco(ancho: int = 640, alto: int = 480):
    import numpy as np

    import mediapipe as mp

    datos = np.full((alto, ancho, 3), 128, dtype=np.uint8)
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=datos)


def _frames_de_camara(indice: int, n: int, bytes_rostro: bytes, espera_s: float = 40.0):
    """Lee n frames de la webcam, **esperando primero a que haya una cara**.

    🔑 Esperar no es comodidad: es lo que hace válida la medida. Sin cara en el
    encuadre el detector se corta antes de inferir los landmarks y el número
    sale entre 5 y 10 veces más bajo de lo real. La primera versión de esta
    herramienta medía sin esperar y dio 1,7 ms para Face, que es imposible.
    """
    import cv2

    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    camara = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
    if not camara.isOpened():
        camara.release()
        raise RuntimeError(
            f"no se pudo abrir la cámara {indice}. ¿Está conectada? "
            "¿La está usando otro programa?"
        )

    def a_mp(frame):
        return mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )

    opciones = vision.FaceLandmarkerOptions(
        base_options=mpp.BaseOptions(model_asset_buffer=bytes_rostro),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=2,
    )

    frames = []
    try:
        # Los primeros frames de una webcam salen oscuros mientras expone.
        for _ in range(10):
            camara.read()

        print("Ponte delante de la cámara. Esperando a detectar una cara...")
        with vision.FaceLandmarker.create_from_options(opciones) as detector:
            limite = time.perf_counter() + espera_s
            ts = 0
            avisado = 0.0
            while time.perf_counter() < limite:
                ok, frame = camara.read()
                if not ok:
                    continue
                ts += 40
                if detector.detect_for_video(a_mp(frame), ts).face_landmarks:
                    print("✅ Cara detectada. Midiendo, no te muevas...")
                    break
                ahora = time.perf_counter()
                if ahora - avisado >= 5.0:
                    restan = limite - ahora
                    print(f"   ...todavía no te veo ({restan:.0f} s)")
                    avisado = ahora
            else:
                raise RuntimeError(
                    f"no se detectó ninguna cara en {espera_s:.0f} s. "
                    "¿Hay luz? ¿La tapa de la cámara está abierta?"
                )

        for _ in range(n):
            ok, frame = camara.read()
            if ok:
                frames.append(a_mp(frame))
    finally:
        camara.release()

    if not frames:
        raise RuntimeError("la cámara abrió pero no entregó ningún frame")
    return frames


def medir(nombre: str, crear_detector, frames, calienta: int = 5) -> dict:
    """Corre el detector sobre los frames y devuelve las estadísticas."""
    tiempos: list[float] = []
    detecciones = 0

    with crear_detector() as detector:
        # Calentamiento: la primera inferencia carga pesos y mide cualquier cosa.
        for i in range(min(calienta, len(frames))):
            detector.detect_for_video(frames[i % len(frames)], i)

        base_ts = calienta + 1
        for i, frame in enumerate(frames):
            inicio = time.perf_counter()
            res = detector.detect_for_video(frame, (base_ts + i) * 40)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            encontrado = getattr(res, "face_landmarks", None) or getattr(
                res, "hand_landmarks", None
            )
            if encontrado:
                detecciones += 1

    return {
        "nombre": nombre,
        "n": len(tiempos),
        "media": statistics.mean(tiempos),
        "mediana": statistics.median(tiempos),
        "p95": sorted(tiempos)[int(len(tiempos) * 0.95) - 1] if tiempos else 0.0,
        "max": max(tiempos) if tiempos else 0.0,
        "detecciones": detecciones,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Mide el coste por frame de MediaPipe.")
    ap.add_argument("--camara", action="store_true", help="usa la webcam (recomendado)")
    ap.add_argument("--indice", type=int, default=0)
    ap.add_argument("--frames", type=int, default=60)
    args = ap.parse_args()

    for modelo in (MODELO_ROSTRO, MODELO_MANOS):
        if not modelo.is_file():
            print(f"🔴 Falta {modelo.name} en modelos/.")
            print("   MediaPipe no trae los modelos dentro: hay que descargarlos.")
            return 1

    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    # Los BYTES, no la ruta: la carpeta del proyecto tiene acentos
    # ("Tecnológico", "Américas") y MediaPipe no puede abrirla en Windows.
    # Ver `fuentes/rostro.cargar_modelo`.
    bytes_rostro = cargar_modelo(MODELO_ROSTRO)
    bytes_manos = cargar_modelo(MODELO_MANOS)

    if args.camara:
        try:
            frames = _frames_de_camara(args.indice, args.frames, bytes_rostro)
        except RuntimeError as exc:
            print(f"🔴 {exc}")
            return 1
        fuente = "cámara, con cara confirmada"
    else:
        frames = [_imagen_en_blanco()] * args.frames
        fuente = "imagen en blanco"

    print(f"\nFuente: {fuente} · {len(frames)} frames · presupuesto {PRESUPUESTO_MS:.1f} ms\n")

    def rostro():
        return vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_buffer=bytes_rostro),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=2,
                output_face_blendshapes=True,
            )
        )

    def manos():
        return vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_buffer=bytes_manos),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,
            )
        )

    resultados = [medir("Face", rostro, frames), medir("Hands", manos, frames)]

    print(f"{'':<8}{'media':>9}{'mediana':>9}{'p95':>9}{'máx':>9}   detectado")
    for r in resultados:
        print(
            f"{r['nombre']:<8}{r['media']:>8.1f}ms{r['mediana']:>8.1f}ms"
            f"{r['p95']:>8.1f}ms{r['max']:>8.1f}ms   {r['detecciones']}/{r['n']}"
        )

    suma = sum(r["media"] for r in resultados)
    print(f"\nFace + Hands en un hilo: {suma:.1f} ms de {PRESUPUESTO_MS:.1f} disponibles")

    if suma > PRESUPUESTO_MS:
        print("🔴 NO CABEN en un hilo a 30 fps.")
        print("   Opciones: un hilo por modelo, bajar a 15 fps para Hands,")
        print("   o dejar Hands fuera y documentarlo en el alcance.")
    else:
        print("✅ Caben en un hilo a 30 fps, con margen de "
              f"{PRESUPUESTO_MS - suma:.1f} ms.")

    # La medida solo vale si de verdad se detectó algo. Sin detección el
    # detector se corta antes de inferir los landmarks y el número sale
    # entre 5 y 10 veces más bajo de lo real.
    sin_detectar = [r["nombre"] for r in resultados if r["detecciones"] == 0]
    if sin_detectar:
        print(f"\n⚠️ {' y '.join(sin_detectar)} no detectó nada en ningún frame.")
        print("   ESE NÚMERO ES UN SUELO, NO LA MEDIDA: sin nada que seguir, el")
        print("   detector se corta antes de inferir los landmarks.")
        if not args.camara:
            print("   Para decidir de verdad: --camara, con una cara delante.")
        else:
            print("   Face confirmó cara, así que si falta Hands es que no había")
            print("   manos en el encuadre: repetir con las manos visibles.")
    else:
        print("\n✅ Medida válida: hubo detección en los frames medidos.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
