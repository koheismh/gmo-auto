"""
テクニカルインジケーター計算

pandas DataFrameに対してインジケーターを追加する関数群。
外部ライブラリ(TA-Lib等)に依存せず、numpy/pandasのみで実装。
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """単純移動平均"""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移動平均"""
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (ATR)

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = TRの移動平均
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (RSI)
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    ボリンジャーバンド

    Returns:
        (middle, upper, lower)
    """
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower


def bollinger_band_width(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> pd.Series:
    """ボリンジャーバンド幅 (upper - lower) / middle"""
    middle, upper, lower = bollinger_bands(series, period, std_dev)
    return (upper - lower) / middle


def donchian_channel(
    df: pd.DataFrame, period: int = 20
) -> tuple[pd.Series, pd.Series]:
    """
    ドンチャンチャネル

    Returns:
        (upper, lower) = (期間内最高値, 期間内最安値)
    """
    upper = df["high"].rolling(window=period).max()
    lower = df["low"].rolling(window=period).min()
    return upper, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index (ADX)

    トレンドの強さを0-100で表す
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # +DM, -DM
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Smoothed TR, +DM, -DM (Wilder's smoothing)
    atr_val = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)

    # DX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)

    # ADX = DXのSmoothed
    adx_val = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return adx_val


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """出来高の単純移動平均"""
    return sma(df["volume"], period)


def add_all_indicators(df: pd.DataFrame, config: dict = None) -> pd.DataFrame:
    """
    全インジケーターをDataFrameに追加する

    Args:
        df: OHLCV DataFrame (timestamp, open, high, low, close, volume)
        config: パラメータ設定 (Noneならデフォルト値使用)

    Returns:
        インジケーター列が追加されたDataFrame
    """
    if config is None:
        config = {}

    c = df["close"]

    # ADX
    adx_period = config.get("adx_period", 14)
    df["adx"] = adx(df, period=adx_period)

    # Bollinger Bands
    bb_period = config.get("bb_period", 20)
    bb_std = config.get("bb_std", 2)
    df["bb_middle"], df["bb_upper"], df["bb_lower"] = bollinger_bands(c, bb_period, bb_std)
    df["bb_width"] = bollinger_band_width(c, bb_period, bb_std)

    # Donchian Channel
    dc_period = config.get("donchian_period", 20)
    df["dc_upper"], df["dc_lower"] = donchian_channel(df, dc_period)

    # EMA
    ema_period = config.get("ema_period", 20)
    df["ema"] = ema(c, ema_period)

    # RSI
    rsi_period = config.get("rsi_period", 14)
    df["rsi"] = rsi(c, rsi_period)

    # ATR
    atr_period = config.get("atr_period", 14)
    df["atr"] = atr(df, atr_period)

    # Volume SMA
    vol_period = config.get("volume_sma_period", 20)
    df["volume_sma"] = volume_sma(df, vol_period)

    return df
