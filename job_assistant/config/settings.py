# job_assistant/config/settings.py

"""配置加载模块：读取 config.yaml，校验并转换为强类型配置对象。

设计决策（对应 ARCHITECTURE.md 关键设计决策"配置驱动，无硬编码"）：
    - 监听群列表、NapCat 地址等全部来自配置文件，程序不写死任何群号
    - 空 monitor_group_ids 表示不监听任何群（安全兜底）
    - V1.0 不支持热加载：修改配置后需重启程序生效
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from .profile import UserProfile, parse_profile

logger = logging.getLogger(__name__)


@dataclass
class NapCatConfig:
    """NapCat 接入配置。"""

    ws_url: str = "ws://127.0.0.1:3001"   # 正向 WebSocket 服务地址（OneBot v11 事件推送）
    reconnect_interval: int = 5           # 断线自动重连间隔（秒），防止长时间空转
    history_backfill: bool = True         # 连接成功后补拉最近群消息（覆盖休眠/重启期间错过）
    backfill_count: int = 50              # 每群补拉条数上限（低频只读，防风控）
    backfill_http_url: str = "http://127.0.0.1:3002"  # NapCat HTTP 服务地址（get_group_msg_history 扩展 API）


@dataclass
class MultimodalConfig:
    """多模态处理配置（模块二）。

    设计决策（对应 ARCHITECTURE.md 非功能需求"稳定性/可配置性"）：
        - enable_ocr / enable_web_fetch 均为开关，不处理的分支直接跳过
        - 网页抓取与图片下载均设超时与大小上限，防止资源失控
    """

    enable_ocr: bool = True                # 图片 OCR 开关（需安装 paddleocr，见 requirements.txt 注释）
    enable_web_fetch: bool = True          # 网页抓取开关
    ocr_lang: str = "ch"                   # OCR 语言（ch=中英混合）
    ocr_timeout: float = 30.0              # OCR 识别超时（秒），超时标记 OcrError，资源可控
    web_timeout: float = 10.0              # 网页抓取/图片下载超时（秒）
    web_max_bytes: int = 1_048_576         # 网页响应体大小上限（字节，1MB）
    image_max_bytes: int = 5_242_880       # 图片下载大小上限（字节，5MB）


@dataclass
class DataConfig:
    """本地数据分目录配置（对应 ARCHITECTURE.md 第六章）。"""

    root_dir: str = "./data"               # 数据根目录（相对程序运行目录）
    raw_msg: str = "raw_msg"               # 原始消息文本
    image_raw: str = "image_raw"           # 下载的图片原始素材（模块二 OCR 输入）
    ocr_result: str = "ocr_result"         # 图片 OCR 识别结果
    web_content: str = "web_content"       # 抓取网页正文
    jobs: str = "jobs"                     # 结构化岗位 JSON（模块六使用）
    report: str = "report"                 # Markdown 每日日报（模块六使用）

    @property
    def paths(self) -> dict[str, Path]:
        """按配置生成全部数据目录的绝对路径（不存在时由调用方创建）。"""
        root = Path(self.root_dir)
        return {
            "raw_msg": root / self.raw_msg,
            "image_raw": root / self.image_raw,
            "ocr_result": root / self.ocr_result,
            "web_content": root / self.web_content,
            "jobs": root / self.jobs,
            "report": root / self.report,
        }


@dataclass
class LLMConfig:
    """LLM 接入配置（模块四 AI 解析 / 模块五 打分共用）。

    设计决策（对应 ARCHITECTURE.md 各层职责"LLM（DeepSeek 官方直连）"）：
        - base_url 指向 DeepSeek 官方 OpenAI 兼容 API（/chat/completions，无中间网关）
        - api_key 属敏感信息：日志禁止打印，仅在请求头中使用
        - model 留空时不发送 model 字段（由服务端使用默认模型）
    """

    base_url: str = "https://api.deepseek.com/v1"  # DeepSeek 官方 OpenAI 兼容 API 地址
    api_key: str = ""                           # API 密钥（可空，敏感信息不写日志）
    model: str = ""                             # 模型名（留空使用服务端默认）
    timeout: float = 60.0                       # 单次 LLM 调用超时（秒）


@dataclass
class CompanyResearchConfig:
    """公司外部信息联网检索配置（问题修复 5：WLB/风评/经营信息补全）。

    设计决策：
        - 检索源为公开网页（百度百科等），不依赖第三方搜索 API Key
        - 检索失败 / 超时 / 反爬时自动降级：打分禁止臆测 WLB，
          日报标注"未自动联网核验，需人工自查"（补偿方案内置）
        - max_chars 控制检索资料截断长度，防止挤占 LLM 上下文
    """

    enable: bool = True                # 联网检索开关（默认开；失败自动降级不阻断）
    timeout: float = 8.0               # 单次检索超时（秒）
    max_chars: int = 1500              # 检索资料截断长度（字符）


@dataclass
class AppConfig:
    """应用全局配置（模块一~五当前使用，后续模块扩展字段）。"""

    napcat: NapCatConfig = field(default_factory=NapCatConfig)
    monitor_group_ids: list[int] = field(default_factory=list)  # 监听群白名单；空=不监听
    multimodal: MultimodalConfig = field(default_factory=MultimodalConfig)
    data: DataConfig = field(default_factory=DataConfig)
    profile: UserProfile = field(default_factory=UserProfile)   # 模块三：用户简历&求职意向
    llm: LLMConfig = field(default_factory=LLMConfig)           # 模块四/五：LLM 接入
    company_research: CompanyResearchConfig = field(default_factory=CompanyResearchConfig)  # 模块五：公司外部信息检索
    recommend_score_threshold: int = 80                         # 模块五：推荐分数阈值（0~100）


def _require_mapping(value: Any, path: str) -> dict:
    """校验配置节点必须是映射（dict），否则抛配置错误。"""
    if not isinstance(value, dict):
        raise ConfigError(f"配置节点 {path} 必须是映射结构，实际为 {type(value).__name__}")
    return value


def _parse_group_ids(raw: Any) -> list[int]:
    """解析监听群列表：只接受正整数列表；空数组合法（表示不监听）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"monitor_group_ids 必须是列表，实际为 {type(raw).__name__}")
    group_ids: list[int] = []
    for item in raw:
        # 兼容 YAML 数字与字符串两种写法，但必须是可转正整数的值
        try:
            gid = int(item)
        except (TypeError, ValueError):
            raise ConfigError(f"群号 {item!r} 不是合法整数") from None
        if gid <= 0:
            raise ConfigError(f"群号 {gid} 必须是正整数")
        group_ids.append(gid)
    return group_ids


def _parse_bool(value: Any, path: str, default: bool) -> bool:
    """解析布尔开关：仅接受 YAML 布尔字面量，缺省用默认值。"""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{path} 必须是 true/false，实际为 {type(value).__name__}")
    return value


def _parse_positive_float(value: Any, path: str, default: float) -> float:
    """解析正数（超时秒数），缺省用默认值。"""
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{path} 必须是正数，实际为 {value!r}") from None
    if num <= 0:
        raise ConfigError(f"{path} 必须大于 0")
    return num


def _parse_positive_int(value: Any, path: str, default: int) -> int:
    """解析正整数（字节数），缺省用默认值。"""
    if value is None:
        return default
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{path} 必须是正整数，实际为 {value!r}") from None
    if num <= 0:
        raise ConfigError(f"{path} 必须大于 0")
    return num


def _parse_score_threshold(value: Any) -> int:
    """解析推荐分数阈值（模块五）：0~100 的整数，缺省用 80。

    设计决策（产品方案 F6）：阈值用于划分推荐岗位（≥ 阈值）与
    仅存档岗位（< 阈值），必须限制在 0~100 分数区间内。
    仅接受 YAML 整数（拒绝浮点/字符串/布尔，杜绝静默截断与强转）。
    """
    if value is None:
        return 80
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"recommend_score_threshold 必须是 0~100 的整数，实际为 {value!r}")
    if not 0 <= value <= 100:
        raise ConfigError(f"recommend_score_threshold 必须在 0~100 之间，实际为 {value}")
    return value


def _parse_multimodal(root: dict) -> MultimodalConfig:
    """解析多模态处理配置（模块二）；旧配置缺省该段时使用默认值。"""
    raw = _require_mapping(root.get("multimodal", {}), "multimodal")

    # ocr_lang 必须是字符串，避免 YAML 数字被静默强转
    ocr_lang_raw = raw.get("ocr_lang", "ch")
    if ocr_lang_raw is None:
        ocr_lang = "ch"
    elif isinstance(ocr_lang_raw, str):
        ocr_lang = ocr_lang_raw.strip()
    else:
        raise ConfigError(f"multimodal.ocr_lang 必须是字符串，实际为 {type(ocr_lang_raw).__name__}")
    if not ocr_lang:
        raise ConfigError("multimodal.ocr_lang 不能为空")

    return MultimodalConfig(
        enable_ocr=_parse_bool(raw.get("enable_ocr"), "multimodal.enable_ocr", True),
        enable_web_fetch=_parse_bool(raw.get("enable_web_fetch"), "multimodal.enable_web_fetch", True),
        ocr_lang=ocr_lang,
        ocr_timeout=_parse_positive_float(raw.get("ocr_timeout"), "multimodal.ocr_timeout", 30.0),
        web_timeout=_parse_positive_float(raw.get("web_timeout"), "multimodal.web_timeout", 10.0),
        web_max_bytes=_parse_positive_int(raw.get("web_max_bytes"), "multimodal.web_max_bytes", 1_048_576),
        image_max_bytes=_parse_positive_int(raw.get("image_max_bytes"), "multimodal.image_max_bytes", 5_242_880),
    )


def _parse_data(root: dict) -> DataConfig:
    """解析本地数据分目录配置（ARCHITECTURE.md 第六章）。"""
    raw = _require_mapping(root.get("data", {}), "data")

    def sub_dir(key: str, default: str) -> str:
        value = str(raw.get(key, default)).strip()
        if not value:
            raise ConfigError(f"data.{key} 不能为空")
        return value

    root_dir = str(raw.get("root_dir", "./data")).strip() or "./data"
    return DataConfig(
        root_dir=root_dir,
        raw_msg=sub_dir("raw_msg", "raw_msg"),
        image_raw=sub_dir("image_raw", "image_raw"),
        ocr_result=sub_dir("ocr_result", "ocr_result"),
        web_content=sub_dir("web_content", "web_content"),
        jobs=sub_dir("jobs", "jobs"),
        report=sub_dir("report", "report"),
    )


def _parse_llm(root: dict) -> LLMConfig:
    """解析 LLM 接入配置（模块四/五）；旧配置缺省该段时使用默认值。"""
    raw = _require_mapping(root.get("llm", {}), "llm")

    base_url = str(raw.get("base_url", "https://api.deepseek.com/v1")).strip()
    if not base_url:
        raise ConfigError("llm.base_url 不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"llm.base_url 必须以 http:// 或 https:// 开头: {base_url}")

    # api_key / model 仅接受字符串，避免 YAML 数字被静默强转
    api_key_raw = raw.get("api_key", "")
    if api_key_raw is None:
        api_key = ""
    elif isinstance(api_key_raw, str):
        api_key = api_key_raw.strip()
    else:
        raise ConfigError(f"llm.api_key 必须是字符串，实际为 {type(api_key_raw).__name__}")

    model_raw = raw.get("model", "")
    if model_raw is None:
        model = ""
    elif isinstance(model_raw, str):
        model = model_raw.strip()
    else:
        raise ConfigError(f"llm.model 必须是字符串，实际为 {type(model_raw).__name__}")

    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=_parse_positive_float(raw.get("timeout"), "llm.timeout", 60.0),
    )


def _parse_company_research(root: dict) -> CompanyResearchConfig:
    """解析公司外部信息检索配置（模块五，问题修复 5）；旧配置缺省该段时使用默认值。"""
    raw = _require_mapping(root.get("company_research", {}), "company_research")
    return CompanyResearchConfig(
        enable=_parse_bool(raw.get("enable"), "company_research.enable", True),
        timeout=_parse_positive_float(raw.get("timeout"), "company_research.timeout", 8.0),
        max_chars=_parse_positive_int(raw.get("max_chars"), "company_research.max_chars", 1500),
    )


def load_config(path: str | Path) -> AppConfig:
    """从 YAML 文件加载并校验配置。

    Args:
        path: config.yaml 的路径。

    Returns:
        校验通过的 AppConfig 对象。

    Raises:
        ConfigError: 文件缺失、格式非法或字段校验失败（统一错误码 CFG.LOAD.001）。
    """
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise ConfigError(f"配置文件不存在: {cfg_path}")

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 语法错误: {e}") from e

    if raw is None:
        raw = {}
    root = _require_mapping(raw, "根节点")

    napcat_raw = _require_mapping(root.get("napcat", {}), "napcat")
    backfill_http_url = str(napcat_raw.get("backfill_http_url", "http://127.0.0.1:3002")).strip()
    if not backfill_http_url.startswith(("http://", "https://")):
        raise ConfigError(f"napcat.backfill_http_url 必须以 http:// 或 https:// 开头: {backfill_http_url}")
    napcat = NapCatConfig(
        ws_url=str(napcat_raw.get("ws_url", "ws://127.0.0.1:3001")),
        reconnect_interval=int(napcat_raw.get("reconnect_interval", 5)),
        history_backfill=_parse_bool(napcat_raw.get("history_backfill"), "napcat.history_backfill", True),
        backfill_count=_parse_positive_int(napcat_raw.get("backfill_count"), "napcat.backfill_count", 50),
        backfill_http_url=backfill_http_url,
    )
    if not napcat.ws_url.startswith(("ws://", "wss://")):
        raise ConfigError(f"napcat.ws_url 必须以 ws:// 或 wss:// 开头: {napcat.ws_url}")

    monitor_group_ids = _parse_group_ids(root.get("monitor_group_ids"))
    multimodal = _parse_multimodal(root)
    data = _parse_data(root)
    profile = parse_profile(root)   # 模块三：用户简历 & 求职意向
    llm = _parse_llm(root)          # 模块四/五：LLM 接入（DeepSeek 官方直连）
    company_research = _parse_company_research(root)  # 模块五：公司外部信息检索
    recommend_score_threshold = _parse_score_threshold(root.get("recommend_score_threshold"))  # 模块五

    cfg = AppConfig(
        napcat=napcat,
        monitor_group_ids=monitor_group_ids,
        multimodal=multimodal,
        data=data,
        profile=profile,
        llm=llm,
        company_research=company_research,
        recommend_score_threshold=recommend_score_threshold,
    )
    logger.info(
        "配置加载完成: ws_url=%s, 监听群数量=%d, reconnect_interval=%ds, "
        "enable_ocr=%s, enable_web_fetch=%s, data_root=%s, llm_base_url=%s, "
        "company_research=%s, recommend_score_threshold=%d",
        napcat.ws_url, len(monitor_group_ids), napcat.reconnect_interval,
        multimodal.enable_ocr, multimodal.enable_web_fetch, data.root_dir,
        llm.base_url, "开" if company_research.enable else "关",
        recommend_score_threshold,
    )
    return cfg
