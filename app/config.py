from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    max_file_size_mb: int = 500
    temp_dir: str = "/tmp/gijiroku"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
