from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    whisper_model_size: str = "base"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
