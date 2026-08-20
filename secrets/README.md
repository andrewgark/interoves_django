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

**Channel** (t.me/interoves, daily ladder in «Отложенные»): Bot API cannot use `schedule_date`. Use a **user** MTProto session (Telethon) of a channel admin. Set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` (my.telegram.org), `TELEGRAM_CHANNEL_CHAT_ID=@interoves`, run `manage.py telegram_user_login` → `TELEGRAM_USER_SESSION`. At 00:15 MSK the minute cron schedules the post for 16:30 MSK **and tweets the same teaser to X immediately** (needs `TWITTER_*` secrets). Image = Playwright screenshot of `SITE_BASE_URL/games/ladder/last/` (needs `playwright install chromium` under `/home/webapp/.cache/ms-playwright`; cron exports `PLAYWRIGHT_BROWSERS_PATH` because it runs as root). Smoke: `manage.py telegram_ladder_admin_preview`, `manage.py telegram_ladder_channel_post schedule`.

Scheduled jobs (EB cron via `.ebextensions/telegram_cron.config`): `telegram_game_announcements` every minute (also ladder channel at 00:15 MSK). Log: `/var/log/telegram_cron.log`. Also `telegram_daily_digest` (daily; set separately if needed).

On prod after deploy:

```bash
eb setenv TELEGRAM_BOT_TOKEN='...' TELEGRAM_ADMIN_CHAT_ID='...' \
  TELEGRAM_ANNOUNCE_CHAT_IDS='-100...' TELEGRAM_CHANNEL_CHAT_ID='@interoves' \
  TELEGRAM_API_ID='...' TELEGRAM_API_HASH='...' TELEGRAM_USER_SESSION='...' \
  TELEGRAM_WEBHOOK_SECRET='...' \
  TWITTER_API_KEY='...' TWITTER_API_SECRET='...' \
  TWITTER_ACCESS_TOKEN='...' TWITTER_ACCESS_TOKEN_SECRET='...'
```

## NOWPayments (crypto tickets on `/pay/`)

Used by `POST /pay/create-crypto-ticket-payment/` and IPN at `/nowpayments/ipn/`.

Local files (or EB env vars):

- `nowpayments_api_key.txt` / `NOWPAYMENTS_API_KEY`
- `nowpayments_ipn_secret.txt` / `NOWPAYMENTS_IPN_SECRET`

In the NOWPayments dashboard: generate API key + IPN secret; set default IPN URL to `https://<domain>/nowpayments/ipn/` (each invoice also sends `ipn_callback_url`).

```bash
eb setenv NOWPAYMENTS_API_KEY='...' NOWPAYMENTS_IPN_SECRET='...'
```

## Tribute (foreign cards on `/pay/`)

Used by `POST /pay/create-tribute-ticket-payment/` and webhook at `/tribute/webhook/`.

Uses two pre-created Tribute **Digital Products** and their fixed browser `webLink` values. It does not use Tribute Shop API. In the Tribute dashboard, generate an API key and set webhook URL to `https://interoves.com/tribute/webhook/`.

Required EB env vars:

- `TRIBUTE_API_KEY` (also used to verify `trbt-signature`; local fallback file: `secrets/tribute_api_key.txt`)
- `TRIBUTE_REGULAR_PRODUCT_ID`, `TRIBUTE_REGULAR_PRODUCT_WEB_URL`, `TRIBUTE_REGULAR_PRODUCT_AMOUNT`, `TRIBUTE_REGULAR_PRODUCT_CURRENCY`
- `TRIBUTE_DISCOUNT_PRODUCT_ID`, `TRIBUTE_DISCOUNT_PRODUCT_WEB_URL`, `TRIBUTE_DISCOUNT_PRODUCT_AMOUNT`, `TRIBUTE_DISCOUNT_PRODUCT_CURRENCY`
- `TELEGRAM_BOT_USERNAME` and the existing `TELEGRAM_BOT_TOKEN`
- `TRIBUTE_MERCHANT=ru_self_employed` or `am_ie`, only after legal seller review
- `TRIBUTE_LEGAL_REVIEW_APPROVED=true`
- `TRIBUTE_ENABLED=true` only after all production values and webhook delivery are verified

Amounts use Tribute's smallest currency units (EUR cents or RUB kopecks). Currency must be `EUR` or `RUB`; web links must use `https://web.tribute.tg/p/...`.

```bash
eb setenv TRIBUTE_ENABLED=false TRIBUTE_API_KEY='...' \
  TRIBUTE_REGULAR_PRODUCT_ID='...' TRIBUTE_REGULAR_PRODUCT_WEB_URL='https://web.tribute.tg/p/...' \
  TRIBUTE_REGULAR_PRODUCT_AMOUNT='...' TRIBUTE_REGULAR_PRODUCT_CURRENCY='EUR' \
  TRIBUTE_DISCOUNT_PRODUCT_ID='...' TRIBUTE_DISCOUNT_PRODUCT_WEB_URL='https://web.tribute.tg/p/...' \
  TRIBUTE_DISCOUNT_PRODUCT_AMOUNT='...' TRIBUTE_DISCOUNT_PRODUCT_CURRENCY='EUR'
```

## X / Twitter (@interoves)

Used by the 00:15 MSK ladder cron: when the Telegram channel post is queued for 16:30, the same teaser is **tweeted immediately** (X has no organic schedule).

Local files (or EB env vars):

- `twitter_api_key.txt` / `TWITTER_API_KEY`
- `twitter_api_secret.txt` / `TWITTER_API_SECRET`
- `twitter_access_token.txt` / `TWITTER_ACCESS_TOKEN`
- `twitter_access_token_secret.txt` / `TWITTER_ACCESS_TOKEN_SECRET`

```bash
eb setenv TWITTER_API_KEY='...' TWITTER_API_SECRET='...'   TWITTER_ACCESS_TOKEN='...' TWITTER_ACCESS_TOKEN_SECRET='...'
```

