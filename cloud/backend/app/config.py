from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zft_api_key: str = ""
    zft_config_secret: str = ""
    zft_cors_origins: str = "http://localhost:3005,http://localhost:3006,http://localhost:8089"

                                                                              
                                                                            
                                                                              
    zft_bootstrap_admin_username: str = "admin"
    zft_bootstrap_admin_password: str = ""
    zft_token_ttl_days: int = 180
    zft_session_secret: str = ""
                                                                                             
    zft_allow_registration: bool = True
                                                                              
    zft_login_max_attempts: int = 10
    zft_login_window_seconds: int = 300
    zft_registration_max_attempts: int = 5
    zft_registration_window_seconds: int = 3600

    zft_public_hardening: bool = True
    zft_allowed_hosts: str = "*"
    zft_expose_api_docs: bool = False
    zft_max_upload_mb: int = 200
    zft_allow_private_provider_endpoints: bool = False
    zft_allow_insecure_provider_http: bool = False

    zft_timezone: str = "Asia/Shanghai"

                                                                                         
    zft_data_dir: str = "/data"
    database_url: str = "sqlite:////data/zft.db"
    zft_storage_dir: str = "/data/files"
    zft_work_dir: str = "/data/work"

                                                                                  
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
                                                                                                
    volc_access_key_id: str = ""
    volc_secret_access_key: str = ""
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""

    zft_static_dir: str = "/app/static/user"
    zft_admin_static_dir: str = "/app/static/admin"
    zft_admin_port: int = 3006

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.zft_cors_origins.split(",") if x.strip()]

    @property
    def allowed_hosts(self) -> list[str]:
        values = [x.strip() for x in self.zft_allowed_hosts.split(",") if x.strip()]
        return values or ["*"]

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
