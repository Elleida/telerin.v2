#!/bin/bash
# start.sh — TeleRadio v2 (Backend FastAPI + Frontend Next.js)
# Uso:
#   ./start.sh           → lanza ambos
#   ./start.sh backend   → solo backend
#   ./start.sh frontend  → solo frontend

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
AGENT_DIR="$SCRIPT_DIR"

# ── Colores ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; ORANGE='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${ORANGE}🚀 TeleRadio Multi-Agent v2.0${NC}"
echo ""

# ── Verificar .env del agente ──────────────────────────────────────────────
if [ ! -f "$AGENT_DIR/.env" ]; then
  echo -e "${RED}❌ No se encontró $AGENT_DIR/.env${NC}"
  echo "   Copia $AGENT_DIR/.env.example y configúralo."
  exit 1
fi

# Copiar .env al backend si no existe allí
if [ ! -f "$BACKEND_DIR/.env" ]; then
  cp "$AGENT_DIR/.env" "$BACKEND_DIR/.env"
  echo -e "${GREEN}✅ .env copiado a backend/${NC}"
fi

# ── Función: arrancar backend ──────────────────────────────────────────────
start_backend() {
  echo -e "${ORANGE}[Backend] Instalando dependencias Python...${NC}"
  pip install -r "$BACKEND_DIR/requirements.txt" -q

  # Liberar puerto 8000 si está ocupado
  if lsof -ti:8000 &>/dev/null; then
    echo -e "${ORANGE}[Backend] Puerto 8000 ocupado, liberando...${NC}"
    kill -9 $(lsof -ti:8000) 2>/dev/null || true
    sleep 1
  fi

  echo -e "${GREEN}[Backend] Arrancando FastAPI en :8000 ...${NC}"
  cd "$SCRIPT_DIR"
  uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
  BACKEND_PID=$!
  echo "   PID backend: $BACKEND_PID"
}

# ── Función: arrancar frontend ─────────────────────────────────────────────
start_frontend() {
  if ! command -v node &>/dev/null; then
    echo -e "${RED}❌ Node.js no encontrado. Instálalo desde https://nodejs.org${NC}"
    exit 1
  fi

  echo -e "${ORANGE}[Frontend] Instalando dependencias npm...${NC}"
  cd "$FRONTEND_DIR"
  npm install --silent

  # Liberar puerto 8502 si está ocupado
  if lsof -ti:8502 &>/dev/null; then
    echo -e "${ORANGE}[Frontend] Puerto 8502 ocupado, liberando...${NC}"
    kill -9 $(lsof -ti:8502) 2>/dev/null || true
    sleep 1
  fi

  echo -e "${GREEN}[Frontend] Arrancando Next.js en :8502 ...${NC}"
  npm run dev &
  FRONTEND_PID=$!
  echo "   PID frontend: $FRONTEND_PID"
  cd "$SCRIPT_DIR"
}

# ── Lanzar según argumento ─────────────────────────────────────────────────
TARGET="${1:-all}"
# activamos venv si existe

if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
  source "$SCRIPT_DIR/venv/bin/activate"
fi

case $TARGET in
  backend)  start_backend ;;
  frontend) start_frontend ;;
  *)
    start_backend
    sleep 2
    start_frontend
    ;;
esac

echo ""
echo -e "${GREEN}✅ Sistema arrancado:${NC}"
echo "   Backend  → http://localhost:8000"
echo "   Frontend → http://localhost:8502"
echo "   API docs → http://localhost:8000/docs"
echo ""
echo "Pulsa Ctrl+C para parar todo."

# Esperar señal de stop
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
