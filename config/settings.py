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
    # 千帆视觉：仅金额歧义预判断（顾问建议，不写 accepted_value）
    QIANFAN_VISION_ENABLED: str = "1"
    QIANFAN_VISION_API_URL: str = "https://qianfan.baidubce.com/v2/chat/completions"
    QIANFAN_VISION_API_KEY: str = ""
    QIANFAN_VISION_MODEL: str = "ernie-4.5-turbo-vl"
    QIANFAN_VISION_TIMEOUT_SECONDS: int = 60
    QIANFAN_VISION_MAX_IMAGE_BYTES: int = 9_500_000
    # 百度文字识别 OCR（增值税发票专精）；与千帆 bce-v3 Bearer 不同
    BAIDU_OCR_API_KEY: str = ""
    BAIDU_OCR_SECRET_KEY: str = ""
    # 金额歧义：扫描后自动增值税专精 / 视觉仲裁（仅写建议，不改 accepted_value）
    AMOUNT_AMBIGUITY_AUTO_VAT: str = "1"
    AMOUNT_AMBIGUITY_AUTO_VISION: str = "1"
    AMOUNT_AMBIGUITY_ENRICH_ON_PROCESS: str = "1"
    # llm_first：凡有 Key 即 LLM 语义抽取（推荐，少堆正则）
    # smart：启发式够用则跳过 LLM（省成本，易漏非常规表述）
    # heuristic：仅正则/启发式
    FIELD_EXTRACT_MODE: str = "llm_first"
    # 金额/合同条款批测：规则后的 LLM 辅助（0/off 关闭；无 Key 自动跳过）
    BATCH_LLM_ASSIST: str = "1"
    # 金额/截止/条款：规则结论的 LLM 解读层（不改 PASS/FAIL）
    CONCLUSION_LLM_ASSIST: str = "1"
    # PASS 是否也调模型解读（默认否，省成本）
    CONCLUSION_LLM_ON_PASS: str = "0"
    # API HITL：空/auto=随正式 OCR（禁 Mock 则开）；显式 0 关闭（批测）；1 强制开
    REQUIRE_FIELDS_CONFIRMED_API: str = "auto"
    REQUIRE_MATCHING_CONFIRMED_API: str = "auto"
    REQUIRE_CONCLUSION_CONFIRMED_API: str = "auto"
    # 正式审计模式：禁止 OCR 不可用时注入 Mock 字段；演示/本地可设 AUDIT_ALLOW_OCR_MOCK=1
    AUDIT_ALLOW_OCR_MOCK: str = "0"
    DEMO_OCR_CACHE: str = "1"
    # HITL 操作人标识（空则回退系统用户名）
    HITL_OPERATOR: str = ""
    # 证据匹配后是否自动调用 LLM 消歧（仅候选，不改终态）
    MATCHING_LLM_DISAMBIGUATION: str = "1"
    # Phase 2：候选关系 / 重复号检测（默认开启）
    ENABLE_RELATION_CANDIDATES: str = "1"
    ENABLE_DUPLICATE_DETECTION: str = "1"
    # V2 自动通过质量复核：风险抽样 + 稳定随机抽样
    QUALITY_RISK_SAMPLE_RATE: float = 0.10
    QUALITY_RANDOM_SAMPLE_RATE: float = 0.05
    QUALITY_SAMPLE_SEED: str = "v2"
    # OCR 前 L1 几何预处理（方向/纠偏/拉平；白页直通）。0=关
    AUDIT_IMAGE_PREPROCESS: str = "1"
    # Paddle 方向模型（纯英文路径）；空则仅 deskew+warp
    AUDIT_ORIENTATION_MODEL_ROOT: str = ""
    AUDIT_ORIENTATION_WORKER_PYTHON: str = ""
    AUDIT_ORIENTATION_TIMEOUT_SECONDS: float = 8.0
    AUDIT_IMAGE_PREPROCESS_RUNTIME: str = "D:/AuditImageLabRuntime/preprocess-runtime"

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
