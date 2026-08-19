"""
相場状態判定 (Regime Detection)

ADXとボリンジャーバンド幅から、現在の相場がトレンド/レンジ/過渡期のいずれかを判定する。
"""

from enum import Enum

import pandas as pd


class Regime(Enum):
    TRENDING = "trending"      # トレンド相場 → ブレイクアウト戦略
    RANGING = "ranging"        # レンジ相場 → グリッド戦略
    TRANSITION = "transition"  # 過渡期 → 待機/ポジション縮小


def detect_regime(
    df: pd.DataFrame,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
    bb_slope_period: int = 5,
) -> Regime:
    """
    最新のローソク足データから相場状態を判定する

    Args:
        df: インジケーター追加済みのDataFrame（最低でも直近数十本必要）
        adx_trend_threshold: ADXがこの値を超えたらトレンド
        adx_range_threshold: ADXがこの値を下回ったらレンジ
        bb_slope_period: BB幅の傾き判定に使う期間

    Returns:
        Regime enum
    """
    if len(df) < bb_slope_period + 1:
        return Regime.TRANSITION

    current_adx = df["adx"].iloc[-1]
    bb_widths = df["bb_width"].iloc[-bb_slope_period:]

    # BB幅の傾き（正なら拡大、負なら収縮）
    bb_slope = bb_widths.iloc[-1] - bb_widths.iloc[0]

    if current_adx > adx_trend_threshold and bb_slope > 0:
        return Regime.TRENDING
    elif current_adx < adx_range_threshold and bb_slope <= 0:
        return Regime.RANGING
    else:
        return Regime.TRANSITION


def detect_regime_series(
    df: pd.DataFrame,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
    bb_slope_period: int = 5,
) -> pd.Series:
    """
    DataFrame全体に対して各行の相場状態を判定する（バックテスト用）

    Returns:
        Regime値のSeries
    """
    regimes = []

    for i in range(len(df)):
        if i < bb_slope_period:
            regimes.append(Regime.TRANSITION)
            continue

        current_adx = df["adx"].iloc[i]
        bb_widths = df["bb_width"].iloc[max(0, i - bb_slope_period + 1):i + 1]

        if len(bb_widths) < 2:
            regimes.append(Regime.TRANSITION)
            continue

        bb_slope = bb_widths.iloc[-1] - bb_widths.iloc[0]

        if pd.isna(current_adx) or pd.isna(bb_slope):
            regimes.append(Regime.TRANSITION)
        elif current_adx > adx_trend_threshold and bb_slope > 0:
            regimes.append(Regime.TRENDING)
        elif current_adx < adx_range_threshold and bb_slope <= 0:
            regimes.append(Regime.RANGING)
        else:
            regimes.append(Regime.TRANSITION)

    return pd.Series(regimes, index=df.index)
