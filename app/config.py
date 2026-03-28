from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://sub2api.nianyi.dpdns.org/v1"
    llm_model_signal: str = "gpt-4o-mini"        # 信号摘要（便宜，每日大量调用）
    llm_model_analysis: str = "gpt-4.1"           # 个股分析 + 经济日历（需要强推理）
    alpha_vantage_api_key: str = ""
    scheduler_cron_hour: int = 17
    push_min_confidence: int = 60
    api_secret: str = ""
    portfolio_value: float = 0.0
    enable_debate: bool = True          # 推送前多空辩论过滤（3次LLM调用/信号）

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
