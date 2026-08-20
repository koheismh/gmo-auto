"""
メインエンジン

全コンポーネントを統合し、メインループを制御する。
WebSocketでリアルタイム価格を受信しながら、15分足確定タイミングで戦略判定を行い、
リスク管理を経て注文を実行する。
"""

import logging
import signal
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from src.data.candle import fetch_klines
from src.exchange.gmo_client import GMOCoinClient, APIError
from src.exchange.gmo_websocket import GMOWebSocket
from src.exchange.models import OrderSide, PositionSide
from src.notify.sns import SNSNotifier
from src.risk.manager import RiskManager, RiskConfig
from src.strategy.indicators import add_all_indicators, ema
from src.strategy.regime import Regime, detect_regime

logger = logging.getLogger(__name__)


class TradingEngine:
    """自動売買エンジン"""

    def __init__(self, config: dict):
        """
        Args:
            config: settings.yamlから読み込んだ設定dict
        """
        self.config = config
        self._running = False

        # 取引所クライアント
        exchange_cfg = config["exchange"]
        self.client = GMOCoinClient(
            api_key=exchange_cfg["api_key"],
            api_secret=exchange_cfg["api_secret"],
        )

        # WebSocket
        self.ws = GMOWebSocket(on_ticker=self._on_ticker)

        # リスク管理
        risk_cfg = config.get("risk", {})
        capital_cfg = config.get("capital", {})
        self.risk_manager = RiskManager(
            config=RiskConfig(
                max_risk_per_trade=risk_cfg.get("max_risk_per_trade", 0.02),
                daily_loss_limit=risk_cfg.get("daily_loss_limit", 0.05),
                consecutive_loss_cooldown=risk_cfg.get("consecutive_loss_cooldown", 3),
                cooldown_minutes=risk_cfg.get("cooldown_minutes", 60),
                max_positions=risk_cfg.get("max_positions", 2),
                min_leverage=risk_cfg.get("min_leverage", 1.2),
                max_leverage=risk_cfg.get("max_leverage", 2.0),
                bankruptcy_threshold=risk_cfg.get("bankruptcy_threshold", 30000),
                profit_take_threshold=capital_cfg.get("profit_take_threshold", 200000),
                profit_take_notify=capital_cfg.get("profit_take_notify", 150000),
            ),
            initial_capital=capital_cfg.get("initial", 100000),
        )

        # 通知
        notify_cfg = config.get("notification", {})
        self.notifier = SNSNotifier(
            topic_arn=notify_cfg.get("sns_topic_arn", ""),
            dry_run=notify_cfg.get("dry_run", True),
        )

        # 戦略パラメータ
        self.strategy_config = config.get("strategy", {})
        self.symbols = exchange_cfg.get("symbols", ["BTC_JPY"])

        # 状態管理
        self._candle_buffer: dict[str, pd.DataFrame] = {}
        self._last_candle_time: dict[str, Optional[datetime]] = {}
        self._position_entry_info: dict = {}  # symbol -> {price, stop, take_profit, time, side}
        self._daily_trades: list = []
        self._daily_summary_sent: bool = False
        self._last_check_time: Optional[datetime] = None

    def start(self):
        """エンジン起動"""
        logger.info("=" * 50)
        logger.info("Trading Engine Starting")
        logger.info("=" * 50)

        self._running = True

        # シグナルハンドラー設定
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 初期データ読み込み
        self._load_initial_candles()

        # WebSocket接続
        self.ws.connect(symbols=self.symbols)
        time.sleep(3)  # 接続待ち

        # 初期残高取得
        self._update_capital()

        logger.info(f"Engine started. Symbols: {self.symbols}")
        logger.info(f"Initial capital: {self.risk_manager.state.total_capital:,.0f}円")

        # メインループ
        self._main_loop()

    def _main_loop(self):
        """メインループ"""
        check_interval = 5  # 秒

        while self._running:
            try:
                now = datetime.now()

                # 15分足確定チェック（00, 15, 30, 45分のタイミング）
                if self._is_candle_closed(now):
                    self._on_candle_close(now)

                # ポジション監視（常時）
                self._check_positions()

                # 日次サマリー（毎日9:00 JST）
                summary_hour = self.config.get("notification", {}).get("daily_summary_hour", 9)
                if now.hour == summary_hour and now.minute == 0 and not self._daily_summary_sent:
                    self._send_daily_summary()
                    self._daily_summary_sent = True
                elif now.hour != summary_hour:
                    self._daily_summary_sent = False

                # 資金アラート
                alerts = self.risk_manager.check_capital_alerts()
                for alert in alerts:
                    self.notifier.notify_alert(alert)
                    if "破産" in alert:
                        self._emergency_close_all()
                        self.notifier.notify_bankruptcy(
                            self.risk_manager.state.total_capital,
                            self.risk_manager.config.bankruptcy_threshold,
                        )
                        self._running = False
                        break

                self._last_check_time = now
                time.sleep(check_interval)

            except APIError as e:
                logger.error(f"API Error in main loop: {e}")
                self.notifier.notify_error(f"API Error: {e}")
                time.sleep(30)

            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                self.notifier.notify_error(f"Unexpected Error: {e}")
                time.sleep(60)

        logger.info("Trading Engine stopped")
        self.ws.disconnect()

    def _on_candle_close(self, now: datetime):
        """15分足確定時の処理"""
        for symbol in self.symbols:
            try:
                # 最新ローソク足データ取得
                self._update_candles(symbol)

                df = self._candle_buffer.get(symbol)
                if df is None or len(df) < 50:
                    continue

                # インジケーター計算（基本インジケーター + EMA短期/長期）
                combined_cfg = self.strategy_config.get("combined", {})
                indicator_config = {
                    "adx_period": combined_cfg.get("adx_period", 14),
                    "bb_period": combined_cfg.get("bb_period", 20),
                    "bb_std": combined_cfg.get("bb_std", 2),
                    "donchian_period": combined_cfg.get("donchian_period", 20),
                    "ema_period": combined_cfg.get("ema_period", 20),
                    "rsi_period": combined_cfg.get("rsi_period", 14),
                    "atr_period": combined_cfg.get("atr_period", 14),
                    "volume_sma_period": 20,
                }
                df = add_all_indicators(df, indicator_config)

                # EMA短期(9)/長期(26) を追加（組み合わせ戦略用）
                ema_short_period = combined_cfg.get("ema_short_period", 9)
                ema_long_period = combined_cfg.get("ema_long_period", 26)
                df["ema_short"] = ema(df["close"], ema_short_period)
                df["ema_long"] = ema(df["close"], ema_long_period)

                self._candle_buffer[symbol] = df

                # 相場状態判定（ログ用）
                regime_cfg = self.strategy_config.get("regime", {})
                regime = detect_regime(
                    df,
                    adx_trend_threshold=regime_cfg.get("adx_trend_threshold", 25),
                    adx_range_threshold=regime_cfg.get("adx_range_threshold", 20),
                    bb_slope_period=regime_cfg.get("bb_slope_period", 5),
                )

                logger.info(f"[{symbol}] Regime: {regime.value}, ADX: {df['adx'].iloc[-1]:.1f}")

                # ポジションがなければエントリー判定（組み合わせ戦略: 相場状態フィルターなし）
                if symbol not in self._position_entry_info:
                    self._check_entry_signal(symbol, df)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}", exc_info=True)

    def _check_entry_signal(self, symbol: str, df: pd.DataFrame):
        """
        エントリーシグナルチェック - 組み合わせ戦略（EMAトレンド + ボラ拡大 + ADX）

        エントリー条件（全て満たす）:
        1. EMA短期(9) > 長期(26) ならロング方向、逆ならショート方向
        2. BB幅が拡大開始（前バーより拡大）
        3. ADX > 18 かつ上昇中
        4. 価格がEMA長期の方向と一致する動き
        5. RSI: 35-65の中間帯（まだ伸びしろがある）
        """
        # リスクチェック
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            logger.info(f"[{symbol}] Trade blocked: {reason}")
            return

        if len(df) < 5:
            return

        row = df.iloc[-1]
        prev_row = df.iloc[-2]
        combined_cfg = self.strategy_config.get("combined", {})

        # 必要な値を取得
        ema_short = row.get("ema_short")
        ema_long = row.get("ema_long")
        current_adx = row["adx"]
        prev_adx = prev_row["adx"]
        current_rsi = row["rsi"]
        bb_width = row["bb_width"]
        prev_bb_width = prev_row["bb_width"]
        close = row["close"]
        current_atr = row["atr"]

        # NaNチェック
        if any(pd.isna(v) for v in [ema_short, ema_long, current_adx, prev_adx,
                                     current_rsi, bb_width, prev_bb_width, current_atr]):
            return

        if current_atr <= 0:
            return

        # --- シグナル判定 ---
        adx_threshold = combined_cfg.get("adx_entry_threshold", 18)
        rsi_lower = combined_cfg.get("rsi_lower", 35)
        rsi_upper = combined_cfg.get("rsi_upper", 65)

        # ADXフィルター: トレンド発生中かつ上昇中
        if current_adx < adx_threshold or current_adx <= prev_adx:
            return

        # RSIフィルター: 中間帯（まだ余地がある）
        if current_rsi < rsi_lower or current_rsi > rsi_upper:
            return

        # ボラティリティ拡大確認
        if bb_width <= prev_bb_width:
            return

        # EMA方向 + 価格位置でシグナル決定
        side = None
        if ema_short > ema_long and close > ema_long:
            side = OrderSide.BUY
        elif ema_short < ema_long and close < ema_long:
            side = OrderSide.SELL

        if side is None:
            return

        # --- ポジションサイズ計算 ---
        sl_mult = combined_cfg.get("stop_loss_atr_mult", 8.0)
        stop_distance = current_atr * sl_mult
        adx_value = row["adx"]
        signal_strength = min(1.0, max(0.0, (adx_value - 18) / 30)) if not pd.isna(adx_value) else 0.5

        position_size = self.risk_manager.calculate_position_size(
            current_price=close,
            stop_distance=stop_distance,
            signal_strength=signal_strength,
        )

        if position_size <= 0:
            return

        # 最小注文単位に丸める（BTC_JPY: 0.01, ETH_JPY: 0.1）
        if "BTC" in symbol:
            position_size = round(position_size, 2)
            if position_size < 0.01:
                return
        elif "ETH" in symbol:
            position_size = round(position_size, 1)
            if position_size < 0.1:
                return

        # --- 注文実行 ---
        try:
            order_id = self.client.place_market_order(
                symbol=symbol,
                side=side,
                size=str(position_size),
            )

            # ストップ/テイクプロフィット計算
            tp_mult = combined_cfg.get("take_profit_atr_mult", 12.0)

            if side == OrderSide.BUY:
                stop_loss = close - current_atr * sl_mult
                take_profit = close + current_atr * tp_mult
            else:
                stop_loss = close + current_atr * sl_mult
                take_profit = close - current_atr * tp_mult

            self._position_entry_info[symbol] = {
                "side": side,
                "entry_price": close,
                "size": position_size,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_time": datetime.now(),
                "max_price": close,
                "min_price": close,
                "atr": current_atr,
            }

            self.risk_manager.update_positions(len(self._position_entry_info))

            # 通知
            self.notifier.notify_entry(
                symbol=symbol,
                side=side.value,
                size=position_size,
                price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            logger.info(
                f"[{symbol}] ENTRY {side.value} size={position_size} "
                f"price={close:,.0f} SL={stop_loss:,.0f} TP={take_profit:,.0f}"
            )

        except APIError as e:
            logger.error(f"Order failed: {e}")
            self.notifier.notify_error(f"Order failed for {symbol}: {e}")

    def _check_positions(self):
        """ポジション監視（損切り/利確/トレーリング/タイムアウト）"""
        for symbol in list(self._position_entry_info.keys()):
            info = self._position_entry_info[symbol]
            current_price = self.ws.get_last_price(symbol)

            if current_price is None:
                continue

            side = info["side"]
            entry_price = info["entry_price"]
            stop_loss = info["stop_loss"]
            take_profit = info["take_profit"]
            entry_time = info["entry_time"]
            atr = info["atr"]

            combined_cfg = self.strategy_config.get("combined", {})
            trailing_mult = combined_cfg.get("trailing_stop_atr_mult", 12.0)
            max_hold_hours = combined_cfg.get("max_hold_hours", 24)

            exit_reason = None

            if side == OrderSide.BUY:
                info["max_price"] = max(info["max_price"], current_price)
                trailing_stop = info["max_price"] - atr * trailing_mult

                # TP距離の60%以上の含み益が出た場合のみトレーリング発動
                tp_distance = take_profit - entry_price
                unrealized_profit = info["max_price"] - entry_price
                trailing_active = tp_distance > 0 and unrealized_profit >= tp_distance * 0.6

                if current_price <= stop_loss:
                    exit_reason = "stop_loss"
                elif current_price >= take_profit:
                    exit_reason = "take_profit"
                elif trailing_active and current_price <= trailing_stop:
                    hours_held = (datetime.now() - entry_time).total_seconds() / 3600
                    if hours_held > 1:  # 最低1時間保有後にトレーリング発動
                        exit_reason = "trailing_stop"

            elif side == OrderSide.SELL:
                info["min_price"] = min(info["min_price"], current_price)
                trailing_stop = info["min_price"] + atr * trailing_mult

                # TP距離の60%以上の含み益が出た場合のみトレーリング発動
                tp_distance = entry_price - take_profit
                unrealized_profit = entry_price - info["min_price"]
                trailing_active = tp_distance > 0 and unrealized_profit >= tp_distance * 0.6

                if current_price >= stop_loss:
                    exit_reason = "stop_loss"
                elif current_price <= take_profit:
                    exit_reason = "take_profit"
                elif trailing_active and current_price >= trailing_stop:
                    hours_held = (datetime.now() - entry_time).total_seconds() / 3600
                    if hours_held > 1:
                        exit_reason = "trailing_stop"

            # タイムアウト
            if exit_reason is None:
                hours_held = (datetime.now() - entry_time).total_seconds() / 3600
                if hours_held >= max_hold_hours:
                    exit_reason = "timeout"

            # 決済実行
            if exit_reason:
                self._close_position(symbol, exit_reason)

    def _close_position(self, symbol: str, reason: str):
        """ポジションを決済"""
        info = self._position_entry_info.get(symbol)
        if info is None:
            return

        side = info["side"]
        size = info["size"]
        entry_price = info["entry_price"]

        # 反対売買
        close_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        try:
            # 建玉を取得して決済
            positions = self.client.get_open_positions(symbol)
            if positions:
                for pos in positions:
                    self.client.close_order(
                        symbol=symbol,
                        side=close_side,
                        size=str(pos.size),
                        position_id=pos.position_id,
                    )
            else:
                # 建玉が見つからない場合は一括決済
                self.client.close_bulk_order(
                    symbol=symbol,
                    side=close_side,
                    size=str(size),
                )

            # 損益計算（概算）
            current_price = self.ws.get_last_price(symbol) or entry_price
            if side == OrderSide.BUY:
                pnl = (current_price - entry_price) * size
            else:
                pnl = (entry_price - current_price) * size

            # リスク管理に記録
            self.risk_manager.record_trade_result(pnl)
            self._daily_trades.append({"pnl": pnl, "time": datetime.now()})

            # ポジション情報削除
            del self._position_entry_info[symbol]
            self.risk_manager.update_positions(len(self._position_entry_info))

            # 通知
            self.notifier.notify_exit(
                symbol=symbol,
                side=side.value,
                size=size,
                entry_price=entry_price,
                exit_price=current_price,
                pnl=pnl,
                reason=reason,
            )

            logger.info(
                f"[{symbol}] EXIT {reason} pnl={pnl:+,.0f}円 "
                f"entry={entry_price:,.0f} exit={current_price:,.0f}"
            )

        except APIError as e:
            logger.error(f"Close position failed: {e}")
            self.notifier.notify_error(f"Close position failed for {symbol}: {e}")

    def _emergency_close_all(self):
        """全ポジション緊急決済"""
        logger.warning("EMERGENCY: Closing all positions!")
        for symbol in list(self._position_entry_info.keys()):
            self._close_position(symbol, "emergency")

    def _load_initial_candles(self):
        """起動時に直近のローソク足データを読み込み"""
        for symbol in self.symbols:
            try:
                today = datetime.now().strftime("%Y%m%d")
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

                # 昨日と今日のデータを取得
                df_list = []
                for date_str in [yesterday, today]:
                    df = fetch_klines(symbol=symbol, interval="15min", date=date_str)
                    if df is not None:
                        df_list.append(df)

                if df_list:
                    self._candle_buffer[symbol] = pd.concat(df_list, ignore_index=True)
                    logger.info(f"Loaded {len(self._candle_buffer[symbol])} candles for {symbol}")

            except Exception as e:
                logger.error(f"Failed to load candles for {symbol}: {e}")

    def _update_candles(self, symbol: str):
        """最新ローソク足を取得してバッファに追加"""
        try:
            today = datetime.now().strftime("%Y%m%d")
            df = fetch_klines(symbol=symbol, interval="15min", date=today)
            if df is not None and len(df) > 0:
                existing = self._candle_buffer.get(symbol)
                if existing is not None:
                    # 重複を除去して結合
                    combined = pd.concat([existing, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                    combined = combined.sort_values("timestamp").reset_index(drop=True)
                    # 直近200本に制限（メモリ節約）
                    self._candle_buffer[symbol] = combined.tail(200).reset_index(drop=True)
                else:
                    self._candle_buffer[symbol] = df
        except Exception as e:
            logger.warning(f"Failed to update candles for {symbol}: {e}")

    def _update_capital(self):
        """口座残高を更新"""
        try:
            margin = self.client.get_margin()
            self.risk_manager.update_capital(margin.available_amount)
            logger.info(f"Capital updated: {margin.available_amount:,.0f}円")
        except APIError as e:
            logger.warning(f"Failed to get margin: {e}")

    def _is_candle_closed(self, now: datetime) -> bool:
        """15分足が確定したかチェック"""
        # 15分の区切り（0, 15, 30, 45分）のタイミングで1回だけ処理
        if now.minute % 15 == 0 and now.second < 10:
            if self._last_check_time is None or (now - self._last_check_time).seconds > 60:
                return True
        return False

    def _on_ticker(self, data: dict):
        """ティッカー受信コールバック（ログ用）"""
        pass  # メインループでポーリングで確認するので特に何もしない

    def _send_daily_summary(self):
        """日次サマリーを送信"""
        daily_pnl = sum(t["pnl"] for t in self._daily_trades)
        wins = sum(1 for t in self._daily_trades if t["pnl"] > 0)
        total = len(self._daily_trades)
        win_rate = wins / total if total > 0 else 0

        status = self.risk_manager.get_status()

        self.notifier.notify_daily_summary(
            capital=status["total_capital"],
            daily_pnl=daily_pnl,
            total_pnl=status["pnl"],
            trades_today=total,
            win_rate=win_rate,
        )

        # 日次リセット
        self._daily_trades = []

    def _signal_handler(self, signum, frame):
        """シグナルハンドラー（Ctrl+C等）"""
        logger.info(f"Signal {signum} received. Shutting down...")
        self._running = False

    def stop(self):
        """エンジン停止"""
        self._running = False
