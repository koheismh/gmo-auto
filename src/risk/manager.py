"""
リスク管理マネージャー

ポジションレベル、アカウントレベル、資金管理の3層でリスクを制御する。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """リスク管理設定"""
    max_risk_per_trade: float = 0.02      # 1トレードリスク（2%）
    daily_loss_limit: float = 0.05        # 日次最大損失（5%）
    consecutive_loss_cooldown: int = 3    # 連続負け閾値
    cooldown_minutes: int = 60            # クールダウン時間
    max_positions: int = 2                # 同時最大ポジション
    min_leverage: float = 1.2             # 最小実効レバレッジ
    max_leverage: float = 2.0             # 最大実効レバレッジ
    bankruptcy_threshold: float = 30000   # 破産判定額（円）
    profit_take_threshold: float = 200000 # 利益確保閾値
    profit_take_notify: float = 150000    # 利益確保推奨通知


@dataclass
class RiskState:
    """リスク管理の現在状態"""
    daily_loss: float = 0.0
    daily_loss_date: Optional[datetime] = None
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None
    current_positions: int = 0
    total_capital: float = 100000
    initial_capital: float = 100000


class RiskManager:
    """リスク管理マネージャー"""

    def __init__(self, config: RiskConfig = None, initial_capital: float = 100000):
        self.config = config or RiskConfig()
        self.state = RiskState(
            total_capital=initial_capital,
            initial_capital=initial_capital,
        )

    def can_trade(self) -> tuple[bool, str]:
        """
        新規トレードが可能かチェック

        Returns:
            (可否, 理由メッセージ)
        """
        now = datetime.now()

        # 破産チェック
        if self.state.total_capital <= self.config.bankruptcy_threshold:
            return False, f"破産ライン到達 (残高: {self.state.total_capital:,.0f}円)"

        # 日次損失制限
        if self._is_same_day(now):
            if self.state.daily_loss >= self.state.total_capital * self.config.daily_loss_limit:
                return False, f"日次損失制限到達 (損失: {self.state.daily_loss:,.0f}円)"

        # クールダウン
        if self.state.cooldown_until and now < self.state.cooldown_until:
            remaining = (self.state.cooldown_until - now).seconds // 60
            return False, f"クールダウン中 (残り{remaining}分)"

        # ポジション上限
        if self.state.current_positions >= self.config.max_positions:
            return False, f"ポジション上限 ({self.config.max_positions}個)"

        return True, "OK"

    def calculate_position_size(
        self,
        current_price: float,
        stop_distance: float,
        signal_strength: float = 0.5,
    ) -> float:
        """
        ポジションサイズを計算

        Args:
            current_price: 現在価格
            stop_distance: 損切り幅（円）
            signal_strength: シグナルの強さ (0.0〜1.0)

        Returns:
            注文数量（BTC等の通貨単位）
        """
        if stop_distance <= 0 or current_price <= 0:
            return 0.0

        # リスク額
        risk_amount = self.state.total_capital * self.config.max_risk_per_trade

        # 理論ポジションサイズ
        theoretical_size = risk_amount / stop_distance

        # 実効レバレッジ（シグナル強度に応じて調整）
        effective_leverage = (
            self.config.min_leverage +
            signal_strength * (self.config.max_leverage - self.config.min_leverage)
        )

        # レバレッジ制限
        max_position_value = self.state.total_capital * effective_leverage
        max_size = max_position_value / current_price

        # 小さい方を採用
        final_size = min(theoretical_size, max_size)

        logger.debug(
            f"Position size: risk={risk_amount:.0f}, "
            f"theoretical={theoretical_size:.6f}, "
            f"max={max_size:.6f}, "
            f"final={final_size:.6f}, "
            f"leverage={effective_leverage:.1f}x"
        )

        return final_size

    def record_trade_result(self, pnl: float) -> None:
        """
        トレード結果を記録

        Args:
            pnl: 損益（正=利益、負=損失）
        """
        now = datetime.now()

        # 日次損失リセット
        if not self._is_same_day(now):
            self.state.daily_loss = 0.0
            self.state.daily_loss_date = now

        # 資金更新
        self.state.total_capital += pnl

        if pnl < 0:
            self.state.daily_loss += abs(pnl)
            self.state.consecutive_losses += 1

            # 連続負けチェック
            if self.state.consecutive_losses >= self.config.consecutive_loss_cooldown:
                self.state.cooldown_until = now + timedelta(minutes=self.config.cooldown_minutes)
                logger.warning(
                    f"連続{self.state.consecutive_losses}敗。"
                    f"{self.config.cooldown_minutes}分クールダウン開始"
                )
        else:
            self.state.consecutive_losses = 0

    def check_capital_alerts(self) -> list[str]:
        """
        資金アラートをチェック

        Returns:
            アラートメッセージのリスト
        """
        alerts = []

        if self.state.total_capital <= self.config.bankruptcy_threshold:
            alerts.append(
                f"[緊急] 破産ライン到達！"
                f"残高: {self.state.total_capital:,.0f}円 "
                f"(閾値: {self.config.bankruptcy_threshold:,.0f}円)"
            )

        elif self.state.total_capital >= self.config.profit_take_threshold:
            alerts.append(
                f"[目標達成] 資金が{self.config.profit_take_threshold:,.0f}円に到達！"
                f"残高: {self.state.total_capital:,.0f}円。出金を推奨。"
            )

        elif self.state.total_capital >= self.config.profit_take_notify:
            alerts.append(
                f"[利益確保推奨] 資金が{self.config.profit_take_notify:,.0f}円に到達。"
                f"残高: {self.state.total_capital:,.0f}円。一部出金を検討。"
            )

        return alerts

    def update_positions(self, count: int) -> None:
        """現在のポジション数を更新"""
        self.state.current_positions = count

    def update_capital(self, capital: float) -> None:
        """現在の資金を更新"""
        self.state.total_capital = capital

    def get_status(self) -> dict:
        """現在の状態をdict形式で返す"""
        can_trade, reason = self.can_trade()
        return {
            "total_capital": self.state.total_capital,
            "initial_capital": self.state.initial_capital,
            "pnl": self.state.total_capital - self.state.initial_capital,
            "pnl_pct": (self.state.total_capital - self.state.initial_capital)
                       / self.state.initial_capital * 100,
            "daily_loss": self.state.daily_loss,
            "consecutive_losses": self.state.consecutive_losses,
            "current_positions": self.state.current_positions,
            "can_trade": can_trade,
            "reason": reason,
        }

    def _is_same_day(self, now: datetime) -> bool:
        """日付が同じかチェック"""
        if self.state.daily_loss_date is None:
            self.state.daily_loss_date = now
            return True
        return self.state.daily_loss_date.date() == now.date()
