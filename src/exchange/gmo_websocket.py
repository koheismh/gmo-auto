"""
GMOコイン WebSocket接続

リアルタイムの価格データ（ティッカー、板情報、約定）をWebSocketで受信する。
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import websocket

logger = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api.coin.z.com/ws/public/v1"


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

    def connect(self, symbols: list[str] = None):
        """WebSocket接続を開始"""
        if symbols is None:
            symbols = ["BTC_JPY"]

        self._subscribed_symbols = symbols
        self._running = True

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
        self.latest_ticker[symbol] = {
            "ask": float(data.get("ask", 0)),
            "bid": float(data.get("bid", 0)),
            "last": float(data.get("last", 0)),
            "high": float(data.get("high", 0)),
            "low": float(data.get("low", 0)),
            "volume": float(data.get("volume", 0)),
            "timestamp": data.get("timestamp", ""),
            "received_at": datetime.now(),
        }

        if self.on_ticker_callback:
            self.on_ticker_callback(self.latest_ticker[symbol])

    def _handle_trade(self, data: dict):
        """約定データ処理"""
        symbol = data.get("symbol", "")
        self.latest_trade[symbol] = {
            "price": float(data.get("price", 0)),
            "side": data.get("side", ""),
            "size": float(data.get("size", 0)),
            "timestamp": data.get("timestamp", ""),
            "received_at": datetime.now(),
        }

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
