from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    sd_base_url: str = "http://host.docker.internal:5000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
