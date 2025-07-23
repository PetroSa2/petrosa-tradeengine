#!/bin/bash

# Setup local environment with Kubernetes configuration
echo "🚀 Setting up local environment with Kubernetes configuration..."

# Copy the example env file to .env
if [ -f "env.example" ]; then
    cp env.example .env
    echo "✅ Created .env file with Kubernetes configuration"
else
    echo "❌ env.example file not found"
    exit 1
fi

# Make the test script executable
chmod +x scripts/test-binance-futures-k8s-config.py

echo ""
echo "📋 Configuration Summary:"
echo "  • Binance API Key: 2fe0e958...54b19aa1"
echo "  • Testnet enabled: true"
echo "  • Futures trading: enabled"
echo "  • Default leverage: 10"
echo "  • Margin type: isolated"
echo "  • Position mode: hedge"
echo ""
echo "🧪 To test the connection, run:"
echo "  python scripts/test-binance-futures-k8s-config.py"
echo ""
echo "🏃 To run the application locally:"
echo "  make run-docker"
echo ""
echo "✅ Local environment setup complete!" 