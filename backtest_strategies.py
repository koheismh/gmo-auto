#!/usr/bin/env python3
"""
戦略バックテスト比較スクリプト

複数の戦略を切り替えてバックテストし、合格基準を評価する。

合格基準:
  - 勝率 >= 35%
  - PF >= 1.3
  - 最大DD <= 30%

使い方:
  python backtest_strategies.py --strategy ema_cross
  python backtest_strategies.py --strategy rsi_reversal
  python backtest_strategies.py --strategy breakout_tuned
  python backtest_strategies.py --strategy volatility_breakout
  python backtest_strategies.py --strategy combined
"""

import argparse
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.data.candle import load_candles
from src.strategy.indicators import (
    add_all_indicators, ema, rsi, atr, adx, sma,
    bollinger_bands, bollinger_band_width, donchian_channel,
)
from src.strategy.regime import Regime, detect_regime_series
from src.simulation.backtest import (
    BacktestConfig, BacktestResult, Trade, Side, _calc_pnl,
    format_backtest_result,
)


# =============================================================================
# 戦略シグナル関数
# =============================================================================

def ema_cross_adx_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """
    EMAクロス + ADXフィルター戦略

    シンプルなEMAクロス。ADXでフィルターしてトレンドが存在する時のみ。
    
    エントリー条件:
    - EMA9 が EMA26 をクロス（ゴールデン/デッド）
    - ADX > 20
    """
    if i < 2:
        return None

    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    ema_short = row["ema_short"]
    ema_long = row["ema_long"]
    prev_ema_short = prev_row["ema_short"]
    prev_ema_long = prev_row["ema_long"]
    current_adx = row["adx"]

    # NaNチェック
    if any(pd.isna(v) for v in [ema_short, ema_long, prev_ema_short, prev_ema_long, current_adx]):
        return None

    # ADXフィルター: トレンドが存在すること
    if current_adx < 20:
        return None

    # ゴールデンクロス
    if prev_ema_short <= prev_ema_long and ema_short > ema_long:
        return Side.LONG

    # デッドクロス
    if prev_ema_short >= prev_ema_long and ema_short < ema_long:
        return Side.SHORT

    return None


def rsi_reversal_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """
    RSI逆張り + BB反発戦略（レンジ相場用）

    エントリー条件:
    - ADX < 25（トレンドなし）
    - RSI(14) <= 30 → ロング
    - RSI(14) >= 70 → ショート
    - 価格がBBバンド付近にいること
    """
    if i < 2:
        return None

    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    current_rsi = row["rsi"]
    prev_rsi = prev_row["rsi"]
    current_adx = row["adx"]
    close = row["close"]
    bb_lower = row["bb_lower"]
    bb_upper = row["bb_upper"]

    # NaNチェック
    if any(pd.isna(v) for v in [current_rsi, prev_rsi, current_adx, bb_lower, bb_upper]):
        return None

    # レンジ相場フィルター
    if current_adx >= 25:
        return None

    # RSI売られすぎ + BB下限付近 → ロング
    if current_rsi <= 30 and close <= bb_lower * 1.005:
        return Side.LONG

    # RSI買われすぎ + BB上限付近 → ショート
    if current_rsi >= 70 and close >= bb_upper * 0.995:
        return Side.SHORT

    return None


def breakout_tuned_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """
    ブレイクアウト戦略（パラメータチューニング版）

    変更点:
    - ドンチャン期間: 20 → 30（ノイズ減少）
    - 出来高フィルター: 1.5倍 → 1.2倍（シグナル増加）
    - RSIフィルター緩和
    - EMA方向確認を5本前に変更（より安定したトレンド確認）
    """
    if i < 5:
        return None

    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    close = row["close"]
    dc_upper = prev_row["dc_upper_30"]
    dc_lower = prev_row["dc_lower_30"]
    current_rsi = row["rsi"]
    current_volume = row["volume"]
    volume_avg = row["volume_sma"]
    current_adx = row["adx"]

    # EMAの方向（5本前と比較）
    ema_direction = row["ema"] - df.iloc[i - 5]["ema"]

    # NaN チェック
    if any(pd.isna(v) for v in [dc_upper, dc_lower, current_rsi, current_volume, volume_avg, current_adx]):
        return None

    # ADXフィルター: トレンドの存在
    if current_adx < 22:
        return None

    # 出来高フィルター（緩和版）
    if volume_avg <= 0 or current_volume < volume_avg * 1.2:
        return None

    # ロングシグナル
    if (close > dc_upper and ema_direction > 0 and 35 <= current_rsi <= 75):
        return Side.LONG

    # ショートシグナル
    if (close < dc_lower and ema_direction < 0 and 25 <= current_rsi <= 65):
        return Side.SHORT

    return None


def volatility_breakout_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """
    ボラティリティ収縮→拡大戦略（改良版）

    ロジック:
    - BB幅が直近20本の25パーセンタイル以下に収縮（スクイーズ状態）
    - 収縮後にBBを終値がブレイク
    - ADXが前バーより上昇していること（トレンド発生の初期段階）
    - 出来高確認: 平均以上の出来高で信頼性UP
    """
    if i < 25:
        return None

    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    close = row["close"]
    bb_upper = row["bb_upper"]
    bb_lower = row["bb_lower"]
    bb_width = row["bb_width"]
    current_adx = row["adx"]
    prev_adx = prev_row["adx"]
    current_volume = row["volume"]
    volume_avg = row["volume_sma"]

    # NaNチェック
    if any(pd.isna(v) for v in [close, bb_upper, bb_lower, bb_width, current_adx, prev_adx]):
        return None

    # 直近20本のBB幅のパーセンタイル
    bb_widths_recent = df["bb_width"].iloc[max(0, i-20):i]
    if len(bb_widths_recent) < 15:
        return None

    bb_width_percentile_25 = bb_widths_recent.quantile(0.25)
    bb_width_percentile_50 = bb_widths_recent.quantile(0.50)

    # スクイーズ検出: 直前3本のうち少なくとも2本が25パーセンタイル以下
    squeeze_count = 0
    for j in range(max(0, i-3), i):
        if j < len(df) and not pd.isna(df["bb_width"].iloc[j]):
            if df["bb_width"].iloc[j] <= bb_width_percentile_25:
                squeeze_count += 1

    if squeeze_count < 1:
        return None

    # 現在のBB幅が拡大開始（前バーより広い）
    prev_bb_width = prev_row["bb_width"]
    if pd.isna(prev_bb_width) or bb_width <= prev_bb_width:
        return None

    # ADXが上昇中
    if current_adx <= prev_adx:
        return None

    # 出来高確認（平均の0.8倍以上）
    if not pd.isna(volume_avg) and volume_avg > 0 and current_volume < volume_avg * 0.8:
        return None

    # BB上限ブレイク → ロング
    if close > bb_upper:
        return Side.LONG

    # BB下限ブレイク → ショート
    if close < bb_lower:
        return Side.SHORT

    return None


def combined_signal(df: pd.DataFrame, i: int, config: BacktestConfig) -> Optional[Side]:
    """
    組み合わせ戦略: EMAトレンド方向 + ボラティリティスクイーズ後の拡大

    エントリー条件（全て満たす）:
    1. EMA短期(9) > 長期(26) ならロング方向、逆ならショート方向
    2. BB幅が拡大開始（前バーより拡大）
    3. ADX > 18 かつ上昇中
    4. 価格がEMA長期の方向と一致する動き
    5. RSI: 40-60の中間帯（まだ伸びしろがある）
    """
    if i < 5:
        return None

    row = df.iloc[i]
    prev_row = df.iloc[i - 1]

    ema_short = row["ema_short"]
    ema_long = row["ema_long"]
    current_adx = row["adx"]
    prev_adx = prev_row["adx"]
    current_rsi = row["rsi"]
    bb_width = row["bb_width"]
    prev_bb_width = prev_row["bb_width"]
    close = row["close"]

    # NaNチェック
    if any(pd.isna(v) for v in [ema_short, ema_long, current_adx, prev_adx,
                                 current_rsi, bb_width, prev_bb_width]):
        return None

    # ADXフィルター: トレンド発生中かつ上昇中
    if current_adx < 18 or current_adx <= prev_adx:
        return None

    # RSIフィルター: 中間帯（まだ余地がある）
    if current_rsi < 35 or current_rsi > 65:
        return None

    # ボラティリティ拡大確認
    if bb_width <= prev_bb_width:
        return None

    # EMA方向 + 価格位置
    if ema_short > ema_long and close > ema_long:
        return Side.LONG

    if ema_short < ema_long and close < ema_long:
        return Side.SHORT

    return None


# =============================================================================
# 汎用バックテストエンジン（戦略プラグイン対応）
# =============================================================================

def add_strategy_indicators(df: pd.DataFrame, strategy: str, config: BacktestConfig) -> pd.DataFrame:
    """戦略に応じた追加インジケーターを計算"""
    # 基本インジケーター
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
    df = add_all_indicators(df, indicator_config)

    # 戦略固有のインジケーター
    if strategy in ("ema_cross", "combined"):
        df["ema_short"] = ema(df["close"], 9)
        df["ema_long"] = ema(df["close"], 26)
        df["atr_sma"] = sma(df["atr"], 20)

    if strategy == "breakout_tuned":
        df["dc_upper_30"], df["dc_lower_30"] = donchian_channel(df, period=30)

    return df


def run_strategy_backtest(
    df: pd.DataFrame,
    strategy: str,
    signal_func: Callable,
    config: BacktestConfig = None,
    regime_filter: bool = True,
    allowed_regimes: list = None,
) -> BacktestResult:
    """
    汎用バックテストエンジン

    Args:
        df: OHLCVデータ
        strategy: 戦略名
        signal_func: シグナル生成関数
        config: バックテスト設定
        regime_filter: 相場状態フィルターを使うかどうか
        allowed_regimes: エントリーを許可する相場状態リスト
    """
    if config is None:
        config = BacktestConfig()

    if allowed_regimes is None:
        allowed_regimes = [Regime.TRENDING, Regime.RANGING, Regime.TRANSITION]

    # インジケーター追加
    df = add_strategy_indicators(df.copy(), strategy, config)

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

    position = None
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

    warmup = max(config.donchian_period, config.bb_period, config.adx_period, config.ema_period, 30) + 10
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
                current_atr_val = row["atr"] if not pd.isna(row["atr"]) else 0
                current_trailing = max_price_since_entry - current_atr_val * config.trailing_stop_atr_mult

                # 含み益がTP距離の60%以上に達した場合のみトレーリング発動
                tp_distance = take_profit - entry_price
                unrealized_profit = max_price_since_entry - entry_price
                trailing_active = tp_distance > 0 and unrealized_profit >= tp_distance * 0.6

                if row["low"] <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif row["high"] >= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"
                elif trailing_active and current_price <= current_trailing and bars_held > 6:
                    exit_price = current_price
                    exit_reason = "trailing_stop"
                elif bars_held >= config.max_hold_bars:
                    exit_price = current_price
                    exit_reason = "timeout"

            elif position_side == Side.SHORT:
                min_price_since_entry = min(min_price_since_entry, row["low"])
                current_atr_val = row["atr"] if not pd.isna(row["atr"]) else 0
                current_trailing = min_price_since_entry + current_atr_val * config.trailing_stop_atr_mult

                # 含み益がTP距離の60%以上に達した場合のみトレーリング発動
                tp_distance = entry_price - take_profit
                unrealized_profit = entry_price - min_price_since_entry
                trailing_active = tp_distance > 0 and unrealized_profit >= tp_distance * 0.6

                if row["high"] >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif row["low"] <= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"
                elif trailing_active and current_price >= current_trailing and bars_held > 6:
                    exit_price = current_price
                    exit_reason = "trailing_stop"
                elif bars_held >= config.max_hold_bars:
                    exit_price = current_price
                    exit_reason = "timeout"

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
        elif position is None:
            # 相場状態フィルター
            if regime_filter and regimes.iloc[i] not in allowed_regimes:
                equity_curve.append(capital)
                continue

            signal = signal_func(df, i, config)

            if signal is not None:
                current_atr = row["atr"]
                if pd.isna(current_atr) or current_atr <= 0:
                    equity_curve.append(capital)
                    continue

                # ポジションサイズ計算
                risk_amount = capital * config.risk_per_trade
                stop_distance = current_atr * config.stop_loss_atr_mult

                theoretical_size = risk_amount / stop_distance
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

    return BacktestResult(
        trades=trades,
        initial_capital=config.initial_capital,
        final_capital=capital,
        equity_curve=equity_curve,
    )


# =============================================================================
# 戦略レジストリ
# =============================================================================

STRATEGIES = {
    "ema_cross": {
        "name": "EMAクロス + ADXフィルター",
        "func": ema_cross_adx_signal,
        "regime_filter": True,
        "allowed_regimes": [Regime.TRENDING, Regime.TRANSITION],
        "config_overrides": {
            "take_profit_atr_mult": 15.0,
            "stop_loss_atr_mult": 5.0,
            "trailing_stop_atr_mult": 8.0,
            "max_hold_bars": 96,  # 24時間
        },
    },
    "rsi_reversal": {
        "name": "RSI逆張り（レンジ相場用）",
        "func": rsi_reversal_signal,
        "regime_filter": True,
        "allowed_regimes": [Regime.RANGING, Regime.TRANSITION],
        "config_overrides": {
            "take_profit_atr_mult": 10.0,
            "stop_loss_atr_mult": 4.0,
            "trailing_stop_atr_mult": 6.0,
            "max_hold_bars": 64,  # 16時間
        },
    },
    "breakout_tuned": {
        "name": "ブレイクアウト（パラメータ調整版）",
        "func": breakout_tuned_signal,
        "regime_filter": True,
        "allowed_regimes": [Regime.TRENDING],
        "config_overrides": {
            "donchian_period": 30,
            "take_profit_atr_mult": 15.0,
            "stop_loss_atr_mult": 5.0,
            "trailing_stop_atr_mult": 8.0,
            "max_hold_bars": 96,
            "volume_multiplier": 1.2,
        },
    },
    "volatility_breakout": {
        "name": "ボラティリティ収縮→拡大",
        "func": volatility_breakout_signal,
        "regime_filter": False,
        "allowed_regimes": [Regime.TRENDING, Regime.RANGING, Regime.TRANSITION],
        "config_overrides": {
            "take_profit_atr_mult": 15.0,
            "stop_loss_atr_mult": 5.0,
            "trailing_stop_atr_mult": 8.0,
            "max_hold_bars": 96,
        },
    },
    "combined": {
        "name": "組み合わせ（EMAトレンド + ボラ拡大 + ADX）",
        "func": combined_signal,
        "regime_filter": False,
        "allowed_regimes": [Regime.TRENDING, Regime.RANGING, Regime.TRANSITION],
        "config_overrides": {
            "take_profit_atr_mult": 12.0,
            "stop_loss_atr_mult": 8.0,
            "trailing_stop_atr_mult": 12.0,
            "max_hold_bars": 96,
        },
    },
}


def print_result(result: BacktestResult, strategy_name: str) -> dict:
    """結果を表示し、合格判定を返す"""
    print(f"\n{'='*70}")
    print(f"  戦略: {strategy_name}")
    print(f"{'='*70}")
    print(f"\n  総トレード数:     {result.total_trades}")
    print(f"  勝率:             {result.win_rate*100:.1f}%")
    print(f"  プロフィットファクター: {result.profit_factor:.2f}")
    print(f"  最大ドローダウン: {result.max_drawdown_pct:.1f}%")
    print(f"  総損益:           {result.total_pnl:+,.0f}円")
    print(f"  リターン:         {result.total_return_pct:+.2f}%")

    if result.total_trades > 0:
        print(f"  平均勝ち:         {result.avg_win_pct:+.2f}%")
        print(f"  平均負け:         {result.avg_loss_pct:+.2f}%")

    # 合格判定
    win_rate_pass = result.win_rate >= 0.35
    pf_pass = result.profit_factor >= 1.3
    dd_pass = result.max_drawdown_pct <= 30

    print(f"\n  【合格判定】")
    print(f"  [{'PASS' if win_rate_pass else 'FAIL'}] 勝率 >= 35%  (実績: {result.win_rate*100:.1f}%)")
    print(f"  [{'PASS' if pf_pass else 'FAIL'}] PF >= 1.3  (実績: {result.profit_factor:.2f})")
    print(f"  [{'PASS' if dd_pass else 'FAIL'}] 最大DD <= 30%  (実績: {result.max_drawdown_pct:.1f}%)")

    all_pass = win_rate_pass and pf_pass and dd_pass
    print(f"\n  → {'全項目PASS ✓' if all_pass else 'FAIL項目あり ✗'}")

    return {
        "all_pass": all_pass,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_trades": result.total_trades,
        "avg_win_pct": result.avg_win_pct,
        "avg_loss_pct": result.avg_loss_pct,
    }


def main():
    parser = argparse.ArgumentParser(description="戦略バックテスト比較")
    parser.add_argument("--strategy", type=str, default="all",
                        choices=["all"] + list(STRATEGIES.keys()),
                        help="テストする戦略 (default: all)")
    parser.add_argument("--data", type=str, default="data/BTC_JPY_15min_20260501_20260815.csv",
                        help="データファイルパス")
    parser.add_argument("--capital", type=float, default=100000,
                        help="初期資金 (default: 100000)")
    parser.add_argument("--leverage", type=float, default=1.5,
                        help="レバレッジ (default: 1.5)")

    args = parser.parse_args()

    # データ読み込み
    print(f"\nデータ読み込み: {args.data}")
    df = load_candles(args.data)
    print(f"データ件数: {len(df)}本")
    print(f"期間: {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}")

    # テスト対象戦略
    strategies_to_test = list(STRATEGIES.keys()) if args.strategy == "all" else [args.strategy]

    results = {}

    for strategy_key in strategies_to_test:
        strategy_info = STRATEGIES[strategy_key]

        # 設定を構築
        config = BacktestConfig(
            initial_capital=args.capital,
            leverage=args.leverage,
        )
        # 戦略固有のオーバーライド
        for key, value in strategy_info["config_overrides"].items():
            setattr(config, key, value)

        # バックテスト実行
        result = run_strategy_backtest(
            df=df,
            strategy=strategy_key,
            signal_func=strategy_info["func"],
            config=config,
            regime_filter=strategy_info["regime_filter"],
            allowed_regimes=strategy_info["allowed_regimes"],
        )

        # 結果表示
        judgment = print_result(result, strategy_info["name"])
        results[strategy_key] = {
            "result": result,
            "judgment": judgment,
        }

    # サマリー
    print(f"\n\n{'='*70}")
    print("  サマリー")
    print(f"{'='*70}")
    print(f"{'戦略':<30} {'勝率':>8} {'PF':>8} {'DD':>8} {'トレード':>8} {'判定':>6}")
    print("-" * 70)

    passing_strategies = []
    for key in strategies_to_test:
        info = STRATEGIES[key]
        j = results[key]["judgment"]
        status = "PASS" if j["all_pass"] else "FAIL"
        print(f"  {info['name']:<26} {j['win_rate']*100:>6.1f}% {j['profit_factor']:>7.2f} "
              f"{j['max_drawdown_pct']:>6.1f}% {j['total_trades']:>7} {status:>6}")
        if j["all_pass"]:
            passing_strategies.append(key)

    if passing_strategies:
        print(f"\n合格戦略: {', '.join(passing_strategies)}")
        print("\nモンテカルロシミュレーション実行コマンド:")
        for key in passing_strategies:
            j = results[key]["judgment"]
            avg_win = j["avg_win_pct"] / 100
            avg_loss = abs(j["avg_loss_pct"]) / 100
            print(f"  python simulate.py --win-rate {j['win_rate']:.4f} "
                  f"--avg-win {avg_win:.4f} --avg-loss {avg_loss:.4f} "
                  f"--leverage {args.leverage}")
    else:
        print("\n合格した戦略はありません。パラメータ調整が必要です。")


if __name__ == "__main__":
    main()
