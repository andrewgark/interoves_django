#!/usr/bin/env bash
# Idempotent CloudWatch alarms for the ECS recheck worker.
# No alarm actions are attached by default; add SNS later when a recipient is chosen.
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

QUEUE_NAME="interoves-recheck"
DLQ_NAME="${QUEUE_NAME}-dlq"
LOG_GROUP="/interoves/workers/recheck"
METRIC_NAMESPACE="InterOves/Recheck"

QUEUE_URL=$(aws sqs get-queue-url --region "$REGION" --queue-name "$QUEUE_NAME" --query QueueUrl --output text)
DLQ_URL=$(aws sqs get-queue-url --region "$REGION" --queue-name "$DLQ_NAME" --query QueueUrl --output text)

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-recheck-oldest-message \
  --alarm-description 'Recheck SQS oldest message is waiting too long' \
  --namespace AWS/SQS --metric-name ApproximateAgeOfOldestMessage \
  --dimensions "Name=QueueName,Value=${QUEUE_NAME}" \
  --statistic Maximum --period 60 --evaluation-periods 5 --datapoints-to-alarm 3 \
  --threshold 600 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-recheck-dlq \
  --alarm-description 'Recheck dead-letter queue contains a message' \
  --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions "Name=QueueName,Value=${DLQ_NAME}" \
  --statistic Maximum --period 60 --evaluation-periods 1 --datapoints-to-alarm 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching

aws logs put-metric-filter --region "$REGION" \
  --log-group-name "$LOG_GROUP" \
  --filter-name interoves-recheck-dispatch-errors \
  --filter-pattern '"recheck outbox dispatch failed"' \
  --metric-transformations \
    metricName=DispatcherErrors,metricNamespace="$METRIC_NAMESPACE",metricValue=1,defaultValue=0

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-recheck-dispatch-errors \
  --alarm-description 'Recheck outbox dispatcher logged an exception' \
  --namespace "$METRIC_NAMESPACE" --metric-name DispatcherErrors \
  --statistic Sum --period 300 --evaluation-periods 1 --datapoints-to-alarm 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-recheck-oldest-outbox \
  --alarm-description 'Recheck DB outbox item has waited too long' \
  --namespace "$METRIC_NAMESPACE" --metric-name OutboxOldestAgeSeconds \
  --statistic Maximum --period 60 --evaluation-periods 5 --datapoints-to-alarm 3 \
  --threshold 600 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-rds-oldest-transaction \
  --alarm-description 'An InnoDB transaction has remained open for too long' \
  --namespace "$METRIC_NAMESPACE" --metric-name OldestInnoDBTransactionAgeSeconds \
  --statistic Maximum --period 60 --evaluation-periods 5 --datapoints-to-alarm 3 \
  --threshold 600 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching

echo "Recheck alarms upserted in ${REGION}."
