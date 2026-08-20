# デプロイマニュアル

GMOコイン自動売買Botを AWS EC2 上にデプロイして稼働させる手順です。

---

## 前提条件

- AWSアカウントを持っている
- GMOコインの口座を開設済みで、APIキー/シークレットを取得済み
- SSH接続用のターミナル環境がある（Mac/Windows Terminal等）

---

## 全体の流れ

```
1. EC2インスタンスを作成
2. セキュリティグループを設定
3. EC2にSSH接続してセットアップ
4. GMOコインのAPIキーを設定
5. SNS通知を設定（任意）
6. Botを起動
```

所要時間: 約30分

---

## Step 1: EC2インスタンスの作成

AWSマネジメントコンソールにログインし、EC2のページを開きます。

### 1-1. インスタンスの起動

「インスタンスを起動」をクリックして以下の設定で作成します。

| 項目 | 設定値 |
|------|--------|
| 名前 | crypto-bot |
| AMI | Amazon Linux 2023 |
| インスタンスタイプ | t3.small（2vCPU, 2GB RAM） |
| キーペア | 新規作成するか既存のものを選択 |
| ストレージ | 20 GiB gp3 |

### 1-2. キーペアの作成（初めての場合）

「新しいキーペアの作成」を選択し、以下で作成します:

- キーペア名: `crypto-bot-key`
- キーペアのタイプ: RSA
- ファイル形式: `.pem`（Mac/Linux）または `.ppk`（Windows + PuTTY）

ダウンロードされた `.pem` ファイルは安全な場所に保管してください。紛失するとSSH接続できなくなります。

### 1-3. ネットワーク設定

「編集」をクリックして以下を確認:

- パブリックIPの自動割り当て: 有効
- セキュリティグループ: 「セキュリティグループを作成する」を選択

セキュリティグループのインバウンドルール:

| タイプ | ポート | ソース | 説明 |
|--------|--------|--------|------|
| SSH | 22 | マイIP | SSH接続用 |

それ以外のインバウンドは不要です（Botは外向き通信のみ）。

### 1-4. IAMロールの設定（SNS通知を使う場合）

「高度な詳細」を展開し、「IAMインスタンスプロファイル」で以下の手順でロールを割り当てます。

IAMロールが未作成の場合:

1. 別タブで IAM > ロール > 「ロールを作成」を開く
2. 信頼されたエンティティ: 「AWSのサービス」→「EC2」を選択
3. ポリシーの作成:
   - 「ポリシーを作成」→ JSON タブに以下を貼り付け

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["sns:Publish"],
      "Resource": "arn:aws:sns:ap-northeast-1:*:crypto-bot-notifications"
    }
  ]
}
```

   - ポリシー名: `crypto-bot-sns-policy`
4. ロール名: `crypto-bot-role`
5. 作成後、EC2作成画面に戻ってこのロールを選択

「インスタンスを起動」をクリックして完了です。

---

## Step 2: EC2にSSH接続

インスタンスが「実行中」になったら、パブリックIPアドレスをコピーします。

```bash
# .pemファイルの権限を変更（初回のみ）
chmod 400 ~/Downloads/crypto-bot-key.pem

# SSH接続
ssh -i ~/Downloads/crypto-bot-key.pem ec2-user@<パブリックIP>
```

`<パブリックIP>` はEC2ダッシュボードで確認できるIPアドレスに置き換えてください。

---

## Step 3: Botのセットアップ

SSH接続後、以下のコマンドを実行します。

```bash
# リポジトリをクローン
cd ~
git clone https://github.com/koheismh/gmo-auto.git
cd gmo-auto

# セットアップスクリプトを実行（5分程度かかる）
bash deploy/setup.sh
```

スクリプトが完了すると以下が自動で行われます:
- Python 3.12 のインストール
- 仮想環境の作成と依存パッケージのインストール
- ディレクトリ構成の作成
- systemdサービスの登録

---

## Step 4: APIキーの設定

GMOコインの会員ページ > API でAPIキーを発行します。

必要な権限:
- 現物取引（参照）
- レバレッジ取引（注文・参照）
- 資産（参照）

```bash
nano config/settings.yaml
```

以下の部分を編集します:

```yaml
exchange:
  api_key: "ここにAPIキーを貼り付け"
  api_secret: "ここにAPIシークレットを貼り付け"
  symbols:
    - "BTC_JPY"
```

`Ctrl+O` で保存、`Ctrl+X` で終了。

---

## Step 5: SNS通知の設定（任意）

メールで取引通知を受け取りたい場合に設定します。不要ならこのステップは飛ばしてください。

```bash
# SNSトピックを作成（メールアドレスを指定）
bash deploy/create-sns-topic.sh your-email@example.com
```

実行すると確認メールが届くので、メール内のリンクをクリックして承認します。

表示されたARNを `config/settings.yaml` に設定:

```yaml
notification:
  sns_topic_arn: "arn:aws:sns:ap-northeast-1:123456789012:crypto-bot-notifications"
  dry_run: false  # falseにすると実際にメール送信される
```

---

## Step 6: 動作確認

### ドライラン（注文を出さずに動作テスト）

```bash
cd ~/gmo-auto
source .venv/bin/activate
python main.py --dry-run
```

- ログにエラーが出なければOK
- `Ctrl+C` で停止

### バックテストの実行（任意）

```bash
python backtest_strategies.py --strategy combined
```

---

## Step 7: 本番起動

```bash
# Bot起動
sudo systemctl start crypto-bot

# 起動確認
sudo systemctl status crypto-bot
```

正常に起動すると `active (running)` と表示されます。

---

## 日常の運用コマンド

```bash
# ログをリアルタイムで確認
sudo journalctl -u crypto-bot -f

# 直近100行のログを見る
sudo journalctl -u crypto-bot -n 100

# Botを停止
sudo systemctl stop crypto-bot

# Botを再起動
sudo systemctl restart crypto-bot

# 起動状態の確認
sudo systemctl status crypto-bot
```

---

## トラブルシューティング

### Botが起動しない

```bash
# エラーログを確認
sudo journalctl -u crypto-bot -n 50 --no-pager

# 手動で起動してエラーを確認
cd ~/gmo-auto && source .venv/bin/activate && python main.py
```

### APIキーのエラーが出る

- GMOコインの会員ページでAPIキーが有効になっているか確認
- IP制限を設定している場合、EC2のパブリックIPを許可リストに追加
- APIキーの権限に「レバレッジ取引」が含まれているか確認

### SNS通知が届かない

- IAMロールが正しくEC2に割り当てられているか確認
- 確認メールを承認したか確認
- `config/settings.yaml` で `dry_run: false` になっているか確認

```bash
# AWS認証が通るかテスト
aws sts get-caller-identity
```

### EC2インスタンスのIPが変わった

EC2を停止→起動するとパブリックIPが変わります。固定したい場合は Elastic IP（月額約500円）を割り当てます:

1. EC2 > Elastic IP > 「Elastic IPアドレスの割り当て」
2. 割り当てたIPを選択 > 「アクション」>「Elastic IPアドレスの関連付け」
3. 対象のEC2インスタンスを選択

---

## 費用の目安

| 項目 | 月額 |
|------|------|
| EC2 t3.small (24時間稼働) | 約 $15（約2,300円） |
| EBS 20GB gp3 | 約 $1.6（約250円） |
| SNS メール通知 | ほぼ無料（月1,000通まで無料） |
| Elastic IP（使う場合） | 約 $3.6（約550円） |
| **合計** | **約2,500〜3,100円/月** |

---

## 停止・削除する場合

```bash
# Bot停止
sudo systemctl stop crypto-bot
sudo systemctl disable crypto-bot
```

EC2インスタンスを完全に削除する場合は、マネジメントコンソールから「インスタンスを終了」を選択します。EBSボリュームも自動で削除されます。

Elastic IPを割り当てている場合は、先に解放してください（未使用のElastic IPには課金されます）。

---

## セキュリティに関する注意

- `config/settings.yaml` にはAPIシークレットが含まれるため、**gitにcommitしない**こと（.gitignoreに含まれています）
- SSH接続のソースIPは「マイIP」に限定し、`0.0.0.0/0`（全開放）にしない
- APIキーにはIP制限を設定することを推奨（GMOコインの設定画面から）
- EC2のセキュリティグループはSSH(22)のみ開放。HTTP/HTTPSは不要
