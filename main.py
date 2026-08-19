#!/usr/bin/env python3
"""
GMOコイン 仮想通貨自動売買Bot - メインエントリーポイント

使い方:
  python main.py                          # デフォルト設定で起動
  python main.py --config path/to/config  # 設定ファイル指定
  python main.py --dry-run                # ドライランモード（注文を出さない）

事前準備:
  1. config/settings.example.yaml を config/settings.yaml にコピー
  2. APIキーとAPIシークレットを設定
  3. SNSトピックARNを設定（通知が必要な場合）
  4. python main.py で起動
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.core.engine import TradingEngine
from src.core.logger import setup_logging


def load_config(config_path: str) -> dict:
    """設定ファイルを読み込む"""
    path = Path(config_path)
    if not path.exists():
        print(f"エラー: 設定ファイルが見つかりません: {config_path}")
        print(f"以下のコマンドで設定ファイルを作成してください:")
        print(f"  cp config/settings.example.yaml config/settings.yaml")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="GMOコイン 仮想通貨自動売買Bot",
    )
    parser.add_argument(
        "--config", type=str, default="config/settings.yaml",
        help="設定ファイルパス (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="ドライランモード（通知はログ出力のみ）",
    )

    args = parser.parse_args()

    # 設定読み込み
    config = load_config(args.config)

    # ドライラン設定
    if args.dry_run:
        config.setdefault("notification", {})["dry_run"] = True

    # ロギング設定
    log_cfg = config.get("logging", {})
    setup_logging(
        level=log_cfg.get("level", "INFO"),
        log_file=log_cfg.get("file", "logs/bot.log"),
        max_size_mb=log_cfg.get("max_size_mb", 50),
        backup_count=log_cfg.get("backup_count", 5),
    )

    # エンジン起動
    engine = TradingEngine(config)
    engine.start()


if __name__ == "__main__":
    main()
