Param(
    [string]$ServiceName = "MySQLDon_Room",
    [int]$TimeoutSeconds = 60,
    [bool]$OpenBrowser = $true,
    [string]$Url = "http://127.0.0.1:8000/"
)

$ErrorActionPreference = 'Stop'

# Resolve paths relative to repo root (one up from scripts)
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$startMysql = Join-Path $PSScriptRoot 'start-mysql.ps1'
$venvPython = Join-Path $repoRoot 'venv\Scripts\python.exe'
$managePy = Join-Path $repoRoot 'manage.py'

# Read DB host/port from env file if present
$dbHost = '127.0.0.1'
$dbPort = 3306
$envFile = Join-Path $repoRoot 'An_Tir_Authorization\sql_details.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*DB_HOST\s*=\s*(.+)\s*$') { $dbHost = $Matches[1].Trim('"\' + " ") }
        if ($line -match '^\s*DB_PORT\s*=\s*(\d+)\s*$') { $dbPort = [int]$Matches[1] }
    }
}

Write-Host "[dev-run] Ensuring MySQL service '$ServiceName' is running and ready on $($dbHost):$dbPort ..."
& $startMysql -ServiceName $ServiceName -TimeoutSeconds $TimeoutSeconds -DbHost $dbHost -DbPort $dbPort

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
