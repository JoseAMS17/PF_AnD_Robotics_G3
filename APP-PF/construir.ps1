# Construye el ejecutable portable de la app.
#
#     .\construir.ps1
#
# Deja una carpeta en `dist\TraductorLSD\` con `TraductorLSD.exe` dentro.
# Esa carpeta es portable: se copia tal cual a cualquier PC con Windows y
# arranca SIN instalar Python ni nada.
#
# ---------------------------------------------------------------------------
# ESTE ARCHIVO ESTA EN ASCII PURO, Y TIENE QUE SEGUIR ASI.
# PowerShell 5.1 lee un .ps1 sin BOM como ANSI: un acento dentro de una cadena
# rompe el parser con errores que no apuntan a la causa.
# ---------------------------------------------------------------------------
#
# DOS COSAS QUE NO SE PUEDEN CAMBIAR, Y POR QUE:
#
# 1. --add-data con la carpeta `modelos`, NO --collect-all mediapipe.
#    MediaPipe NO trae los modelos dentro del paquete; son archivos nuestros.
#    `--collect-all` es la receta que sale en todas las busquedas y aqui NO
#    sirve: recoge lo que esta dentro de mediapipe, y los .task no lo estan.
#    Sin esto el .exe abre y MUERE al encender la camara.
#
# 2. Se apunta a `lanzar.py`, NO a `lsd_app\__main__.py`.
#    Si se le apunta al __main__.py del paquete, PyInstaller lo ejecuta como
#    script suelto y los imports relativos de dentro revientan con
#    "attempted relative import with no known parent package". El .exe se
#    construye sin un solo error y muere al arrancar. Paso el 19 de octubre.
#
# 3. --onedir, NO --onefile.
#    Portable no obliga a un solo archivo. `--onefile` se autoextrae en la
#    carpeta temporal en CADA arranque, y con Qt + MediaPipe eso son 10-20
#    segundos mirando la pantalla. Delante del tribunal eso es una eternidad.
#    Una carpeta comprimida en un ZIP sigue siendo portable y arranca al
#    instante.

[CmdletBinding()]
param(
    [switch]$Limpiar
)

$ErrorActionPreference = "Stop"

$venv = Join-Path $env:LOCALAPPDATA "lsd-app-venv"
$py = Join-Path $venv "Scripts\python.exe"

function Paso($n, $t) { Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function Bien($t)     { Write-Host "    OK   $t" -ForegroundColor Green }
function Mal($t)      { Write-Host "    X    $t" -ForegroundColor Red }

Paso 1 "Comprobaciones"

if (-not (Test-Path $py)) {
    Mal "no hay entorno virtual. Corre primero: .\instalar.ps1"
    exit 1
}
Bien "entorno virtual"

foreach ($m in @("face_landmarker.task", "hand_landmarker.task")) {
    $r = Join-Path $PSScriptRoot "modelos\$m"
    if (-not (Test-Path $r)) {
        Mal "falta modelos\$m. Corre .\instalar.ps1, que lo baja."
        exit 1
    }
}
Bien "los dos modelos de MediaPipe"

& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Mal "PyInstaller no esta instalado en el entorno. Corre .\instalar.ps1"
    exit 1
}
Bien "PyInstaller"

if ($Limpiar) {
    Paso 2 "Limpiando construcciones anteriores"
    foreach ($c in @("build", "dist")) {
        $r = Join-Path $PSScriptRoot $c
        if (Test-Path $r) { Remove-Item $r -Recurse -Force; Bien "borrado $c\" }
    }
} else {
    Paso 2 "Limpieza (saltada; usa -Limpiar para forzarla)"
}

Paso 3 "Construyendo (tarda varios minutos la primera vez)"

Push-Location $PSScriptRoot
try {
    & $py -m PyInstaller `
        --name TraductorLSD `
        --onedir `
        --windowed `
        --noconfirm `
        --add-data "modelos;modelos" `
        --collect-submodules mediapipe `
        --hidden-import mediapipe `
        --exclude-module matplotlib `
        --exclude-module tkinter `
        lanzar.py

    if ($LASTEXITCODE -ne 0) {
        Mal "PyInstaller fallo"
        exit 1
    }
} finally {
    Pop-Location
}

Paso 4 "Verificacion"

$exe = Join-Path $PSScriptRoot "dist\TraductorLSD\TraductorLSD.exe"
if (-not (Test-Path $exe)) {
    Mal "no se genero el ejecutable"
    exit 1
}

$mb = [math]::Round((Get-ChildItem (Split-Path $exe) -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Bien "TraductorLSD.exe generado ($mb MB la carpeta completa)"

# Los modelos TIENEN que haber entrado. Si no, el .exe muere al abrir la camara.
$modelosDentro = Get-ChildItem (Split-Path $exe) -Recurse -Filter "*.task" -ErrorAction SilentlyContinue
if ($modelosDentro) {
    Bien "los modelos entraron: $($modelosDentro.Count) archivo(s) .task"
} else {
    Mal "LOS MODELOS NO ENTRARON. El .exe abrira pero morira al encender la camara."
    exit 1
}

Paso 5 "Empaquetando en un ZIP para la otra PC"

# POR QUE UN ZIP, Y NO dejar `dist\` tal cual:
# `dist\TraductorLSD\` son miles de archivos pequenos y cientos de MB, y esta
# DENTRO de la carpeta de OneDrive. Sincronizar eso es lento y lo llena todo.
# Un ZIP es UN archivo: OneDrive lo sube de una vez, y en la otra PC se
# descomprime y ya. Ademas es lo que "portable" quiere decir de verdad.
$zip = Join-Path $PSScriptRoot "TraductorLSD-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $PSScriptRoot "dist\TraductorLSD\*") -DestinationPath $zip -CompressionLevel Optimal
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Bien "TraductorLSD-portable.zip ($zipMb MB)"

Write-Host ""
Write-Host "LISTO." -ForegroundColor Green
Write-Host "  Para esta PC:   dist\TraductorLSD\TraductorLSD.exe"
Write-Host "  Para la otra:   TraductorLSD-portable.zip  (lo lleva OneDrive)"
Write-Host ""
Write-Host "En la otra PC: descomprimir el ZIP y doble clic en TraductorLSD.exe."
Write-Host "No hace falta Python, ni instalar nada."
Write-Host ""
Write-Host "Para probarlo aqui:"
Write-Host "  .\dist\TraductorLSD\TraductorLSD.exe --transporte simulador"
Write-Host ""
Write-Host "OJO: la carpeta dist\ y build\ NO deberian sincronizarse en OneDrive."
Write-Host "     Son miles de archivos. El ZIP es lo unico que hace falta llevar."
