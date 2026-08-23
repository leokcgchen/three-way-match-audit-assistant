"""pytest 夹具：批测默认关闭独立 API 的 REQUIRE_*（工作台 job 门禁不受影响）。"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# 须在导入 app / settings 消费方之前生效；显式 0 覆盖 auto/正式默认
os.environ["REQUIRE_FIELDS_CONFIRMED_API"] = "0"
os.environ["REQUIRE_MATCHING_CONFIRMED_API"] = "0"
os.environ["REQUIRE_CONCLUSION_CONFIRMED_API"] = "0"

# 单测不得写入 D:/Dev/Temp/cutoff_jobs，否则侧栏任务清单会随每次 pytest 暴涨
os.environ["AUDIT_JOB_PERSIST"] = "0"
_PYTEST_JOB_ROOT = tempfile.mkdtemp(prefix="pytest_cutoff_jobs_")
os.environ["CUTOFF_JOB_ROOT"] = _PYTEST_JOB_ROOT
atexit.register(lambda: shutil.rmtree(_PYTEST_JOB_ROOT, ignore_errors=True))
