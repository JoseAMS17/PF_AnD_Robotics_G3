# Deja esta PC lista para trabajar en la app de los guantes. Un solo comando.
#
#     .\instalar.ps1
#
# Hace, en este orden:
#   1. Comprueba que hay Python 3.12
#   2. Crea el entorno virtual FUERA de OneDrive
#   3. Instala las dependencias desde `ruedas\` SIN internet, o por
#      internet si esa carpeta no esta (no viaja en el repositorio)
#   4. Comprueba los modelos de MediaPipe (y los baja si faltan)
#   5. Instala Mosquitto si no esta, y le aplica la configuracion de los guantes
#   6. Verifica que todo quedo bien y corre las pruebas
#
# Es idempotente: se puede volver a correr cuantas veces haga falta.
#
# Opciones:
#   -SinMosquitto   se salta el paso 5 (si no hace falta broker en esta PC)
#   -SinPruebas     se salta el paso 6
#
# Para volver a entrar al entorno en sesiones siguientes:
#     & "$env:LOCALAPPDATA\lsd-app-venv\Scripts\Activate.ps1"
#
# ---------------------------------------------------------------------------
# ESTE ARCHIVO ESTA ESCRITO EN ASCII PURO, Y TIENE QUE SEGUIR ASI.
#
# PowerShell 5.1 lee un .ps1 sin BOM como ANSI, no como UTF-8. Un acento o un
# guion largo dentro de una cadena se corrompe y ROMPE EL PARSER, con errores
# como "Token 'sin' inesperado" que no apuntan a la causa. Paso el 17 de
# octubre de 2026 con la primera version de este archivo.
#
# Es el mismo problema que el BOM de mosquitto.conf, al reves: ahi el BOM
# estorba, aqui su ausencia estorba. La salida segura para los dos casos es
# no usar caracteres no ASCII.
# ---------------------------------------------------------------------------

[CmdletBinding()]
param(
    [switch]$SinMosquitto,
    [switch]$SinPruebas,
    [string]$CarpetaRuedas = ""
)

$ErrorActionPreference = "Stop"

# El entorno virtual va FUERA de OneDrive, a proposito. Un venv guarda rutas
# absolutas dentro, asi que uno sincronizado llega roto a la otra maquina, y
# ademas son decenas de miles de archivos que OneDrive intentaria sincronizar.
$venv        = Join-Path $env:LOCALAPPDATA "lsd-app-venv"
# Las ruedas (334 MB de .whl) NO viajan en el repositorio de git: dos de ellas
# pasan del limite de 100 MB por archivo que impone GitHub. Se buscan al lado
# del guion; si no estan, se puede apuntar a otra carpeta (la de OneDrive, por
# ejemplo) con -CarpetaRuedas, y si tampoco, se instala por internet.
$ruedas      = Join-Path $PSScriptRoot "ruedas"
if ($CarpetaRuedas -ne "") { $ruedas = $CarpetaRuedas }
$modelos     = Join-Path $PSScriptRoot "modelos"
$confGuantes = Join-Path $PSScriptRoot "mosquitto\mosquitto-guantes.conf"

$esAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$pendientes = @()

function Paso($n, $texto) { Write-Host "`n[$n] $texto" -ForegroundColor Cyan }
function Bien($texto)     { Write-Host "    OK   $texto" -ForegroundColor Green }
function Ojo($texto)      { Write-Host "    !    $texto" -ForegroundColor Yellow }
function Mal($texto)      { Write-Host "    X    $texto" -ForegroundColor Red }

# --------------------------------------------------------------- 1. Python --
Paso 1 "Python"

$version = (python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($version -ne "3.12") {
    Mal "esta PC tiene Python $version y el proyecto esta verificado en 3.12."
    Write-Host "         Tres ruedas (numpy, matplotlib, pillow) son cp312 y no instalaran."
    Write-Host "         Instala Python 3.12, o vuelve a bajar las ruedas en la otra PC:"
    Write-Host "           python -m pip download -r requirements.txt -d ruedas --only-binary :all:"
    exit 1
}
Bien "Python $version"

# ------------------------------------------------------ 2 y 3. entorno, pip --
Paso 2 "Entorno virtual"

$hayRuedas = Test-Path $ruedas
if (-not $hayRuedas) {
    Ojo "no hay carpeta de ruedas: las dependencias se bajaran de internet."
    Write-Host "         Para instalar sin internet, pasa -CarpetaRuedas <ruta>."
}

if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "    creando en $venv ..."
    python -m venv $venv
    Bien "entorno creado"
} else {
    Bien "ya existia en $venv"
}
$py = Join-Path $venv "Scripts\python.exe"

$req = Join-Path $PSScriptRoot "requirements.txt"
if ($hayRuedas) {
    Paso 3 "Dependencias (desde las ruedas, sin internet)"
    & $py -m pip install --quiet --disable-pip-version-check `
        --no-index --find-links $ruedas -r $req
    Bien "instaladas sin internet"
} else {
    Paso 3 "Dependencias (por internet)"
    & $py -m pip install --quiet --disable-pip-version-check -r $req
    Bien "instaladas por internet"
}

# ------------------------------------------------- 4. modelos de MediaPipe --
Paso 4 "Modelos de MediaPipe"

# MediaPipe NO trae los modelos dentro del paquete: son archivos del proyecto.
# Normalmente ya vienen por OneDrive; esto es la red de seguridad.
$urlFace = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
$urlHand = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
$necesarios = [ordered]@{
    "face_landmarker.task" = $urlFace
    "hand_landmarker.task" = $urlHand
}

New-Item -ItemType Directory -Force $modelos | Out-Null
foreach ($nombre in $necesarios.Keys) {
    $ruta = Join-Path $modelos $nombre
    if ((Test-Path $ruta) -and ((Get-Item $ruta).Length -gt 1MB)) {
        $mb = [math]::Round((Get-Item $ruta).Length / 1MB, 2)
        Bien "$nombre ya esta ($mb MB)"
        continue
    }
    Ojo "$nombre no esta. Bajandolo (hace falta internet)..."
    try {
        Invoke-WebRequest -Uri $necesarios[$nombre] -OutFile $ruta -UseBasicParsing -TimeoutSec 180
        $mb = [math]::Round((Get-Item $ruta).Length / 1MB, 2)
        Bien "$nombre bajado ($mb MB)"
    } catch {
        Mal "no se pudo bajar $nombre : $($_.Exception.Message)"
        $pendientes += "Bajar $nombre a modelos\ ; sin el, la camara no funciona"
    }
}

# ------------------------------------------------------------ 5. Mosquitto --
if ($SinMosquitto) {
    Paso 5 "Mosquitto (saltado por -SinMosquitto)"
} else {
    Paso 5 "Mosquitto"

    $exe = "C:\Program Files\mosquitto\mosquitto.exe"
    if (-not (Test-Path $exe)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Ojo "no esta instalado. Instalando con winget (hace falta internet)..."
            try {
                winget install --id EclipseFoundation.Mosquitto --exact `
                    --accept-package-agreements --accept-source-agreements `
                    --disable-interactivity | Out-Null
            } catch {
                Mal "winget fallo: $($_.Exception.Message)"
            }
        } else {
            Mal "no esta instalado y no hay winget en esta PC."
        }
    }

    if (Test-Path $exe) {
        Bien "instalado"

        # La configuracion por defecto de Mosquitto 2.x escucha SOLO en
        # localhost: los ESP32 no conectarian nunca. Hay que aplicar la nuestra.
        $destino = "C:\Program Files\mosquitto\mosquitto.conf"
        $yaAplicada = $false
        if (Test-Path $destino) {
            $yaAplicada = Select-String -Path $destino -Pattern "listener 1883 0.0.0.0" -SimpleMatch -Quiet
        }

        if ($yaAplicada) {
            Bien "la configuracion de los guantes ya estaba aplicada"
        } elseif ($esAdmin) {
            # Copy-Item y NO Set-Content: PowerShell 5.1 mete un BOM con
            # -Encoding utf8 y mosquitto no arranca ni dice por que.
            Copy-Item -LiteralPath $confGuantes -Destination $destino -Force
            Restart-Service mosquitto
            Start-Sleep -Seconds 2
            Bien "configuracion aplicada y servicio reiniciado"
        } else {
            Ojo "falta aplicar la configuracion, y hace falta ser administrador."
            $pendientes += "Abrir PowerShell COMO ADMINISTRADOR en esta carpeta y correr: Copy-Item 'mosquitto\mosquitto-guantes.conf' 'C:\Program Files\mosquitto\mosquitto.conf' -Force ; Restart-Service mosquitto   (sin esto Mosquitto solo escucha en localhost y los ESP32 no conectan)"
        }

        $escucha = @((Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue).LocalAddress)
        if ($escucha -contains "0.0.0.0") {
            Bien "escuchando en 0.0.0.0:1883 ; los ESP32 pueden llegar"
        } elseif ($escucha.Count -gt 0) {
            Ojo "escuchando solo en $($escucha -join ', ') ; los ESP32 NO pueden llegar"
        } else {
            Ojo "el puerto 1883 no esta escuchando"
        }
    } else {
        $pendientes += "Instalar Mosquitto: winget install EclipseFoundation.Mosquitto"
    }
}

# ----------------------------------------------------------- 6. verificar ---
Paso 6 "Verificacion"

& $py -c "import pytest, paho.mqtt, PySide6, mediapipe, cv2, numpy; print('    OK   todas las dependencias importan')"

if (-not $SinPruebas) {
    Push-Location $PSScriptRoot
    try {
        & $py -m pytest -q
        if ($LASTEXITCODE -eq 0) { Bien "pruebas en verde" } else { Mal "hay pruebas fallando" }
    } finally {
        Pop-Location
    }
}

# -------------------------------------------------------------- resumen -----
Write-Host ""
if ($pendientes.Count -eq 0) {
    Write-Host "LISTO. No queda nada pendiente." -ForegroundColor Green
} else {
    Write-Host "LISTO, pero queda esto por hacer a mano:" -ForegroundColor Yellow
    foreach ($p in $pendientes) { Write-Host "  -> $p" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Para usarlo:"
Write-Host ('  & "' + $venv + '\Scripts\Activate.ps1"')
Write-Host "  python -m lsd_app.herramientas.probar_simulador"
Write-Host "  python -m lsd_app.herramientas.probar_mqtt"
Write-Host "  python -m lsd_app.herramientas.medir_rostro --camara"
Write-Host "  python -m pytest -q"

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress
if ($ip) {
    Write-Host ""
    Write-Host "IP de esta PC en la red (la que va en el firmware de los ESP32): $ip"
    Write-Host "Ojo: cambia al cambiar de red. En el aula sera otra, con el hotspot otra."
}
