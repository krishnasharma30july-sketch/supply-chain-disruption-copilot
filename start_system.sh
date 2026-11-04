#!/usr/bin/env bash
# Same as start_system.bat, for Git Bash on Windows (also works on Linux/macOS when Docker is already running).
#   ./start_system.sh [--build] [--local-llm] [--no-browser]
set -u
cd "$(dirname "$0")"

BUILD=0; LOCAL_LLM=0; BROWSER=1
for a in "$@"; do
  case "$a" in
    --build) BUILD=1 ;; --local-llm) LOCAL_LLM=1 ;; --no-browser) BROWSER=0 ;;
  esac
done
say() { printf '[system] %s\n' "$*"; }
fail() { printf '[system] ERROR: %s\n' "$*" >&2; exit 1; }

# Docker Desktop (per-user or machine-wide install) on Windows.
if [ -n "${LOCALAPPDATA:-}" ]; then
  export PATH="$(cygpath -u "$LOCALAPPDATA")/Programs/DockerDesktop/resources/bin:$(cygpath -u "${PROGRAMFILES:-C:/Program Files}")/Docker/Docker/resources/bin:$PATH"
fi
command -v docker >/dev/null 2>&1 || fail "Docker is not installed (https://www.docker.com/products/docker-desktop/)."

if ! docker info >/dev/null 2>&1; then
  EXE=""
  for p in "${LOCALAPPDATA:-}/Programs/DockerDesktop/Docker Desktop.exe" "${PROGRAMFILES:-}/Docker/Docker/Docker Desktop.exe"; do
    [ -f "$p" ] && EXE="$p" && break
  done
  [ -n "$EXE" ] || fail "Docker is not running and Docker Desktop was not found. Start it by hand and run this again."
  say "Starting Docker Desktop (a minute or two after a reboot)..."
  powershell.exe -NoProfile -Command "Start-Process '$(cygpath -w "$EXE")'" >/dev/null 2>&1
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
  docker info >/dev/null 2>&1 || fail "Docker did not become ready within 5 minutes."
fi
say "Docker is ready."

[ -f .env ] || { cp .env.example .env; say "Created .env from .env.example (add GEMINI_API_KEY / GROQ_API_KEY for LLM mode)."; }

ARGS=(compose)
[ "$LOCAL_LLM" = 1 ] && ARGS+=(--profile local-llm)
ARGS+=(up -d)
[ "$BUILD" = 1 ] && ARGS+=(--build)
say "docker ${ARGS[*]}"
docker "${ARGS[@]}" || fail "docker compose failed (see: docker compose logs)."

wait_url() { # url, attempts (5 s each)
  for _ in $(seq 1 "$2"); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1")" = "200" ] && return 0
    sleep 5
  done
  return 1
}
say "Waiting for the API (first ever start: about 10 minutes to load the data; later starts: seconds)..."
wait_url http://localhost:8000/api/v1/health/live 300 || fail "The API did not become healthy (docker compose logs api seed)."
wait_url http://localhost:8080 36 || fail "The web app did not answer on http://localhost:8080."

docker compose ps
cat <<'EOT'

  The system is running.
    Web app        http://localhost:8080
    API docs       http://localhost:8000/docs
    Test inbox     http://localhost:8025   (approved emails arrive here)

  Demo logins (the sign-in page also has one-click buttons):
    analyst@example.com    analyst-demo     drafts and edits, cannot approve
    approver@example.com   approver-demo    can approve and send emails
    admin@example.com      admin-demo       also manages users

  Stop later with stop_system.bat (your data is kept).
EOT
if [ "$BROWSER" = 1 ]; then
  (cmd.exe //c start "" "http://localhost:8080" 2>/dev/null || xdg-open http://localhost:8080 2>/dev/null || open http://localhost:8080 2>/dev/null) || true
fi
