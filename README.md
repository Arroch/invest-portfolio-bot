# invest-portfolio-bot

Personal Telegram bot. Reads your T-Invest portfolio, compares it with `target.yaml`,
and suggests what to buy to reduce drift.

**Read-only — never places orders.**

## Quick start

### 1. BotFather

Create a bot in [@BotFather](https://t.me/BotFather), copy the token.

Run:

```text
/setjoingroups
```

then choose:

```text
Disable
```

### 2. T-Invest token

Issue a **read-only** token in the T-Invest app:

```text
Profile → Tokens → Issue
```

Use the most restricted scope you can.

### 3. Find your Telegram numeric id

Open [@userinfobot](https://t.me/userinfobot). It replies with your `Id`.

That value is `OWNER_CHAT_ID`.

### 4. Find your T-Invest account id

Run once with only `TINVEST_TOKEN` set to list accounts:

```python
import asyncio, os
from t_tech.invest import AsyncClient

async def main():
    async with AsyncClient(os.environ["TINVEST_TOKEN"]) as c:
        for a in (await c.users.get_accounts()).accounts:
            print(a.id, a.name, a.type, a.status)

asyncio.run(main())
```

Pick the account UUID you want to track. That is `TINVEST_ACCOUNT_ID`.

### 5. Configure

```bash
cp .env.example .env
$EDITOR .env
$EDITOR target.yaml
```

Fill at least:

```env
BOT_TOKEN=
TINVEST_TOKEN=
OWNER_CHAT_ID=
TINVEST_ACCOUNT_ID=
```

Adjust `target.yaml` to your target portfolio structure.

### 6. Run locally

```bash
python -m venv .venv
source .venv/bin/activate
```

`t-tech-investments` is published on T-Bank's own package index, not PyPI.
Add it as an extra index for this install:

```bash
pip install --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple -e '.[dev]'
```

Run tests:

```bash
pytest -q
```

Start the bot:

```bash
python bot.py
```

In Telegram:

- `/start`, `/help` — intro
- `/portfolio` — current state vs target, drift per category and bucket
- `/rebalance` — distribute current free cash across underweight buckets
- `/rebalance 50000` — distribute 50 000 ₽ of fresh money on top of the cash pool
- `/untracked` — positions outside of `target.yaml`

`/rebalance` keeps cash above `cash_target − 2 pp`.

### 7. Deploy with Docker Compose

```bash
git clone <repo>
cd invest-portfolio-bot

cp .env.example .env
$EDITOR .env
$EDITOR target.yaml

docker compose up -d --build
docker compose logs -f
```

Update:

```bash
git pull
docker compose up -d --build
```

If Telegram is reachable only through a local host proxy, for example Xray or
sing-box SOCKS on `127.0.0.1:10808`, enable the Compose override:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
```

See [Telegram proxy / local Xray or sing-box](#telegram-proxy--local-xray-or-sing-box-optional).

### 8. Autostart on server reboot with systemd, optional

`docker-compose.yml` already has:

```yaml
restart: unless-stopped
```

So as long as the Docker daemon is enabled at boot, the container comes back after
a reboot.

Check Docker autostart:

```bash
systemctl is-enabled docker
```

Usually it returns:

```text
enabled
```

The systemd unit below adds explicit on/off control and centralised logs.

Run once on the server. Adjust `PROJECT_DIR` if you cloned elsewhere:

```bash
PROJECT_DIR=$HOME/invest-portfolio-bot

sed \
  -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
  -e "s|{{USER}}|${USER}|g" \
  "${PROJECT_DIR}/scripts/invest-portfolio-bot.service" \
| sudo tee /etc/systemd/system/invest-portfolio-bot.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now invest-portfolio-bot.service
```

> Note: use `sed | sudo tee`, not `sudo sed > file`.
>
> The redirect `>` is executed by your shell before `sudo`, so writing directly
> to `/etc/systemd/system/` would fail without root permissions.

Operating it:

```bash
systemctl status invest-portfolio-bot
sudo systemctl restart invest-portfolio-bot
sudo systemctl stop invest-portfolio-bot
journalctl -u invest-portfolio-bot -f
docker compose logs -f
```

To remove autostart:

```bash
sudo systemctl disable --now invest-portfolio-bot.service
sudo rm /etc/systemd/system/invest-portfolio-bot.service
sudo systemctl daemon-reload
```

---

## Telegram proxy / local Xray or sing-box, optional

If the server cannot reach `api.telegram.org` directly, set a proxy URL in `.env`:

```env
TELEGRAM_PROXY_URL=socks5://user:pass@1.2.3.4:1080
```

Supported schemes:

- `http`
- `https`
- `socks4`
- `socks5`

**MTProto proxies do not work.** They speak Telegram's client protocol, not Bot
API HTTPS.

Only Telegram Bot API traffic goes through this proxy. T-Invest connects directly.

### Remote proxy examples

SOCKS5:

```env
TELEGRAM_PROXY_URL=socks5://user:pass@1.2.3.4:1080
```

HTTP proxy:

```env
TELEGRAM_PROXY_URL=http://user:pass@proxy.example.com:3128
```

IPv6 proxy hosts must be wrapped in square brackets:

```env
TELEGRAM_PROXY_URL=socks5://user:pass@[2001:db8::1]:1080
```

Docker's default bridge network is IPv4-only. If your proxy is IPv6-only, either:

1. enable host networking via Compose override:

   ```bash
   cp docker-compose.override.yml.example docker-compose.override.yml
   docker compose up -d --build
   ```

2. or enable IPv6 in the Docker daemon, for example in `/etc/docker/daemon.json`
   with `"ipv6": true` and `"fixed-cidr-v6"`, then restart Docker.

Sanity-check a remote SOCKS proxy from the host:

```bash
curl -x socks5h://user:pass@[2001:db8::1]:1080 https://api.telegram.org/ --max-time 15 -v
```

### Local host proxy: Xray / sing-box SOCKS

If the Docker host already has Xray, sing-box, v2ray or similar configured, expose
a local SOCKS or mixed inbound and use it for Telegram.

Example:

```text
127.0.0.1:10808
```

Set in `.env`:

```env
TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808
```

Because `127.0.0.1` inside a normal Docker bridge container is the container
itself, enable host networking:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
```

`docker-compose.override.yml.example` contains:

```yaml
services:
  bot:
    network_mode: host
```

With this setup:

```text
Telegram Bot API -> local SOCKS -> Xray/sing-box -> VPS
T-Invest API     -> direct
```

Sanity-check from the host:

```bash
curl -x socks5h://127.0.0.1:10808 https://api.telegram.org/ --max-time 15 -v
```

Sanity-check from the container:

```bash
docker compose run --rm bot python - <<'PY'
import socket

s = socket.create_connection(("127.0.0.1", 10808), timeout=5)
print("SOCKS TCP OK:", s.getpeername())
s.close()
PY
```

Check the effective Compose config:

```bash
docker compose config | grep -A3 -B3 network_mode
```

Expected:

```text
network_mode: host
```

---

## T-Invest TLS and Russian Trusted Root CA

T-Invest API may serve a TLS certificate chain rooted at:

```text
Russian Trusted Root CA
Russian Trusted Sub CA
```

Minimal Debian/Python Docker images and gRPC Python usually do not trust this CA
by default.

If the CA is missing, the bot fails on startup while resolving instruments:

```text
Tls handshake failed
CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain
```

The Docker image includes:

```text
certs/russian_trusted_root_ca.crt
```

and installs it into the container CA store:

```dockerfile
COPY certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
RUN update-ca-certificates
```

For multi-stage Dockerfiles, make sure the certificate is installed in the final
runtime stage, not only in the build stage.

For gRPC, `.env.example` sets:

```env
GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
```

This points gRPC to the container CA bundle.

Verify the certificate file:

```bash
openssl x509 \
  -in certs/russian_trusted_root_ca.crt \
  -noout \
  -subject \
  -issuer \
  -fingerprint \
  -sha256
```

Expected subject/issuer:

```text
subject=C = RU, O = The Ministry of Digital Development and Communications, CN = Russian Trusted Root CA
issuer=C = RU, O = The Ministry of Digital Development and Communications, CN = Russian Trusted Root CA
```

Check that gRPC sees the environment variable inside the container:

```bash
docker compose run --rm bot env | grep GRPC_DEFAULT_SSL_ROOTS_FILE_PATH
```

Expected:

```text
GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
```

Test T-Invest from inside the container:

```bash
docker compose run --rm bot python - <<'PY'
import asyncio, os
from t_tech.invest import AsyncClient

async def main():
    async with AsyncClient(os.environ["TINVEST_TOKEN"]) as c:
        for a in (await c.users.get_accounts()).accounts:
            print(a.id, a.name, a.type, a.status)

asyncio.run(main())
PY
```

The certificate is public and may be committed to the repository if it contains
only:

```text
-----BEGIN CERTIFICATE-----
...
-----END CERTIFICATE-----
```

Do **not** commit private keys, tokens or `.env`.

---

## Target schema

The target is a flat list of **buckets**, grouped under **categories** for display
only.

Each bucket's `weight` is a percentage of the **whole portfolio**, and the sum
across all buckets must equal `100`.

Categories themselves carry no weight. They are just labels.

A bucket binds value to instruments in one of three ways:

1. explicit `tickers:`
2. metadata `match:` filter
3. `cash_currencies:`

Exactly one bucket must be marked:

```yaml
is_cash: true
```

That bucket is the cash pool.

See [target.yaml](target.yaml) for a complete commented example.

Sketch:

```yaml
base_currency: RUB

categories:
  stocks:
    buckets:
      sber:
        weight: 4
        tickers:
          SBER: 100

      ru_etf:
        weight: 35
        tickers:
          TMOS@: 100

  bonds:
    buckets:
      ofz_short:
        weight: 4
        match:
          bond_type: ofz
          maturity_max_years: 3

      ofz_long:
        weight: 5
        match:
          bond_type: ofz
          maturity_min_years: 7

  gold:
    buckets:
      gold:
        weight: 10
        cash_currencies: [XAU]
        tickers:
          GLDRUB_TOM: 100

  cash:
    buckets:
      liquid:
        weight: 5
        is_cash: true
        tickers:
          TMON@: 100
```

`XAU` from T-Invest cash lands in the `gold` bucket, not in the `cash` bucket.

### `match:` filters

Filter keys are AND-ed when combined.

| key | values | notes |
|---|---|---|
| `bond_type` | `replaced` / `ofz` / `corp` / `any` | `replaced` = T-Invest `BOND_TYPE_REPLACED`; `ofz` = class `TQOB`; `corp` = `TQCB` / `TQTE` / `TQTD` / `TQIR` |
| `nominal_currency` | `rub` / `usd` / `eur` / `not_rub` | currency of the bond nominal |
| `class_code` | exact MOEX board code | for example `TQTF` for ETF |
| `maturity_max_years` | number | matches if remaining maturity ≤ N years |
| `maturity_min_years` | number | matches if remaining maturity > N years |

A filter-mode bucket always produces a **bucket allocation** in `/rebalance`, not
a per-ticker buy suggestion.

Bonds drift between buckets as maturity ticks down, so the bot reserves money for
the bucket and lets you pick the instrument.

### Hard invariants

The bot crashes on startup if any invariant is violated:

- Sum of bucket weights across all categories equals `100`.
- Within a bucket, ticker weights sum to `100`.
- Exactly one bucket has `is_cash: true`.
- An explicit ticker appears in at most one bucket.
- A `cash_currencies` entry appears in at most one bucket.
- Every explicit ticker resolves on a MOEX board at startup.

---

## Rebalance math

`/rebalance` never suggests sells.

For each non-cash bucket:

```text
total_after = current_portfolio_value + extra_cash
gap = total_after × portfolio_weight / 100 − current_value
```

Only positive gaps are considered. Overweight buckets are left alone.

### Cash floor

The cash bucket is not depleted below:

```text
cash_target − 2 pp
```

of `total_after`.

For example, if:

```yaml
cash.weight: 5
```

deployment stops at `3%` cash.

### Drift cap +2 pp

A buy that would push the destination bucket above:

```text
target + 2 pp
```

is dropped silently.

This prevents single-lot overshoots in small buckets.

### Explicit-ticker buckets

For a bucket like:

```yaml
tickers:
  SBER: 100
```

the bot produces concrete `BuySuggestion` items, floored to whole lots.

Leftover money is then retried to promote any bucket that did not fit a single lot
from its initial pro-rata share.

### Filter-mode buckets

For a bucket like:

```yaml
match:
  bond_type: ofz
  maturity_max_years: 3
```

the bot always produces a `BucketAllocation`.

It does not suggest a concrete ticker.

### Filter group throttling

Within a group:

```text
(category, bond_type)
```

of at least two filter buckets, the smallest-gap bucket is dropped so deployable
cash concentrates on the most underweight bucket of each kind.

### Unspent cash

Unspent cash is reported as:

```text
Остаток
```

---

## Troubleshooting

### T-Invest fails with `CERTIFICATE_VERIFY_FAILED`

Symptom:

```text
Tls handshake failed
CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain
```

Check that the Russian Trusted Root CA is present in the runtime container:

```bash
docker compose run --rm bot ls -l /usr/local/share/ca-certificates/
docker compose run --rm bot env | grep GRPC_DEFAULT_SSL_ROOTS_FILE_PATH
```

Expected:

```text
GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
```

Rebuild without cache:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

If the Dockerfile is multi-stage, ensure that this is in the final runtime stage:

```dockerfile
COPY certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
RUN update-ca-certificates
```

### Telegram fails with timeout

Symptom:

```text
aiogram.exceptions.TelegramNetworkError: HTTP Client says - Request timeout error
```

Check direct access from the host:

```bash
curl -v https://api.telegram.org/ --max-time 15
```

If direct access does not work, configure `TELEGRAM_PROXY_URL`.

For local Xray/sing-box SOCKS on the host:

```env
TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808
```

Enable host networking:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
```

Check proxy access:

```bash
curl -x socks5h://127.0.0.1:10808 https://api.telegram.org/ --max-time 15 -v
```

### Telegram proxy fails with `Connection refused`

Symptom:

```text
Couldn't connect to proxy 127.0.0.1:1080
ConnectionRefusedError: [Errno 111]
```

Check the proxy port on the host:

```bash
ss -lntp | grep -E '1080|10808'
```

If the proxy is bound to `127.0.0.1` on
