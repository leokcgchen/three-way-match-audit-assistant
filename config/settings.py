from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BASE_DIR / ".env"

_PLACEHOLDER_KEYS = frozenset(
    {
        "",
        "your_api_key",
        "your_secret_key",
        "placeholder",
        "changeme",
        "xxx",
    }
)


def is_valid_api_credential(value: str) -> bool:
    """判断 API Key / Secret 是否为有效配置（非占位符）。"""
    text = (value or "").strip().lower()
    if not text:
        return False
    if text in _PLACEHOLDER_KEYS:
        return False
    if text.startswith("your_"):
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BASE_DIR: Path = _BASE_DIR

    DATA_DIR: Path = BASE_DIR / "data"
    MOCK_DATA_DIR: Path = DATA_DIR / "mock"
    # 相对路径相对于 BASE_DIR；也可配置绝对路径
    REPORTS_DIR: str = Field(default="reports", description="底稿输出目录")
    WORKBOOK_FILENAME: str = Field(
        default="底稿_GOSPD01010.csv", description="底稿CSV文件名"
    )
    LOGS_DIR: Path = BASE_DIR / "logs"
    LOG_LEVEL: str = "INFO"
    API_PORT: int = 8000

    # 千帆 OCR / LLM（legacy_ocr）
    QIANFAN_API_KEY: str = ""
    QIANFAN_SECRET_KEY: str = ""
    QIANFAN_ACCESS_KEY: str = ""
    QIANFAN_OCR_MODEL: str = "pp-structurev3"
    QIANFAN_OCR_TIMEOUT_MS: int = 120000
    LLM_API_URL: str = "https://qianfan.baidubce.com/v2/chat/completions"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "ernie-4.5-turbo-128k"

    def is_qianfan_configured(self) -> bool:
        """是否已配置可用的千帆凭证（bce-v3 API Key 或 AK/SK）。"""
        if is_valid_api_credential(self.QIANFAN_API_KEY) and self.QIANFAN_API_KEY.startswith(
            "bce-v3/"
        ):
            return True
        ak = self.QIANFAN_ACCESS_KEY or self.QIANFAN_API_KEY
        sk = self.QIANFAN_SECRET_KEY
        return is_valid_api_credential(ak) and is_valid_api_credential(sk)

    def get_reports_dir(self) -> Path:
        """解析后的底稿目录（自动创建由调用方负责）。"""
        path = Path(self.REPORTS_DIR)
        if not path.is_absolute():
            path = self.BASE_DIR / path
        return path

    def get_workbook_path(self) -> Path:
        """底稿 CSV 绝对路径。"""
        return self.get_reports_dir() / self.WORKBOOK_FILENAME

    def get_workbook_relative_path(self) -> str:
        """API 返回用的相对路径展示。"""
        rel = str(Path(self.REPORTS_DIR) / self.WORKBOOK_FILENAME)
        return rel.replace("\\", "/")


settings = Settings()
