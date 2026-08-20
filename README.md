# GMOコイン 仮想通貨自動売買Bot

GMOコインのAPIを使った仮想通貨の自動売買システム。  
BTC/JPY のブレイクアウト戦略をベースに、レバレッジ取引で月利100%を目指す。

## クイックスタート

```bash
# リポジトリクローン
git clone https://github.com/koheismh/gmo-auto.git
cd gmo-auto

# Python環境構築
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 設定ファイル作成
cp config/settings.example.yaml config/settings.yaml
# → APIキーを編集

# シミュレーション実行（APIキー不要）
python simulate.py

# バックテスト実行（APIキー不要、GMO Public APIを使用）
python backtest.py --start 20260501 --end 20260815

# 本番Bot起動
python main.py
```

## コマンド一覧

| コマンド | 説明 | APIキー |
|----------|------|---------|
| `python simulate.py` | モンテカルロシミュレーション（期待値検証） | 不要 |
| `python simulate.py --sensitivity` | パラメータ感度分析 | 不要 |
| `python backtest.py` | 直近3ヶ月のバックテスト | 不要 |
| `python backtest.py --start YYYYMMDD --end YYYYMMDD` | 期間指定バックテスト | 不要 |
| `python main.py` | 本番Bot起動 | 必要 |
| `python main.py --dry-run` | ドライランモード | 必要 |

## シミュレーション

```bash
# デフォルト設定で10,000回試行
python simulate.py

# パラメータを変更して実行
python simulate.py --win-rate 0.45 --avg-win 0.05 --leverage 2.0

# パラメータ組み合わせの感度分析
python simulate.py --sensitivity
```

## バックテスト

```bash
# BTC/JPY 直近3ヶ月
python backtest.py

# 期間指定
python backtest.py --start 20260301 --end 20260601

# ETH/JPY
python backtest.py --symbol ETH_JPY --start 20260601 --end 20260801
```

バックテスト結果から得られた勝率・平均損益でシミュレーションを再実行できる:
```bash
# バックテスト出力の最後に表示されるコマンドをそのまま実行
python simulate.py --win-rate 0.32 --avg-win 0.0042 --avg-loss 0.0028 --leverage 1.5
```

## EC2デプロイ

```bash
# EC2にSSH接続後
bash deploy/setup.sh

# APIキー設定
nano config/settings.yaml

# SNSトピック作成（メール通知）
bash deploy/create-sns-topic.sh your-email@example.com

# Bot起動
sudo systemctl start crypto-bot

# ログ確認
sudo journalctl -u crypto-bot -f

# 停止
sudo systemctl stop crypto-bot
```

## プロジェクト構成

```
crypto-bot/
├── config/
│   ├── settings.example.yaml  # 設定テンプレート
│   └── simulation.yaml        # シミュレーション設定
├── src/
│   ├── exchange/              # GMOコイン API接続
│   ├── strategy/              # 戦略ロジック（インジケーター、相場判定）
│   ├── risk/                  # リスク管理
│   ├── simulation/            # モンテカルロ＆バックテスト
│   ├── notify/                # AWS SNS通知
│   ├── data/                  # データ取得
│   └── core/                  # エンジン、ロギング
├── deploy/                    # EC2デプロイ用ファイル
├── main.py                    # 本番Bot
├── simulate.py                # シミュレーション
├── backtest.py                # バックテスト
└── design.md                  # 設計書
```

## 戦略概要

- **相場判定**: ADX + ボリンジャーバンド幅でトレンド/レンジを分類
- **ブレイクアウト**: ドンチャンチャネルブレイクでエントリー、ATRベースの損切り/利確
- **リスク管理**: 1トレード2%リスク、日次5%損失制限、連続負け3回でクールダウン
- **破産対応**: 残高3万円以下で停止→通知→追加投入後に再起動

## 注意事項

- 投資助言ではありません
- 元本割れリスクがあります（破産を前提とした設計です）
- バックテスト結果は将来の利益を保証しません
- APIキーの管理は自己責任です
