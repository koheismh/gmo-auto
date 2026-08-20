#!/bin/bash
# EC2セットアップスクリプト
# Amazon Linux 2023 / t3.small 想定
#
# 使い方:
#   1. EC2インスタンスにSSH接続
#   2. このスクリプトを実行: bash deploy/setup.sh
#   3. config/settings.yaml を編集してAPIキーを設定
#   4. sudo systemctl start crypto-bot

set -e

echo "=========================================="
echo "  GMO Coin Crypto Bot - EC2 Setup"
echo "=========================================="

# システムアップデート
echo "[1/7] System update..."
sudo dnf update -y

# Python 3.12 インストール
echo "[2/7] Installing Python 3.12..."
sudo dnf install -y python3.12 python3.12-pip python3.12-devel git

# プロジェクトディレクトリ
PROJECT_DIR="/home/ec2-user/gmo-auto"
echo "[3/7] Setting up project at ${PROJECT_DIR}..."

if [ ! -d "$PROJECT_DIR" ]; then
    cd /home/ec2-user
    git clone https://github.com/koheismh/gmo-auto.git
fi

cd "$PROJECT_DIR"

# Python仮想環境
echo "[4/7] Creating Python virtual environment..."
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ディレクトリ作成
echo "[5/7] Creating directories..."
mkdir -p logs data config

# 設定ファイルのコピー（存在しない場合のみ）
if [ ! -f config/settings.yaml ]; then
    cp config/settings.example.yaml config/settings.yaml
    echo ""
    echo "  *** config/settings.yaml を作成しました ***"
    echo "  *** APIキーを設定してください:          ***"
    echo "  ***   nano config/settings.yaml          ***"
    echo ""
fi

# systemdサービス登録
echo "[6/7] Installing systemd service..."
sudo cp deploy/crypto-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable crypto-bot

# SNS通知用のIAMロール確認
echo "[7/7] Checking AWS configuration..."
if aws sts get-caller-identity > /dev/null 2>&1; then
    echo "  AWS credentials OK"
else
    echo ""
    echo "  *** AWS認証が設定されていません ***"
    echo "  *** EC2にIAMロール（SNS publish権限）を付与するか、***"
    echo "  *** aws configure で設定してください ***"
    echo ""
fi

echo ""
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "次のステップ:"
echo "  1. APIキーを設定:     nano config/settings.yaml"
echo "  2. 動作確認:          cd $PROJECT_DIR && source .venv/bin/activate && python main.py --dry-run"
echo "  3. Bot起動:           sudo systemctl start crypto-bot"
echo "  4. ログ確認:          sudo journalctl -u crypto-bot -f"
echo "  5. 停止:              sudo systemctl stop crypto-bot"
echo ""
echo "便利コマンド:"
echo "  バックテスト:         python backtest.py"
echo "  シミュレーション:     python simulate.py"
echo "  感度分析:             python simulate.py --sensitivity"
echo ""
