"""
GMOコイン REST APIクライアント

Private APIの認証付きHTTPクライアント。
注文、ポジション管理、残高確認等の取引操作を行う。
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Optional

import requests

from .models import (
    AccountMargin,
    Asset,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Ticker,
)

logger = logging.getLogger(__name__)

PUBLIC_ENDPOINT = "https://api.coin.z.com/public"
PRIVATE_ENDPOINT = "https://api.coin.z.com/private"


class GMOCoinClient:
    """GMOコイン REST APIクライアント"""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ===== Private API認証 =====

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """HMAC-SHA256署名を生成"""
        text = timestamp + method + path + body
        sign = hmac.new(
            key=self.api_secret.encode("utf-8"),
            msg=text.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return sign

    def _private_headers(self, method: str, path: str, body: str = "") -> dict:
        """Private API用ヘッダーを生成"""
        timestamp = str(int(time.time() * 1000))
        sign = self._sign(timestamp, method, path, body)
        return {
            "API-KEY": self.api_key,
            "API-TIMESTAMP": timestamp,
            "API-SIGN": sign,
        }

    def _get_private(self, path: str) -> dict:
        """Private API GETリクエスト"""
        headers = self._private_headers("GET", path)
        url = PRIVATE_ENDPOINT + path
        response = self.session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != 0:
            logger.error(f"API error: {path} -> {data.get('messages')}")
            raise APIError(data.get("messages", [{}]))
        return data.get("data", {})

    def _post_private(self, path: str, body: dict) -> dict:
        """Private API POSTリクエスト"""
        body_str = json.dumps(body)
        headers = self._private_headers("POST", path, body_str)
        url = PRIVATE_ENDPOINT + path
        response = self.session.post(url, data=body_str, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != 0:
            logger.error(f"API error: {path} -> {data.get('messages')}")
            raise APIError(data.get("messages", [{}]))
        return data.get("data", {})

    # ===== Public API =====

    def get_status(self) -> dict:
        """取引所ステータスを取得"""
        url = PUBLIC_ENDPOINT + "/v1/status"
        response = self.session.get(url, timeout=10)
        return response.json().get("data", {})

    def get_ticker(self, symbol: str = "BTC_JPY") -> Ticker:
        """ティッカーを取得"""
        url = PUBLIC_ENDPOINT + f"/v1/ticker?symbol={symbol}"
        response = self.session.get(url, timeout=10)
        data = response.json().get("data", [])

        if not data:
            raise APIError(f"No ticker data for {symbol}")

        # 複数銘柄が返る可能性があるのでフィルター
        ticker_data = None
        for d in data:
            if d.get("symbol") == symbol:
                ticker_data = d
                break
        if ticker_data is None:
            ticker_data = data[0]

        return Ticker(
            symbol=ticker_data["symbol"],
            ask=float(ticker_data["ask"]),
            bid=float(ticker_data["bid"]),
            last=float(ticker_data["last"]),
            high=float(ticker_data["high"]),
            low=float(ticker_data["low"]),
            volume=float(ticker_data["volume"]),
            timestamp=datetime.now(),
        )

    def get_symbols(self) -> list:
        """取引ルールを取得"""
        url = PUBLIC_ENDPOINT + "/v1/symbols"
        response = self.session.get(url, timeout=10)
        return response.json().get("data", [])

    # ===== Private API: 口座情報 =====

    def get_margin(self) -> AccountMargin:
        """余力情報を取得"""
        data = self._get_private("/v1/account/margin")
        return AccountMargin(
            actual_profit_loss=float(data.get("actualProfitLoss", 0)),
            available_amount=float(data.get("availableAmount", 0)),
            margin=float(data.get("margin", 0)),
            margin_ratio=float(data.get("marginRatio", "0") or "0"),
            profit_loss_ratio=float(data.get("profitLoss", 0) or 0),
        )

    def get_assets(self) -> list[Asset]:
        """資産残高を取得"""
        data = self._get_private("/v1/account/assets")
        assets = []
        for item in data:
            assets.append(Asset(
                amount=float(item.get("amount", 0)),
                available=float(item.get("available", 0)),
                conversion_rate=float(item.get("conversionRate", 1)),
            ))
        return assets

    # ===== Private API: 注文 =====

    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        size: str,
    ) -> str:
        """
        成行注文を出す

        Args:
            symbol: "BTC_JPY"
            side: OrderSide.BUY or OrderSide.SELL
            size: 注文数量（文字列）

        Returns:
            注文ID
        """
        body = {
            "symbol": symbol,
            "side": side.value,
            "executionType": "MARKET",
            "size": size,
        }
        data = self._post_private("/v1/order", body)
        order_id = data
        logger.info(f"Market order placed: {side.value} {size} {symbol} -> ID: {order_id}")
        return str(order_id)

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        size: str,
        price: str,
    ) -> str:
        """指値注文を出す"""
        body = {
            "symbol": symbol,
            "side": side.value,
            "executionType": "LIMIT",
            "price": price,
            "size": size,
        }
        data = self._post_private("/v1/order", body)
        logger.info(f"Limit order placed: {side.value} {size} {symbol} @ {price} -> ID: {data}")
        return str(data)

    def place_stop_order(
        self,
        symbol: str,
        side: OrderSide,
        size: str,
        price: str,
    ) -> str:
        """逆指値注文を出す"""
        body = {
            "symbol": symbol,
            "side": side.value,
            "executionType": "STOP",
            "losscutPrice": price,
            "size": size,
        }
        data = self._post_private("/v1/order", body)
        logger.info(f"Stop order placed: {side.value} {size} {symbol} @ {price} -> ID: {data}")
        return str(data)

    def cancel_order(self, order_id: str) -> None:
        """注文をキャンセル"""
        body = {"orderId": int(order_id)}
        self._post_private("/v1/cancelOrder", body)
        logger.info(f"Order cancelled: {order_id}")

    def close_order(
        self,
        symbol: str,
        side: OrderSide,
        size: str,
        position_id: int,
        execution_type: str = "MARKET",
        price: Optional[str] = None,
    ) -> str:
        """決済注文を出す"""
        body = {
            "symbol": symbol,
            "side": side.value,
            "executionType": execution_type,
            "settlePosition": [{"positionId": position_id, "size": size}],
        }
        if price and execution_type != "MARKET":
            body["price"] = price

        data = self._post_private("/v1/closeOrder", body)
        logger.info(f"Close order: {side.value} {size} {symbol} posID={position_id}")
        return str(data)

    def close_bulk_order(
        self,
        symbol: str,
        side: OrderSide,
        size: str,
        execution_type: str = "MARKET",
    ) -> str:
        """一括決済注文"""
        body = {
            "symbol": symbol,
            "side": side.value,
            "executionType": execution_type,
            "size": size,
        }
        data = self._post_private("/v1/closeBulkOrder", body)
        logger.info(f"Bulk close order: {side.value} {size} {symbol}")
        return str(data)

    # ===== Private API: ポジション・注文照会 =====

    def get_open_positions(self, symbol: str = "BTC_JPY") -> list[Position]:
        """建玉一覧を取得"""
        data = self._get_private(f"/v1/openPositions?symbol={symbol}")
        positions = []

        if isinstance(data, dict):
            items = data.get("list", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for item in items:
            positions.append(Position(
                position_id=int(item["positionId"]),
                symbol=item["symbol"],
                side=PositionSide(item["side"]),
                size=float(item["size"]),
                price=float(item["price"]),
                losscut_price=float(item.get("losscutPrice", 0)),
                timestamp=datetime.now(),
            ))
        return positions

    def get_active_orders(self, symbol: str = "BTC_JPY") -> list[Order]:
        """有効注文一覧を取得"""
        data = self._get_private(f"/v1/activeOrders?symbol={symbol}")
        orders = []

        if isinstance(data, dict):
            items = data.get("list", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for item in items:
            orders.append(Order(
                order_id=int(item["orderId"]),
                symbol=item["symbol"],
                side=OrderSide(item["side"]),
                order_type=OrderType(item.get("executionType", "MARKET")),
                size=float(item["size"]),
                price=float(item.get("price", 0)) if item.get("price") else None,
                status=OrderStatus(item.get("status", "ORDERED")),
            ))
        return orders

    def get_latest_executions(self, symbol: str = "BTC_JPY", count: int = 10) -> list:
        """最新約定一覧を取得"""
        data = self._get_private(f"/v1/latestExecutions?symbol={symbol}&count={count}")
        if isinstance(data, dict):
            return data.get("list", [])
        return data if isinstance(data, list) else []


class APIError(Exception):
    """GMOコインAPIエラー"""
    def __init__(self, messages):
        if isinstance(messages, list) and messages:
            msg = messages[0].get("message_string", str(messages))
        else:
            msg = str(messages)
        super().__init__(msg)
        self.messages = messages
