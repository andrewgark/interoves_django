# Local secrets (not in git)

This directory holds **local-only** files: passwords, API keys, and the RDS CA bundle.

## RDS TLS bundle

Download or refresh the AWS RDS combined CA file:

```bash
curl -fsSL -o secrets/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
```

Use with the MySQL client, for example:

```bash
mysql --ssl-mode=VERIFY_CA --ssl-ca=secrets/global-bundle.pem -h "$RDS_HOSTNAME" -u "$RDS_USERNAME" -p "$RDS_DB_NAME"
```

### One-liner with Secrets Manager (password from AWS, not `rds.env`)

If the master password is stored in Secrets Manager (RDS integration), you can use Python instead of `jq`:

```bash
PW=$(aws secretsmanager get-secret-value --secret-id 'YOUR_SECRET_ARN' --region eu-central-1 --query SecretString --output text \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
export MYSQL_PWD="$PW"   # avoids passing -p on the command line
mysql -h "$RDS_HOSTNAME" -P 3306 -u admin --ssl-mode=VERIFY_IDENTITY --ssl-ca=secrets/global-bundle.pem ebdb -e "SELECT 1"
```

Or run the helper script from the repo root (same defaults as EB dev RDS; override with env vars):

```bash
./scripts/rds_mysql.sh -e "SELECT 1"
```

**Connectivity:** if this **hangs** or **times out**, your current IP is probably not allowed on the RDS security group (common when the DB is only open to the EB/VPC). Add a temporary inbound rule for your IP, or connect via **bastion / SSM port forwarding** / **EB `eb ssh`**.

## RDS environment variables

1. Copy a template and fill in the password (never commit the copy):

   ```bash
   cp secrets/rds.env.example secrets/rds.env
   # or for the Elastic Beanstalk–coupled dev DB defaults:
   cp secrets/rds.elasticbeanstalk-env.example secrets/rds.env
   ```

2. Edit `secrets/rds.env` and set `RDS_PASSWORD`. **Wrap the value in single quotes** if it contains shell-special characters such as `( ) | $ < > [ ] * ?`. Example: `RDS_PASSWORD='…'`. If the password itself contains a single quote, use the bash form `RDS_PASSWORD='foo'\''bar'` (that is one quoted string).

3. Load before Django / `manage.py` against RDS:

   ```bash
   set -a && source secrets/rds.env && set +a
   python manage.py ensure_games_0109_indexes --dry-run
   ```

`secrets/rds.env` is ignored by git (everything under `secrets/*` except the whitelisted examples and this README).
