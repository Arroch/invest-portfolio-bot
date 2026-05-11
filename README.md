# portfolio-bot

Personal Telegram bot. Reads your T-Invest portfolio, compares with `target.yaml`, and suggests what to buy to reduce drift. **Read-only — never places orders.**

## Quick start

### 1. BotFather

Create a bot in [@BotFather](https://t.me/BotFather), copy the token. Run `/setjoingroups` → `Disable`.

### 2. T-Invest token

Issue a **read-only** token in the T-Invest app: *Profile → Tokens → Issue*. Use the most restricted scope you can.

### 3. Find your Telegram numeric id

Open [@userinfobot](https://t.me/userinfobot), it replies with your `Id`. That's `OWNER_CHAT_ID`.

### 4. Find your account id

Run once with just `TINVEST_TOKEN` set to list accounts:

```python
import asyncio, os
from t_tech.invest import AsyncClient

async def main():
    async with AsyncClient(os.environ["TINVEST_TOKEN"]) as c:
        for a in (await c.users.get_accounts()).accounts:
            print(a.id, a.name, a.type, a.status)

asyncio.run(main())
```

Pick the account UUID you want to track. That's `TINVEST_ACCOUNT_ID`.

### 5. Configure

```bash
cp .env.example .env
$EDITOR .env          # fill BOT_TOKEN, TINVEST_TOKEN, OWNER_CHAT_ID, TINVEST_ACCOUNT_ID
$EDITOR target.yaml   # adjust to your target structure
```

### 6. Run locally

```bash
python -m venv .venv && source .venv/bin/activate

# t-tech-investments is published on T-Bank's own package index, not PyPI.
# Add it as an extra index for this install.
pip install --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple -e '.[dev]'

pytest -q              # math sanity check
python bot.py
```

In Telegram:

- `/start` — intro
- `/portfolio` — current state vs target
- `/rebalance` — distribute current free cash across underweight positions
- `/rebalance 50000` — distribute 50 000 of fresh money

### 7. Deploy on VPS

```bash
git clone <repo> && cd portfolio-bot
cp .env.example .env && $EDITOR .env
$EDITOR target.yaml
docker compose up -d --build
docker compose logs -f
```

Update:

```bash
git pull && docker compose up -d --build
```

## Target schema

```yaml
base_currency: RUB

categories:
  stocks:
    weight: 60          # % of total portfolio
    tickers:
      SBER: 40          # % of the category (stocks here)
      LKOH: 30
      YNDX: 30
  bonds:
    weight: 30
    tickers:
      SU26240RMFS0: 100
  gold:
    weight: 10
    tickers:
      TGLD: 100
```

Hard invariants (bot crashes on violation):

- Category weights sum to 100.
- Ticker weights within each category sum to 100.
- Every ticker resolves on a MOEX board at startup.
- A ticker is in at most one category.

The "real" portfolio weight of a ticker is `category_weight × ticker_weight / 100`. So SBER above targets `60 × 40 / 100 = 24%` of the whole portfolio.

## Rebalance math

- `total_after = current_portfolio_value + extra_cash`
- For each target ticker: `gap = total_after × portfolio_weight / 100 − current_value`
- Positive gaps (underweight) get pro-rata share of cash; overweight positions are left alone — **the bot never suggests sells**.
- Allocations are floored to whole lots; the unspent remainder is reported as `Leftover`.

## Out of scope (MVP)

Historical snapshots · daily/weekly auto-reports · sell suggestions · multi-account aggregation · web UI · multi-user · drift alerts · inline keyboards · tax-aware logic.
