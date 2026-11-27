# Lo usa _lugar_seguro_windows.bat. Solo texto ASCII: PowerShell 5 lee mal los acentos de un .ps1 sin BOM.
# Codigo de salida: 0 = la carpeta ya esta en un sitio seguro; 1 = copiada a -Destino; 2 = error.
param(
    [Parameter(Mandatory = $true)][string]$App,
    [Parameter(Mandatory = $true)][string]$Destino
)
$ErrorActionPreference = 'Stop'

function Quitar-Marca([string]$Carpeta) {
    # Marca "descargado de internet" (Mark of the Web): sin ella los .bat no muestran el aviso de SmartScreen.
    Get-ChildItem -LiteralPath $Carpeta -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike '*\.venv\*' } |
        Unblock-File -ErrorAction SilentlyContinue
}

try {
    $app = (Resolve-Path -LiteralPath $App).ProviderPath.TrimEnd('\')
    $conBarra = $app + '\'

    $inseguras = New-Object System.Collections.Generic.List[string]
    $perfil = $env:USERPROFILE.TrimEnd('\')
    foreach ($nombre in 'Downloads', 'Desktop', 'Documents') { $inseguras.Add("$perfil\$nombre") }
    foreach ($especial in 'Desktop', 'MyDocuments') {
        $ruta = [Environment]::GetFolderPath($especial)
        if ($ruta) { $inseguras.Add($ruta) }
    }
    try {
        # Descargas real (puede estar movida a otro disco o a OneDrive)
        $descargas = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path
        if ($descargas) { $inseguras.Add($descargas) }
    } catch { }
    foreach ($variable in 'OneDrive', 'OneDriveConsumer', 'OneDriveCommercial', 'TEMP') {
        $ruta = [Environment]::GetEnvironmentVariable($variable)
        if ($ruta) { $inseguras.Add($ruta) }
    }

    $insegura = $false
    foreach ($ruta in $inseguras) {
        if ($conBarra.StartsWith($ruta.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { $insegura = $true }
    }
    if ($conBarra -match '\\OneDrive') { $insegura = $true }

    if (-not $insegura) {
        Quitar-Marca $app
        exit 0
    }

    Write-Host 'El bot no debe funcionar desde Descargas, Escritorio, Documentos ni OneDrive'
    Write-Host '(esas carpetas se limpian o se sincronizan y el bot dejaria de arrancar).'
    Write-Host "Lo copio a una carpeta segura: $Destino"
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    robocopy $app $Destino /E /XD .venv __pycache__ .git .pytest_cache /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy no pudo copiar la carpeta (codigo $LASTEXITCODE)" }
    Quitar-Marca $Destino
    exit 1
} catch {
    Write-Host ''
    Write-Host 'No se pudo preparar la carpeta del bot:'
    Write-Host $_
    exit 2
}
