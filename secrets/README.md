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

## AWS CLI / boto3 — automation role (`secrets/aws.env`)

For a consistent IAM role (e.g. `ai-bot`) without pasting ARNs into every command:

1. Copy the template and set a **base** identity that is allowed to `sts:AssumeRole` that role (SSO profile or IAM user keys — never commit keys):

   ```bash
   cp secrets/aws.env.example secrets/aws.env
   ```

2. Edit `secrets/aws.env`: set `INTEROVES_AWS_ROLE_ARN` and uncomment `AWS_PROFILE` or access keys.

3. Repo scripts run `scripts/interoves_aws_bootstrap.sh` automatically (`with_rds.sh`, `eb_run.sh`, `rds_mysql.sh`). For ad-hoc commands:

   ```bash
   ./scripts/aws_with_role.sh aws sts get-caller-identity
   ./scripts/aws_with_role.sh eb status
   ```

SSO users still run `aws sso login` when the refresh token expires; after that, bootstrap assumes `ai-bot` for each script invocation.

Full matrix (RDS vs Redis, `eb_run` vs `with_rds`, Cursor permissions): **[`agents/aws-eb.md`](../agents/aws-eb.md)** → **Agent playbook**.

## Telegram bot (admin + announce chats)

Copy `secrets/telegram.env.example` and follow the steps inside. Minimum for **admin mode**:

1. Create a bot via `@BotFather`, save token to `secrets/telegram_bot_token.txt` (or `TELEGRAM_BOT_TOKEN` on EB).
2. Send `/start` to the bot from your personal Telegram account.
3. `../venv/interoves_django/bin/python manage.py telegram_notify_chat_id` — copy your `chat_id`.
4. Save it to `secrets/telegram_notify_chat_id.txt` (or `TELEGRAM_ADMIN_CHAT_ID` on EB).
5. `../venv/interoves_django/bin/python manage.py telegram_notify_test --admin-only`
6. Set `secrets/telegram_webhook_secret.txt`, then `manage.py telegram_set_webhook`.

**Chat mode** (group «Десяточек, посылка»): add the bot to the group, run step 3 again, put the group `chat_id` into `secrets/telegram_announce_chat_ids.txt`. Enable per game: `tags.telegram_announce = true` in Django admin.

**Channel** (t.me/interoves, daily ladder in «Отложенные»): Bot API cannot use `schedule_date`. Use a **user** MTProto session (Telethon) of a channel admin. Set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` (my.telegram.org), `TELEGRAM_CHANNEL_CHAT_ID=@interoves`, run `manage.py telegram_user_login` → `TELEGRAM_USER_SESSION`. At 00:15 MSK the minute cron schedules the post for 16:30 MSK. Image = Playwright screenshot of `SITE_BASE_URL/games/ladder/last/` (needs `playwright install chromium` on the host). Smoke: `manage.py telegram_ladder_admin_preview`, `manage.py telegram_ladder_channel_post schedule`.

Scheduled jobs (EB cron via `.ebextensions/telegram_cron.config`): `telegram_game_announcements` every minute (also ladder channel at 00:15 MSK). Log: `/var/log/telegram_cron.log`. Also `telegram_daily_digest` (daily; set separately if needed).

On prod after deploy:

```bash
eb setenv TELEGRAM_BOT_TOKEN='...' TELEGRAM_ADMIN_CHAT_ID='...' \
  TELEGRAM_ANNOUNCE_CHAT_IDS='-100...' TELEGRAM_CHANNEL_CHAT_ID='@interoves' \
  TELEGRAM_API_ID='...' TELEGRAM_API_HASH='...' TELEGRAM_USER_SESSION='...' \
  TELEGRAM_WEBHOOK_SECRET='...'
```
