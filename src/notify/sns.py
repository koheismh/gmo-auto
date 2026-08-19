"""
AWS SNS通知

取引イベント（約定、損切り、目標達成等）をメールで通知する。
"""

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logger.warning("boto3 not installed. SNS notifications will be logged only.")


class SNSNotifier:
    """AWS SNS通知クライアント"""

    def __init__(self, topic_arn: str, region: str = "ap-northeast-1", dry_run: bool = False):
        """
        Args:
            topic_arn: SNSトピックARN
            region: AWSリージョン
            dry_run: Trueの場合はログ出力のみ（SNSに送信しない）
        """
        self.topic_arn = topic_arn
        self.dry_run = dry_run

        if HAS_BOTO3 and not dry_run:
            self.client = boto3.client("sns", region_name=region)
        else:
            self.client = None

    def _publish(self, subject: str, message: str) -> None:
        """SNSにメッセージを送信"""
        if self.dry_run or not self.client:
            logger.info(f"[SNS dry_run] {subject}: {message[:200]}")
            return

        try:
            self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],  # Subject上限100文字
                Message=message,
            )
            logger.info(f"SNS sent: {subject}")
        except Exception as e:
            logger.error(f"SNS publish error: {e}")

    def notify_entry(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        """エントリー通知"""
        subject = f"[Entry] {side} {symbol}"
        message = (
            f"=== エントリー通知 ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"銘柄: {symbol}\n"
            f"方向: {side}\n"
            f"数量: {size}\n"
            f"価格: {price:,.0f}円\n"
            f"損切り: {stop_loss:,.0f}円\n"
            f"利確: {take_profit:,.0f}円\n"
        )
        self._publish(subject, message)

    def notify_exit(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> None:
        """決済通知"""
        pnl_emoji = "+" if pnl > 0 else ""
        subject = f"[Exit] {reason} {symbol} {pnl_emoji}{pnl:,.0f}円"
        message = (
            f"=== 決済通知 ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"銘柄: {symbol}\n"
            f"方向: {side}\n"
            f"数量: {size}\n"
            f"エントリー: {entry_price:,.0f}円\n"
            f"決済: {exit_price:,.0f}円\n"
            f"損益: {pnl_emoji}{pnl:,.0f}円\n"
            f"理由: {reason}\n"
        )
        self._publish(subject, message)

    def notify_daily_summary(
        self,
        capital: float,
        daily_pnl: float,
        total_pnl: float,
        trades_today: int,
        win_rate: float,
    ) -> None:
        """日次サマリー通知"""
        pnl_sign = "+" if daily_pnl >= 0 else ""
        total_sign = "+" if total_pnl >= 0 else ""
        subject = f"[日次サマリー] {pnl_sign}{daily_pnl:,.0f}円"
        message = (
            f"=== 日次サマリー ===\n"
            f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"現在資金: {capital:,.0f}円\n"
            f"本日損益: {pnl_sign}{daily_pnl:,.0f}円\n"
            f"累計損益: {total_sign}{total_pnl:,.0f}円\n"
            f"本日トレード数: {trades_today}回\n"
            f"本日勝率: {win_rate*100:.1f}%\n"
        )
        self._publish(subject, message)

    def notify_alert(self, alert_message: str) -> None:
        """アラート通知"""
        subject = f"[ALERT] Crypto Bot"
        message = (
            f"=== アラート ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{alert_message}\n"
        )
        self._publish(subject, message)

    def notify_error(self, error_message: str) -> None:
        """エラー通知"""
        subject = "[ERROR] Crypto Bot"
        message = (
            f"=== エラー通知 ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"エラー: {error_message}\n"
        )
        self._publish(subject, message)

    def notify_bankruptcy(self, capital: float, threshold: float) -> None:
        """破産通知（緊急）"""
        subject = "[緊急] 破産ライン到達 - Bot停止"
        message = (
            f"=== 緊急通知 ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"残高が破産ラインに到達したため、Botを停止しました。\n\n"
            f"残高: {capital:,.0f}円\n"
            f"破産ライン: {threshold:,.0f}円\n\n"
            f"対応:\n"
            f"1. 原因を確認してください\n"
            f"2. 追加資金を投入する場合は10万円を入金\n"
            f"3. Botを再起動してください\n"
        )
        self._publish(subject, message)

    def notify_target_reached(self, capital: float, target: float) -> None:
        """目標達成通知"""
        subject = "[目標達成] 利益確保を推奨"
        message = (
            f"=== 目標達成通知 ===\n"
            f"時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"資金が目標に到達しました！\n\n"
            f"現在資金: {capital:,.0f}円\n"
            f"目標額: {target:,.0f}円\n\n"
            f"推奨アクション:\n"
            f"- 10万円を出金して原資を回収\n"
            f"- 残りの利益で運用を継続\n"
        )
        self._publish(subject, message)
