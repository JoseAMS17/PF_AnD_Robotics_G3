"""La ventana de la app: Start, Configuracion y Calibracion.

Tres cosas que no son de estilo, son de arquitectura:

1. **Nada de Qt toca MQTT ni la camara.** La ventana habla con `FuenteGuante` y
   con nada mas, asi que funciona igual con el simulador o con los guantes
   reales. Cambiar de uno a otro es un campo en Configuracion.

2. **El hilo de la interfaz nunca se bloquea.** Un `QTimer` vacia la cola de la
   fuente; no hay `leer()` bloqueante en ningun sitio. Si la fuente se cae, la
   ventana sigue viva y lo dice.

3. **La calibracion es un test de hardware, no una pantalla bonita.** Dice que
   componente esta malo con nombre y apellido, y no deja pasar hasta que esta
   en verde.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import TRANSPORTES_LISTOS, Config, Transporte, ruta_config
from ..contrato import (
    BAT_MINIMA_PARA_TRABAJAR,
    DEDOS,
    GUANTES,
    PARES_CONTACTO,
    pares_en_contacto,
)
from ..fuentes import FuenteGuante, crear_fuente
from ..nucleo.estados import EstadoDedo, PerfilFlex, RangoDedo

# Umbrales de la fase 0 de la calibracion obligatoria.
FREC_MINIMA_HZ = 45
PERDIDA_MAXIMA_PCT = 2.0
LATENCIA_MAXIMA_MS = 100

COLOR_ESTADO = {
    EstadoDedo.EXTENDIDO: "#1D9E75",
    EstadoDedo.DOBLADO: "#BA7517",
    EstadoDedo.RETRAIDO: "#A32D2D",
}

HOJA_ESTILO = """
QWidget { background: #14161a; color: #e7e5df; font-size: 14px; }
QLabel#titulo { font-size: 26px; font-weight: 500; }
QLabel#subtitulo { color: #9c9a92; }
QLabel#seccion { font-size: 17px; font-weight: 500; }
QPushButton {
    background: #22262c; border: 1px solid #343a42;
    border-radius: 8px; padding: 10px 18px;
}
QPushButton:hover { background: #2b3038; }
QPushButton:checked { background: #1D9E75; border-color: #1D9E75; color: #04120d; }
QPushButton#primario { background: #1D9E75; border-color: #1D9E75; color: #04120d; }
QFrame#tarjeta { background: #1a1d22; border: 1px solid #272c33; border-radius: 12px; }
QLineEdit, QSpinBox, QComboBox {
    background: #0f1114; border: 1px solid #343a42;
    border-radius: 6px; padding: 6px 8px;
}
QProgressBar { background: #0f1114; border: 1px solid #343a42;
               border-radius: 6px; height: 14px; text-align: center; }
QProgressBar::chunk { background: #1D9E75; border-radius: 5px; }
"""


def _tarjeta(titulo: str) -> tuple[QFrame, QVBoxLayout]:
    marco = QFrame()
    marco.setObjectName("tarjeta")
    caja = QVBoxLayout(marco)
    caja.setContentsMargins(16, 14, 16, 14)
    rotulo = QLabel(titulo)
    rotulo.setObjectName("seccion")
    caja.addWidget(rotulo)
    return marco, caja


# --------------------------------------------------------------- Start -------


class PantallaInicio(QWidget):
    """Los datos del guante en vivo. Es el visor, no el traductor."""

    def __init__(self, ventana: "VentanaPrincipal") -> None:
        super().__init__()
        self.ventana = ventana
        raiz = QVBoxLayout(self)
        raiz.setSpacing(14)

        self.traduccion = QLabel("--")
        self.traduccion.setAlignment(Qt.AlignCenter)
        self.traduccion.setFont(QFont("", 46, QFont.Medium))
        self.traduccion.setMinimumHeight(110)
        marco_t, caja_t = _tarjeta("Sena reconocida")
        caja_t.addWidget(self.traduccion)
        # El indicador de la segunda opinion. MediaPipe VERIFICA, no traduce:
        # si el guante y la camara no coinciden, el sistema se calla y lo dice.
        # Lo que compra no es exactitud, es la opcion de decir "no se".
        self.verificador = QLabel("verificador: --")
        self.verificador.setAlignment(Qt.AlignCenter)
        caja_t.addWidget(self.verificador)

        self.nota_reconocedor = QLabel(
            "El reconocedor todavia es un stub: no devuelve nada a proposito. "
            "Se conecta en la parte 9, cuando haya dataset."
        )
        self.nota_reconocedor.setObjectName("subtitulo")
        self.nota_reconocedor.setWordWrap(True)
        caja_t.addWidget(self.nota_reconocedor)
        raiz.addWidget(marco_t)

        # Una columna de dedos por guante.
        fila = QHBoxLayout()
        self.celdas: dict[str, dict[str, QLabel]] = {}
        self.contactos: dict[str, QLabel] = {}
        for guante in GUANTES:
            marco, caja = _tarjeta(f"Guante {guante}")
            rejilla = QGridLayout()
            rejilla.setHorizontalSpacing(14)
            self.celdas[guante] = {}
            for i, dedo in enumerate(DEDOS):
                rejilla.addWidget(QLabel(dedo), i, 0)
                crudo = QLabel("--")
                crudo.setObjectName("subtitulo")
                estado = QLabel("--")
                rejilla.addWidget(crudo, i, 1)
                rejilla.addWidget(estado, i, 2)
                self.celdas[guante][dedo] = estado
                self.celdas[guante][dedo + "_crudo"] = crudo
            caja.addLayout(rejilla)

            contacto = QLabel("contacto: --")
            contacto.setObjectName("subtitulo")
            contacto.setWordWrap(True)
            caja.addWidget(contacto)
            self.contactos[guante] = contacto
            fila.addWidget(marco)
        raiz.addLayout(fila)
        raiz.addStretch()

    def refrescar(self) -> None:
        rostro = self.ventana.rostro
        if not self.ventana.config.camara.activa:
            self.verificador.setText(
                "verificador: apagado - el guante decide solo, sin segunda opinion"
            )
            self.verificador.setStyleSheet("color: #9c9a92;")
        elif rostro is None:
            self.verificador.setText("verificador: la camara no arranco")
            self.verificador.setStyleSheet("color: #E24B4A;")
        else:
            muestra = rostro.leer()
            if muestra is None or not muestra.una_sola_cara:
                self.verificador.setText(
                    "verificador: sin una sola cara en el encuadre"
                )
                self.verificador.setStyleSheet("color: #BA7517;")
            else:
                self.verificador.setText(
                    f"verificador: listo - {muestra.ms_inferencia:.1f} ms por frame"
                )
                self.verificador.setStyleSheet("color: #1D9E75;")

        perfil = self.ventana.perfil
        for guante in GUANTES:
            muestra = self.ventana.ultima.get(guante)
            if muestra is None:
                continue
            for i, dedo in enumerate(DEDOS):
                crudo = muestra.flex[i]
                self.celdas[guante][dedo + "_crudo"].setText(str(crudo))
                estado = perfil.estado(crudo, i)
                etiqueta = self.celdas[guante][dedo]
                etiqueta.setText(str(estado))
                etiqueta.setStyleSheet(f"color: {COLOR_ESTADO[estado]};")
            pares = pares_en_contacto(muestra.touch)
            self.contactos[guante].setText(
                "contacto: "
                + (", ".join("-".join(p) for p in pares) if pares else "ninguno")
            )


# ------------------------------------------------------- Configuracion -------


class PantallaConfig(QWidget):
    """El transporte es configuracion, no arquitectura. Aqui se elige."""

    def __init__(self, ventana: "VentanaPrincipal") -> None:
        super().__init__()
        self.ventana = ventana
        raiz = QVBoxLayout(self)

        marco, caja = _tarjeta("Transporte")
        formulario = QFormLayout()

        self.transporte = QComboBox()
        for t in Transporte:
            listo = t in TRANSPORTES_LISTOS
            self.transporte.addItem(
                str(t) if listo else f"{t} (sin implementar)", t.value
            )
            # Los que faltan se muestran en gris, no se ocultan: asi se ve que
            # el plan los contempla.
            self.transporte.model().item(self.transporte.count() - 1).setEnabled(listo)
        formulario.addRow("Fuente de datos", self.transporte)

        self.host = QLineEdit()
        self.puerto = QSpinBox()
        self.puerto.setRange(1, 65535)
        self.camara = QSpinBox()
        self.camara.setRange(0, 8)
        self.camara_activa = QComboBox()
        self.camara_activa.addItem("apagada", False)
        self.camara_activa.addItem("encendida", True)
        formulario.addRow("Broker (host)", self.host)
        formulario.addRow("Broker (puerto)", self.puerto)
        formulario.addRow("Camara (indice)", self.camara)
        formulario.addRow("Camara", self.camara_activa)
        caja.addLayout(formulario)

        aviso = QLabel(
            "La camara viene apagada de fabrica: una app no deberia encender la "
            "webcam sola. MediaPipe es el VERIFICADOR, no el traductor, asi que "
            "sin camara el sistema funciona con el guante solo; lo que se pierde "
            "es la opcion de decir 'no se' cuando las dos no coinciden."
        )
        aviso.setObjectName("subtitulo")
        aviso.setWordWrap(True)
        caja.addWidget(aviso)

        self.donde = QLabel(f"Se guarda en {ruta_config()}")
        self.donde.setObjectName("subtitulo")
        self.donde.setWordWrap(True)
        caja.addWidget(self.donde)
        raiz.addWidget(marco)

        botones = QHBoxLayout()
        guardar = QPushButton("Guardar y reconectar")
        guardar.setObjectName("primario")
        guardar.clicked.connect(self._guardar)
        botones.addStretch()
        botones.addWidget(guardar)
        raiz.addLayout(botones)
        raiz.addStretch()

        self.cargar()

    def cargar(self) -> None:
        c = self.ventana.config
        self.transporte.setCurrentIndex(
            max(0, self.transporte.findData(c.transporte.value))
        )
        self.host.setText(c.broker.host)
        self.puerto.setValue(c.broker.puerto)
        self.camara.setValue(c.camara.indice)
        self.camara_activa.setCurrentIndex(1 if c.camara.activa else 0)

    def _guardar(self) -> None:
        c = self.ventana.config
        c.transporte = Transporte(self.transporte.currentData())
        c.broker.host = self.host.text().strip() or "localhost"
        c.broker.puerto = self.puerto.value()
        c.camara.indice = self.camara.value()
        c.camara.activa = bool(self.camara_activa.currentData())
        c.guardar()
        try:
            self.ventana.reconectar()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "No se pudo conectar", str(exc))


# --------------------------------------------------------- Calibracion -------


class PantallaCalibracion(QWidget):
    """Fase 0: el enlace. Las fases 1 a 5 son las partes siguientes."""

    def __init__(self, ventana: "VentanaPrincipal") -> None:
        super().__init__()
        self.ventana = ventana
        raiz = QVBoxLayout(self)

        marco, caja = _tarjeta("Fase 0 - Enlace")
        self.filas: dict[str, QLabel] = {}
        for guante in GUANTES:
            etiqueta = QLabel(f"{guante}: esperando...")
            caja.addWidget(etiqueta)
            self.filas[guante] = etiqueta

        # La camara es parte de la fase 0: "una sola cara detectada".
        self.fila_camara = QLabel("camara: --")
        caja.addWidget(self.fila_camara)
        raiz.addWidget(marco)

        marco_f4, caja_f4 = _tarjeta("Fase 4 - Rostro neutro")
        explicacion = QLabel(
            "Pon cara neutra y pulsa el boton. Sin restar esta linea base, el "
            "modelo aprende QUIEN es la persona en vez de QUE expresion hizo."
        )
        explicacion.setObjectName("subtitulo")
        explicacion.setWordWrap(True)
        caja_f4.addWidget(explicacion)

        fila_f4 = QHBoxLayout()
        self.boton_neutro = QPushButton("Capturar rostro neutro")
        self.boton_neutro.clicked.connect(self._capturar_neutro)
        self.estado_neutro = QLabel("sin capturar")
        self.estado_neutro.setObjectName("subtitulo")
        fila_f4.addWidget(self.boton_neutro)
        fila_f4.addWidget(self.estado_neutro)
        fila_f4.addStretch()
        caja_f4.addLayout(fila_f4)
        raiz.addWidget(marco_f4)

        marco_d, caja_d = _tarjeta("Que esta mal")
        self.diagnostico = QLabel("--")
        self.diagnostico.setWordWrap(True)
        caja_d.addWidget(self.diagnostico)
        raiz.addWidget(marco_d)

        marco_p, caja_p = _tarjeta("Fases siguientes")
        pendientes = QLabel(
            "Fase 1 BNO055 - Fase 2 rango de flex por usuario - "
            "Fase 3 contactos de cobre - Fase 5 prueba de humo.\n"
            "Todavia no estan implementadas. La app no debe dejar grabar ni "
            "traducir hasta que las seis esten en verde."
        )
        pendientes.setObjectName("subtitulo")
        pendientes.setWordWrap(True)
        caja_p.addWidget(pendientes)
        raiz.addWidget(marco_p)
        raiz.addStretch()

    def _capturar_neutro(self) -> None:
        rostro = self.ventana.rostro
        if rostro is None:
            QMessageBox.information(
                self,
                "La camara esta apagada",
                "Enciendela en Configuracion. Sin camara no hay fase 4, y sin "
                "fase 4 el modelo aprende quien es la persona en vez de que "
                "expresion hizo.",
            )
            return
        if rostro.fijar_base_neutra():
            self.estado_neutro.setText("linea base capturada")
            self.estado_neutro.setStyleSheet("color: #1D9E75;")
        else:
            self.estado_neutro.setText("no hay cara en el encuadre todavia")
            self.estado_neutro.setStyleSheet("color: #E24B4A;")

    def refrescar(self) -> None:
        fuente = self.ventana.fuente
        problemas: list[str] = []

        for guante in GUANTES:
            s = fuente.stats[guante]
            if s.recibidos == 0:
                self.filas[guante].setText(f"{guante}: sin paquetes")
                self.filas[guante].setStyleSheet("color: #E24B4A;")
                continue

            fallos = []
            if s.frecuencia_hz < FREC_MINIMA_HZ:
                fallos.append(f"frecuencia {s.frecuencia_hz:.1f} Hz")
            if s.perdida_pct > PERDIDA_MAXIMA_PCT:
                fallos.append(f"perdida {s.perdida_pct:.1f} %")
            if s.latencia_media_ms > LATENCIA_MAXIMA_MS:
                fallos.append(f"latencia {s.latencia_media_ms:.0f} ms")
            if s.bat < BAT_MINIMA_PARA_TRABAJAR:
                fallos.append(f"bateria {s.bat:.2f} V")

            self.filas[guante].setText(
                f"{guante}: {s.frecuencia_hz:.1f} Hz - perdida {s.perdida_pct:.1f} % - "
                f"latencia {s.latencia_media_ms:.0f} ms - bateria {s.bat:.2f} V"
            )
            self.filas[guante].setStyleSheet(
                f"color: {'#E24B4A' if fallos else '#1D9E75'};"
            )
            problemas += [f"{guante}: {f}" for f in fallos]
            if s.invalidos:
                problemas.append(f"{guante}: {s.invalidos} rechazados - {s.ultimo_error}")

        propios = getattr(fuente, "diagnostico", None)
        if callable(propios):
            problemas += propios()

        # --- la camara, que tambien es fase 0 ---
        rostro = self.ventana.rostro
        if not self.ventana.config.camara.activa:
            self.fila_camara.setText(
                "camara: apagada (se enciende en Configuracion)"
            )
            self.fila_camara.setStyleSheet("color: #9c9a92;")
            problemas.append(
                "la camara esta apagada: sin ella no hay fase 4 ni verificacion, "
                "y el sistema no puede decir 'no se'"
            )
        elif rostro is None:
            self.fila_camara.setText(f"camara: {self.ventana.error_camara or 'no arranco'}")
            self.fila_camara.setStyleSheet("color: #E24B4A;")
            problemas.append(self.ventana.error_camara or "la camara no arranco")
        else:
            muestra = rostro.leer()
            if muestra is None:
                self.fila_camara.setText("camara: esperando el primer frame...")
                self.fila_camara.setStyleSheet("color: #BA7517;")
            else:
                ok = muestra.una_sola_cara
                self.fila_camara.setText(
                    f"camara: {muestra.caras_detectadas} cara(s) - "
                    f"{muestra.ms_inferencia:.1f} ms por frame"
                )
                self.fila_camara.setStyleSheet(
                    f"color: {'#1D9E75' if ok else '#E24B4A'};"
                )
            problemas += rostro.diagnostico()

        self.diagnostico.setText(
            "\n".join(f"- {p}" for p in problemas) if problemas else "Todo en verde."
        )
        self.diagnostico.setStyleSheet(
            f"color: {'#E24B4A' if problemas else '#1D9E75'};"
        )


# ------------------------------------------------------------- ventana -------


class VentanaPrincipal(QWidget):
    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config.cargar()
        self.fuente: FuenteGuante | None = None
        self.rostro = None          # FuenteRostro, solo si la camara esta activa
        self.error_camara = ""
        self.ultima: dict = {}

        # Perfil de ejemplo hasta que la fase 2 lo mida por usuario.
        self.perfil = PerfilFlex(
            guante="izq", rangos=[RangoDedo(abierto=1500, cerrado=2900) for _ in DEDOS]
        )

        self.setWindowTitle("Traductor de Lengua de Senas Dominicana")
        self.setMinimumSize(880, 640)
        self.setStyleSheet(HOJA_ESTILO)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(22, 20, 22, 20)
        raiz.setSpacing(16)

        titulo = QLabel("Traductor de Lengua de Senas Dominicana")
        titulo.setObjectName("titulo")
        raiz.addWidget(titulo)

        self.estado_enlace = QLabel("--")
        self.estado_enlace.setObjectName("subtitulo")
        raiz.addWidget(self.estado_enlace)

        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setFormat("enlace %p%")
        raiz.addWidget(self.barra)

        # Menu
        menu = QHBoxLayout()
        self.botones: dict[str, QPushButton] = {}
        for nombre in ("Start", "Configuracion", "Calibracion"):
            b = QPushButton(nombre)
            b.setCheckable(True)
            b.clicked.connect(lambda _c, n=nombre: self.mostrar(n))
            menu.addWidget(b)
            self.botones[nombre] = b
        menu.addStretch()
        raiz.addLayout(menu)

        self.paginas = QStackedWidget()
        self.inicio = PantallaInicio(self)
        self.configuracion = PantallaConfig(self)
        self.calibracion = PantallaCalibracion(self)
        for p in (self.inicio, self.configuracion, self.calibracion):
            self.paginas.addWidget(p)
        raiz.addWidget(self.paginas, 1)

        self.mostrar("Start")

        # Un solo timer vacia la cola. Nada bloqueante en el hilo de Qt.
        self.reloj = QTimer(self)
        self.reloj.timeout.connect(self._tick)
        self.reloj.start(33)  # ~30 Hz, la frecuencia comun del contrato

        self.reconectar()

    # -- navegacion --

    def mostrar(self, nombre: str) -> None:
        indice = {"Start": 0, "Configuracion": 1, "Calibracion": 2}[nombre]
        self.paginas.setCurrentIndex(indice)
        for n, b in self.botones.items():
            b.setChecked(n == nombre)

    # -- fuente --

    def reconectar(self) -> None:
        if self.fuente is not None:
            self.fuente.detener()
            self.fuente = None
        self.ultima.clear()
        self.fuente = crear_fuente(self.config)
        self.fuente.iniciar()
        self.reconectar_camara()

    def reconectar_camara(self) -> None:
        """Arranca o para la cámara segun la configuracion.

        🔴 **La camara nunca se enciende sola.** Viene apagada de fabrica y se
        enciende desde Configuracion. Y si falla al arrancar, **la app sigue
        funcionando**: MediaPipe es el verificador, no el traductor, asi que
        sin camara el sistema trabaja con el guante solo.
        """
        if self.rostro is not None:
            self.rostro.detener()
            self.rostro = None
        self.error_camara = ""

        if not self.config.camara.activa:
            return

        try:
            from ..fuentes.rostro import FuenteRostro

            self.rostro = FuenteRostro(indice_camara=self.config.camara.indice)
            self.rostro.iniciar()
        except Exception as exc:  # noqa: BLE001 - la camara no debe tumbar la app
            self.rostro = None
            self.error_camara = str(exc)

    def closeEvent(self, evento) -> None:  # noqa: N802 - firma de Qt
        if self.fuente is not None:
            self.fuente.detener()
        if self.rostro is not None:
            self.rostro.detener()
        super().closeEvent(evento)

    # -- el latido --

    def _tick(self) -> None:
        if self.fuente is None:
            return

        # Se vacia la cola sin bloquear y se queda la ultima de cada guante:
        # un dato viejo no sirve para tiempo real.
        for _ in range(200):
            muestra = self.fuente.leer(timeout=0)
            if muestra is None:
                break
            self.ultima[muestra.id] = muestra

        vivos = sum(1 for g in GUANTES if self.fuente.stats[g].recibidos > 0)
        frecs = [self.fuente.stats[g].frecuencia_hz for g in GUANTES]
        peor = min(frecs) if frecs else 0.0
        self.barra.setValue(int(min(100, 100 * peor / 50)))
        self.estado_enlace.setText(
            f"{self.config.transporte} - {vivos}/{len(GUANTES)} guantes - "
            f"{peor:.1f} Hz"
        )

        actual = self.paginas.currentWidget()
        refrescar = getattr(actual, "refrescar", None)
        if callable(refrescar):
            refrescar()
