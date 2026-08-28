from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zft_api_key: str = "change-me"
    zft_config_secret: str = ""
    zft_cors_origins: str = "http://localhost:3002,http://localhost:3005,http://localhost:8089"

    # Single-container persistence. Everything below /data should be mounted to a volume.
    zft_data_dir: str = "/data"
    database_url: str = "sqlite:////data/zft.db"
    zft_storage_dir: str = "/data/files"
    zft_work_dir: str = "/data/work"

    # Runtime bootstrap defaults. They are persisted to SQLite after first launch.
    zft_max_active_jobs: int = 1
    babeldoc_qps: int = 1
    babeldoc_pool_max_workers: int = 1
    babeldoc_multi_pool_max_workers: int = 12
    babeldoc_aggregate_qps_cap: int = 100
    babeldoc_report_interval: float = 0.5
    babeldoc_max_pages_per_part: int = 50
    babeldoc_skip_scanned_detection: bool = False
    babeldoc_auto_ocr_workaround: bool = True

    zft_translator_provider: str = "openai_compatible"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    baidu_app_id: str = ""
    baidu_secret_key: str = ""
    baidu_endpoint: str = "https://fanyi-api.baidu.com/api/trans/vip/translate"

    tencent_tokenhub_api_key: str = ""
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    volc_api_key: str = ""
    # Deprecated v1.4.0 bootstrap fields are kept only so old .env files do not fail validation.
    volc_access_key_id: str = ""
    volc_secret_access_key: str = ""
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""

    zft_static_dir: str = "/app/static"

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.zft_cors_origins.split(",") if x.strip()]

    @property
    def data_dir(self) -> Path:
        return Path(self.zft_data_dir)

    @property
    def storage_dir(self) -> Path:
        return Path(self.zft_storage_dir)

    @property
    def work_dir(self) -> Path:
        return Path(self.zft_work_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
