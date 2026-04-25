from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    kokoro_voice: str = "af_heart"
    kokoro_lang: str = "en-us"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
