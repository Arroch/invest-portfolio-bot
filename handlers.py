import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from t_tech.invest import AsyncClient

from formatting import format_error, format_portfolio, format_rebalance
from portfolio import build_portfolio_state
from rebalance import compute_drift, suggest_buys
from target import Target
from tinvest import Instrument

log = logging.getLogger(__name__)


def build_router(owner_chat_id: int) -> Router:
    router = Router(name="portfolio")
    router.message.filter(F.from_user.id == owner_chat_id)

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "\U0001f44b I track your T-Invest portfolio against target.yaml.\n\n"
            "Commands:\n"
            "/portfolio — current state vs target\n"
            "/rebalance — distribute free cash across underweight positions\n"
            "/rebalance &lt;amount&gt; — distribute a fresh amount\n"
            "/help — this message\n\n"
            "<i>Read-only: I never place orders.</i>"
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await cmd_start(message)

    @router.message(Command("portfolio"))
    async def cmd_portfolio(
        message: Message,
        tinvest: AsyncClient,
        account_id: str,
        target: Target,
        figi_map: dict[str, Instrument],
    ) -> None:
        try:
            state = await build_portfolio_state(tinvest, account_id, target, figi_map)
            cat_drifts, _ = compute_drift(state, target)
            await message.answer(format_portfolio(state, cat_drifts))
        except Exception as e:
            log.exception("portfolio command failed")
            await message.answer(format_error(f"Portfolio fetch failed: {e}"))

    @router.message(Command("rebalance"))
    async def cmd_rebalance(
        message: Message,
        command: CommandObject,
        tinvest: AsyncClient,
        account_id: str,
        target: Target,
        figi_map: dict[str, Instrument],
    ) -> None:
        extra_cash = Decimal(0)
        if command.args:
            arg = command.args.strip().replace(" ", "").replace(",", ".")
            try:
                extra_cash = Decimal(arg)
            except InvalidOperation:
                await message.answer(format_error(f"Cannot parse amount: {command.args!r}"))
                return
            if extra_cash < 0:
                await message.answer(format_error("Amount must be non-negative"))
                return
        try:
            state = await build_portfolio_state(tinvest, account_id, target, figi_map)
            result = suggest_buys(state, target, extra_cash)
            await message.answer(format_rebalance(result, target.base_currency))
        except Exception as e:
            log.exception("rebalance command failed")
            await message.answer(format_error(f"Rebalance failed: {e}"))

    return router
