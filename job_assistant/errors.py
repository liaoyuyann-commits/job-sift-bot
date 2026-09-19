# job_assistant/errors.py

"""全局异常基类：统一携带错误码，供全局异常处理与日志输出使用。

错误码格式与分类详见 docs/错误码体系.md：
    {业务域}.{模块}.{三位数字}，例如 CFG.LOAD.001
"""

from __future__ import annotations


class JobAssistantError(Exception):
    """业务异常基类：所有自定义异常的父类。

    GENERAL.UNKNOWN.000 为基类保留码（仅兜底，不允许直接抛出）；
    具体业务异常必须在子类通过 error_code 类属性设置专属错误码，
    并登记到 docs/错误码体系.md，保持代码与文档一一对应。
    """

    error_code: str = "GENERAL.UNKNOWN.000"

    def __init__(self, message: str, *, error_code: str | None = None):
        # 子类可通过 error_code 类属性预置错误码，也可实例化时显式指定
        self.error_code = error_code or self.error_code
        super().__init__(f"[{self.error_code}] {message}")


class ConfigError(JobAssistantError):
    """配置加载或校验失败（对应 docs/错误码体系.md 的 CFG 域）。"""

    error_code = "CFG.LOAD.001"


class ListenerError(JobAssistantError):
    """监听模块运行异常（对应 docs/错误码体系.md 的 LST 域）。"""

    error_code = "LST.RUN.001"


class MultimodalError(JobAssistantError):
    """多模态处理模块异常（对应 docs/错误码体系.md 的 MUL 域）。

    设计要点：模块二单条分支失败以异常形式抛出后，由编排层捕获并
    标记为"失败分支"，不阻断整体流程（见 docs/异常处理指南.md）。
    """

    error_code = "MUL.PARSE.001"


class ImageDownloadError(MultimodalError):
    """图片下载失败（网络类，可重试）。"""

    error_code = "MUL.DOWN.001"


class OcrError(MultimodalError):
    """OCR 识别失败（依赖未安装或识别报错）。"""

    error_code = "MUL.OCR.001"


class WebFetchError(MultimodalError):
    """网页抓取失败（连接超时、HTTP 错误等，可重试）。"""

    error_code = "MUL.FETCH.001"


class PageTooLargeError(WebFetchError):
    """响应体超过大小上限（抓取/下载时的资源保护）。"""

    error_code = "MUL.FETCH.002"


class ParserError(JobAssistantError):
    """解析模块异常基类（对应 docs/错误码体系.md 的 PAR 域）。

    设计要点：模块四单条消息解析失败由编排入口（JobExtractor.parse）
    捕获并写入 ParseResult.error，不阻断整体流程（见 docs/异常处理指南.md）。
    """

    error_code = "PAR.RUN.001"


class LLMCallError(ParserError):
    """LLM 调用失败（网络/超时/HTTP 非 2xx/响应结构非法，可重试）。"""

    error_code = "PAR.LLM.001"


class LLMFormatError(ParserError):
    """LLM 响应内容格式非法（非 JSON / 缺少字段，抽取阶段重试后仍失败）。"""

    error_code = "PAR.LLM.002"


class MatchError(JobAssistantError):
    """岗位匹配打分模块异常（对应 docs/错误码体系.md 的 MAT 域）。

    设计要点：模块五单条岗位打分失败由编排入口（JobScorer.score）
    捕获并写入 MatchResult.error，不阻断整体流程（见 docs/异常处理指南.md）。
    注意：LLM 调用失败复用 LLMCallError（PAR.LLM.001，LLM 客户端层共用）。
    """

    error_code = "MAT.RUN.001"


class ScoreFormatError(MatchError):
    """LLM 打分响应格式非法（非 JSON / 缺 score / score 非数字，重试后仍失败）。"""

    error_code = "MAT.SCORE.001"


class StorageError(JobAssistantError):
    """本地归档与日报模块异常（对应 docs/错误码体系.md 的 STO 域）。

    设计要点：模块六单条岗位归档失败由编排入口（JobRepository.save_job）
    捕获并标记返回，不阻断整体流程；日报生成失败由调用方（main）记录
    ERROR 后降级退出，不影响当日已归档数据（见 docs/异常处理指南.md）。
    """

    error_code = "STO.RUN.001"


class JobSaveError(StorageError):
    """岗位 JSON 归档失败（磁盘写入失败、序列化异常等，单条标记不阻断）。"""

    error_code = "STO.SAVE.001"


class ReportError(StorageError):
    """每日 Markdown 日报生成失败（磁盘写入失败等，记录 ERROR 后降级）。"""

    error_code = "STO.REPORT.001"
