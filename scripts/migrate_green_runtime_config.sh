#!/usr/bin/env bash
# One-time switch of Green web from per-variable EB environment secrets to the
# interoves/production/runtime-config JSON bundle (see
# scripts/build_runtime_config_secret.py).  Plans by default; --apply changes AWS.
#
# Green keeps its live .ebextensions across deploys and django.config is a
# protected file, so this repacks the live ZIP with only django.config edited:
# the environmentsecrets block is dropped and INTEROVES_CONFIG_SECRET_ID added.
# The new ZIP becomes the live baseline for later ./deploy.sh runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
APP=interoves
ENVIRONMENT=interoves-web-green-lb
ROLE=interoves-green-web-role
POLICY=interoves-green-runtime-config-read
BUNDLE=interoves/production/runtime-config
PYTHON="$ROOT/../venv/interoves_django/bin/python"
APPLY=0
case "${1:-}" in --apply) APPLY=1 ;; "") ;; *) echo "Usage: $0 [--apply]" >&2; exit 2 ;; esac
[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }
aws_cmd() { AWS_DEFAULT_REGION="$REGION" aws "$@"; }

BUNDLE_ARN="$(aws_cmd secretsmanager describe-secret --secret-id "$BUNDLE" --query ARN --output text)"
secret_names="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name "$APP" --environment-name "$ENVIRONMENT" \
    --query 'ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environmentsecrets`].OptionName' --output text)"
# Every variable EB injects today must exist in the bundle; only key names are compared.
aws_cmd secretsmanager get-secret-value --secret-id "$BUNDLE" --query SecretString --output text \
    | "$PYTHON" -c '
import json, sys
keys = set(json.load(sys.stdin))
missing = sorted(set(sys.argv[1].split()) - keys)
if missing:
    sys.exit("Bundle is missing: " + " ".join(missing))
' "$secret_names"

VERSION="$(aws_cmd elasticbeanstalk describe-environments --environment-names "$ENVIRONMENT" --query 'Environments[0].VersionLabel' --output text)"
read -r BUCKET KEY < <(aws_cmd elasticbeanstalk describe-application-versions --application-name "$APP" --version-labels "$VERSION" \
    --query 'ApplicationVersions[0].SourceBundle.[S3Bucket,S3Key]' --output text)
NEW_VERSION="${VERSION}-runtimecfg"
NEW_KEY="${KEY%/*}/${NEW_VERSION}.zip"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
aws_cmd s3 cp --quiet "s3://$BUCKET/$KEY" "$work/live.zip"
"$PYTHON" - "$work/live.zip" "$work/new.zip" "$BUNDLE" <<'PY'
import hashlib
import json
import sys
import zipfile

source, target, bundle = sys.argv[1:4]
CONFIG = ".ebextensions/django.config"
MANIFEST = "interoves_eb_config_manifest.json"


def rewrite(text):
    lines = text.splitlines(keepends=True)
    out = []
    skipping = False
    for line in lines:
        if line.startswith("  aws:elasticbeanstalk:application:environmentsecrets:"):
            skipping = True
            while out and out[-1].lstrip().startswith("#") and out[-1].startswith("  #"):
                out.pop()
            continue
        if skipping and line.startswith("    "):
            continue
        skipping = False
        out.append(line)
        if line.startswith("  aws:elasticbeanstalk:application:environment:"):
            out.append("    INTEROVES_CONFIG_SECRET_ID: \"{}\"\n".format(bundle))
    result = "".join(out)
    if result.count("INTEROVES_CONFIG_SECRET_ID") != 1 or "environmentsecrets" in result:
        sys.exit("Unexpected django.config layout; refusing to rewrite.")
    return result


with zipfile.ZipFile(source) as live, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
    config = rewrite(live.read(CONFIG).decode()).encode()
    before = hashlib.sha256(live.read(CONFIG)).hexdigest()
    after = hashlib.sha256(config).hexdigest()
    for info in live.infolist():
        data = live.read(info.filename)
        if info.filename == CONFIG:
            data = config
        elif info.filename == MANIFEST:
            manifest = json.loads(data)
            for item in manifest["configuration"]:
                if item["path"] == CONFIG:
                    item["sha256"] = after
                    item["source"] = "explicit-green-overlay"
            manifest["diff_from_live"].append({
                "path": CONFIG, "before_sha256": before, "after_sha256": after, "action": "modified",
            })
            data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        new.writestr(info, data)
print("django.config {} -> {}".format(before[:12], after[:12]))
PY

echo "environment=$ENVIRONMENT version=$VERSION new_version=$NEW_VERSION"
echo "bundle=$BUNDLE_ARN"
echo "remove_environmentsecrets=$(echo $secret_names | wc -w)"
if [[ "$APPLY" != 1 ]]; then echo "plan_only=true (pass --apply to change AWS)"; exit 0; fi

policy="$(printf '{"Version":"2012-10-17","Statement":[{"Sid":"ReadRuntimeConfig","Effect":"Allow","Action":["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],"Resource":"%s"}]}' "$BUNDLE_ARN")"
aws_cmd iam put-role-policy --role-name "$ROLE" --policy-name "$POLICY" --policy-document "$policy"
role_arn="$(aws_cmd iam get-role --role-name "$ROLE" --query Role.Arn --output text)"
decision="$(aws_cmd iam simulate-principal-policy --policy-source-arn "$role_arn" --action-names secretsmanager:GetSecretValue \
    --resource-arns "$BUNDLE_ARN" --query 'EvaluationResults[0].EvalDecision' --output text)"
[[ "$decision" == allowed ]] || { echo "Green role cannot read $BUNDLE_ARN: $decision" >&2; exit 1; }

aws_cmd s3 cp --quiet "$work/new.zip" "s3://$BUCKET/$NEW_KEY"
aws_cmd elasticbeanstalk create-application-version --application-name "$APP" --version-label "$NEW_VERSION" \
    --description "Runtime secrets from $BUNDLE" --source-bundle "S3Bucket=$BUCKET,S3Key=$NEW_KEY" --query 'ApplicationVersion.Status' --output text

remove="$("$PYTHON" -c '
import json, sys
print(json.dumps([{"Namespace": "aws:elasticbeanstalk:application:environmentsecrets", "OptionName": n} for n in sys.argv[1].split()]))
' "$secret_names")"
aws_cmd elasticbeanstalk update-environment --environment-name "$ENVIRONMENT" --version-label "$NEW_VERSION" \
    --option-settings "[{\"Namespace\":\"aws:elasticbeanstalk:application:environment\",\"OptionName\":\"INTEROVES_CONFIG_SECRET_ID\",\"Value\":\"$BUNDLE\"}]" \
    --options-to-remove "$remove" --query '[EnvironmentName,Status]' --output text

for _ in $(seq 1 120); do
    read -r status health < <(aws_cmd elasticbeanstalk describe-environments --environment-names "$ENVIRONMENT" --query 'Environments[0].[Status,Health]' --output text)
    [[ "$status" == Ready ]] && break
    sleep 15
done
echo "status=$status health=$health"
left="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name "$APP" --environment-name "$ENVIRONMENT" \
    --query 'length(ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environmentsecrets`])' --output text)"
echo "environmentsecrets_left=$left"
curl -fsS https://interoves.com/health/live/ && echo
[[ "$status" == Ready && "$health" == Green && "$left" == 0 ]]
