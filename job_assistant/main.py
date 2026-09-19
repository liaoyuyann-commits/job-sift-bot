# job_assistant/main.py

"""程序入口：加载配置 → 模块一（群监听）→ 模块二（多模态解析）→ 模块四（AI 解析）→ 模块五（匹配打分）→ 模块六（去重归档 + 每日日报）。

当前装配模块一 ~ 模块六：消息全链路处理，运行结束时生成当日 Markdown 日报。
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from pathlib import Path

from .config.settings import CompanyResearchConfig, load_config
from .errors import JobAssistantError, StorageError
from .listener.group_filter import GroupMessage
from .listener.history_backfill import run_backfill
from .listener.napcat_client import NapCatListener
from .matcher.scorer import JobScorer
from .multimodal.processor import MultimodalProcessor
from .parser.extractor import JobExtractor
from .parser.llm_client import HttpLLMClient
from .parser.schema import MISSING
from .research.company_research import enrich_missing_fields, research_company
from .storage.repository import JobRepository, dedupe_jobs, recompute_recommended
from .storage.report import DailyReport

# 日志输出到控制台（日志规范详见 docs/日志规范.md）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def _print_recommended_job(match) -> None:
    """控制台完整输出推荐岗位（查看入口之一，产品方案 4.6）。

    展示岗位关键字段与打分理由；日志仍保持摘要，不打印全文。
    """
    job = match.job
    print("\n★ 推荐岗位 | 匹配分 %d（阈值 %d）" % (match.score, match.threshold))
    print("  公司: %s    岗位: %s" % (job.company_name, job.job_title))
    print("  地点: %s    学历: %s" % (job.location, job.education))
    print("  技术栈: %s" % job.tech_stack)
    print("  投递方式: %s    截止: %s" % (job.apply_method, job.deadline))
    print("  信息来源: %s" % job.source)
    print("  打分理由: %s" % match.reason)


def make_on_message(
    processor: MultimodalProcessor,
    extractor: JobExtractor,
    scorer: JobScorer,
    repository: JobRepository,
    llm: HttpLLMClient,
    company_research: CompanyResearchConfig | None = None,
) -> Callable[[GroupMessage], None]:
    """构造模块一消息回调：多模态解析 → AI 解析 → 岗位打分 → 去重归档。

    仅打印摘要（群号、消息 ID、类别、岗位名、分数），不打印消息/岗位全文，
    避免敏感信息进入日志（docs/日志规范.md）。
    单条消息任一环节失败仅 WARN 记录，不阻断后续消息处理。
    """

    def on_message(msg: GroupMessage) -> None:
        result = processor.process(msg)
        if result.has_errors:
            logger.warning(
                "消息处理存在失败分支: group_id=%s message_id=%s errors=%d",
                msg.group_id, msg.message_id, len(result.errors),
            )
        logger.info(
            "消息处理完成: group_id=%s message_id=%s merged_len=%d ocr=%d web=%d",
            msg.group_id, msg.message_id, len(result.merged_text),
            len(result.ocr_texts), len(result.web_texts),
        )
        if not result.merged_text.strip():
            return  # 无有效文本（纯图片失败/空消息），跳过 AI 解析

        # 模块四：消息分类 +（招聘相关）结构化抽取
        parse = extractor.parse(
            result.merged_text, group_id=msg.group_id, message_id=msg.message_id,
        )
        if parse.has_error:
            logger.warning(
                "消息 AI 解析失败: group_id=%s message_id=%s error=%s",
                msg.group_id, msg.message_id, parse.error,
            )
            return
        if parse.job is None:
            logger.info(
                "AI 解析完成（非招聘相关）: group_id=%s message_id=%s category=%s",
                msg.group_id, msg.message_id, parse.category.value,
            )
            return

        # 模块五：岗位匹配打分 + 阈值过滤（推荐完整输出 / 仅存档极简日志）
        # 问题修复 4：抽取后缺公司名/岗位名 → 先联网搜索自动补全（同时获取风评），
        #             补不上才保留【基础信息残缺】标记并跳过打分
        # 问题修复 5：打分前联网检索公司外部资料（WLB/风评/经营），检索失败自动降级不阻断
        external_info = ""
        if company_research is not None and company_research.enable:
            if parse.job.incomplete_reason:
                enrich = enrich_missing_fields(
                    result.merged_text, parse.job, llm,
                    timeout=company_research.timeout,
                    max_chars=company_research.max_chars,
                )
                if enrich:
                    external_info = enrich["external_info"]
            if parse.job.company_name != MISSING:
                extra = research_company(
                    parse.job.company_name,
                    job_title=parse.job.job_title,
                    timeout=company_research.timeout,
                    max_chars=company_research.max_chars,
                )
                if extra:
                    external_info = (
                        external_info + "\n\n" + extra if external_info else extra
                    )
        match = scorer.score(parse.job, external_info=external_info)
        if match.has_error:
            logger.warning(
                "岗位打分失败: group_id=%s message_id=%s job=%s error=%s",
                msg.group_id, msg.message_id, parse.job.brief(), match.error,
            )
            return
        if match.incomplete:
            logger.info(
                "基础信息残缺（跳过打分）: group_id=%s message_id=%s job=%s 原因=%s",
                msg.group_id, msg.message_id, parse.job.brief(),
                parse.job.incomplete_reason,
            )
        elif match.review_required:
            logger.info(
                "待人工复核（跳过打分）: group_id=%s message_id=%s job=%s 原因=%s",
                msg.group_id, msg.message_id, parse.job.brief(),
                parse.job.review_reason,
            )
        # 模块六：全量归档（推荐 + 仅存档 + 残缺/复核标记均落盘 JSON，公司+岗位去重）
        repository.save_job(match)
        if match.is_recommended:
            _print_recommended_job(match)
            logger.info(
                "推荐岗位: group_id=%s message_id=%s job=%s score=%d",
                msg.group_id, msg.message_id, parse.job.brief(), match.score,
            )
        else:
            # 仅存档岗位：控制台极简日志（产品方案 F6，完整信息只入 JSON 存档）
            logger.info(
                "仅存档岗位: group_id=%s message_id=%s job=%s score=%d（低于阈值 %d）",
                msg.group_id, msg.message_id, parse.job.brief(),
                match.score, match.threshold,
            )

    return on_message


def _generate_daily_report(repository: JobRepository, cfg) -> None:
    """模块六：运行结束时生成当日日报（读取当日全量存档中的推荐岗位）。

    日报生成失败仅记录 ERROR，不影响当日已归档的岗位数据（降级不阻断）。
    """
    try:
        result = DailyReport(cfg.data.paths["report"]).generate(
            repository, threshold=cfg.recommend_score_threshold,
        )
        logger.info(
            "日报生成完成: %s 推荐=%d 当日全量=%d",
            result.path.name, result.recommended_count, result.archived_count,
        )
    except StorageError as e:
        logger.error("日报生成失败: %s", e)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 求职信息智能筛选助手")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "config.yaml"),
        help="配置文件路径（默认 job_assistant/config/config.yaml）",
    )
    return parser.parse_args()


def _make_backfill(cfg, on_message) -> Callable[[], None]:
    """构造历史补拉回调：每次 WS 连接成功（含断线重连）执行一次。

    覆盖场景：电脑休眠 / 程序重启期间正向 WS 不推消息，恢复后补拉最近
    `backfill_count` 条，按 (群号, 消息ID) 去重后走正常消息链路。
    只读安全：仅拉取历史消息（NapCat 扩展 API），不发送任何内容。
    """

    def backfill() -> None:
        try:
            run_backfill(cfg, on_message, cfg.data.paths["raw_msg"])
        except Exception:  # noqa: BLE001 - 补拉失败仅记录，不影响监听主流程
            logger.exception("历史消息补拉异常")

    return backfill


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    if not cfg.monitor_group_ids:
        logger.warning("monitor_group_ids 为空，当前不监听任何群，程序退出")
        return

    # 启动时自动清理重复岗位（用户诉求：小助手自动去重；重复移入备份目录）
    try:
        dedupe_jobs(cfg.data.paths["jobs"])
    except Exception:  # noqa: BLE001 - 去重失败不阻断监听启动
        logger.exception("启动时岗位自动去重失败")

    # 启动时按当前阈值全量重算已存档岗位的推荐标记（用户诉求：
    # 手动改 yaml / Web 面板改阈值后，重启即与最新阈值重新比对全部岗位）
    try:
        recompute_recommended(cfg.data.paths["jobs"], cfg.recommend_score_threshold)
    except Exception:  # noqa: BLE001 - 重算失败不阻断监听启动
        logger.exception("启动时推荐标记全量重算失败")

    processor = MultimodalProcessor(cfg.multimodal, cfg.data)
    llm = HttpLLMClient(cfg.llm)
    extractor = JobExtractor(llm)
    scorer = JobScorer(llm, cfg.profile, threshold=cfg.recommend_score_threshold)
    repository = JobRepository(cfg.data.paths["jobs"])   # 模块六：岗位去重归档
    on_message = make_on_message(
        processor, extractor, scorer, repository,
        llm=llm,   # 问题修复 4：残缺字段自动搜索补全（缺公司名/岗位名 → 搜索 + LLM 抽取）
        company_research=cfg.company_research,   # 模块五：公司外部信息联网检索
    )
    listener = NapCatListener(
        cfg,
        on_message=on_message,
        on_connected=_make_backfill(cfg, on_message),   # 连接成功补拉历史（覆盖休眠/重启错过）
    )
    listener.start()
    try:
        # 主线程保持运行，等待 Ctrl+C 手动关停（启停可控，规避风控）
        while listener.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关停监听...")
    finally:
        listener.stop()
        # 模块六：运行结束生成当日日报（仅推荐岗位，按分数降序）
        _generate_daily_report(repository, cfg)
    logger.info("本次运行结束，累计运行 %.1f 秒", listener.runtime_seconds)


if __name__ == "__main__":
    try:
        main()
    except JobAssistantError as e:
        # 入口统一兜底（docs/异常处理指南.md 二.3）：业务异常只输出带错误码的
        # 中文提示并以退出码 1 退出，不向使用方暴露原始堆栈
        logger.error("程序启动/运行失败: %s", e)
        raise SystemExit(1)
