# GMOコイン 仮想通貨自動売買Bot 設計書

## 1. プロジェクト概要

### 1.1 目的

GMOコインのAPIを利用した仮想通貨の自動売買システム。
10万円の原資から1ヶ月で倍（20万円）を目指す。

### 1.2 前提条件

- 破産（原資喪失）は許容する。破産した場合は原資を追加投入する
- 元本がプラスで10万円を超えた時点で原資回収し、利益分で再スタート
- 利益の一部を定期的に確保し、同じサイクルを繰り返す
- リスクは高いが、トータルの期待値がプラスになる設計を目指す

### 1.3 技術スタック

| 項目 | 選定 |
|------|------|
| 言語 | Python 3.12+ |
| 取引所 | GMOコイン（取引所レバレッジ取引） |
| インフラ | AWS EC2 (t3.small, Amazon Linux 2023) |
| 通知 | AWS SNS → メール |
| データ | WebSocket（リアルタイム）+ REST API（ローソク足） |

---

## 2. アーキテクチャ

### 2.1 システム構成図

```
┌─────────────────────────────────────────────────────────┐
│  AWS EC2 (t3.small)                                     │
│                                                         │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │ Data Layer  │──▶│ Strategy     │──▶│ Execution   │  │
│  │ (WebSocket  │   │ Engine       │   │ (Orders)    │  │
│  │  + REST)    │   │              │   │             │  │
│  └─────────────┘   └──────────────┘   └─────────────┘  │
│        │                   │                  │         │
│        ▼                   ▼                  ▼         │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │ Local DB    │   │ Risk Manager │   │ AWS SNS     │  │
│  │ (SQLite)    │   │              │   │ (通知)      │  │
│  └─────────────┘   └──────────────┘   └─────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐
│ GMOコイン API        │
│ - Public REST/WS    │
│ - Private REST/WS   │
└─────────────────────┘
```

### 2.2 ディレクトリ構造

```
crypto-bot/
├── config/
│   ├── settings.yaml          # 戦略パラメータ、API設定
│   └── settings.example.yaml  # テンプレート
├── src/
│   ├── __init__.py
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── gmo_client.py      # REST APIクライアント
│   │   ├── gmo_websocket.py   # WebSocket接続
│   │   └── models.py          # 注文/ポジション等のデータモデル
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py            # 戦略基底クラス
│   │   ├── breakout.py        # ブレイクアウト戦略
│   │   ├── grid.py            # グリッド戦略
│   │   └── regime.py          # 相場状態判定
│   ├── risk/
│   │   ├── __init__.py
│   │   └── manager.py         # リスク管理
│   ├── simulation/
│   │   ├── __init__.py
│   │   ├── monte_carlo.py     # モンテカルロシミュレーション
│   │   └── backtest.py        # 簡易バックテスト
│   ├── notify/
│   │   ├── __init__.py
│   │   └── sns.py             # AWS SNS通知
│   ├── data/
│   │   ├── __init__.py
│   │   └── candle.py          # ローソク足データ取得・管理
│   └── core/
│       ├── __init__.py
│       ├── engine.py           # メインエンジン（ループ制御）
│       └── logger.py           # ロギング
├── data/                       # ローソク足CSVデータ保存
├── logs/                       # 取引ログ
├── main.py                     # 本番bot エントリーポイント
├── simulate.py                 # シミュレーション実行
├── backtest.py                 # バックテスト実行
├── requirements.txt
├── deploy/
│   ├── setup.sh               # EC2セットアップスクリプト
│   └── crypto-bot.service     # systemdユニットファイル
└── design.md                   # 本ファイル
```

### 2.3 メインループ

```python
# 概念的なフロー
while running:
    # 1. WebSocketから最新価格を受信（常時）
    # 2. 15分足確定を検知
    if candle_closed:
        # 3. インジケーター更新
        update_indicators()
        # 4. 相場状態判定
        regime = detect_regime()
        # 5. 戦略に応じたシグナル生成
        signal = strategy.evaluate(regime)
        # 6. リスクチェック
        if risk_manager.can_trade(signal):
            # 7. 注文実行
            execute_order(signal)
    
    # 8. ポジション監視（損切り/利確/トレーリング）
    check_positions()
    
    # 9. 定期通知
    if hourly:
        send_summary()
    
    sleep(5)
```

---

## 3. 売買戦略仕様

### 3.1 相場状態判定（Regime Detection）

相場を3つの状態に分類し、使用する戦略を切り替える。

**判定ロジック:**

| 条件 | 状態 | 戦略 |
|------|------|------|
| ADX(14) > 25 かつ BB幅が前回より拡大 | TRENDING | ブレイクアウト |
| ADX(14) < 20 かつ BB幅が前回より収縮 | RANGING | グリッド |
| 上記以外 | TRANSITION | ポジション縮小 / 待機 |

**使用時間足:** 15分足（メイン）、1時間足（トレンド方向確認）

**パラメータ:**
- ADX期間: 14
- ボリンジャーバンド期間: 20, σ=2
- BB幅拡大/収縮判定: 直近5本のBB幅の傾き

### 3.2 ブレイクアウト戦略

トレンド発生時に、価格がレンジを抜けた方向にエントリーする。

**エントリー条件（ロング）:**

```
すべてを満たす場合にロングエントリー:
1. 15分足終値 > ドンチャンチャネル上限（期間20）
2. 1時間足 20EMA が上向き（現在値 > 3本前の値）
3. RSI(14) が 40〜70（過熱していない）
4. 出来高 > 20本移動平均の1.5倍（ブレイクの信頼性）
```

**エントリー条件（ショート）:**

```
すべてを満たす場合にショートエントリー:
1. 15分足終値 < ドンチャンチャネル下限（期間20）
2. 1時間足 20EMA が下向き（現在値 < 3本前の値）
3. RSI(14) が 30〜60（過熱していない）
4. 出来高 > 20本移動平均の1.5倍
```

**決済条件:**

| 条件 | タイプ | 計算 |
|------|--------|------|
| 利確 | 指値 | エントリー価格 ± ATR(14) × 2.0 |
| 損切り | 逆指値 | エントリー価格 ∓ ATR(14) × 1.0 |
| トレーリングストップ | 動的 | 最大含み益からATR(14) × 1.0 戻し |
| 時間切れ | 成行 | エントリーから6時間経過 |

**リスクリワード比:** 1:2（期待値がプラスになる前提）

**ポジションサイズ計算:**

```
リスク額 = 総資金 × リスク率（2%）
損切り幅 = ATR(14) × 1.0
理論ポジション = リスク額 ÷ 損切り幅

# レバレッジ制限を適用
最大ポジション額 = 総資金 × 実効レバレッジ（1.5倍）
実際のポジション = min(理論ポジション, 最大ポジション額 ÷ 現在価格)
```

### 3.3 グリッド戦略

レンジ相場時に等間隔の指値注文を配置し、小さな値動きで利益を積む。

**グリッド設定:**

```
グリッド中心 = 現在価格
グリッド幅（片側） = ATR(14) × 3.0
グリッド本数 = 片側5本（合計10本）
グリッド間隔 = グリッド幅 ÷ 5
```

**動作フロー:**

1. 中心より下に買い指値×5本、中心より上に売り指値×5本を配置
2. 買い指値が約定 → 1グリッド間隔上に利確の売り指値を配置
3. 売り指値が約定 → 1グリッド間隔下に利確の買い指値を配置
4. 相場状態がTRENDINGに変化 → 全グリッドキャンセル + ポジション決済

**グリッドのポジションサイズ:**

```
1グリッドあたりの注文額 = 総資金 × 実効レバレッジ ÷ グリッド本数(10)
```

**グリッドの損切り:**

- グリッド全体の含み損が総資金の3%超過 → 全ポジション成行決済
- 価格がグリッド範囲外に出た → 全キャンセル + ポジション決済 + グリッド再配置

### 3.4 使用インジケーター一覧

| インジケーター | 用途 | パラメータ |
|---------------|------|-----------|
| ADX | 相場状態判定 | 期間14 |
| ボリンジャーバンド | 相場状態判定 | 期間20, σ=2 |
| ドンチャンチャネル | ブレイクアウト判定 | 期間20 |
| EMA | トレンド方向確認 | 20（15分足, 1時間足） |
| RSI | 過熱判定 | 期間14 |
| ATR | 損切り/利確幅, ポジションサイズ, グリッド幅 | 期間14 |
| 出来高SMA | ブレイク信頼性 | 期間20 |

---

## 4. シミュレーション仕様

### 4.1 モンテカルロシミュレーション

戦略の期待値を、破産・追加投入込みで評価する。

**入力パラメータ:**

```yaml
simulation:
  initial_capital: 100000        # 初期資金（円）
  win_rate: 0.40                 # 勝率
  avg_win_pct: 0.04              # 勝ち時の平均利益率（4%）
  avg_loss_pct: 0.02             # 負け時の平均損失率（2%）
  trades_per_day: 4              # 1日の平均トレード回数
  days: 30                       # シミュレーション期間（日）
  num_trials: 10000              # 試行回数
  
  # 破産・追加投入ルール
  bankruptcy_threshold: 30000    # この金額以下で破産と判定（円）
  top_up_amount: 100000          # 追加投入額（円）
  
  # 利益確保ルール
  profit_take_threshold: 200000  # この金額に達したら利益確保
  profit_take_amount: 100000     # 確保する金額（原資回収）
  
  # 出金後の再スタート
  restart_capital: 100000        # 出金後の運用資金
```

**出力:**

```
=== モンテカルロシミュレーション結果 (10,000回試行) ===

● 1ヶ月後の結果分布
  - 平均最終資金: XXX,XXX円
  - 中央値: XXX,XXX円
  - 最大: X,XXX,XXX円
  - 最小: XX,XXX円

● 目標達成率
  - 20万円到達回数: X,XXX / 10,000 (XX.X%)

● 破産統計
  - 破産発生試行数: X,XXX / 10,000 (XX.X%)
  - 平均破産回数/試行: X.X回
  - 追加投入の平均合計: XXX,XXX円

● トータル収支
  - 総投入額平均: XXX,XXX円（初期 + 追加投入）
  - 総回収額平均: XXX,XXX円（利益確保 + 最終残高）
  - 純利益平均: +XX,XXX円
  - トータルプラスになる確率: XX.X%

● リスク指標
  - 最大ドローダウン平均: XX.X%
  - 連続負け最大: XX回
```

### 4.2 簡易バックテスト

目的: 「明らかにダメな戦略を排除する」ためのフィルター。

**仕様:**

- GMOコインのPublic APIから過去3ヶ月分の15分足データを取得
- ブレイクアウト戦略/グリッド戦略それぞれを単独で実行
- 手数料・スリッページを加味（taker: 0.05%, スリッページ: 0.01%）
- 結果が「負け越し」ならパラメータ調整 or 戦略変更を検討

**評価基準:**

| 指標 | 合格ライン |
|------|-----------|
| 勝率 | 35%以上 |
| プロフィットファクター | 1.3以上 |
| 最大ドローダウン | 30%以下 |
| トレード回数/月 | 50回以上（十分なサンプル） |

合格しない場合はパラメータを変更して再テスト。
それでもダメなら戦略自体を見直す。

---

## 5. リスク管理仕様

### 5.1 ポジションレベル

| ルール | 値 | アクション |
|--------|------|-----------|
| 1トレード最大損失 | 総資金の2% | 損切り実行 |
| トレーリングストップ | ATR × 1.0 | 利益確保して決済 |
| 最大保有時間 | 6時間 | 成行決済 |

### 5.2 アカウントレベル

| ルール | 値 | アクション |
|--------|------|-----------|
| 日次最大損失 | 総資金の5% | 当日取引停止 |
| 連続損切り | 3回連続 | 1時間クールダウン |
| 同時ポジション上限 | 2（BTC+ETH各1） | 新規エントリー拒否 |
| 実効レバレッジ上限 | 2.0倍 | ポジションサイズ制限 |

### 5.3 資金管理ルール

```
[破産判定]
残高 ≤ 30,000円 → 破産とみなし全ポジション決済 + 取引停止 + SNS緊急通知

[追加投入]
破産通知を受けた後、ユーザーが手動で10万円を入金し、bot再起動

[利益確保]
残高 ≥ 200,000円:
  → SNS通知「目標達成！10万円の出金を推奨」
  → ユーザーが手動で出金
  → 出金後、残りの資金で自動的に運用継続

残高 ≥ 150,000円:
  → SNS通知「利益50%到達。一部出金検討を推奨」
```

### 5.4 レバレッジ制御

```
シグナル強度に応じて実効レバレッジを調整:

signal_strength = (ADXの値 - 25) / 25  # 0.0〜1.0に正規化

effective_leverage = 1.2 + (signal_strength × 0.8)
# シグナル弱: 1.2倍
# シグナル強: 2.0倍
```

---

## 6. GMOコイン API仕様

### 6.1 エンドポイント

| API | URL |
|-----|-----|
| Public REST | https://api.coin.z.com/public |
| Public WebSocket | wss://api.coin.z.com/ws/public/v1 |
| Private REST | https://api.coin.z.com/private |
| Private WebSocket | wss://api.coin.z.com/ws/private/v1/{token} |

### 6.2 認証方式

```
ヘッダー:
  API-KEY: アクセスキー
  API-TIMESTAMP: Unix Timestamp (ミリ秒)
  API-SIGN: HMAC-SHA256署名

署名生成:
  text = timestamp + method + path + body
  sign = HMAC-SHA256(secret_key, text)
```

### 6.3 レート制限

| Tier | 条件 | 上限 |
|------|------|------|
| Tier 1 | 先週取引高 < 10億円 | GET 20req/s, POST 20req/s |
| Tier 2 | 先週取引高 >= 10億円 | GET 30req/s, POST 30req/s |

### 6.4 使用するAPI

| API | メソッド | 用途 |
|-----|----------|------|
| /v1/status | GET | 取引所ステータス確認 |
| /v1/ticker | GET | 最新レート取得 |
| /v1/orderbooks | GET | 板情報取得 |
| /v1/klines | GET | ローソク足データ取得 |
| /v1/symbols | GET | 取引ルール取得 |
| /v1/account/margin | GET | 余力情報取得 |
| /v1/account/assets | GET | 資産残高取得 |
| /v1/order | POST | 新規注文 |
| /v1/cancelOrder | POST | 注文キャンセル |
| /v1/closeOrder | POST | 決済注文 |
| /v1/closeBulkOrder | POST | 一括決済 |
| /v1/activeOrders | GET | 有効注文一覧 |
| /v1/openPositions | GET | 建玉一覧 |
| /v1/latestExecutions | GET | 約定履歴 |

### 6.5 取引対象

| 銘柄 | 役割 | 最小注文 | レバレッジ |
|------|------|----------|-----------|
| BTC_JPY | メイン | 0.01 BTC | 2倍 |
| ETH_JPY | サブ | 0.1 ETH | 2倍 |

### 6.6 手数料

| 項目 | 料率 |
|------|------|
| 取引所 taker | 0.05% |
| 取引所 maker | -0.01%（リベート） |
| レバレッジ手数料 | 0.04%/日（建玉管理料、6:00付与） |

---

## 7. AWS構成

### 7.1 EC2

- **インスタンスタイプ:** t3.small (2vCPU, 2GB RAM)
- **OS:** Amazon Linux 2023
- **ストレージ:** EBS 20GB gp3
- **セキュリティグループ:** SSH(22)のみインバウンド許可
- **月額コスト概算:** 約$15〜20

### 7.2 SNS

- **トピック名:** crypto-bot-notifications
- **サブスクリプション:** Email
- **通知タイミング:**
  - 約定時（エントリー/決済）
  - 損切り実行時
  - 日次サマリー（毎日9:00 JST）
  - 目標達成時
  - 破産時（緊急）
  - エラー発生時

### 7.3 デプロイ

```bash
# EC2セットアップ
sudo yum update -y
sudo yum install python3.12 -y
pip3 install -r requirements.txt

# systemdサービスとして登録
sudo cp deploy/crypto-bot.service /etc/systemd/system/
sudo systemctl enable crypto-bot
sudo systemctl start crypto-bot
```

---

## 8. 設定ファイル仕様

```yaml
# config/settings.yaml

exchange:
  api_key: "YOUR_API_KEY"
  api_secret: "YOUR_API_SECRET"
  symbols:
    - "BTC_JPY"
    - "ETH_JPY"

strategy:
  # 相場状態判定
  regime:
    adx_period: 14
    adx_trend_threshold: 25
    adx_range_threshold: 20
    bb_period: 20
    bb_std: 2
    bb_slope_period: 5

  # ブレイクアウト
  breakout:
    donchian_period: 20
    ema_period: 20
    rsi_period: 14
    rsi_long_range: [40, 70]
    rsi_short_range: [30, 60]
    volume_multiplier: 1.5
    atr_period: 14
    take_profit_atr_mult: 2.0
    stop_loss_atr_mult: 1.0
    trailing_stop_atr_mult: 1.0
    max_hold_hours: 6

  # グリッド
  grid:
    atr_period: 14
    grid_width_atr_mult: 3.0
    grid_count: 5  # 片側

risk:
  max_risk_per_trade: 0.02      # 1トレード最大リスク（2%）
  daily_loss_limit: 0.05        # 日次最大損失（5%）
  consecutive_loss_cooldown: 3  # 連続負け回数
  cooldown_minutes: 60          # クールダウン時間
  max_positions: 2              # 同時最大ポジション
  min_leverage: 1.2             # 最小実効レバレッジ
  max_leverage: 2.0             # 最大実効レバレッジ
  bankruptcy_threshold: 30000   # 破産判定額（円）

capital:
  initial: 100000               # 初期資金
  profit_take_threshold: 200000 # 利益確保閾値
  profit_take_notify: 150000    # 利益確保推奨通知

notification:
  sns_topic_arn: "arn:aws:sns:ap-northeast-1:XXXX:crypto-bot-notifications"
  daily_summary_hour: 9         # 日次サマリー送信時刻（JST）

logging:
  level: "INFO"
  file: "logs/bot.log"
  max_size_mb: 50
  backup_count: 5
```

---

## 9. 開発・運用フロー

### 9.1 開発フェーズ

```
Phase 1: シミュレーション
  → モンテカルロシミュレーションを実装
  → 各種パラメータで期待値を確認
  → 「トータルでプラスになるか」を数字で検証

Phase 2: バックテスト
  → GMOコインから過去データ取得
  → ブレイクアウト/グリッド各戦略を検証
  → 明らかにダメな設定を排除

Phase 3: 本番bot実装
  → シミュレーションで確認済みのパラメータで実装
  → EC2デプロイ
  → 最初は少額（1万円）でフォワードテスト
  → 問題なければ10万円投入
```

### 9.2 運用ルール

1. **毎日**: SNSのメール通知で損益確認
2. **毎週**: パフォーマンスレビュー（勝率、PF確認）
3. **破産時**: 原因分析 → パラメータ調整 → 追加投入 → 再起動
4. **目標達成時**: 出金 → 利益分で再スタート
5. **2週間連続マイナス**: 戦略の見直し

---

## 10. 注意事項・免責

- 本システムは投資助言ではない
- 仮想通貨取引には元本割れリスクがある
- 過去のバックテスト結果は将来の利益を保証しない
- APIキー・シークレットの管理は利用者の責任
- GMOコインのAPI利用規約を遵守すること
