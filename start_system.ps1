# Starts (or restarts after a reboot) the whole Supply Chain Disruption Copilot on Windows.
#   start_system.bat                 normal start
#   start_system.bat -Build          rebuild the images first (after you changed the code)
#   start_system.bat -LocalLlm       also start the optional local Qwen3-4B model (needs an NVIDIA GPU)
#   start_system.bat -NoBrowser      do not open the browser
# Your data (database, search index, model cache) lives in Docker volumes, so a reboot loses nothing.
param([switch]$Build, [switch]$LocalLlm, [switch]$NoBrowser)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Say($m) { Write-Host "[system] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[system] ERROR: $m" -ForegroundColor Red; exit 1 }

# Docker Desktop may be a per-user or a machine-wide install: add both to PATH for this window.
foreach ($p in "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin", "$env:ProgramFiles\Docker\Docker\resources\bin") {
  if (Test-Path $p) { $env:PATH = "$p;$env:PATH" }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker Desktop is not installed. Install it from https://www.docker.com/products/docker-desktop/" }

function Test-Docker { docker info 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) }

# 1. Docker Desktop must be running.
if (-not (Test-Docker)) {
  $exe = @("$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe", "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe) { Fail "Docker is not running and Docker Desktop.exe was not found. Start Docker Desktop by hand and run this again." }
  Say "Starting Docker Desktop (this takes a minute or two after a reboot)..."
  Start-Process $exe
  $deadline = (Get-Date).AddMinutes(5)
  while (-not (Test-Docker)) {
    if ((Get-Date) -gt $deadline) { Fail "Docker did not become ready within 5 minutes." }
    Start-Sleep -Seconds 5
  }
}
Say "Docker is ready."

# 2. Settings file (API keys live here and are never committed).
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Say "Created .env from .env.example. The system runs without any API key (rule parser and templates); add GEMINI_API_KEY / GROQ_API_KEY to .env for LLM mode."
}

# 3. Start everything. Existing data volumes are reused, so after a reboot this takes seconds.
$composeArgs = @("compose")
if ($LocalLlm) { $composeArgs += @("--profile", "local-llm") }
$composeArgs += @("up", "-d")
if ($Build) { $composeArgs += "--build" }
Say ("docker " + ($composeArgs -join " "))
& docker @composeArgs
if ($LASTEXITCODE -ne 0) { Fail "docker compose failed. Run 'docker compose logs' to see why." }
if ($LocalLlm) { Say "Local model started. Set LOCAL_LLM_ENABLED=true in .env (then run this script again) so the app uses it as the last-resort LLM." }

# 4. Wait until the API and the web app answer. The very first start downloads the dataset and builds the search index (about 10 minutes).
function Wait-Url($url, $minutes) {
  $deadline = (Get-Date).AddMinutes($minutes)
  while ((Get-Date) -lt $deadline) {
    try { if ((Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { return $true } } catch { }
    Start-Sleep -Seconds 5
  }
  return $false
}
Say "Waiting for the API (the first ever start needs about 10 minutes to load the data; later starts take seconds)..."
if (-not (Wait-Url "http://localhost:8000/api/v1/health/live" 25)) { Fail "The API did not become healthy. Run 'docker compose logs api seed' to see why." }
if (-not (Wait-Url "http://localhost:8080" 3)) { Fail "The web app did not answer on http://localhost:8080." }

docker compose ps

Write-Host ""
Write-Host "  The system is running." -ForegroundColor Green
Write-Host "    Web app        http://localhost:8080"
Write-Host "    API docs       http://localhost:8000/docs"
Write-Host "    Test inbox     http://localhost:8025   (approved emails arrive here)"
Write-Host ""
Write-Host "  Demo logins (the sign-in page also has one-click buttons):"
Write-Host "    analyst@example.com    analyst-demo     drafts and edits, cannot approve"
Write-Host "    approver@example.com   approver-demo    can approve and send emails"
Write-Host "    admin@example.com      admin-demo       also manages users"
Write-Host ""
Write-Host "  Stop later with stop_system.bat (your data is kept)."
if (-not $NoBrowser) {
  Say "Opening the web page in your browser..."
  $url = "http://localhost:8080"
  $opened = $false
  try { Start-Process $url -ErrorAction Stop; $opened = $true } catch { }
  if (-not $opened) {
    # Fallbacks for machines where the default-browser association does not work from Start-Process.
    try { Start-Process -FilePath "$env:SystemRoot\explorer.exe" -ArgumentList $url -ErrorAction Stop; $opened = $true } catch { }
  }
  if (-not $opened) {
    try { Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList "/c start `"`" $url" -WindowStyle Hidden -ErrorAction Stop; $opened = $true } catch { }
  }
  if (-not $opened) { Write-Host "  Could not open a browser automatically. Open this address yourself: $url" -ForegroundColor Yellow }
}
