#!/bin/bash
# Start ZJ_AiDataProxy server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         ZJ_AiDataProxy Server                              ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Find Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "${RED}❌ Python not found!${NC}"
    exit 1
fi

# Check if dependencies are installed
echo -e "${BLUE}🔍 Checking dependencies...${NC}"
if ! $PYTHON -c "import uvicorn" 2>/dev/null; then
    echo -e "${RED}❌ Dependencies not installed!${NC}"
    echo ""
    echo "Please run the installation script first:"
    echo -e "  ${BLUE}./install.sh${NC}"
    echo ""
    exit 1
fi
echo -e "${GREEN}✅ Dependencies OK${NC}"
echo ""

# Check if port 8000 is available
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo -e "${YELLOW}⚠️  Port 8000 is already in use${NC}"
    echo ""
    read -p "Kill the existing process and continue? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Killing process on port 8000..."
        kill $(lsof -t -i:8000) 2>/dev/null || true
        sleep 2
    else
        echo "Using alternative port 8001..."
        PORT=8001
    fi
else
    PORT=8000
fi

# Create directories if they don't exist
mkdir -p data logs

echo -e "${GREEN}🚀 Starting server on port ${PORT}...${NC}"
echo ""
echo -e "${BLUE}📍 Server URLs:${NC}"
echo -e "   API Docs:   ${GREEN}http://localhost:${PORT}/docs${NC}"
echo -e "   Monitor:    ${GREEN}http://localhost:${PORT}/api/admin/monitor?format=html&user_id=admin_test${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""
echo "─────────────────────────────────────────────────────────────"
echo ""

# Start server
$PYTHON -m uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT}
