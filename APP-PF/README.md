# App de PC — Guantes traductores de LSD

La app que recibe lo que mandan los ESP32 de los guantes y **traduce a seña de LSD**.
Los guantes mandan el estado de la mano; **traducir es trabajo de la PC**, no del microcontrolador.

> Se trabaja **por partes**. Abajo está qué parte está hecha y qué sigue.
> Las decisiones de arquitectura viven en `CLAUDE.md`, en la raíz del proyecto.

---

## Stack, y por qué

| | | Por qué |
|---|---|---|
| **Python 3.12** | comprobado: 3.12.10 en esta PC | MediaPipe declara 3.9 a 3.14; es donde más soporte tiene |
| **PySide6** | interfaz | Streamlit está descartado: no sirve para tiempo real |
| **paho-mqtt** | transporte | el contrato ya está congelado sobre MQTT |
| **mediapipe 1.1.1** | rostro | rueda `py3-none-win_amd64` comprobada en esta PC |
| **PyInstaller** | ejecutable portable | — |

**C quedó fuera** aunque sea cómodo: MediaPipe en C es C++ con Bazel, y compilar eso en
Windows es un proyecto en sí mismo. En Python es `pip install`.

---

## Montarlo — en cualquiera de las dos PCs, un solo comando

```powershell
.\instalar.ps1
```

Hace los seis pasos, es **idempotente**, y al final dice qué quedó pendiente y cuál es la IP de
esta PC en la red (la que va al firmware de los ESP32):

| | Paso | ¿Internet? |
|---|---|---|
| 1 | Comprueba que hay **Python 3.12** | no |
| 2 | Crea el **entorno fuera de OneDrive** | no |
| 3 | Instala las dependencias desde **`ruedas\`** | **no** |
| 4 | Comprueba los **modelos de MediaPipe**, los baja si faltan | solo si faltan |
| 5 | Instala **Mosquitto** y le aplica la configuración de los guantes | solo si falta |
| 6 | Verifica que todo importa y corre **las 56 pruebas** | no |

⚠️ **El paso 5 necesita administrador** para copiar la configuración de Mosquitto. Si no lo eres,
**no falla**: lo deja como pendiente con el comando exacto. `-SinMosquitto` y `-SinPruebas` saltan
pasos.

🔴 **`instalar.ps1` está en ASCII puro y tiene que seguir así.** PowerShell 5.1 lee un `.ps1` sin
BOM **como ANSI**, así que un acento dentro de una cadena **rompe el parser** con errores que no
apuntan a la causa. Es el mismo problema que el BOM de `mosquitto.conf`, al revés.

Para volver a entrar al entorno en sesiones siguientes:

```powershell
& "$env:LOCALAPPDATA\lsd-app-venv\Scripts\Activate.ps1"
```

### Por qué las ruedas sí se sincronizan y el entorno no

| | ¿Va en OneDrive? | Por qué |
|---|---|---|
| **`ruedas\`** | ✅ **sí** | Son 35 archivos `.whl` sueltos. Es justo lo que hace falta para instalar sin internet en la otra PC |
| **El entorno virtual** | ❌ **no** | Un venv guarda **rutas absolutas** dentro, y los usuarios de las dos PCs son distintos: llegaría roto. Además son decenas de miles de archivos. Va en `%LOCALAPPDATA%\lsd-app-venv` |

🔴 **Las dos PCs tienen que tener Python 3.12.** Tres ruedas (`numpy`, `matplotlib`, `pillow`)
son `cp312`: solo sirven en 3.12. El resto es `abi3` o `py3-none` y da igual la versión.
`instalar.ps1` comprueba la versión y avisa antes de intentar nada.

### Si hace falta añadir una dependencia

Se añade a `requirements.txt` y se vuelven a bajar las ruedas **en la PC que tenga internet**:

```powershell
python -m pip download -r requirements.txt -d ruedas --only-binary :all:
```

OneDrive las lleva a la otra PC y allá solo se corre `.\instalar.ps1` otra vez.

## El ejecutable portable

```powershell
.\construir.ps1
```

Deja dos cosas:

| | Para qué |
|---|---|
| `dist\TraductorLSD\TraductorLSD.exe` | probarlo en esta PC |
| `TraductorLSD-portable.zip` | **llevarlo a la otra PC o a la demo** |

En la otra PC: descomprimir el ZIP y doble clic. **No hace falta Python ni instalar nada** — el
ejecutable lleva su propio Python dentro. Eso es lo que lo diferencia de `instalar.ps1`, que es el
montaje de desarrollo y sí exige Python 3.12.

### 🔴 Dos cosas del build que no se pueden cambiar

1. **`--add-data modelos`, NO `--collect-all mediapipe`.** MediaPipe no trae los modelos dentro;
   son archivos nuestros. `--collect-all` es la receta que sale en todas las búsquedas y **aquí no
   sirve**: recoge lo que está dentro de mediapipe, y los `.task` no lo están. Sin esto el `.exe`
   abre y **muere al encender la cámara**. `construir.ps1` lo verifica y falla si no entraron.
2. **`--onedir`, no `--onefile`.** Portable no obliga a un solo archivo: onefile se autoextrae en
   temp en cada arranque y con Qt + MediaPipe son 10-20 segundos de espera. Delante del tribunal
   eso es una eternidad.

⚠️ **`dist\` y `build\` no deberían sincronizarse en OneDrive** — son miles de archivos. Lo único
que hace falta llevar es el ZIP, que es uno solo.

🔑 **Y por eso existe `lsd_app/rutas.py`:** dentro de un `.exe` las rutas cambian (PyInstaller
descomprime en `sys._MEIPASS`), así que una ruta calculada con `Path(__file__).parent` funciona en
desarrollo y **apunta a la nada empaquetada**. Todo lo que busca un archivo pasa por ahí. Y
`config.json` mira **primero al lado del `.exe`**, que es lo que hace que una copia en un USB se
lleve su propia configuración.

## Abrir la app en desarrollo

```powershell
python -m lsd_app
```

Arranca con lo que diga `config.json`. Para forzar una fuente sin tocar la configuracion guardada:

```powershell
python -m lsd_app --transporte simulador
```

```powershell
python -m lsd_app --transporte mqtt --host 192.168.0.13
```

Tres secciones: **Start** (los datos del guante en vivo), **Configuracion** (transporte, broker,
cámara) y **Calibracion** (fase 0, con el diagnóstico de qué está mal).

🔑 **Abre y funciona sin guantes, sin broker y sin cámara**, que es la regla de oro. El selector de
transporte vive en Configuración y se aplica sin reiniciar la app.

Opciones útiles para comprobar sin quedarse mirando:
`--pantalla Calibracion`, `--autocerrar 6`, `--captura salida.png`.

## Correrlo por partes

Sin nada de hardware ni broker:

```powershell
python -m lsd_app.herramientas.probar_simulador
```

Y para ver si la fase 0 caza las averías:

```powershell
python -m lsd_app.herramientas.probar_simulador --averiado
```

```powershell
python -m pytest -q
```

### Por MQTT, con el broker

Hacen falta **dos terminales**. En la primera, los ESP32 falsos publicando:

```powershell
python -m lsd_app.herramientas.publicar_simulador
```

En la segunda, la app midiendo el enlace:

```powershell
python -m lsd_app.herramientas.probar_mqtt
```

✅ **Mosquitto 2.1.2 instalado y verificado** (16 oct 2026). El servicio arranca solo con Windows.
Las **56 pruebas pasan**, incluidas las dos que necesitan broker.

```powershell
python -m pytest -q -m broker
```

### 🔴 Para que los ESP32 lleguen hay que cambiar la configuración de Mosquitto

**Mosquitto 2.x, por defecto, escucha SOLO en localhost** (`127.0.0.1` y `::1`). Comprobado en
esta PC. La app en la misma máquina conecta sin problema; **los ESP32, desde el WiFi, no conectan
nunca** — y sin error útil, simplemente no llegan. Es el cambio que rompió Mosquitto 2.0, y es el
tipo de fallo que aparece el día de la demo porque en desarrollo todo corre en la misma PC.

Y hay una trampa encadenada: **en cuanto se declara un `listener`, `allow_anonymous` pasa a
`false`**. Declarar solo el listener cambia "no conecta nadie" por "conectan y los rechaza".

En `mosquitto/mosquitto-guantes.conf` está la configuración con las dos cosas resueltas:

```powershell
copy "mosquitto\mosquitto-guantes.conf" "C:\Program Files\mosquitto\mosquitto.conf"
Restart-Service mosquitto
```

🔴 **Usar `copy`, no `Set-Content -Encoding utf8`:** PowerShell 5.1 mete un BOM y **mosquitto no
arranca ni dice por qué**. Pasó mientras se probaba esto.

✅ **El firewall ya está resuelto:** el instalador de winget crea dos reglas de entrada por
programa, así que cubren cualquier puerto. No hay que tocar nada.

✅ **Probado de punta a punta por la IP de red** (no por localhost), que es como llegarán los
ESP32: **50,0 Hz, 0 % de pérdida, 1 ms de latencia**, `estado online` por retain.

📌 **La IP que va en el firmware es la de esta PC en la red, no `localhost`**, y cambia al cambiar
de red. En el aula será otra; con el hotspot, otra. Por eso es un campo de configuración.

🔑 **Correr `probar_mqtt` en la red del aula, en semana 2, no en diciembre.** `CLAUDE.md` avisa
de que las redes institucionales aíslan clientes entre sí y cierran el puerto 1883. Esta
herramienta lo detecta en 10 segundos y dice qué comprobar.

---

## Qué hay en cada archivo

```
lsd_app/
├── contrato.py              el contrato de datos, como código. La única definición
├── config.py                configuración + selector de transporte. NO se sincroniza
├── fuentes/
│   ├── __init__.py          crear_fuente(): el único sitio que sabe qué transportes hay
│   ├── base.py              interfaz FuenteGuante + estadísticas de enlace
│   ├── simulador.py         el gemelo falso a 50 Hz, con averías a propósito
│   └── mqtt_guante.py       la fuente real, sobre Mosquitto
├── nucleo/
│   └── estados.py           de flex crudo a extendido / doblado / retraído
└── herramientas/
    ├── probar_simulador.py   fase 0 sin nada de hardware
    ├── publicar_simulador.py ESP32 falsos publicando a un broker real
    └── probar_mqtt.py        fase 0 contra el broker
pruebas/
├── test_contrato.py         contrato, estados y simulador
└── test_mqtt.py             MQTT, configuración y selector — sin broker
```

**El transporte es configuración, no arquitectura.** `crear_fuente(config)` es el único sitio
que sabe qué transportes existen; todo lo demás recibe una `FuenteGuante` y no pregunta de dónde
viene. Cambiar de simulador a MQTT es un campo en `config.json`.

🔴 **`config.json` tampoco se sincroniza.** Va en `%LOCALAPPDATA%\lsd-app\config.json`, porque
la IP del broker y el índice de la cámara **son distintos en cada PC**. Si viajara por OneDrive,
cada máquina pisaría la configuración de la otra.

### 🔑 `publicar_simulador.py` es también la especificación del firmware

Lo que hace ahí es exactamente lo que tiene que hacer cada ESP32:

| Topic | Payload | QoS | Retain |
|---|---|---|---|
| `guante/<id>/datos` | el JSON del contrato, 50 Hz | 0 | **no** |
| `guante/<id>/estado` | `online` al conectar | 1 | **sí** |
| `guante/<id>/estado` | `offline` **como Last Will** | 1 | **sí** |

⚠️ **El Last Will es la parte que se olvida.** Sin él, si un ESP32 se muere de golpe el topic de
estado se queda diciendo `online` para siempre y la app no se entera.
⚠️ **Y `online` no basta para dar un guante por vivo:** un ESP32 colgado que no cierra la
conexión sigue marcado online sin publicar nada. `guantes_vivos()` exige además que haya
publicado hace menos de 500 ms.

**La regla de oro:** la app tiene que funcionar completa **sin guantes**. Todo lo que consuma
datos habla con `FuenteGuante` y nunca con MQTT directamente, así que el simulador y los
guantes reales son intercambiables con un parámetro de arranque.

---

## Estado por partes

- [x] **Parte 1 — contrato y simulador.** Hecho. **26 pruebas en verde**, 50,0 Hz medidos,
      inyección de averías funcionando (pérdida, retardo, un solo guante, batería baja,
      BNO sin calibrar, guante que se muere).
- [x] **Parte 2 — transporte MQTT.** Hecho. `fuentes/mqtt_guante` detrás de la misma interfaz,
      con reconexión, seguimiento del topic de estado, contador de reconexiones y diagnóstico en
      frases mostrables. **56 pruebas en verde.** 54 corren sin broker: el cliente paho se inyecta
      y se le llama con los **tipos y firmas reales de paho 2.x**, verificados en su código fuente.
      Las otras 2 corren contra **Mosquitto 2.1.2 de verdad**: la ida y vuelta completa, y el
      **Last Will** —simulado cerrando el socket TCP sin `DISCONNECT`, como un ESP32 al que se le
      acaba la batería—. Verificado además **por la IP de red**, no solo por localhost.
- [~] **Parte 3 — cámara.** Empezada. `fuentes/rostro.py` con MediaPipe Face en su propio hilo,
      los dos modelos en `modelos/`, y `herramientas/medir_rostro.py` para medir el coste por
      frame. ⚠️ **Falta probarlo contra la cámara** y escribirle pruebas.

      🔴🔴 **REGLA QUE NO SE PUEDE SALTAR: nunca `model_asset_path`, siempre
      `model_asset_buffer`.** MediaPipe en Windows **no abre rutas con acentos**, y la carpeta de
      este proyecto los tiene de forma permanente (`Instituto Tecnológico de Las Américas`). Da
      `FileNotFoundError` **sobre un archivo que existe**, lo que lleva a buscar en la dirección
      equivocada. Usar `fuentes.rostro.cargar_modelo()`, que lee los bytes con Python.

      🔴 **MediaPipe no trae los modelos dentro.** Están en `modelos/` y viajan por OneDrive. Al
      empaquetar hacen falta con **`--add-data`**: `--collect-all mediapipe` **no basta**.
- [ ] **Parte 4 — sincronizador.** Buffers circulares y alineación temporal a 30 Hz.
- [~] **Parte 5 — interfaz.** La ventana ya existe y **corre**: Start · Configuración ·
      Calibración, en PySide6. Verificada con captura, no solo con código de salida: 50,0 Hz,
      los dos guantes, los tres estados en color, y la fase 0 diciendo qué falla.
      Falta: tema visual de lengua de señas, y las fases 1 a 5.
- [ ] **Parte 6 — calibración, fases 0 a 5.** Es un test de hardware, no una pantalla bonita:
      tiene que decir qué componente está malo con nombre y apellido.
- [ ] **Parte 7 — visor en vivo** de los features.
- [ ] **Parte 8 — grabador de dataset** conectado al diccionario. 🔴 **Graba vídeo siempre.**
- [ ] **Parte 9 — el clasificador, al final.** El cuello de botella es la recolección.

---

## Tres cosas pendientes de confirmar

1. ✅ **Los contactos son 4 huecos, resuelto.** Las tiras van en los **costados** de los dedos,
   así que solo se tocan dedos **adyacentes**, y cinco dedos en fila dejan **cuatro huecos**:
   `pulgar-indice`, `indice-medio`, `medio-anular`, `anular-menique`. **Máscara de 4 bits**, y
   **8 tiras de cobre** (dos por hueco). Un par no adyacente como `pulgar-anular` no es un dato
   que falte: es uno que **no existe**, y `bit_del_par()` lo rechaza a propósito.
   🔴 **Los 4 pines no pueden ir a GPIO2, GPIO8 ni GPIO9** del ESP32-C3: con pull-down, un hueco
   abierto deja el pin bajo, y **GPIO9 bajo al reset es modo descarga — el guante no arranca.**
2. **El ADC es el ADS7128**, decidido el 14 de octubre. 6 de sus 8 canales en uso.
3. **El transporte.** Listos: **simulador y MQTT**. Pendientes: **ESP-NOW** con equipo central y
   **BLE** — están declarados en `Transporte` para que la pantalla de configuración los muestre
   en gris, y `crear_fuente()` avisa claro si se eligen en vez de fallar de forma rara.

## Dos cosas que el contrato NO deja hacer, y por qué

**Los flex viajan crudos, no como estado.** Los tres estados los deriva la app. El umbral es
*por persona* (sale de la fase 2 de calibración), y un ESP32 no sabe quién se puso el guante.
Y sobre todo: **la semana del 2 al 8 de noviembre no se repite.** Con el crudo grabado, el
umbral se recalcula en diciembre cuantas veces haga falta; con el estado grabado, un umbral
mal puesto se lleva el dataset entero. El guante no pierde nada: la app entrega los tres
estados igual — está en `nucleo/estados.py` y hay una prueba que lo demuestra
(`test_el_umbral_es_por_persona`).

**La batería viaja en voltios, no en porcentaje.** El porcentaje depende de la curva de la
celda, y el modelo de batería todavía no está decidido. De voltios a porcentaje se puede;
al revés, no.

## Al empaquetar (parte 5, no ahora)

- **MediaPipe lleva modelos `.tflite` y grafos que PyInstaller no recoge solo.**
  Hace falta `--collect-all mediapipe`. Es el fallo clásico: funciona en desarrollo y el
  `.exe` muere al abrir la cámara.
- **Preferir `--onedir` a `--onefile`.** Portable no obliga a un solo archivo: `--onefile`
  se autoextrae en temp en cada arranque y con Qt + MediaPipe eso son 10-20 segundos de
  espera delante del tribunal. Una carpeta comprimida arranca al instante y sigue siendo
  portable.
