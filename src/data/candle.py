"""
GMOコイン Public APIからローソク足データを取得・保存する

使用API:
  GET https://api.coin.z.com/public/v1/klines?symbol=BTC_JPY&interval=15min&date=20240101

interval: 1min, 5min, 10min, 15min, 30min, 1hour, 4hour, 8hour, 12hour, 1day, 1week, 1month
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import pandas as pd


BASE_URL = "https://api.coin.z.com/public"


def fetch_klines(
    symbol: str = "BTC_JPY",
    interval: str = "15min",
    date: str = "20240101",
) -> Optional[pd.DataFrame]:
    """
    GMOコインからローソク足データを1日分取得する

    Args:
        symbol: 取引ペア (BTC_JPY, ETH_JPY等)
        interval: 時間足 (1min, 5min, 15min, 30min, 1hour, 4hour, 1day等)
        date: 取得日 (YYYYMMDD形式)

    Returns:
        DataFrame or None (エラー時)
    """
    path = f"/v1/klines?symbol={symbol}&interval={interval}&date={date}"
    url = BASE_URL + path

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            print(f"  API error for {date}: {data.get('messages', 'unknown')}")
            return None

        klines = data.get("data", [])
        if not klines:
            return None

        df = pd.DataFrame(klines)
        # カラム名を統一
        df = df.rename(columns={
            "openTime": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        })

        # 型変換
        df["timestamp"] = pd.to_datetime(
            df["timestamp"].astype(int), unit="ms", utc=True
        ).dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    except requests.exceptions.RequestException as e:
        print(f"  Request error for {date}: {e}")
        return None


def fetch_klines_range(
    symbol: str = "BTC_JPY",
    interval: str = "15min",
    start_date: str = "20240101",
    end_date: str = "20240331",
    sleep_sec: float = 0.5,
) -> pd.DataFrame:
    """
    指定期間のローソク足データを取得する（日単位でループ）

    Args:
        symbol: 取引ペア
        interval: 時間足
        start_date: 開始日 (YYYYMMDD)
        end_date: 終了日 (YYYYMMDD)
        sleep_sec: APIコール間のスリープ秒数

    Returns:
        結合されたDataFrame
    """
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")

    all_dfs = []
    current = start
    total_days = (end - start).days + 1
    fetched = 0

    print(f"データ取得開始: {symbol} {interval} ({start_date} - {end_date})")

    while current <= end:
        date_str = current.strftime("%Y%m%d")
        df = fetch_klines(symbol=symbol, interval=interval, date=date_str)

        if df is not None and len(df) > 0:
            all_dfs.append(df)
            fetched += len(df)

        current += timedelta(days=1)
        time.sleep(sleep_sec)

        # 進捗表示（10日ごと）
        days_done = (current - start).days
        if days_done % 10 == 0:
            print(f"  進捗: {days_done}/{total_days}日 ({fetched}本取得)")

    if not all_dfs:
        print("データが取得できませんでした")
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.sort_values("timestamp").reset_index(drop=True)

    print(f"取得完了: {len(result)}本 ({result['timestamp'].iloc[0]} ~ {result['timestamp'].iloc[-1]})")
    return result


def save_candles(df: pd.DataFrame, filepath: str) -> None:
    """ローソク足データをCSVに保存"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(filepath, index=False)
    print(f"保存完了: {filepath} ({len(df)}行)")


def load_candles(filepath: str) -> pd.DataFrame:
    """保存済みローソク足データを読み込む"""
    df = pd.read_csv(filepath, parse_dates=["timestamp"])
    return df


def get_or_fetch_candles(
    symbol: str = "BTC_JPY",
    interval: str = "15min",
    start_date: str = "20240101",
    end_date: str = "20240331",
    data_dir: str = "data",
) -> pd.DataFrame:
    """
    キャッシュがあれば読み込み、なければAPIから取得して保存する

    Args:
        symbol: 取引ペア
        interval: 時間足
        start_date: 開始日
        end_date: 終了日
        data_dir: データ保存ディレクトリ

    Returns:
        DataFrame
    """
    filename = f"{symbol}_{interval}_{start_date}_{end_date}.csv"
    filepath = str(Path(data_dir) / filename)

    if Path(filepath).exists():
        print(f"キャッシュ読み込み: {filepath}")
        return load_candles(filepath)

    df = fetch_klines_range(
        symbol=symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    )

    if len(df) > 0:
        save_candles(df, filepath)

    return df
