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

## RDS environment variables

1. Copy a template and fill in the password (never commit the copy):

   ```bash
   cp secrets/rds.env.example secrets/rds.env
   # or for the Elastic Beanstalk–coupled dev DB defaults:
   cp secrets/rds.elasticbeanstalk-env.example secrets/rds.env
   ```

2. Edit `secrets/rds.env` and set `RDS_PASSWORD`.

3. Load before Django / `manage.py` against RDS:

   ```bash
   set -a && source secrets/rds.env && set +a
   python manage.py ensure_games_0109_indexes --dry-run
   ```

`secrets/rds.env` is ignored by git (everything under `secrets/*` except the whitelisted examples and this README).
