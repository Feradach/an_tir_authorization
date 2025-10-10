Param(
    [string]$ServiceName = "MySQLDon_Room",
    [int]$TimeoutSeconds = 20,
    [bool]$OpenBrowser = $true,
    [string]$Url = "http://127.0.0.1:8000/"
)

$ErrorActionPreference = 'Stop'

# Resolve paths relative to repo root (one up from scripts)
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$startMysql = Join-Path $PSScriptRoot 'start-mysql.ps1'
$venvPython = Join-Path $repoRoot 'venv\Scripts\python.exe'
$managePy = Join-Path $repoRoot 'manage.py'

Write-Host "[dev-run] Ensuring MySQL service '$ServiceName' is running..."
& $startMysql -ServiceName $ServiceName -TimeoutSeconds $TimeoutSeconds

if (-not (Test-Path $managePy)) {
    throw "manage.py not found at $managePy"
}

if (-not (Test-Path $venvPython)) {
    Write-Warning "Venv Python not found at $venvPython. Falling back to 'python' on PATH."
    $venvPython = 'python'
}

Write-Host "[dev-run] Starting Django server..."
Push-Location $repoRoot
try {
    if ($OpenBrowser) {
        try {
            Start-Process -FilePath "powershell.exe" -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", "Start-Sleep -Seconds 1; Start-Process '$Url'"
            ) | Out-Null
        } catch { Write-Warning "[dev-run] Could not open browser: $_" }
    }
    & $venvPython $managePy runserver
} finally {
    Pop-Location
}
