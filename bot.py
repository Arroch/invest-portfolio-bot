import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from t_tech.invest import AsyncClient

from config import Config
from handlers import build_router
from target import load_target
from tinvest import resolve_instruments


async def main() -> None:
    cfg = Config.from_env()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("bot")

    target = load_target(cfg.target_file)
    n_filter_buckets = sum(1 for b in target.buckets if b.has_filter)
    log.info(
        "Target loaded: %d categories, %d buckets (%d explicit, %d filter), base=%s",
        len(target.categories),
        len(target.buckets),
        len(target.buckets) - n_filter_buckets,
        n_filter_buckets,
        target.base_currency,
    )

    async with AsyncClient(cfg.tinvest_token) as tinvest:
        explicit_tickers = target.explicit_tickers
        log.info("Resolving FIGIs for %d explicit-target tickers...", len(explicit_tickers))
        figi_map = await resolve_instruments(tinvest, explicit_tickers)
        for ticker, info in figi_map.items():
            log.info("  %s -> %s lot=%d %s", ticker, info.figi, info.lot, info.currency)

        bot = Bot(
            cfg.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()
        dp["tinvest"] = tinvest
        dp["account_id"] = cfg.tinvest_account_id
        dp["target"] = target
        dp["figi_map"] = figi_map
        dp.include_router(build_router(cfg.owner_chat_id))

        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Starting polling. Owner chat id: %d", cfg.owner_chat_id)
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
