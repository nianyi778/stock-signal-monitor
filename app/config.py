from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://sub2api.nianyi.dpdns.org/v1"
    scheduler_cron_hour: int = 17
    push_min_confidence: int = 60
    api_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
