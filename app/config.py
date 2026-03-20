from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_api_key: str = ""
    scheduler_cron_hour: int = 17

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
