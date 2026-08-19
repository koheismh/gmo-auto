"""
ロギング設定
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/bot.log",
    max_size_mb: int = 50,
    backup_count: int = 5,
) -> None:
    """
    ロギングを設定

    Args:
        level: ログレベル (DEBUG, INFO, WARNING, ERROR)
        log_file: ログファイルパス
        max_size_mb: ログファイル最大サイズ（MB）
        backup_count: ローテーションで保持するバックアップ数
    """
    # ログディレクトリ作成
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # ルートロガー設定
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # フォーマッター
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # コンソールハンドラー
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.addHandler(console_handler)

    # ファイルハンドラー（ローテーション）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # ファイルにはDEBUGまで記録
    root_logger.addHandler(file_handler)

    # 外部ライブラリのログレベルを制限
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
