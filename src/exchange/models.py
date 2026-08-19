"""
取引データモデル
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(Enum):
    WAITING = "WAITING"
    ORDERED = "ORDERED"
    MODIFYING = "MODIFYING"
    CANCELLING = "CANCELLING"
    CANCELED = "CANCELED"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"


class PositionSide(Enum):
    LONG = "BUY"
    SHORT = "SELL"


@dataclass
class Ticker:
    """ティッカー情報"""
    symbol: str
    ask: float
    bid: float
    last: float
    high: float
    low: float
    volume: float
    timestamp: datetime


@dataclass
class Position:
    """建玉情報"""
    position_id: int
    symbol: str
    side: PositionSide
    size: float
    price: float  # 平均建玉レート
    losscut_price: float
    timestamp: datetime
    unrealized_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT


@dataclass
class Order:
    """注文情報"""
    order_id: int
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.WAITING
    timestamp: Optional[datetime] = None


@dataclass
class AccountMargin:
    """余力情報"""
    actual_profit_loss: float  # 実現損益
    available_amount: float  # 取引余力
    margin: float  # 拘束証拠金
    margin_ratio: float  # 証拠金維持率
    profit_loss_ratio: float  # 評価損益率


@dataclass
class Asset:
    """資産残高"""
    amount: float  # 残高
    available: float  # 利用可能額
    conversion_rate: float = 1.0
