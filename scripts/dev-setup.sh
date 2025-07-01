#!/bin/bash

# Petrosa Trading Engine - Development Setup Script

set -e

echo "🚀 Setting up Petrosa Trading Engine development environment..."

# Check if Python 3.11+ is installed
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
required_version="3.11"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Python 3.11 or higher is required. Current version: $python_version"
    exit 1
fi

echo "✅ Python version check passed: $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📚 Installing dependencies..."
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file..."
    cat > .env << EOF
# Petrosa Trading Engine Environment Configuration
ENVIRONMENT=development
LOG_LEVEL=DEBUG
API_HOST=0.0.0.0
API_PORT=8000

# Database Configuration
MONGODB_URL=mongodb://localhost:27017/petrosa

# NATS Configuration
NATS_SERVERS=nats://localhost:4222

# Binance Configuration (Testnet)
BINANCE_API_KEY=your-testnet-api-key
BINANCE_API_SECRET=your-testnet-api-secret
BINANCE_TESTNET=true

# JWT Configuration
JWT_SECRET_KEY=your-jwt-secret-key-for-development

# Simulation Mode
SIMULATION_ENABLED=true
EOF
    echo "📝 Created .env file. Please update with your actual values."
fi

# Run linting
echo "🔍 Running code quality checks..."
echo "Running flake8..."
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=88 --statistics

echo "Running black check..."
black --check --diff .

echo "Running ruff..."
ruff check .

echo "Running mypy..."
mypy tradeengine/ contracts/ shared/

# Run tests
echo "🧪 Running tests..."
pytest --cov=tradeengine --cov=contracts --cov=shared --cov-report=term-missing

echo "✅ Development environment setup complete!"
echo ""
echo "Next steps:"
echo "1. Update .env file with your actual configuration"
echo "2. Start the application: python -m tradeengine.api"
echo "3. Run tests: pytest"
echo "4. Format code: black ."
echo "5. Lint code: ruff check ."
echo ""
echo "Happy coding! 🎉" 