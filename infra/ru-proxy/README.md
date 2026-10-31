# ru.interoves.com: устойчивый российский вход

Статус: **предложение, ничего не применено**. Шаблон, конфиги и скрипты в этом
каталоге не создают ресурсов, пока их не применят вручную.

## Сейчас

```
браузер (РФ) → ru.interoves.com → UltraVDS Nginx (77.81.5.159)
            → Tailscale → 100.93.29.79 (Tailscale, поставленный руками на один EC2 Green)
            → EB Nginx :80 на этом EC2 → Daphne
```

Основной маршрут не меняется: `interoves.com → Cloudflare → ALB :80 → EB Nginx → Daphne`.

Проблема: EC2 с `100.93.29.79` — одна из двух инстанций ASG Green
(`awseb-e-724phkmr9x-stack-AWSEBAutoScalingGroup-…`, 2/2 `t3.small`, rolling
deploy, batch 1). Обычный деплой, обновление платформы или замена после сбоя
уничтожает этот узел, и ru.interoves.com ложится до ручной переустановки
Tailscale и правки UltraVDS. Вторая инстанция в ASG тут не помогает: Tailscale
на ней нет.

## Что проверено в AWS (read-only, 2026-10-31)

| Что | Значение |
|---|---|
| VPC | default `vpc-35fb595e`, `172.31.0.0/16` |
| Подсети | публичные 1a `subnet-29483d42`, 1b `subnet-df8b26a2`, 1c `subnet-ff71f9b2` (auto public IP); приватные 1a/1c без маршрута в интернет |
| NAT Gateway | нет (и не нужен ни одному варианту ниже) |
| ALB | `awseb--AWSEB-9kClukce8vto`, **internet-facing**, 1a+1b |
| ALB listeners | `HTTP:80` и `HTTPS:443` (ACM `origin.interoves.com`, до 2027-05-17) → один target group, health `/health/live/` |
| ALB SG `sg-00bb9ba3c6ae88fbd` | `:80` только Cloudflare (16 диапазонов); `:443` — `0.0.0.0/0` **и** `77.81.5.159/32` «UltraVDS origin HTTPS» |
| Instance SG | `:80` только от ALB SG; `interoves-green-web-sg` без правил |
| ALB idle timeout | 60 s (track WebSocket шлёт sync каждые 25 s — хватает) |
| ALB XFF | `append`, Host не переписывается |
| Elastic IP | 3 шт. **не привязаны** (`3.127.41.124`, `3.78.22.206` «interoves-web-origin», `63.182.147.248`) |
| `11_associate_green_origin_eip.sh` | срабатывает только для env `interoves-web-green` (старое single-instance), на `-lb` ничего не делает |

Выводы:

- Публичный ALB уже является стабильным upstream для всех инстанций Green,
  и до него можно дойти из VPC — через его публичные адреса (IGW hairpin).
  Приватного listener'а у internet-facing ALB для клиентов VPC нет: адреса
  его ENI меняются, их нельзя прописывать.
- `:443` ALB уже открыт для всего интернета. Значит HTTPS-путь
  «шлюз → ALB» заработает без изменения SG. (Отдельно: это открытый обход
  Cloudflare к origin; закрывать или нет — отдельное решение.)
- Три свободных EIP оплачиваются уже сейчас (~$3.65/мес каждый). Один можно
  отдать шлюзу, тогда его публичный IPv4 не добавляет расходов.

## Чего не хватает (проверить вручную)

1. На какой из `i-05c6013ca9afe0c8f` / `i-0b33c2d0416e32c7d` стоит Tailscale,
   включён ли `tailscaled` в автозапуск, какой у узла тег/владелец и ключ
   (expiry). SSM-запрос к прод-инстанции из агента был заблокирован.
2. Текущий конфиг Nginx на UltraVDS (сертификат, `proxy_pass`, заголовки).
3. Tailnet policy (ACL/grants), план Tailscale, есть ли OAuth-клиенты; тег
   самого UltraVDS.
4. Работает ли с UltraVDS прямое HTTPS к ALB (правило `77.81.5.159/32` на
   `:443` кто-то уже добавил) и почему от него ушли к Tailscale — см. вариант D.
5. Какой трафик идёт через ru-вход (ГБ/мес) — для оценки data transfer.
6. Свободны ли три EIP (никакие DNS/whitelist на них не ссылаются).

## Варианты

### A. Отдельный Tailscale-шлюз → публичный ALB (рекомендуется)

```
UltraVDS → Tailscale → gateway (ASG 1–2 × t4g.nano, вне EB)
        → HTTPS origin.interoves.com → публичный ALB :443 → любая здоровая инстанция Green
```

- `gateway/ru-gateway.yaml` — CloudFormation: SG без inbound, IAM-роль
  (SSM Session Manager, чтение одного SSM-параметра, `SetInstanceHealth`
  только для своего ASG), launch template (AL2023 arm64, IMDSv2, gp3 8 GB
  encrypted), ASG `GatewayCount` = 1 или 2.
- user-data: Tailscale из официального репозитория, вход **ephemeral**
  узлом с `tag:interoves-ru-gw` и именем `interoves-ru-gw-<instance-id>`;
  Nginx проксирует на ALB по HTTPS с проверкой сертификата
  (`proxy_ssl_name origin.interoves.com`), ALB-адрес перерезолвится каждые
  30 s, WebSocket-заголовки и таймауты 3600 s; принимает только `100.64.0.0/10`.
- Секрет: **OAuth client secret** Tailscale (scope `auth_keys`, тег
  `tag:interoves-ru-gw`) в SSM SecureString
  `/interoves/ru-gateway/tailscale-oauth-client-secret`. В отличие от
  auth key он не истекает через ≤90 дней, а значит замена шлюза через полгода
  не сломается. Секрет читается в файл 0600 на tmpfs и передаётся как
  `--auth-key=file:…`: не попадает в argv, лог bootstrap или git.
- Самолечение: systemd-таймер раз в 30 s; если 3 раза подряд не работает
  Tailscale или локальный Nginx — `set-instance-health Unhealthy`, ASG
  поднимает новый. Недоступность самого origin только логируется — циклить
  шлюз из-за аварии прода бессмысленно.
- При корректном завершении `tailscale logout` сразу удаляет ephemeral-узел;
  при аварийном его уберёт Tailscale, а UltraVDS всё равно игнорирует
  offline-узлы.

На стороне UltraVDS IP не прописывается: `ultravds/ts-upstream-sync.sh`
(таймер 30 s) берёт из локального `tailscale status --json` все online-пиры с
`tag:interoves-ru-gw` и пересобирает `upstream`, проверяет `nginx -t`,
делает reload; при пустом списке оставляет прежний файл.

### B. Tailscale на каждой инстанции EB

`.platform/hooks/prebuild` ставит Tailscale, берёт OAuth secret из SSM,
поднимает ephemeral `tag:interoves-web`; UltraVDS балансирует по online-пирам
тем же sync-скриптом.

Минусы, из-за которых не рекомендуется:
- UltraVDS обходит ALB: во время rolling deploy EB снимает инстанцию с ALB,
  а UltraVDS продолжает слать на неё запросы, пока не сработают пассивные
  проверки. Нужно дублировать логику target health.
- Секрет tailnet и сетевой доступ из РФ-сегмента на каждой прод-инстанции;
  больше поверхность атаки.
- Green деплоится через `deploy.sh`, который сохраняет живые `.platform`
  из zip; хук пришлось бы вносить и туда. Нужна правка IAM-роли
  `interoves-green-web-role` (сейчас только `s3:GetObject`).
- Время bootstrap новой инстанции растёт; сбой установки Tailscale ломает
  deploy или оставляет инстанцию без ru-входа без явного сигнала.

### C. Tailscale subnet router

Шлюз объявляет маршрут в VPC (`--advertise-routes`), UltraVDS ходит
в приватные адреса. Стабильного приватного endpoint нет: у internet-facing
ALB его не бывает, IP инстанций меняются. Понадобится **второй, internal
ALB** (~$20–26/мес) с регистрацией тех же инстанций — а их регистрирует EB,
т.е. ещё и правка EB/ASG. SNAT по умолчанию, SG пришлось бы открывать для
роутера. L3-доступ из tailnet в VPC — шире, чем нужно (только TCP 80/443
одного endpoint). По сути это A, но дороже и с большей поверхностью.

### D. Без Tailscale: UltraVDS → ALB :443 напрямую (для сравнения)

`proxy_pass https://<ALB DNS>` с `proxy_ssl_name origin.interoves.com`.
Ноль новых ресурсов, ALB сам переживает замену инстанций. Правило SG для
`77.81.5.159/32` уже есть. Работает, только если трафик UltraVDS → AWS
Frankfurt по HTTPS стабильно не режется. Требование «через Tailscale» оно не
выполняет, но годится как `backup` в upstream UltraVDS для A.

## Сравнение

Цены eu-central-1 on-demand, приблизительные (у `ai-bot` нет
`pricing:GetProducts`; перед согласованием проверить в AWS Pricing
Calculator).

| | A: шлюз → ALB | B: Tailscale на EB | C: subnet router + internal ALB | D: напрямую в ALB |
|---|---|---|---|---|
| Доп. AWS $/мес | ≈ $4–5 (t4g.nano ≈ $3.5, gp3 8 GB ≈ $0.8; IPv4 $3.65 или $0 с уже оплачиваемым EIP); HA ×2 ≈ $9 | ≈ $0 | ≈ $28–35 (шлюз + internal ALB + LCU) | $0 |
| Замена EC2 EB | не влияет (ALB) | upstream обновится за ≤30 s, но запросы на снимаемую инстанцию падают | не влияет | не влияет |
| Отказ одного узла | 1 шлюз: ~3–6 мин (ASG); 2 шлюза: секунды | секунды | как A | — (ALB multi-AZ) |
| Сложность внедрения | средняя (1 стек CFN + UltraVDS) | высокая (EB hooks, IAM, deploy.sh) | высокая | минимальная |
| Эксплуатация | низкая; обновлять AMI раз в квартал (instance refresh) | средняя, связана с каждым деплоем | средняя | нет |
| Безопасность | секрет только на шлюзе; шлюз без inbound; tailnet → только tcp:80 шлюза | секрет и tailnet на всех прод-инстанциях | L3 в VPC | origin открыт в интернет (уже так) |
| Совместимость с EB | полная, EB не трогается | требует изменений EB | требует регистрации в 2-м ALB | полная |
| WebSocket | да (ALB + Nginx Upgrade) | да | да | да |
| RTO | 3–6 мин / секунды при HA | ≤30 s + пассивные проверки | как A | — |

Data transfer во всех Tailscale-вариантах: выход в интернет к UltraVDS
($0.09/ГБ) есть и сейчас. A добавляет шлюз ↔ ALB через публичные адреса
(≈ $0.01/ГБ в каждую сторону).

## Рекомендация

**A, начать с `GatewayCount=1`** и одним из уже оплачиваемых EIP.
Шлюз переживает любые операции EB, upstream у UltraVDS вычисляется
автоматически. Для HA позже поставить `GatewayCount=2`, это +$4–5/мес и
никаких других изменений. Если D окажется рабочим, держать его в upstream
UltraVDS как `backup`.

## Миграция (каждый шаг — после согласования)

0. Задеплоить Django-релиз с `ru.interoves.com` в `ALLOWED_HOSTS` /
   `CSRF_TRUSTED_ORIGINS` и относительным `STATIC_URL` (этот коммит).
   UltraVDS пока шлёт `Host: interoves.com` — ничего не меняется.
1. UltraVDS: заменить `proxy_set_header Host interoves.com` на `$host`,
   добавить заголовки из `ultravds/nginx/ru.interoves.com.conf`. Проверить:
   `curl -sSI https://ru.interoves.com/` → 200, в HTML нет `https://interoves.com/static`.
   Upstream пока прежний `100.93.29.79`.
2. Tailscale admin: добавить `tailscale/policy-snippet.hujson`; создать OAuth
   client (scope `auth_keys`, tag `tag:interoves-ru-gw`).
3. AWS: `aws ssm put-parameter --type SecureString --name /interoves/ru-gateway/tailscale-oauth-client-secret --value file:///dev/stdin`
   (значение не в shell history и не в git).
4. AWS: создать стек `interoves-ru-gateway` из `gateway/ru-gateway.yaml`
   (`--capabilities CAPABILITY_IAM`, при желании `EipAllocationId`).
5. Проверить шлюз из UltraVDS:
   `curl -sS -H 'Host: ru.interoves.com' http://<gw-tailscale-ip>/health/live/` → `ok`;
   WebSocket: `websocat`/браузер на `wss://ru.interoves.com/ws/track/` после шага 6.
6. UltraVDS: установить `ts-upstream-sync.sh` + timer с
   `FALLBACK=100.93.29.79` — шлюз становится основным, старый EC2 `backup`.
7. Учение: `aws autoscaling terminate-instance-in-auto-scaling-group` для
   шлюза (не для Green!) и/или обычный деплой Green. Ожидаемо: при деплое
   Green — без ошибок; при убийстве единственного шлюза — 3–6 мин 502 /
   переход на backup.
8. После недели стабильной работы убрать `FALLBACK`, затем (отдельно)
   удалить ручной Tailscale с EC2 Green.

## Health checks

- ALB → Green: `/health/live/` (как сейчас).
- Шлюз: `/gateway-health` (локально), таймер `interoves-ru-gw-health`
  (Tailscale + Nginx → `SetInstanceHealth`; origin → лог).
- UltraVDS: пассивные `max_fails=2 fail_timeout=10s`,
  `proxy_next_upstream error timeout http_502 http_503 http_504`.
- Внешний мониторинг (рекомендуется): проверка
  `https://ru.interoves.com/health/live/` **из РФ** раз в минуту
  (UltraVDS cron → Telegram admin chat или внешний сервис).

## Сценарии восстановления

| Событие | Что происходит | Ручные действия |
|---|---|---|
| Деплой / замена EC2 Green | ALB уводит трафик; шлюз не замечает | нет |
| Шлюз завис / Tailscale упал | self-check → Unhealthy → ASG заменяет; sync на UltraVDS подхватывает новый IP | нет |
| AZ шлюза недоступна | ASG поднимает в другой подсети | нет (с `GatewayCount=2` — без простоя) |
| OAuth secret отозван | новый шлюз не входит в tailnet, старый работает до замены | новый секрет в SSM, instance refresh |
| Tailscale control plane недоступен | уже подключённые узлы продолжают работать | нет; новые узлы ждут |
| Прод недоступен | ru и основной домен лежат одинаково | как для основного домена |

## Rollback

На UltraVDS вернуть upstream на текущий узел:

```bash
echo 'server 100.93.29.79:80;' > /etc/nginx/interoves-upstreams.conf
systemctl disable --now ts-upstream-sync.timer
nginx -t && systemctl reload nginx
```

При необходимости вернуть `proxy_set_header Host interoves.com;` (Django
принимает оба). Ручной Tailscale на EC2 Green до шага 8 не удаляется,
поэтому этот откат работает, пока жива та инстанция. Шлюз удаляется
`aws cloudformation delete-stack --stack-name interoves-ru-gateway`;
EB, ALB, SG, DNS и Cloudflare в процессе не меняются.
