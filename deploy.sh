#!/bin/bash

# GitHub Actions Self-Hosted Runner Manager Deployment Script

set -e

echo "🚀 GitHub Actions Self-Hosted Runner Manager Setup"
echo "================================================="

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Copy environment file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "📝 Creating environment file..."
    cp env.example .env
    echo "⚠️  Please edit .env file with your configuration before running!"
    echo "   Required: GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET"
    echo "   AWS: SECURITY_GROUP_IDS, SUBNET_ID"
fi

# Create systemd service file
echo "🛠️  Creating systemd service file..."
cat > github-runner-manager.service << EOF
[Unit]
Description=GitHub Actions Runner Manager
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin
ExecStart=$(pwd)/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your configuration"
echo "2. Install systemd service: sudo cp github-runner-manager.service /etc/systemd/system/"
echo "3. Enable service: sudo systemctl enable github-runner-manager"
echo "4. Start service: sudo systemctl start github-runner-manager"
echo ""
echo "Or run manually:"
echo "source venv/bin/activate && python main.py"
echo ""
echo "Monitor with:"
echo "curl http://localhost:8080/health" 