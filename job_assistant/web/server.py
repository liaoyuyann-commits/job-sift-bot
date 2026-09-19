# job_assistant/web/server.py

"""V2.0 Web 前台 UI：轻量本地 Web 服务（Python 标准库，零第三方依赖）。

产品方案第七章 / ARCHITECTURE.md V2.0 技术方案：
    - 项目内新增轻量 Web 服务（Python 标准库/轻量框架），原生 HTML + 少量 JS
    - 复用 V1.0 全部后端逻辑与文件存储，不引入外部 Web 框架
    - 子页面：岗位看板 / 日报预览 / 实时日志 / 状态与配置（配置只读回显）

启动（项目根目录）：
    python -m job_assistant.web.server [--port 8080]

路由：
    GET /                    前端单页（static/index.html）
    GET /api/status          系统状态（进程/监听群/LLM/数据统计）
    GET /api/jobs            岗位列表（?q=关键词 &date=YYYY-MM-DD &recommended=1&sort=score|time）
    POST /api/jobs           手动录入就业信息（与自动抓取共用打分/去重/落盘链路）
    GET /api/reports         日报文件列表
    GET /api/report?date=    日报内容（Markdown 文本）
    GET /api/logs?lines=200  运行日志尾部（自动 UTF-8/GBK 探测）
    GET /api/config          config.yaml 只读回显（api_key 脱敏）
    PUT /api/config          在线更新 config.yaml 允许字段（问题修复 2）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录 = 本文件上三级（job_assistant/web/server.py → 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
JOBS_DIR = DATA_DIR / "jobs"
REPORT_DIR = DATA_DIR / "report"
LOG_FILE = PROJECT_ROOT / "run.err.log"
STATIC_FILE = Path(__file__).resolve().parent / "static" / "index.html"
CONFIG_FILE = PROJECT_ROOT / "job_assistant" / "config" / "config.yaml"

DEFAULT_PORT = 8080


# ---------- 数据读取（复用 V1.0 存储格式，不改底层结构） ----------

def read_log_tail(lines: int = 200) -> str:
    """读取运行日志尾部 N 行（UTF-8 优先，失败回退 GBK）。"""
    if not LOG_FILE.exists():
        return "（暂无日志文件：主程序未启动或尚未产生输出）"
    raw = LOG_FILE.read_bytes()
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    parts = text.splitlines()
    return "\n".join(parts[-max(1, lines):])


def load_jobs() -> list[dict]:
    """读取全部岗位存档记录（与 JobRepository.load_all 同格式，损坏文件跳过）。

    每条记录附加 file 字段（所属 JSON 文件名），供删除/定位使用。
    """
    records: list[dict] = []
    if not JOBS_DIR.is_dir():
        return records
    for path in sorted(JOBS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["file"] = path.name
                records.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return records


def delete_job(filename: str) -> dict:
    """删除一条岗位记录（用户诉求：投完的岗位手动删除）。

    安全策略（防路径穿越）：仅接受 JOBS_DIR 下的 *.json 文件名（basename 白名单）；
    删除 = 移动到 data/jobs_deleted_backup_<时间戳>/（不物理删除，可恢复）。
    """
    name = (filename or "").strip()
    if not name or name != Path(name).name or not name.endswith(".json"):
        return {"ok": False, "error": "非法文件名"}
    path = JOBS_DIR / name
    if not path.is_file():
        return {"ok": False, "error": f"岗位不存在: {name}"}
    backup_dir = DATA_DIR / f"jobs_deleted_backup_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        path.rename(backup_dir / name)
    except OSError as e:
        return {"ok": False, "error": f"删除失败: {e}"}
    logger.info("手动删除岗位: %s → %s", name, backup_dir.name)
    return {"ok": True, "deleted": name, "backup": backup_dir.name}


def list_reports() -> list[dict]:
    """日报文件列表（按文件名倒序，即最新在前）。"""
    items: list[dict] = []
    if not REPORT_DIR.is_dir():
        return items
    for path in sorted(REPORT_DIR.glob("*.md"), reverse=True):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        items.append({
            "name": path.name,
            "date": path.stem,
            "size": size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime)
            .isoformat(timespec="seconds") if path.exists() else "",
        })
    return items


def read_report(day: str) -> str | None:
    """按日期读取日报正文；路径仅允许 YYYY-MM-DD 白名单，防目录穿越。"""
    if not day or len(day) != 10:
        return None
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    path = REPORT_DIR / f"{day}.md"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_config() -> dict:
    """读取 config.yaml 只读回显（api_key 脱敏；解析失败返回原始文本 + 错误标记）。"""
    try:
        import yaml  # PyYAML 为项目既有依赖（V1.0 settings 使用）
        text = CONFIG_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            llm = data.get("llm")
            if isinstance(llm, dict) and llm.get("api_key"):
                llm["api_key"] = "sk-****（已脱敏）"  # 敏感信息禁止回显到前端
        return {"ok": True, "data": data if isinstance(data, dict) else {}}
    except Exception as e:  # noqa: BLE001 - 配置页需要向前端返回可读错误
        return {"ok": False, "error": str(e)}


def save_config(payload: dict) -> dict:
    """在线更新 config.yaml 的允许字段（问题修复 2：配置面板在线修改）。

    支持字段：monitor_group_ids / recommend_score_threshold / user_resume /
    user_intention / multimodal.enable_ocr / multimodal.enable_web_fetch。

    实现策略：yaml 解析原文件 → 校验并更新允许字段 → 备份原文件
    （config.yaml.bak）→ 写回。说明：
        - 写回会归一化 YAML 格式（原文件注释会丢失），属预期行为；
        - 主程序配置为启动时加载（V1.0 不支持热加载），保存后需重启主程序生效。
    """
    try:
        import yaml
        text = CONFIG_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读取配置失败: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "配置文件根节点不是映射结构"}

    # monitor_group_ids：正整数列表
    if "monitor_group_ids" in payload:
        raw = payload["monitor_group_ids"]
        if not isinstance(raw, list):
            return {"ok": False, "error": "monitor_group_ids 必须是列表"}
        groups: list[int] = []
        for item in raw:
            try:
                gid = int(item)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"群号 {item!r} 不是合法整数"}
            if gid <= 0:
                return {"ok": False, "error": f"群号 {gid} 必须是正整数"}
            groups.append(gid)
        data["monitor_group_ids"] = groups

    # recommend_score_threshold：0~100 整数
    if "recommend_score_threshold" in payload:
        try:
            threshold = int(payload["recommend_score_threshold"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "recommend_score_threshold 必须是整数"}
        if not 0 <= threshold <= 100:
            return {"ok": False, "error": "recommend_score_threshold 必须在 0~100 之间"}
        data["recommend_score_threshold"] = threshold

    # user_resume / user_intention：字符串（空串=清空）
    for key, label in (("user_resume", "用户简历"), ("user_intention", "求职意向")):
        if key in payload:
            value = payload[key]
            if value is None:
                data[key] = ""
            elif isinstance(value, str):
                data[key] = value
            else:
                return {"ok": False, "error": f"{key}（{label}）必须是字符串"}

    # multimodal.enable_ocr / enable_web_fetch：布尔
    multimodal = data.setdefault("multimodal", {})
    if not isinstance(multimodal, dict):
        return {"ok": False, "error": "multimodal 配置节点不是映射结构"}
    for key in ("enable_ocr", "enable_web_fetch"):
        if key in payload:
            if not isinstance(payload[key], bool):
                return {"ok": False, "error": f"{key} 必须是 true/false"}
            multimodal[key] = payload[key]

    # 备份原文件后写回（保留可审计的回滚点）
    try:
        if CONFIG_FILE.exists():
            backup = CONFIG_FILE.with_name(CONFIG_FILE.name + ".bak")
            backup.write_text(CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        CONFIG_FILE.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as e:
        return {"ok": False, "error": f"写回配置失败: {e}"}

    # 推荐阈值变化时：全量重算已存档岗位的推荐标记（用户诉求：改一次阈值比对全部岗位）
    if "recommend_score_threshold" in payload:
        try:
            from ..storage.repository import recompute_recommended
            recalc = recompute_recommended(JOBS_DIR, int(data["recommend_score_threshold"]))
        except Exception as e:  # noqa: BLE001 - 重算失败不影响配置保存
            logger.warning("推荐标记全量重算失败: %s", e)
            recalc = {"scanned": 0, "updated": 0, "failed": -1}
        result = {"ok": True, "data": data, "recalc": recalc}
    else:
        result = {"ok": True, "data": data}
    return result


def save_manual_job(payload: dict) -> dict:
    """手动录入一条就业信息（POST /api/jobs）。

    设计（与自动抓取链路完全一致，保证 UI/日报/去重行为统一）：
        - 必填：公司名称 + 岗位名称（缺任一返回错误）
        - 组装 JobPosting（category=recruitment，source=手动录入，群/消息溯源为 0）
        - 复用 JobScorer 走 LLM 打分；LLM 不可用/打分失败时降级为
          “未打分”记录落盘（score=0、recommended=False），绝不丢失用户录入
        - 复用 JobRepository 去重 + 落盘（文件名 g0_m0 前缀区分手动录入）
        - 扩展字段（薪资 salary / 备注 note）写入存档，不影响既有字段

    Returns:
        {"ok": True, "score":…, "recommended":…, "deduped":…, "record":…}
        或 {"ok": False, "error": "…"}
    """
    from ..config.settings import load_config
    from ..matcher.scorer import JobScorer, MatchResult
    from ..parser.llm_client import HttpLLMClient
    from ..parser.schema import JobPosting, MISSING, MessageCategory
    from ..storage.repository import JobRepository

    company = str(payload.get("company_name") or "").strip()
    job_title = str(payload.get("job_title") or "").strip()
    if not company or not job_title:
        return {"ok": False, "error": "公司名称和岗位名称必填"}

    def _field(key: str) -> str:
        value = payload.get(key)
        text = str(value).strip() if value is not None else ""
        return text if text else MISSING

    salary = str(payload.get("salary") or "").strip()
    note = str(payload.get("note") or "").strip()
    job = JobPosting(
        company_name=company,
        job_title=job_title,
        location=_field("location"),
        education=_field("education"),
        tech_stack=_field("tech_stack"),
        apply_method=_field("apply_method"),
        deadline=_field("deadline"),
        source="手动录入",
        category=MessageCategory.RECRUITMENT,
        group_id=0,
        message_id=0,
    )

    # LLM 打分（延迟导入保持模块级零第三方依赖）；失败降级为未打分记录
    cfg = load_config(CONFIG_FILE)
    threshold = cfg.recommend_score_threshold
    match: MatchResult | None = None
    try:
        llm = HttpLLMClient(cfg.llm)
        scorer = JobScorer(llm, cfg.profile, threshold=threshold)
        match = scorer.score(job)
    except Exception as e:  # noqa: BLE001 - 打分异常降级，不阻断手动录入
        logger.warning("手动录入打分异常，降级为未打分记录: %s", e)
        match = None
    if match is None or match.has_error:
        match = MatchResult(
            job=job, score=0, threshold=threshold,
            reason="手动录入（LLM 打分暂不可用，需人工评估）",
        )

    extra = {}
    if salary:
        extra["salary"] = salary
    if note:
        extra["note"] = note
    try:
        repo = JobRepository(JOBS_DIR)
        saved = repo.save_job(match, extra=extra)
    except Exception as e:  # noqa: BLE001 - 未预期异常统一兜底
        return {"ok": False, "error": f"归档失败: {e}"}

    if not saved:
        # 区分“去重跳过”与“写盘失败”：同名记录已存在 = 去重；否则为写盘异常
        duplicate = any(
            str(r.get("company_name", "")) == company
            and str(r.get("job_title", "")) == job_title
            for r in load_jobs()
        )
        if duplicate:
            return {"ok": True, "deduped": True, "duplicate": True,
                    "score": None, "recommended": False}
        return {"ok": False, "error": "岗位已存在或写入失败，请检查后重试"}

    record = match.job.to_dict()
    record.update({
        "score": match.score,
        "reason": match.reason,
        "threshold": threshold,
        "recommended": match.is_recommended,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "source": "手动录入",
    })
    if salary:
        record["salary"] = salary
    if note:
        record["note"] = note
    logger.info(
        "手动录入岗位: %s score=%d %s", job.brief(), match.score,
        "推荐" if match.is_recommended else "仅存档",
    )
    return {"ok": True, "deduped": False, "duplicate": False,
            "score": match.score, "recommended": match.is_recommended,
            "record": record}


# ---------- HTTP 处理 ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "JobAssistantWeb/0.1"

    # ---- 工具 ----

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    def _text(self, text: str, status: int = 200) -> None:
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _html(self, text: str) -> None:
        self._send(200, text.encode("utf-8"), "text/html; charset=utf-8")

    def _not_found(self, msg: str = "Not Found") -> None:
        self._json({"success": False, "error": msg})

    def _query(self) -> dict[str, str]:
        return urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query,
            keep_blank_values=True,
        )

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802 - http.server 协议命名
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._serve_index()
            elif path == "/api/status":
                self._api_status()
            elif path == "/api/jobs":
                self._api_jobs()
            elif path == "/api/reports":
                self._json({"success": True, "data": list_reports()})
            elif path == "/api/report":
                self._api_report()
            elif path == "/api/logs":
                self._api_logs()
            elif path == "/api/config":
                self._json({"success": True, **read_config()})
            else:
                self._not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前断开：静默忽略
        except Exception as e:  # noqa: BLE001 - 服务端异常统一兜底
            try:
                self._json({"success": False, "error": f"服务端异常: {e}"})
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802 - http.server 协议命名
        """手动录入就业信息（POST /api/jobs，字段与自动抓取链路对齐）。"""
        if urllib.parse.urlparse(self.path).path != "/api/jobs":
            self._not_found()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65_536:
                self._json({"success": False, "error": "请求体过大（>64KB）"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                self._json({"success": False, "error": "请求体必须是 JSON 对象"})
                return
            result = save_manual_job(payload)
            self._json({"success": bool(result.get("ok")), **result})
        except json.JSONDecodeError as e:
            self._json({"success": False, "error": f"请求体不是合法 JSON: {e}"})
        except Exception as e:  # noqa: BLE001 - 服务端异常统一兜底
            self._json({"success": False, "error": f"手动录入异常: {e}"})

    def do_PUT(self) -> None:  # noqa: N802 - http.server 协议命名
        """在线更新 config.yaml 允许字段（问题修复 2：配置面板在线修改）。"""
        if urllib.parse.urlparse(self.path).path != "/api/config":
            self._not_found()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65_536:
                self._json({"success": False, "error": "请求体过大（>64KB）"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                self._json({"success": False, "error": "请求体必须是 JSON 对象"})
                return
            result = save_config(payload)
            self._json({"success": bool(result.get("ok")), **result})
        except json.JSONDecodeError as e:
            self._json({"success": False, "error": f"请求体不是合法 JSON: {e}"})
        except Exception as e:  # noqa: BLE001 - 服务端异常统一兜底
            self._json({"success": False, "error": f"保存配置异常: {e}"})

    def do_DELETE(self) -> None:  # noqa: N802 - http.server 协议命名
        """删除岗位记录（DELETE /api/jobs?file=文件名，移入备份目录可恢复）。"""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/jobs":
            self._not_found()
            return
        try:
            filename = (self._query().get("file") or [""])[0].strip()
            result = delete_job(filename)
            self._json({"success": bool(result.get("ok")), **result})
        except Exception as e:  # noqa: BLE001 - 服务端异常统一兜底
            self._json({"success": False, "error": f"删除岗位异常: {e}"})

    # ---- 各接口实现 ----

    def _serve_index(self) -> None:
        if STATIC_FILE.is_file():
            self._html(STATIC_FILE.read_text(encoding="utf-8"))
        else:
            self._text("前端页面缺失: " + str(STATIC_FILE), 500)

    def _api_status(self) -> None:
        jobs = load_jobs()
        recommended = [r for r in jobs if r.get("recommended")]
        companies = {str(r.get("company_name", "")).strip()
                     for r in jobs if r.get("company_name")}
        # 监听群：从配置读取
        groups: list = []
        cfg = read_config()
        if cfg.get("ok"):
            groups = cfg["data"].get("monitor_group_ids") or []
        self._json({
            "success": True,
            "data": {
                "time": datetime.now().isoformat(timespec="seconds"),
                "job_count": len(jobs),
                "recommended_count": len(recommended),
                "company_count": len([c for c in companies if c]),
                "report_count": len(list_reports()),
                "monitor_groups": [str(g) for g in groups],
                "llm_base_url": (cfg.get("data") or {}).get("llm", {}).get("base_url", ""),
                "log_size": LOG_FILE.stat().st_size if LOG_FILE.exists() else 0,
            },
        })

    def _api_jobs(self) -> None:
        q = self._query()
        records = load_jobs()

        # 关键词过滤（公司/岗位/城市/技术栈/理由）
        keyword = (q.get("q") or [""])[0].strip()
        if keyword:
            kw = keyword.lower()
            records = [r for r in records if keyword in str(r.get("company_name", ""))
                       or keyword in str(r.get("job_title", ""))
                       or keyword in str(r.get("location", ""))
                       or keyword in str(r.get("tech_stack", ""))
                       or keyword in str(r.get("reason", ""))]

        # 日期过滤（YYYY-MM-DD，按文件名日期前缀）
        day = (q.get("date") or [""])[0].strip()
        if day:
            records = [r for r in records
                       if str(r.get("recorded_at", "")).startswith(day)]

        # 推荐/低分过滤
        rec = (q.get("recommended") or [""])[0].strip()
        if rec == "1":
            records = [r for r in records if r.get("recommended")]
        elif rec == "0":
            records = [r for r in records if not r.get("recommended")]

        # 排序：score 降序（默认）或 recorded_at 降序
        sort = (q.get("sort") or ["score"])[0].strip()
        if sort == "time":
            records.sort(key=lambda r: str(r.get("recorded_at", "")), reverse=True)
        else:
            records.sort(key=lambda r: float(r.get("score", -1)), reverse=True)

        self._json({"success": True, "count": len(records), "data": records})

    def _api_report(self) -> None:
        day = (self._query().get("date") or [""])[0].strip()
        content = read_report(day)
        if content is None:
            self._json({"success": False, "error": f"日报不存在: {day or '(未指定日期)'}"})
        else:
            self._json({"success": True, "date": day, "content": content})

    def _api_logs(self) -> None:
        try:
            lines = int((self._query().get("lines") or ["200"])[0])
            lines = max(1, min(lines, 2000))
        except ValueError:
            lines = 200
        # 日志等级筛选（问题修复 2：实时日志等级筛选）：?level=INFO|WARN|ERROR|DEBUG
        level = (self._query().get("level") or [""])[0].strip().upper()
        content = read_log_tail(lines)
        if level and level in ("INFO", "WARN", "ERROR", "DEBUG", "WARNING"):
            tag = f"[{level}]"
            if level == "WARNING":
                tag = "[WARN]"
            content = "\n".join(
                line for line in content.splitlines() if tag in line.upper()
            )
        self._json({"success": True, "lines": lines, "level": level, "content": content})


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 求职信息智能筛选助手 · V2.0 Web UI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1，仅本机访问）")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[web] V2.0 Web UI 已启动: http://{args.host}:{args.port} "
          f"(数据目录: {DATA_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] 已停止", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
