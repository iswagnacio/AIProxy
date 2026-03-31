#!/bin/bash
# Complete installation script for ZJ_AiDataProxy

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
echo -e "${BLUE}║         ZJ_AiDataProxy Installation                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Find Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "${RED}❌ Python not found!${NC}"
    echo "Please install Python 3.8 or higher from https://www.python.org/"
    exit 1
fi

echo -e "${BLUE}🐍 Using Python: $(command -v $PYTHON)${NC}"
echo -e "${BLUE}   Version: $($PYTHON --version)${NC}"
echo ""

# Check Python version
PYTHON_VERSION=$($PYTHON -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
PYTHON_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info[0])')
PYTHON_MINOR=$($PYTHON -c 'import sys; print(sys.version_info[1])')

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}❌ Python 3.8 or higher is required (found $PYTHON_VERSION)${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Python version OK ($PYTHON_VERSION)${NC}"
echo ""

# Upgrade pip
echo -e "${BLUE}📦 Upgrading pip...${NC}"
$PYTHON -m pip install --upgrade pip --quiet
echo -e "${GREEN}✅ pip upgraded${NC}"
echo ""

# Install dependencies
echo -e "${BLUE}📦 Installing server dependencies...${NC}"
echo ""

if [ -f "requirements.txt" ]; then
    echo "Installing from requirements.txt..."
    $PYTHON -m pip install -r requirements.txt
else
    echo "Installing packages individually..."
    $PYTHON -m pip install fastapi uvicorn[standard] aiosqlite pydantic pydantic-settings pyjwt python-multipart requests
fi

echo ""
echo -e "${GREEN}✅ Dependencies installed${NC}"
echo ""

# Verify installation
echo -e "${BLUE}🔍 Verifying installation...${NC}"
$PYTHON -c "
import sys
packages = ['fastapi', 'uvicorn', 'aiosqlite', 'pydantic', 'jwt', 'requests']
all_ok = True
for pkg in packages:
    try:
        __import__(pkg)
        print(f'  ✓ {pkg}')
    except ImportError:
        print(f'  ✗ {pkg} - MISSING')
        all_ok = False
if all_ok:
    print('\nAll packages installed successfully!')
else:
    print('\nSome packages are missing!')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Some packages failed to install${NC}"
    exit 1
fi

echo ""

# Create directories
echo -e "${BLUE}📁 Creating directories...${NC}"
mkdir -p data logs
echo -e "${GREEN}✅ Directories created${NC}"
echo ""

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo -e "${BLUE}📝 Creating .env file...${NC}"
    cat > .env << 'EOF'
# Application
DEBUG=true
APP_NAME=ZJ_AiDataProxy
APP_VERSION=1.0.0

# Database
DB_PATH=./data/proxy.db

# Logs
LOG_DIR=./logs

# JWT (Change in production!)
JWT_SECRET_KEY=change_me_in_production_please
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Agent tokens (format: agent_id:token) - JSON array format
AGENT_TOKENS=["agent_test_01:secret_token_123", "agent_test_02:secret_token_456"]

# CORS
CORS_ORIGINS=["*"]

# Timeouts (seconds)
AGENT_CLAIM_CONFIRM_TIMEOUT=30
STREAM_FIRST_BYTE_TIMEOUT=60
STREAM_CHUNK_INTERVAL_TIMEOUT=90
AGENT_LEASE_TIMEOUT=120
SESSION_RETENTION_DAYS=10
EOF
    echo -e "${GREEN}✅ .env file created${NC}"
else
    echo -e "${YELLOW}⚠️  .env file already exists, skipping${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Installation Complete! 🎉                         ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Start the server:"
echo -e "     ${BLUE}./start_server.sh${NC}"
echo ""
echo "  2. In another terminal, run tests:"
echo -e "     ${BLUE}./tests/run_tests.sh${NC}"
echo ""
echo "  3. View API documentation:"
echo -e "     ${BLUE}http://localhost:8000/docs${NC}"
echo ""
echo "  4. View monitor dashboard:"
echo -e "     ${BLUE}http://localhost:8000/api/admin/monitor?format=html&user_id=admin_test${NC}"
echo ""
