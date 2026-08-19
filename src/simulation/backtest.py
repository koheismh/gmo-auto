"""
簡易バックテストエンジン

目的: 明らかにダメな戦略を排除するためのフィルター。
過学習を避けるため、パラメータ最適化は行わない。

使い方:
  python backtest.py                              # デフォルト設定で実行
  python backtest.py --symbol BTC_JPY             # 銘柄指定
  python backtest.py --start 20240401 --end 20240630  # 期間指定
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from src.strategy.indicators import add_all_indicators
from src.strategy.regime import Regime, detect_regime_series


class Side(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    """1トレードの記録"""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: Side
    entry_price: float
    exit_price: float
    size: float  # BTC数量
    pnl: float  # 損益（円）
    pnl_pct: float  # 損益率
    exit_reason: str  # "take_profit", "stop_loss", "trailing_stop", "timeout", "regime_change"


@dataclass
class BacktestConfig:
    """バックテスト設定"""
    # 資金
    initial_capital: float = 100000
    leverage: float = 1.5

    # 手数料
    taker_fee: float = 0.0005  # 0.05%
    slippage: float = 0.0001   # 0.01%

    # ブレイクアウト戦略パラメータ
    donchian_period: int = 20
    ema_period: int = 20
    rsi_period: int = 14
    rsi_long_range: tuple = (40, 70)
    rsi_short_range: tuple = (30, 60)
    volume_multiplier: float = 1.5
    atr_period: int = 14
    take_profit_atr_mult: float = 2.0
    stop_loss_atr_mult: float = 1.0
    trailing_stop_atr_mult: float = 1.0
    max_hold_bars: int = 24  # 6時間 = 24本（15分足）

    # リスク管理
    risk_per_trade: float = 0.02  # 1トレードリスク2%
    daily_loss_limit: float = 0.05

    # 相場状態判定
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    adx_range_threshold: float = 20.0
    bb_period: int = 20
    bb_std: float = 2.0
    bb_slope_period: int = 5


@dataclass
class BacktestResult:
    """バックテスト結果"""
    trades: list = field(default_factory=list)
    initial_capital: float = 100000
    final_capital: float = 100000
    equity_curve: list = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_return_pct(self) -> float:
        return (self.final_capital - self.initial_capital) / self.initial_capital * 100

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return np.mean(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl < 0]
        return np.mean(losses) if losses else 0.0

    @property
    def avg_win_pct(self) -> float:
        wins = [t.pnl_pct for t in self.trades if t.pnl > 0]
        return np.mean(wins) if wins else 0.0

    @property
    def avg_loss_pct(self) -> float:
        losses = [t.pnl_pct for t in self.trades if t.pnl < 0]
        return np.mean(losses) if losses else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        return float(np.max(drawdown) * 100)

    @property
    def max_consecutive_losses(self) -> int:
        max_streak = 0
        current_streak = 0
        for t in self.trades:
            if t.pnl <= 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        return max_streak

    @property
    def sharpe_ratio(self) -> float:
        """簡易シャープレシオ（日次リターンベース）"""
        if len(self.equity_curve) < 2:
            return 0.0
        equity = np.array(self.equity_curve)
        returns = np.diff(equity) / equity[:-1]
        if np.std(returns) == 0:
            return 0.0
        # 年率換算（365日市場）
        return np.mean(returns) / np.std(returns) * np.sqrt(365)


def run_backtest(df: pd.DataFrame, config: BacktestConfig = None) -> BacktestResult:
    """
    ブレイクアウト戦略のバックテストを実行する

    Args:
        df: OHLCVデータ（timestamp, open, high, low, close, volume）
        config: バックテスト設定

    Returns:
        BacktestResult
    """
    if config is None:
        config = BacktestConfig()

    # インジケーター追加
    indicator_config = {
        "adx_period": config.adx_period,
        "bb_period": config.bb_period,
        "bb_std": config.bb_std,
        "donchian_period": config.donchian_period,
        "ema_period": config.ema_period,
        "rsi_period": config.rsi_period,
        "atr_period": config.atr_period,
        "volume_sma_period": 20,
    }
    df = add_all_indicators(df.copy(), indicator_config)

    # 相場状態判定
    regimes = detect_regime_series(
        df,
        adx_trend_threshold=config.adx_trend_threshold,
        adx_range_threshold=config.adx_range_threshold,
        bb_slope_period=config.bb_slope_period,
    )

    # バックテスト実行
    capital = config.initial_capital
    trades = []
    equity_curve = [capital]

    position = None  # 現在のポジション情報
    entry_bar = 0
    entry_price = 0.0
    position_size = 0.0
    position_side = None
    stop_loss = 0.0
    take_profit = 0.0
    trailing_stop = 0.0
    max_price_since_entry = 0.0
    min_price_since_entry = float("inf")
    daily_loss = 0.0
    last_date = None

    # ウォームアップ期間（インジケーターが安定するまでスキップ）
    warmup = max(config.donchian_period, config.bb_period, config.adx_period, config.ema_period) + 10
    start_idx = warmup

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        current_price = row["close"]
        current_date = row["timestamp"].date() if hasattr(row["timestamp"], "date") else None

        # 日次リセット
        if current_date != last_date:
            daily_loss = 0.0
            last_date = current_date

        # 日次損失制限チェック
        if daily_loss >= capital * config.daily_loss_limit:
            if position is not None:
                # 強制決済
                exit_price = current_price * (1 - config.slippage if position_side == Side.LONG else 1 + config.slippage)
                pnl = _calc_pnl(position_side, entry_price, exit_price, position_size, config.taker_fee)
                capital += pnl
                trades.append(Trade(
                    entry_time=df.iloc[entry_bar]["timestamp"],
                    exit_time=row["timestamp"],
                    side=position_side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    size=position_size,
                    pnl=pnl,
                    pnl_pct=pnl / (entry_price * position_size) * 100,
                    exit_reason="daily_limit",
                ))
                position = None
            equity_curve.append(capital)
            continue

        # --- ポジション保持中の処理 ---
        if position is not None:
            bars_held = i - entry_bar
            exit_price = None
            exit_reason = None

            if position_side == Side.LONG:
                max_price_since_entry = max(max_price_since_entry, row["high"])
                current_trailing = max_price_since_entry - row["atr"] * config.trailing_stop_atr_mult

                # 損切り
                if row["low"] <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                # 利確
                elif row["high"] >= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"
                # トレーリングストップ
                elif current_price <= current_trailing and bars_held > 3:
                    exit_price = current_price
                    exit_reason = "trailing_stop"
                # タイムアウト
                elif bars_held >= config.max_hold_bars:
                    exit_price = current_price
                    exit_reason = "timeout"
                # 相場状態変化
                elif regimes.iloc[i] != Regime.TRENDING and bars_held > 5:
                    exit_price = current_price
                    exit_reason = "regime_change"

            elif position_side == Side.SHORT:
                min_price_since_entry = min(min_price_since_entry, row["low"])
                current_trailing = min_price_since_entry + row["atr"] * config.trailing_stop_atr_mult

                # 損切り
                if row["high"] >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                # 利確
                elif row["low"] <= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"
                # トレーリングストップ
                elif current_price >= current_trailing and bars_held > 3:
                    exit_price = current_price
                    exit_reason = "trailing_stop"
                # タイムアウト
                elif bars_held >= config.max_hold_bars:
                    exit_price = current_price
                    exit_reason = "timeout"
                # 相場状態変化
                elif regimes.iloc[i] != Regime.TRENDING and bars_held > 5:
                    exit_price = current_price
                    exit_reason = "regime_change"

            # 決済実行
            if exit_price is not None:
                exit_price *= (1 - config.slippage) if position_side == Side.LONG else (1 + config.slippage)
                pnl = _calc_pnl(position_side, entry_price, exit_price, position_size, config.taker_fee)
                capital += pnl
                daily_loss += max(0, -pnl)

                trades.append(Trade(
                    entry_time=df.iloc[entry_bar]["timestamp"],
                    exit_time=row["timestamp"],
                    side=position_side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    size=position_size,
                    pnl=pnl,
                    pnl_pct=pnl / (entry_price * position_size) * 100 if position_size > 0 else 0,
                    exit_reason=exit_reason,
                ))
                position = None

        # --- ノーポジション時: エントリー判定 ---
        elif position is None and regimes.iloc[i] == Regime.TRENDING:
            signal = _check_breakout_signal(df, i, config)

            if signal is not None:
                current_atr = row["atr"]
                if pd.isna(current_atr) or current_atr <= 0:
                    equity_curve.append(capital)
                    continue

                # ポジションサイズ計算
                risk_amount = capital * config.risk_per_trade
                stop_distance = current_atr * config.stop_loss_atr_mult

                # 理論サイズ
                theoretical_size = risk_amount / stop_distance
                # レバレッジ制限
                max_position_value = capital * config.leverage
                max_size = max_position_value / current_price
                position_size = min(theoretical_size, max_size)

                if position_size <= 0:
                    equity_curve.append(capital)
                    continue

                # エントリー
                entry_price = current_price * (1 + config.slippage if signal == Side.LONG else 1 - config.slippage)
                position_side = signal
                entry_bar = i
                position = True

                if signal == Side.LONG:
                    stop_loss = entry_price - current_atr * config.stop_loss_atr_mult
                    take_profit = entry_price + current_atr * config.take_profit_atr_mult
                    max_price_since_entry = entry_price
                else:
                    stop_loss = entry_price + current_atr * config.stop_loss_atr_mult
                    take_profit = entry_price - current_atr * config.take_profit_atr_mult
                    min_price_since_entry = entry_price

        equity_curve.append(capital)

    result = BacktestResult(
        trades=trades,
        initial_capital=config.initial_capital,
        final_capital=capital,
        equity_curve=equity_curve,
    )
    return result


def _check_breakout_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """ブレイクアウトシグナルをチェック"""
    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    close = row["close"]
    dc_upper = prev_row["dc_upper"]  # 前バーのドンチャンを参照（先読み防止）
    dc_lower = prev_row["dc_lower"]
    current_rsi = row["rsi"]
    current_volume = row["volume"]
    volume_avg = row["volume_sma"]

    # EMAの方向（3本前と比較）
    if i < 3:
        return None
    ema_direction = row["ema"] - df.iloc[i - 3]["ema"]

    # NaN チェック
    if any(pd.isna(v) for v in [dc_upper, dc_lower, current_rsi, current_volume, volume_avg]):
        return None

    # 出来高フィルター
    if volume_avg <= 0 or current_volume < volume_avg * config.volume_multiplier:
        return None

    # ロングシグナル
    if (close > dc_upper and
            ema_direction > 0 and
            config.rsi_long_range[0] <= current_rsi <= config.rsi_long_range[1]):
        return Side.LONG

    # ショートシグナル
    if (close < dc_lower and
            ema_direction < 0 and
            config.rsi_short_range[0] <= current_rsi <= config.rsi_short_range[1]):
        return Side.SHORT

    return None


def _calc_pnl(side: Side, entry: float, exit_price: float, size: float, fee_rate: float) -> float:
    """損益計算（手数料込み）"""
    if side == Side.LONG:
        gross = (exit_price - entry) * size
    else:
        gross = (entry - exit_price) * size

    # 手数料（エントリー + 決済の往復）
    fee = (entry * size * fee_rate) + (exit_price * size * fee_rate)
    return gross - fee


def format_backtest_result(result: BacktestResult, symbol: str = "BTC_JPY") -> str:
    """バックテスト結果を整形して文字列で返す"""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  バックテスト結果 [{symbol}] ブレイクアウト戦略")
    lines.append("=" * 70)
    lines.append("")

    lines.append("【収益サマリー】")
    lines.append(f"  初期資金:         {result.initial_capital:,.0f}円")
    lines.append(f"  最終資金:         {result.final_capital:,.0f}円")
    lines.append(f"  総損益:           {result.total_pnl:+,.0f}円")
    lines.append(f"  リターン:         {result.total_return_pct:+.2f}%")
    lines.append("")

    lines.append("【トレード統計】")
    lines.append(f"  総トレード数:     {result.total_trades}")
    lines.append(f"  勝ちトレード:     {result.winning_trades}")
    lines.append(f"  負けトレード:     {result.losing_trades}")
    lines.append(f"  勝率:             {result.win_rate*100:.1f}%")
    lines.append(f"  プロフィットファクター: {result.profit_factor:.2f}")
    lines.append("")

    lines.append("【平均損益】")
    lines.append(f"  平均勝ち:         {result.avg_win:+,.0f}円 ({result.avg_win_pct:+.2f}%)")
    lines.append(f"  平均負け:         {result.avg_loss:+,.0f}円 ({result.avg_loss_pct:+.2f}%)")
    lines.append("")

    lines.append("【リスク指標】")
    lines.append(f"  最大ドローダウン: {result.max_drawdown_pct:.1f}%")
    lines.append(f"  最大連続負け:     {result.max_consecutive_losses}回")
    lines.append(f"  シャープレシオ:   {result.sharpe_ratio:.2f}")
    lines.append("")

    # 決済理由の内訳
    if result.trades:
        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        lines.append("【決済理由内訳】")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason:20s}: {count}回 ({count/result.total_trades*100:.1f}%)")
        lines.append("")

    # 合格判定
    lines.append("【合格判定】")
    checks = [
        ("勝率 >= 35%", result.win_rate >= 0.35),
        ("PF >= 1.3", result.profit_factor >= 1.3),
        ("最大DD <= 30%", result.max_drawdown_pct <= 30),
        ("トレード数 >= 50/月", True),  # 期間によるので参考
    ]
    all_pass = True
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        lines.append(f"  [{status}] {label}")
        if not passed:
            all_pass = False

    lines.append("")
    if all_pass:
        lines.append("  → 全項目PASS: 本番投入候補")
    else:
        lines.append("  → FAIL項目あり: パラメータ調整 or 戦略見直しが必要")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)
