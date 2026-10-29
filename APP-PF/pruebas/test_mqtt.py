"""Pruebas de la fuente MQTT, la configuración y el selector de transporte.

**No hace falta broker.** El cliente paho se inyecta, así que todo el manejo de
mensajes se prueba en memoria. Las pruebas que sí necesitan Mosquitto están
marcadas con `@pytest.mark.broker` y se saltan solas si no hay broker:

    python -m pytest pruebas -q                      # todo lo que no lo necesita
    python -m pytest pruebas -q -m broker            # solo las de broker
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

import pytest
from paho.mqtt.client import ConnectFlags, DisconnectFlags
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from lsd_app.config import (
    TRANSPORTES_LISTOS,
    Config,
    ConfigBroker,
    Transporte,
)
from lsd_app.contrato import (
    ESTADO_OFFLINE,
    ESTADO_ONLINE,
    N_FLEX,
    TOPIC_DATOS,
    TOPIC_ESTADO,
    Muestra,
)
from lsd_app.fuentes import TransporteNoImplementado, crear_fuente
from lsd_app.fuentes.mqtt_guante import MqttGuante
from lsd_app.fuentes.simulador import SimuladorGuante


# --- el doble del cliente paho ----------------------------------------------


@dataclass
class MensajeFalso:
    topic: str
    payload: bytes


class ClienteFalso:
    """Lo mínimo de la interfaz de paho que usa `MqttGuante`.

    🔑 **Llama a los callbacks con los tipos y las firmas REALES de paho 2.x**,
    verificadas en su código fuente (`client.py`, las ramas `VERSION2`):

        on_connect(cliente, userdata, ConnectFlags, ReasonCode, Properties)
        on_disconnect(cliente, userdata, DisconnectFlags, ReasonCode, Properties)
        on_message(cliente, userdata, MQTTMessage)

    Esto importa más de lo que parece: si el doble llamara a los callbacks con
    las firmas que a uno le parezcan, **las pruebas pasarían y la app fallaría
    contra un broker real**, porque paho además se traga las excepciones de los
    callbacks ("Caught exception in on_connect") y el fallo sería silencioso.
    """

    def __init__(self) -> None:
        self.suscripciones: list[tuple[str, int]] = []
        self.conectado_a: tuple[str, int] | None = None
        self.loop_corriendo = False
        self.desconexiones = 0
        self.on_connect = self.on_disconnect = self.on_message = None

    # -- API de paho --
    def connect_async(self, host, puerto, keepalive=60):
        self.conectado_a = (host, puerto)

    def loop_start(self):
        self.loop_corriendo = True

    def loop_stop(self):
        self.loop_corriendo = False

    def disconnect(self):
        self.desconexiones += 1

    def subscribe(self, topico, qos=0):
        self.suscripciones.append((topico, qos))

    def username_pw_set(self, *_a, **_k):
        pass

    def reconnect_delay_set(self, **_k):
        pass

    # -- empujar eventos desde la prueba, con los tipos reales de paho --
    def simular_conexion(self, ok: bool = True):
        self.on_connect(
            self,
            None,
            ConnectFlags(session_present=False),
            ReasonCode(PacketTypes.CONNACK, "Success" if ok else "Not authorized"),
            Properties(PacketTypes.CONNACK),
        )

    def simular_desconexion(self, motivo: str = "Keep alive timeout"):
        self.on_disconnect(
            self,
            None,
            DisconnectFlags(is_disconnect_packet_from_server=True),
            ReasonCode(PacketTypes.DISCONNECT, motivo),
            Properties(PacketTypes.DISCONNECT),
        )

    def entregar(self, topico: str, payload: bytes | str):
        if isinstance(payload, str):
            payload = payload.encode()
        self.on_message(self, None, MensajeFalso(topico, payload))


def muestra(guante: str = "izq", seq: int = 1, **cambios) -> Muestra:
    base = dict(
        id=guante,
        seq=seq,
        t=1_700_000_000_000,
        flex=[1800] * N_FLEX,
        quat=[1.0, 0.0, 0.0, 0.0],
        cal=[3, 3, 3, 3],
        touch=0b0110,
        bat=4.0,
    )
    base.update(cambios)
    return Muestra(**base)  # type: ignore[arg-type]


@pytest.fixture
def fuente():
    cliente = ClienteFalso()
    f = MqttGuante(broker=ConfigBroker(host="broker.local"), cliente=cliente)
    f.iniciar()
    cliente.simular_conexion()
    return f, cliente


# --- conexión y suscripción --------------------------------------------------


def test_se_suscribe_a_datos_y_estado_de_los_dos_guantes(fuente):
    f, cliente = fuente
    topicos = [t for t, _ in cliente.suscripciones]
    for guante in ("izq", "der"):
        assert TOPIC_DATOS.format(id=guante) in topicos
        assert TOPIC_ESTADO.format(id=guante) in topicos
    assert f.conectado


def test_el_estado_va_en_qos_1_y_los_datos_en_qos_0(fuente):
    _f, cliente = fuente
    qos = dict(cliente.suscripciones)
    assert qos[TOPIC_DATOS.format(id="izq")] == 0
    assert qos[TOPIC_ESTADO.format(id="izq")] == 1


def test_no_bloquea_al_arrancar(fuente):
    # connect_async + loop_start: si el broker no está, la app abre igual.
    _f, cliente = fuente
    assert cliente.conectado_a == ("broker.local", 1883)
    assert cliente.loop_corriendo


def test_un_broker_que_rechaza_no_deja_la_fuente_conectada():
    cliente = ClienteFalso()
    f = MqttGuante(cliente=cliente)
    f.iniciar()
    cliente.simular_conexion(ok=False)
    assert not f.conectado
    assert "rechazó la conexión" in f.ultimo_motivo_desconexion
    assert any("no hay conexión con el broker" in p for p in f.diagnostico())


def test_cuenta_reconexiones(fuente):
    f, cliente = fuente
    cliente.simular_desconexion()
    cliente.simular_conexion()
    cliente.simular_desconexion()
    assert f.reconexiones == 2
    assert any("reconexiones" in p for p in f.diagnostico())
    # El aviso del plan B tiene que estar: es lo que salva la defensa.
    assert any("hotspot" in p for p in f.diagnostico())


# --- paquetes de datos -------------------------------------------------------


def test_un_paquete_bueno_llega_a_la_cola(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_DATOS.format(id="izq"), muestra("izq", 1).a_json())
    recibida = f.leer(timeout=0.5)
    assert recibida is not None
    assert recibida.id == "izq"
    assert recibida.touch == 0b0110


def test_la_perdida_sale_de_los_saltos_de_seq(fuente):
    f, cliente = fuente
    for seq in (1, 2, 7):  # se perdieron 3, 4, 5 y 6
        cliente.entregar(TOPIC_DATOS.format(id="izq"), muestra("izq", seq).a_json())
    while f.leer(timeout=0.1):
        pass
    assert f.stats["izq"].perdidos == 4
    assert f.stats["izq"].recibidos == 3


def test_un_paquete_roto_se_rechaza_con_motivo_y_no_tumba_nada(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_DATOS.format(id="izq"), b"{no es json")
    assert f.stats["izq"].invalidos == 1
    assert "no es JSON" in f.stats["izq"].ultimo_error
    # Y la app sigue funcionando: el siguiente paquete bueno pasa.
    cliente.entregar(TOPIC_DATOS.format(id="izq"), muestra("izq", 1).a_json())
    assert f.leer(timeout=0.5) is not None


def test_un_flex_fuera_de_rango_dice_que_dedo_es(fuente):
    f, cliente = fuente
    malo = muestra("izq", flex=[1800, 1800, 9999, 1800, 1800])
    cliente.entregar(TOPIC_DATOS.format(id="izq"), json.dumps(
        {
            "id": "izq", "seq": 1, "t": 1, "flex": malo.flex,
            "quat": [1.0, 0, 0, 0], "cal": [3, 3, 3, 3], "touch": 0, "bat": 4.0,
        }
    ))
    assert "flex del medio" in f.stats["izq"].ultimo_error


def test_touch_que_no_cabe_en_los_cuatro_huecos_se_rechaza(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_DATOS.format(id="izq"), json.dumps(
        {
            "id": "izq", "seq": 1, "t": 1, "flex": [1800] * N_FLEX,
            "quat": [1.0, 0, 0, 0], "cal": [3, 3, 3, 3], "touch": 0b11111, "bat": 4.0,
        }
    ))
    assert "huecos" in f.stats["izq"].ultimo_error


def test_si_el_topic_y_el_id_no_cuadran_se_rechaza(fuente):
    # El `id` es redundante con el topic a propósito, y por eso sirve de
    # comprobación: si no cuadran, el firmware está mal.
    f, cliente = fuente
    cliente.entregar(TOPIC_DATOS.format(id="der"), muestra("izq", 1).a_json())
    assert f.stats["der"].invalidos == 1
    assert "el topic dice der y el payload dice izq" in f.stats["der"].ultimo_error
    assert f.leer(timeout=0.1) is None


def test_un_topic_que_no_es_del_contrato_se_cuenta(fuente):
    f, cliente = fuente
    cliente.entregar("guante/izq/datoss", muestra("izq", 1).a_json())
    assert f.topicos_desconocidos == 1
    assert any("topics que no son del contrato" in p for p in f.diagnostico())


# --- el topic de estado ------------------------------------------------------


def test_sigue_el_estado_online_y_offline(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_ESTADO.format(id="izq"), ESTADO_ONLINE)
    assert f.stats["izq"].estado == ESTADO_ONLINE
    cliente.entregar(TOPIC_ESTADO.format(id="izq"), ESTADO_OFFLINE)
    assert f.stats["izq"].estado == ESTADO_OFFLINE


def test_un_estado_desconocido_se_rechaza_con_motivo(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_ESTADO.format(id="izq"), "arrancando")
    assert f.stats["izq"].invalidos == 1
    assert "estado desconocido" in f.stats["izq"].ultimo_error


def test_online_no_basta_para_estar_vivo(fuente):
    # Un ESP32 colgado sin cerrar la conexión sigue marcado online y no manda
    # nada. Estar vivo es haber publicado hace poco, no decir que sí.
    f, cliente = fuente
    cliente.entregar(TOPIC_ESTADO.format(id="izq"), ESTADO_ONLINE)
    assert f.guantes_vivos() == []
    cliente.entregar(TOPIC_DATOS.format(id="izq"), muestra("izq", 1).a_json())
    assert f.guantes_vivos() == ["izq"]


def test_el_diagnostico_nombra_al_guante_que_falta(fuente):
    f, cliente = fuente
    cliente.entregar(TOPIC_DATOS.format(id="izq"), muestra("izq", 1).a_json())
    problemas = " ".join(f.diagnostico())
    assert "guante der no ha publicado" in problemas
    assert "guante izq no ha publicado" not in problemas


def test_detener_dos_veces_no_falla(fuente):
    f, _cliente = fuente
    f.detener()
    f.detener()
    assert not f.conectado


# --- guardia contra un cambio de API en paho --------------------------------


def test_los_callbacks_tienen_la_aridad_que_paho_les_pasa():
    """Si paho cambia las firmas, esto falla aquí y no delante del tribunal.

    paho **se traga las excepciones de los callbacks** (las registra como
    "Caught exception in on_connect" y sigue), así que una firma mal puesta no
    se ve: la app simplemente nunca se daría por conectada.
    """
    import inspect

    cliente = ClienteFalso()
    f = MqttGuante(cliente=cliente)
    # paho llama: (cliente, userdata, flags, reason, properties) y
    # (cliente, userdata, mensaje). `self` ya está ligado en el método.
    esperado = {
        f._al_conectar: 5,
        f._al_desconectar: 5,
        f._al_recibir: 3,
    }
    for metodo, n in esperado.items():
        params = list(inspect.signature(metodo).parameters.values())
        obligatorios = [p for p in params if p.default is inspect.Parameter.empty]
        assert len(params) >= n, f"{metodo.__name__} recibe menos de {n} argumentos"
        assert len(obligatorios) <= n, f"{metodo.__name__} exige más de {n}"


def test_el_codigo_de_razon_de_paho_se_entiende():
    # Se usa `reason_code.is_failure` para saber si el broker aceptó. Si paho
    # quitara ese atributo, la app daría por buena una conexión rechazada.
    assert ReasonCode(PacketTypes.CONNACK, "Success").is_failure is False
    assert ReasonCode(PacketTypes.CONNACK, "Not authorized").is_failure is True


# --- configuración y selector de transporte ---------------------------------


def test_la_config_va_y_viene_de_json(tmp_path):
    c = Config(transporte=Transporte.MQTT, broker=ConfigBroker(host="10.0.0.5", puerto=8883))
    ruta = c.guardar(tmp_path / "config.json")
    recuperada = Config.cargar(ruta)
    assert recuperada.transporte is Transporte.MQTT
    assert recuperada.broker.host == "10.0.0.5"
    assert recuperada.broker.puerto == 8883


def test_una_config_corrupta_no_impide_arrancar(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text("{roto", encoding="utf-8")
    c = Config.cargar(ruta)
    assert c.transporte is Transporte.SIMULADOR  # cae a fábrica y la app abre


def test_si_no_hay_config_se_usa_la_de_fabrica(tmp_path):
    assert Config.cargar(tmp_path / "no-existe.json").transporte is Transporte.SIMULADOR


def test_el_client_id_lleva_el_pid():
    # Si dos copias de la app usan el mismo client_id, Mosquitto expulsa a la
    # primera, y el síntoma es un guante que "se desconecta solo".
    import os

    assert str(os.getpid()) in ConfigBroker().id_efectivo()
    assert ConfigBroker(client_id="fijo").id_efectivo() == "fijo"


def test_el_selector_devuelve_el_simulador():
    f = crear_fuente(Config(transporte=Transporte.SIMULADOR))
    assert isinstance(f, SimuladorGuante)


def test_el_selector_devuelve_mqtt_con_su_broker():
    f = crear_fuente(Config(transporte=Transporte.MQTT, broker=ConfigBroker(host="x")))
    assert isinstance(f, MqttGuante)
    assert f.broker.host == "x"


@pytest.mark.parametrize("t", [Transporte.ESP_NOW, Transporte.BLE])
def test_los_transportes_que_faltan_avisan_claro(t):
    with pytest.raises(TransporteNoImplementado, match="todavía no está implementado"):
        crear_fuente(Config(transporte=t))


def test_los_transportes_listos_son_los_que_de_verdad_funcionan():
    for t in TRANSPORTES_LISTOS:
        crear_fuente(Config(transporte=t)).detener()


# --- con broker de verdad ----------------------------------------------------


def hay_broker(host="localhost", puerto=1883) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.broker
@pytest.mark.skipif(not hay_broker(), reason="no hay un broker en localhost:1883")
def test_el_last_will_avisa_cuando_un_guante_se_muere():
    """Un ESP32 que se muere de golpe: el broker publica `offline` por él.

    🔑 Es la prueba que de verdad importa del topic de estado, y la que no se
    puede hacer sin broker: el Last Will **lo publica el broker**, no la app ni
    el guante. Se simula la muerte cerrando el socket TCP sin enviar DISCONNECT,
    que es lo que pasa cuando a un ESP32 se le acaba la batería.
    """
    import time

    import paho.mqtt.client as mqtt

    topico = TOPIC_ESTADO.format(id="der")

    with MqttGuante(broker=ConfigBroker()) as f:
        espera = time.perf_counter() + 3
        while time.perf_counter() < espera and not f.conectado:
            f.leer(timeout=0.1)
        assert f.conectado, "no conectó al broker"

        # Un guante que declara su Last Will antes de conectar, como debe
        # hacerlo el firmware.
        esp32 = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="guante-der-prueba-will",
        )
        esp32.will_set(topico, ESTADO_OFFLINE, qos=1, retain=True)
        esp32.connect("localhost", 1883, keepalive=5)
        esp32.loop_start()
        esp32.publish(topico, ESTADO_ONLINE, qos=1, retain=True)

        fin = time.perf_counter() + 3
        while time.perf_counter() < fin and f.stats["der"].estado != ESTADO_ONLINE:
            f.leer(timeout=0.1)
        assert f.stats["der"].estado == ESTADO_ONLINE, "no llegó el online"

        # Ahora se muere: se cierra el socket a lo bruto, SIN disconnect().
        # Un disconnect limpio no dispara el Last Will, y ese es justo el punto.
        sock = esp32.socket()
        assert sock is not None
        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        esp32.loop_stop()

        fin = time.perf_counter() + 8
        while time.perf_counter() < fin and f.stats["der"].estado != ESTADO_OFFLINE:
            f.leer(timeout=0.1)

        assert f.stats["der"].estado == ESTADO_OFFLINE, (
            "el broker no publicó el Last Will: sin eso, un guante muerto se "
            "queda marcado online para siempre"
        )
        # Y la app no lo da por vivo, que es la consecuencia que importa.
        assert "der" not in f.guantes_vivos()


@pytest.mark.broker
@pytest.mark.skipif(not hay_broker(), reason="no hay un broker en localhost:1883")
def test_ida_y_vuelta_contra_mosquitto():
    """El camino completo: publicar y recibir por un broker real."""
    import time

    import paho.mqtt.client as mqtt

    with MqttGuante(broker=ConfigBroker()) as f:
        espera = time.perf_counter() + 3
        while time.perf_counter() < espera and not f.conectado:
            f.leer(timeout=0.1)
        assert f.conectado, "no conectó al broker"

        pub = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        pub.connect("localhost", 1883, keepalive=10)
        pub.loop_start()
        try:
            pub.publish(TOPIC_ESTADO.format(id="izq"), ESTADO_ONLINE, qos=1, retain=True)
            for seq in range(1, 21):
                pub.publish(
                    TOPIC_DATOS.format(id="izq"), muestra("izq", seq).a_json(), qos=0
                )
                time.sleep(0.02)

            recibidas = []
            fin = time.perf_counter() + 3
            while time.perf_counter() < fin and len(recibidas) < 20:
                m = f.leer(timeout=0.2)
                if m is not None:
                    recibidas.append(m)
        finally:
            pub.disconnect()
            pub.loop_stop()

    assert len(recibidas) >= 15, f"solo llegaron {len(recibidas)} de 20"
    assert f.stats["izq"].estado == ESTADO_ONLINE
    assert f.stats["izq"].invalidos == 0
