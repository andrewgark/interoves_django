#!/usr/bin/env bash
# Cost hygiene for interoves Redis (ElastiCache): drop replica, then resize to cache.t4g.micro.
# Needs ElastiCache modify rights on AWS_PROFILE (e.g. AmazonElastiCacheFullAccess on interoves / ai-bot).
#
# Usage:
#   ./scripts/resize_redis_cost.sh
#   DRY_RUN=1 ./scripts/resize_redis_cost.sh
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
RG="${REDIS_REPLICATION_GROUP_ID:-interoves-redis}"
NODE_TYPE="${REDIS_NODE_TYPE:-cache.t4g.micro}"
DRY_RUN="${DRY_RUN:-0}"

export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

echo "Region=$REGION ReplicationGroup=$RG TargetNodeType=$NODE_TYPE DryRun=$DRY_RUN Profile=$AWS_PROFILE"

aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'ReplicationGroups[0].{Status:Status,NodeType:CacheNodeType,Members:MemberClusters,Failover:AutomaticFailover,MultiAZ:MultiAZ}' \
  --output json

MEMBER_COUNT=$(aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'length(ReplicationGroups[0].MemberClusters)' --output text)
CURRENT_TYPE=$(aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'ReplicationGroups[0].CacheNodeType' --output text)
FAILOVER=$(aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'ReplicationGroups[0].AutomaticFailover' --output text)

echo "Members=$MEMBER_COUNT CurrentType=$CURRENT_TYPE Failover=$FAILOVER"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN: would disable failover if needed, decrease replicas to 0, then modify to $NODE_TYPE"
  exit 0
fi

if [[ "$MEMBER_COUNT" -gt 1 ]]; then
  if [[ "$FAILOVER" == "enabled" || "$FAILOVER" == "enabling" ]]; then
    echo "Disabling automatic failover / Multi-AZ (required before last replica removal)..."
    aws elasticache modify-replication-group --region "$REGION" \
      --replication-group-id "$RG" \
      --no-automatic-failover-enabled \
      --no-multi-az-enabled \
      --apply-immediately >/dev/null
    aws elasticache wait replication-group-available --region "$REGION" --replication-group-id "$RG"
  fi

  echo "Decreasing replica count to 0 (brief Channels/WS disruption possible)..."
  # API allows only one of: NewReplicaCount | ReplicaConfiguration | ReplicasToRemove
  REPLICAS=()
  while IFS= read -r m; do
    [[ -z "$m" || "$m" == *"-001" ]] && continue
    REPLICAS+=("$m")
  done < <(aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
    --query 'ReplicationGroups[0].MemberClusters[]' --output text | tr '\t' '\n')

  if [[ ${#REPLICAS[@]} -gt 0 ]]; then
    aws elasticache decrease-replica-count --region "$REGION" \
      --replication-group-id "$RG" \
      --apply-immediately \
      --replicas-to-remove "${REPLICAS[@]}"
  else
    aws elasticache decrease-replica-count --region "$REGION" \
      --replication-group-id "$RG" \
      --new-replica-count 0 \
      --apply-immediately
  fi

  echo "Waiting for replication group available..."
  aws elasticache wait replication-group-available --region "$REGION" --replication-group-id "$RG"
fi

CURRENT_TYPE=$(aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'ReplicationGroups[0].CacheNodeType' --output text)
if [[ "$CURRENT_TYPE" != "$NODE_TYPE" ]]; then
  echo "Modifying node type $CURRENT_TYPE -> $NODE_TYPE..."
  aws elasticache modify-replication-group --region "$REGION" \
    --replication-group-id "$RG" \
    --cache-node-type "$NODE_TYPE" \
    --apply-immediately
  echo "Waiting for replication group available..."
  aws elasticache wait replication-group-available --region "$REGION" --replication-group-id "$RG"
else
  echo "Already on $NODE_TYPE"
fi

aws elasticache describe-replication-groups --region "$REGION" --replication-group-id "$RG" \
  --query 'ReplicationGroups[0].{Status:Status,NodeType:CacheNodeType,Members:MemberClusters,Failover:AutomaticFailover,MultiAZ:MultiAZ}' \
  --output table
echo "Done. Smoke-check: ./scripts/eb_run.sh manage.py check --database default && curl -sS -o /dev/null -w '%{http_code}\\n' https://interoves.com/"
