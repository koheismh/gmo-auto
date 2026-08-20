#!/bin/bash
# SNSトピックとメールサブスクリプションを作成するスクリプト
#
# 使い方:
#   bash deploy/create-sns-topic.sh your-email@example.com
#
# 実行後、メールに確認メールが届くので承認してください。

set -e

EMAIL=${1:-""}

if [ -z "$EMAIL" ]; then
    echo "Usage: bash deploy/create-sns-topic.sh your-email@example.com"
    exit 1
fi

TOPIC_NAME="crypto-bot-notifications"
REGION="ap-northeast-1"

echo "Creating SNS topic: ${TOPIC_NAME}..."
TOPIC_ARN=$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --output text --query 'TopicArn')
echo "Topic ARN: ${TOPIC_ARN}"

echo "Subscribing ${EMAIL}..."
aws sns subscribe \
    --topic-arn "$TOPIC_ARN" \
    --protocol email \
    --notification-endpoint "$EMAIL" \
    --region "$REGION"

echo ""
echo "Done! 確認メールが ${EMAIL} に送信されました。"
echo "メール内のリンクをクリックして承認してください。"
echo ""
echo "config/settings.yaml に以下を設定してください:"
echo "  sns_topic_arn: \"${TOPIC_ARN}\""
echo "  dry_run: false"
echo ""
