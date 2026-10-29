"""Pruebas del contrato de datos y del simulador.

    python -m pytest pruebas -q

Lo que se prueba no es que el código corra: es que **el contrato se respeta**.
Un paquete que no cumple el contrato tiene que ser rechazado con un motivo
legible, porque la pantalla de calibración muestra ese motivo al usuario.
"""

from __future__ import annotations

import json

import pytest

from lsd_app.contrato import (
    DEDOS,
    N_BITS_TOUCH,
    N_FLEX,
    N_TIRAS_COBRE,
    PARES_CONTACTO,
    Muestra,
    PaqueteInvalido,
    bit_del_par,
    mascara_de_pares,
    pares_en_contacto,
)
from lsd_app.fuentes.simulador import Averias, SimuladorGuante
from lsd_app.nucleo.estados import EstadoDedo, PerfilFlex, RangoDedo


def muestra_valida(**cambios) -> Muestra:
    base = dict(
        id="izq",
        seq=1,
        t=1_700_000_000_000,
        flex=[1500] * N_FLEX,
        quat=[1.0, 0.0, 0.0, 0.0],
        cal=[3, 3, 3, 3],
        touch=0,
        bat=4.0,
    )
    base.update(cambios)
    return Muestra(**base)  # type: ignore[arg-type]


# --- ida y vuelta ------------------------------------------------------------


def test_json_ida_y_vuelta():
    original = muestra_valida(seq=42, touch=0b101, bat=3.87)
    recuperada = Muestra.desde_json(original.a_json())
    assert recuperada.seq == 42
    assert recuperada.touch == 0b101
    assert recuperada.bat == pytest.approx(3.87)
    assert recuperada.flex == original.flex


def test_el_json_usa_los_nombres_del_contrato():
    # Si alguien renombra un campo, el firmware deja de entenderse con la app.
    d = json.loads(muestra_valida().a_json())
    assert set(d) == {"id", "seq", "t", "flex", "quat", "cal", "touch", "bat"}


# --- validación: cada fallo con su motivo ------------------------------------


@pytest.mark.parametrize(
    "cambios, texto",
    [
        ({"id": "centro"}, "id desconocido"),
        ({"flex": [1500] * 4}, "flex trae 4"),
        ({"flex": [1500, 1500, 9999, 1500, 1500]}, "flex del medio"),
        ({"quat": [1.0, 0.0, 0.0]}, "quat trae 3"),
        ({"cal": [3, 3, 3, 9]}, "cal fuera de 0..3"),
        ({"touch": 1 << N_BITS_TOUCH}, "no cabe"),
        ({"bat": 7.4}, "fuera de rango"),
    ],
)
def test_paquetes_malos_se_rechazan_con_motivo(cambios, texto):
    with pytest.raises(PaqueteInvalido, match=texto):
        muestra_valida(**cambios).validar()


def test_json_roto_no_revienta_la_app():
    with pytest.raises(PaqueteInvalido, match="no es JSON"):
        Muestra.desde_json("{esto no es json")
    with pytest.raises(PaqueteInvalido, match="faltan campos"):
        Muestra.desde_json('{"id":"izq"}')


# --- la máscara de contactos -------------------------------------------------


def test_cinco_dedos_dejan_cuatro_huecos():
    # Las tiras van en los costados, así que hay un hueco entre cada par de
    # dedos vecinos: 5 dedos -> 4 huecos. Si esto falla, el hardware cambió.
    assert N_BITS_TOUCH == len(DEDOS) - 1
    assert N_TIRAS_COBRE == 8  # las ocho del boceto: dos por hueco


def test_todos_los_pares_son_adyacentes():
    # Un par no adyacente no es un dato que falte: es uno que no existe.
    for a, b in PARES_CONTACTO:
        assert abs(DEDOS.index(a) - DEDOS.index(b)) == 1


def test_mascara_ida_y_vuelta():
    pares = [("pulgar", "indice"), ("medio", "anular")]
    m = mascara_de_pares(pares)
    assert set(pares_en_contacto(m)) == {("pulgar", "indice"), ("medio", "anular")}


def test_el_orden_de_los_dedos_no_importa():
    assert bit_del_par("medio", "anular") == bit_del_par("anular", "medio")


def test_un_par_no_adyacente_se_rechaza():
    # pulgar y anular no se pueden tocar con tiras en los costados.
    with pytest.raises(ValueError, match="no son dedos adyacentes"):
        bit_del_par("pulgar", "anular")


def test_mascara_vacia():
    assert pares_en_contacto(0) == ()


def test_los_cuatro_huecos_a_la_vez():
    assert len(pares_en_contacto(0b1111)) == 4


# --- crudo -> tres estados ---------------------------------------------------


def perfil() -> PerfilFlex:
    return PerfilFlex(
        guante="izq", rangos=[RangoDedo(abierto=1500, cerrado=2900) for _ in DEDOS]
    )


def test_los_tres_estados_salen_del_crudo():
    p = perfil()
    assert p.estado(1500, 0) is EstadoDedo.EXTENDIDO
    assert p.estado(2200, 0) is EstadoDedo.DOBLADO
    assert p.estado(2900, 0) is EstadoDedo.RETRAIDO


def test_normalizar_se_recorta():
    # Apretar más fuerte que en la calibración no es un error.
    p = perfil()
    assert p.normalizar(1000, 0) == 0.0
    assert p.normalizar(4000, 0) == 1.0


def test_el_umbral_es_por_persona():
    # La misma lectura cruda da estados distintos en dos manos distintas:
    # esta es la razón de que el ESP32 no pueda discretizar.
    mano_grande = PerfilFlex("izq", [RangoDedo(1500, 2900) for _ in DEDOS])
    mano_chica = PerfilFlex("izq", [RangoDedo(1500, 2000) for _ in DEDOS])
    assert mano_grande.estado(1900, 0) is EstadoDedo.EXTENDIDO
    assert mano_chica.estado(1900, 0) is EstadoDedo.RETRAIDO


def test_diagnostico_senala_el_dedo_con_nombre():
    malo = PerfilFlex(
        "izq",
        [
            RangoDedo(1500, 2900),
            RangoDedo(1500, 2900),
            RangoDedo(1500, 2900),
            RangoDedo(1500, 1540),   # 40 unidades: soldadura sospechosa
            RangoDedo(1500, 2900),
        ],
    )
    assert malo.dedos_sospechosos() == ["anular"]
    assert "anular" in malo.diagnostico()[0]
    assert "soldadura" in malo.diagnostico()[0]


# --- el simulador ------------------------------------------------------------


def test_el_simulador_produce_paquetes_que_cumplen_el_contrato():
    with SimuladorGuante(semilla=1) as fuente:
        recibidas = []
        for _ in range(400):
            m = fuente.leer(timeout=0.5)
            if m is None:
                break
            recibidas.append(m)
            # Si el simulador genera algo que el validador rechaza, el bug
            # está en el simulador y no en el enlace.
            m.validar()
            Muestra.desde_json(m.a_json())
    assert len(recibidas) > 50
    assert {m.id for m in recibidas} == {"izq", "der"}


def test_el_simulador_sabe_perder_paquetes():
    with SimuladorGuante(averias=Averias(perdida_pct=30.0), semilla=3) as fuente:
        for _ in range(300):
            if fuente.leer(timeout=0.5) is None:
                break
        # La pérdida se detecta por saltos de seq, que es para lo que seq está.
        assert fuente.stats["izq"].perdidos > 0
        assert fuente.stats["izq"].perdida_pct > 10


def test_el_simulador_sabe_mandar_un_solo_guante():
    with SimuladorGuante(averias=Averias(un_solo_guante="der"), semilla=4) as fuente:
        for _ in range(200):
            if fuente.leer(timeout=0.5) is None:
                break
        assert fuente.stats["der"].recibidos > 0
        assert fuente.stats["izq"].recibidos == 0


def test_el_simulador_sabe_quedarse_con_bateria_baja():
    with SimuladorGuante(averias=Averias(bateria_baja=True), semilla=5) as fuente:
        for _ in range(100):
            if fuente.leer(timeout=0.5) is None:
                break
        assert fuente.stats["izq"].bat < 3.6


def test_detener_dos_veces_no_falla():
    fuente = SimuladorGuante(semilla=6)
    fuente.iniciar()
    fuente.detener()
    fuente.detener()
