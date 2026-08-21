"""
GMOコイン WebSocket接続

リアルタイムの価格データ（ティッカー、板情報、約定）をWebSocketで受信する。
tickデータから15分足OHLCVを自前で構築する機能を含む。
"""

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd
import websocket

logger = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api.coin.z.com/ws/public/v1"


def _floor_to_15min(dt: datetime) -> datetime:
    """datetimeを15分単位に切り捨て"""
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


class CandleBuilder:
    """
    tickデータから15分足OHLCVを構築するクラス

    各15分区間（00-14, 15-29, 30-44, 45-59）ごとにOHLCVを蓄積し、
    区間が確定したタイミングで確定足として保持する。
    """

    def __init__(self, max_candles: int = 50):
        """
        Args:
            max_candles: 保持する確定足の最大本数
        """
        self.max_candles = max_candles

        # 確定済み足のリスト（古い順）
        self._confirmed_candles: list[dict] = []

        # 現在構築中の足
        self._current_period: Optional[datetime] = None
        self._current_open: Optional[float] = None
        self._current_high: Optional[float] = None
        self._current_low: Optional[float] = None
        self._current_close: Optional[float] = None
        self._current_volume: float = 0.0
        self._tick_count: int = 0

        self._lock = threading.Lock()

    def on_tick(self, price: float, volume: float = 0.0, timestamp: Optional[datetime] = None):
        """
        tick（約定 or ティッカー更新）を受信して15分足に反映する

        Args:
            price: 約定価格 or 最終取引価格
            volume: 約定数量（ティッカーの場合は0でOK）
            timestamp: tickのタイムスタンプ（Noneなら現在時刻）
        """
        if price <= 0:
            return

        now = timestamp or datetime.now()
        period = _floor_to_15min(now)

        with self._lock:
            # 期間が変わった → 前の足を確定させる
            if self._current_period is not None and period != self._current_period:
                self._confirm_current_candle()

            # 新しい期間を開始
            if self._current_period is None or period != self._current_period:
                self._current_period = period
                self._current_open = price
                self._current_high = price
                self._current_low = price
                self._current_close = price
                self._current_volume = volume
                self._tick_count = 1
            else:
                # 同じ期間内 → OHLCVを更新
                self._current_high = max(self._current_high, price)
                self._current_low = min(self._current_low, price)
                self._current_close = price
                self._current_volume += volume
                self._tick_count += 1

    def _confirm_current_candle(self):
        """現在構築中の足を確定する"""
        if self._current_period is None or self._current_open is None:
            return

        candle = {
            "timestamp": self._current_period,
            "open": self._current_open,
            "high": self._current_high,
            "low": self._current_low,
            "close": self._current_close,
            "volume": self._current_volume,
        }
        self._confirmed_candles.append(candle)

        # 最大本数を超えたら古い足を削除
        if len(self._confirmed_candles) > self.max_candles:
            self._confirmed_candles = self._confirmed_candles[-self.max_candles:]

        logger.debug(
            f"Candle confirmed: {self._current_period} "
            f"O={self._current_open:.0f} H={self._current_high:.0f} "
            f"L={self._current_low:.0f} C={self._current_close:.0f} "
            f"V={self._current_volume:.4f} ticks={self._tick_count}"
        )

    def get_confirmed_candles(self) -> pd.DataFrame:
        """
        確定済みの15分足をDataFrameで返す

        Returns:
            DataFrame (timestamp, open, high, low, close, volume)
            確定足がない場合は空のDataFrame
        """
        with self._lock:
            if not self._confirmed_candles:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            return pd.DataFrame(self._confirmed_candles)

    def get_latest_confirmed(self) -> Optional[dict]:
        """最新の確定足を1本返す"""
        with self._lock:
            if self._confirmed_candles:
                return self._confirmed_candles[-1].copy()
            return None

    def get_building_candle(self) -> Optional[dict]:
        """現在構築中（未確定）の足を返す"""
        with self._lock:
            if self._current_period is None:
                return None
            return {
                "timestamp": self._current_period,
                "open": self._current_open,
                "high": self._current_high,
                "low": self._current_low,
                "close": self._current_close,
                "volume": self._current_volume,
                "tick_count": self._tick_count,
                "confirmed": False,
            }

    @property
    def confirmed_count(self) -> int:
        """確定足の本数"""
        with self._lock:
            return len(self._confirmed_candles)

    @property
    def has_data(self) -> bool:
        """データを受信しているか"""
        with self._lock:
            return self._tick_count > 0 or len(self._confirmed_candles) > 0


class GMOWebSocket:
    """GMOコイン Public WebSocket クライアント"""

    def __init__(self, on_ticker: Optional[Callable] = None, on_trade: Optional[Callable] = None):
        """
        Args:
            on_ticker: ティッカー受信時のコールバック fn(data: dict)
            on_trade: 約定データ受信時のコールバック fn(data: dict)
        """
        self.on_ticker_callback = on_ticker
        self.on_trade_callback = on_trade
        self.ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._subscribed_symbols: list[str] = []

        # 最新データを保持
        self.latest_ticker: dict = {}
        self.latest_trade: dict = {}

        # 15分足構築用のCandleBuilder（シンボルごと）
        self._candle_builders: dict[str, CandleBuilder] = defaultdict(
            lambda: CandleBuilder(max_candles=50)
        )

    def connect(self, symbols: list[str] = None):
        """WebSocket接続を開始"""
        if symbols is None:
            symbols = ["BTC_JPY"]

        self._subscribed_symbols = symbols
        self._running = True

        # 各シンボルのCandleBuilderを初期化
        for symbol in symbols:
            if symbol not in self._candle_builders:
                self._candle_builders[symbol] = CandleBuilder(max_candles=50)

        self.ws = websocket.WebSocketApp(
            PUBLIC_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"WebSocket connecting to {PUBLIC_WS_URL}")

    def _run(self):
        """WebSocket実行ループ（自動再接続付き）"""
        while self._running:
            try:
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info("WebSocket disconnected. Reconnecting in 5 seconds...")
                time.sleep(5)
                self.ws = websocket.WebSocketApp(
                    PUBLIC_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

    def _on_open(self, ws):
        """接続時: チャネルをsubscribe"""
        logger.info("WebSocket connected")
        for symbol in self._subscribed_symbols:
            # ティッカー購読
            msg = json.dumps({
                "command": "subscribe",
                "channel": "ticker",
                "symbol": symbol,
            })
            ws.send(msg)
            time.sleep(1)  # レート制限: 1秒1リクエスト

            # 約定履歴購読
            msg = json.dumps({
                "command": "subscribe",
                "channel": "trades",
                "symbol": symbol,
            })
            ws.send(msg)
            time.sleep(1)

        logger.info(f"Subscribed to: {self._subscribed_symbols}")

    def _on_message(self, ws, message):
        """メッセージ受信"""
        try:
            data = json.loads(message)
            channel = data.get("channel", "")

            if channel == "ticker":
                self._handle_ticker(data)
            elif channel == "trades":
                self._handle_trade(data)

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON: {e}")
        except Exception as e:
            logger.error(f"Message handling error: {e}")

    def _handle_ticker(self, data: dict):
        """ティッカーデータ処理"""
        symbol = data.get("symbol", "")
        last_price = float(data.get("last", 0))
        volume = float(data.get("volume", 0))

        self.latest_ticker[symbol] = {
            "ask": float(data.get("ask", 0)),
            "bid": float(data.get("bid", 0)),
            "last": last_price,
            "high": float(data.get("high", 0)),
            "low": float(data.get("low", 0)),
            "volume": volume,
            "timestamp": data.get("timestamp", ""),
            "received_at": datetime.now(),
        }

        # CandleBuilderに反映（tickerのlast価格を使用）
        if last_price > 0:
            self._candle_builders[symbol].on_tick(price=last_price, volume=0.0)

        if self.on_ticker_callback:
            self.on_ticker_callback(self.latest_ticker[symbol])

    def _handle_trade(self, data: dict):
        """約定データ処理"""
        symbol = data.get("symbol", "")
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))

        self.latest_trade[symbol] = {
            "price": price,
            "side": data.get("side", ""),
            "size": size,
            "timestamp": data.get("timestamp", ""),
            "received_at": datetime.now(),
        }

        # CandleBuilderに反映（約定価格と数量を使用）
        if price > 0:
            self._candle_builders[symbol].on_tick(price=price, volume=size)

        if self.on_trade_callback:
            self.on_trade_callback(self.latest_trade[symbol])

    def _on_error(self, ws, error):
        """エラー発生"""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """切断"""
        logger.info(f"WebSocket closed: {close_status_code} {close_msg}")

    def get_last_price(self, symbol: str = "BTC_JPY") -> Optional[float]:
        """最新価格を取得"""
        ticker = self.latest_ticker.get(symbol)
        if ticker:
            return ticker["last"]
        return None

    def get_spread(self, symbol: str = "BTC_JPY") -> Optional[float]:
        """スプレッドを取得"""
        ticker = self.latest_ticker.get(symbol)
        if ticker:
            return ticker["ask"] - ticker["bid"]
        return None

    def get_candle_builder(self, symbol: str = "BTC_JPY") -> CandleBuilder:
        """指定シンボルのCandleBuilderを取得"""
        return self._candle_builders[symbol]

    def get_realtime_candles(self, symbol: str = "BTC_JPY") -> pd.DataFrame:
        """
        WebSocketから構築した確定済み15分足をDataFrameで取得

        Args:
            symbol: 取引ペア

        Returns:
            DataFrame (timestamp, open, high, low, close, volume)
        """
        return self._candle_builders[symbol].get_confirmed_candles()

    def disconnect(self):
        """WebSocket切断"""
        self._running = False
        if self.ws:
            self.ws.close()
        logger.info("WebSocket disconnected")

    @property
    def is_connected(self) -> bool:
        """接続状態"""
        return self._running and self.ws is not None
