import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    tinvest_token: str
    owner_chat_id: int
    tinvest_account_id: str
    target_file: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        required = {
            "BOT_TOKEN": os.environ.get("BOT_TOKEN"),
            "TINVEST_TOKEN": os.environ.get("TINVEST_TOKEN"),
            "OWNER_CHAT_ID": os.environ.get("OWNER_CHAT_ID"),
            "TINVEST_ACCOUNT_ID": os.environ.get("TINVEST_ACCOUNT_ID"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

        try:
            owner_chat_id = int(required["OWNER_CHAT_ID"])
        except ValueError as e:
            raise RuntimeError("OWNER_CHAT_ID must be an integer (numeric Telegram user id)") from e

        return cls(
            bot_token=required["BOT_TOKEN"],
            tinvest_token=required["TINVEST_TOKEN"],
            owner_chat_id=owner_chat_id,
            tinvest_account_id=required["TINVEST_ACCOUNT_ID"],
            target_file=os.environ.get("TARGET_FILE", "./target.yaml"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
