# CHANGELOG.md

# 版本变更记录

本文件按 rule.md 规范维护，记录迭代改动；**面试重点：写二次开发新增的功能**。

---

## [v0.1.0] - 2026-09-17

### 新增功能

**模块一：群监听配置模块（首个实现模块，基于 ARCHITECTURE.md V1.1）**

- 配置层（`job_assistant/config/`）
  - 新增 YAML 配置加载与强类型转换，支持 `monitor_group_ids` 群白名单（1~N 个群，空数组=不监听）、`napcat.ws_url`、`reconnect_interval`
  - 配置校验：非法群号、非法 ws_url、坏 YAML、文件缺失均抛出带错误码的 `ConfigError`
  - 提供 `config.example.yaml` 示例配置
- 监听层（`job_assistant/listener/`）
  - `NapCatListener`：以客户端身份连接 NapCat 正向 WebSocket（OneBot v11），**纯只读接收，代码层面不提供任何发送能力**
  - 群 ID 白名单过滤：不在列表内的群消息直接丢弃，不进入下游流程
  - 消息规范化：输出 `GroupMessage`（消息 ID、群号、发送者、时间戳、文本、图片 URL 列表、网页链接列表、原始事件）
  - 断线自动重连（间隔可配）、手动启停、运行时长统计、收发统计（received/passed）
- 入口（`job_assistant/main.py`）
  - 支持 `--config` 指定配置文件；空群白名单安全退出；Ctrl+C 优雅停止
- 全局异常基类（`job_assistant/errors.py`）
  - `JobAssistantError`：统一携带错误码 `{业务域}.{模块}.{三位数字}`，子类 `ConfigError`、`ListenerError`

### Bug 修复

- 无（v0.1.0 为首个功能版本，无存量问题）

### Breaking Changes

- 无（首个版本，未涉及既有行为变更；后续模块实现时若有接口调整将在本节记录）

---

## [v0.2.0] - 2026-09-17

### 新增功能

**模块二：多模态消息解析模块（基于 ARCHITECTURE.md V1.1）**

- 多模态处理层（`job_assistant/multimodal/`，新增）
  - `text_branch.py`：文本直传分支，原始文本不改写直接透传
  - `ocr_engine.py`：图片下载（限量/超时）+ 本地 OCR（PaddleOCR 惰性加载），兼容 2.x/3.x 返回结构；识别失败抛 `OcrError`
  - `web_fetcher.py`：URL 网页抓取 + HTML 正文清洗（跳过 script/style，提取标题与正文），设超时与响应体大小上限，纯标准库零依赖
  - `processor.py`：三分支编排入口，输出 `MultimodalResult` 合并完整文本；**单条分支失败标记异常、不阻断整体流程**
- 配置扩展（`job_assistant/config/settings.py`、`config.example.yaml`）
  - 新增 `multimodal` 配置段：`enable_ocr` / `enable_web_fetch` / `ocr_lang` / `web_timeout` / `web_max_bytes` / `image_max_bytes`
  - 新增 `data` 配置段：数据根目录 + 6 个分目录（raw_msg / image_raw / ocr_result / web_content / jobs / report），启动时自动创建
- 入口接入（`job_assistant/main.py`）：消息回调改为 `MultimodalProcessor.process`，日志仅输出摘要（合并文本长度、各分支数量、失败数）
- 异常扩展（`job_assistant/errors.py`）：新增 `MultimodalError` 及子类 `ImageDownloadError` / `OcrError` / `WebFetchError` / `PageTooLargeError`（MUL 域错误码，详见 docs/错误码体系.md）
- 数据归档：原始消息 → `raw_msg/`，下载图片 → `image_raw/`，OCR 结果 → `ocr_result/`，网页正文 → `web_content/`（分目录隔离，便于回溯）
- 测试：`tests/test_module2_multimodal.py`（24 项单元）、`tests/test_module2_integration.py`（模拟 NapCat + 本地站点全链路）

### Bug 修复

- 无（新增模块，无存量问题）

### Breaking Changes

- 无（配置为增量新增；旧配置缺省时使用默认值，`multimodal.enable_ocr=true` 需安装 paddleocr，未安装时该分支标记异常而非崩溃）

---

## [v0.2.1] - 2026-09-17

### 修复与改进（模块二对齐产品方案 V1.4 复核）

**Bug 修复**

- `web_fetcher.fetch_url_bytes`：删除不可达的 `status >= 400` 分支（`urlopen` 对 4xx/5xx 直接抛 `HTTPError`，该分支冗余）
- `config.settings`：`multimodal.ocr_lang` 增加类型校验，YAML 中写入数字不再被静默强转

**改进**

- URL 链接提取移入模块二（`web_fetcher.extract_urls`）：按产品方案 4.2，模块二 URL 分支自行正则提取 http/https 链接，不再依赖模块一 `GroupMessage.urls` 字段
- 需登录 / JS 强渲染页面显式识别（`web_fetcher.page_skip_reason` / `fetch_web_page`）：命中登录特征或 SPA 空壳特征时按策略跳过并记录原因日志，落实产品方案 3.2 控制策略
- OCR 识别超时控制（`ocr_engine.OCREngine`）：PaddleOCR 调用放入守护线程执行并设 `ocr_timeout`（默认 30 秒），超时抛 `OcrError` 标记失败，落实产品方案"OCR 资源可控"
- 图片下载文件名加消息前缀（`g{群号}_m{消息ID}_`）：同一海报在多个群/多条消息重复出现时，`image_raw` 原始素材不再互相覆盖
- 新增配置项 `multimodal.ocr_timeout`（配置示例已同步）

**测试**

- 模块二单元测试扩充至 45 项：新增链接提取、登录/JS 渲染页识别、OCR 超时与失败、开关跳过、图片失败路径、新增配置字段校验
- 全部回归通过（模块一 17 项 + 模块二 45 项 + 集成 7 项）

### Breaking Changes

- 无（配置为增量新增，旧配置缺省使用默认值）

---

## [v0.3.0] - 2026-09-18

### 新增功能

**模块三：用户简历 & 求职意向配置模块（基于 ARCHITECTURE.md 模块三 / 产品方案 4.3）**

- 用户画像层（`job_assistant/config/profile.py`，新增）
  - `UserProfile` 数据类：`resume`（简历全文）+ `intention`（求职意向文本），程序**不预置任何固定标签与权重**
  - `parse_profile()`：从 YAML 解析 `user_resume` / `user_intention`，仅接受字符串（数字/列表/映射抛 `ConfigError`，杜绝静默强转）
  - `full_text()`：拼接「【用户简历】+【用户求职意向】」完整画像文本，作为模块五岗位匹配打分的输入
  - 缺省字段视为空：允许先配置监听群、后补填简历意向（加载时 WARN 提示）
  - **敏感信息保护**：简历/意向全文可能含手机号等隐私，日志仅记录长度，不打印内容
- 配置接入（`job_assistant/config/settings.py`、`config.example.yaml`）
  - `AppConfig` 新增 `profile` 字段，`load_config` 自动解析挂载（模块五可直接读取）
  - `config.example.yaml` 新增 `user_resume` / `user_intention` 配置段与填写模板（意向岗位/城市/可接受行业/硬性排除）
- 测试：`tests/test_module3_profile.py`（21 项：解析/类型校验/属性/full_text/load_config 集成）

### Bug 修复

- 无（新增模块，无存量问题）

### Breaking Changes

- 无（配置为增量新增，旧配置缺省 profile 字段视为空，向后兼容）

---

## [v0.4.0] - 2026-09-18

### 新增功能

**模块四：AI 岗位结构化抽取模块（基于 ARCHITECTURE.md 模块四 / 产品方案 4.4）**

- 解析层（`job_assistant/parser/`，新增）
  - `schema.py`：`MessageCategory` 消息类别枚举（宣讲/招聘/内推/闲聊广告/诈骗 + OTHER 兜底）与 `JobPosting` 结构化岗位模型（8 个业务字段 + 类别 + 群号/消息 ID 溯源）；缺失字段统一标记【信息未提及】；`to_dict` 输出字段一次性定义完整，兼容 V2.0 Web UI 读取
  - `llm_client.py`：`LLMClient` 协议 + `HttpLLMClient` 默认实现（AstrBot OpenAI 兼容 `/chat/completions`），base_url / api_key / model / timeout 全部配置驱动；model 留空不发 model 字段，api_key 仅用于请求头、日志禁止打印；测试可注入替身
  - `classifier.py`：`MessageClassifier` 消息分类（LLM 单次调用，输出 JSON 解析；无法映射归入 OTHER 兜底，不抛异常）
  - `extractor.py`：`JobExtractor` 编排入口（分类 → 招聘相关才抽取 → `ParseResult`）；抽取响应格式非法自动重试一次（`max_retries` 可配）；**单条消息解析失败写入 `ParseResult.error`，不阻断整体流程**；支持从 markdown 代码块 / 内嵌文本中提取 JSON
- 配置扩展（`job_assistant/config/settings.py`、`config.example.yaml`）
  - 新增 `llm` 配置段：`base_url` / `api_key` / `model` / `timeout`；类型校验拒绝静默强转，旧配置缺省该段使用默认值
- 入口接入（`job_assistant/main.py`）：消息回调在模块二之后接入模块四（分类 + 抽取），日志仅输出类别与岗位摘要，不打印全文
- 异常扩展（`job_assistant/errors.py`）：新增 `ParserError`（PAR 域兜底，PAR.RUN.001）及子类 `LLMCallError`（PAR.LLM.001，网络/超时/HTTP 错误，可重试）、`LLMFormatError`（PAR.LLM.002，响应格式非法，重试后仍失败），详见 docs/错误码体系.md
- 测试：`tests/test_module4_parser.py`（53 项：schema / 类别容错 / 分类 / 抽取与重试 / llm 配置校验）

### Bug 修复

- 无（新增模块，无存量问题）

### Breaking Changes

- 无（配置为增量新增，旧配置缺省 llm 段使用默认值，向后兼容）
- 行为变化提示：main.py 入口链路由「模块一 → 模块二」扩展为「模块一 → 模块二 → 模块四」；LLM 服务（AstrBot OpenAI 兼容接口）不可用时，每条消息的 AI 解析将记录 WARN 失败标记，不影响消息采集与多模态处理

---

## [v0.5.0] - 2026-09-18

### 新增功能

**模块五：岗位匹配打分模块（基于 ARCHITECTURE.md 模块五 / 产品方案 4.5、F6）**

- 匹配层（`job_assistant/matcher/`，新增）
  - `scorer.py`：`JobScorer` LLM 动态打分——输入结构化岗位（JobPosting）+ 用户画像（resume + intention），由 LLM 对比两个维度（岗位要求 vs 简历能力、岗位地点/类型 vs 求职意向），输出 **0~100 分 + 简短理由**；**取消 V1.0 固定权重（西安+30 等），完全由用户画像驱动**
  - `ranking.py`：`rank_by_score` 按分数降序稳定排序（日报用），失败项固定排末尾
  - `MatchResult`：score / reason / threshold / is_recommended（≥ 阈值=推荐）/ error 失败标记
- 阈值过滤（产品方案 F6）
  - 新增配置 `recommend_score_threshold`（默认 80，0~100 整数严格校验，拒绝浮点/字符串静默截断）
  - **分数 ≥ 阈值 → 推荐岗位**：main 控制台完整输出（公司/岗位/地点/学历/技术栈/投递/截止/理由）+ 完整日志
  - **分数 < 阈值 → 仅存档岗位**：控制台仅极简日志（岗位 + 分数），完整信息留给模块六 JSON 存档
- 容错设计（对齐"单条失败不阻断整体"）
  - 打分响应格式非法自动重试一次（`max_retries`）；LLM 调用失败/格式非法写入 `MatchResult.error`，不阻断后续消息
  - 分数越界（<0 或 >100）容错截断到 [0,100] 并记录 WARN，不视为失败
  - 用户画像未配置时前置校验跳过打分（提示填写 user_resume / user_intention），不调用 LLM
- 入口接入（`job_assistant/main.py`）：链路扩展为 模块一 → 二 → 四 → 五；推荐岗位以 `print` 业务展示完整信息，日志保持摘要
- 异常扩展（`job_assistant/errors.py`）：新增 `MatchError`（MAT.RUN.001 兜底）及子类 `ScoreFormatError`（MAT.SCORE.001）；LLM 调用失败复用 `LLMCallError`（PAR.LLM.001，LLM 客户端层模块四/五共用）
- 测试：`tests/test_module5_matcher.py`（34 项：打分解析 / 阈值边界（80 推荐、79 仅存档）/ 容错重试 / 排序 / 阈值配置校验）

### Bug 修复

- 无（新增模块，无存量问题）

### Breaking Changes

- 无（配置为增量新增，旧配置缺省 recommend_score_threshold 使用默认 80，向后兼容）
- 行为变化提示：main 入口链路由「模块一 → 二 → 四」扩展为「模块一 → 二 → 四 → 五」；LLM 不可用或用户画像未配置时，岗位打分将记录 WARN 失败标记，不影响消息采集、多模态与 AI 解析环节

---

## [v0.6.0] - 2026-09-18

### 新增功能

**模块六：本地数据归档与日报模块（基于 ARCHITECTURE.md 模块六 / 产品方案 4.6、F7、F8）**

- 存储层（`job_assistant/storage/`，新增）
  - `repository.py`：`JobRepository` 岗位归档仓储
    - **去重规则（F7）**：基于「公司名称 + 岗位名称」去重，重复岗位保留首次记录（INFO 日志记录已存在文件名）；去重索引启动时扫描历史存档重建，重启后去重仍生效
    - **存储规则**：所有打分成功的岗位（推荐 + 仅存档）全量 JSON 永久存入 `./data/jobs`，文件名 `{日期}_g{群号}_m{消息ID}_{公司}_{岗位}.json`（非法字符清洗、日期前缀供日报按当日聚合）
    - **存档记录格式稳定**：`JobPosting.to_dict()` 的 11 个字段 + 模块五打分字段（score / reason / threshold / recommended）+ 归档时间戳（recorded_at），字段只增不减，兼容 V2.0 Web UI 直接读取
    - **容错**：打分失败岗位不入档（WARN）；关键字段缺失（【信息未提及】）无法可靠去重时直接归档不丢弃；单条写盘失败标记返回、不阻断整体流程
  - `report.py`：`DailyReport` 每日日报生成器 + `ReportResult`
    - **日报规则（F8）**：只读取当日新增推荐岗位（≥ 阈值，按存档时 recommended 标记），按匹配分数降序稳定排序，附带分数、打分理由、信息来源、消息来源溯源；低分岗位不写入日报
    - **生成时机**：V1.0 手动启停，每次运行结束时生成（读取当日全量存档），同一日多次运行覆盖为最新日报；空日报兜底（0 推荐仍落盘并提示）
    - 容错：损坏 JSON 跳过并 WARN；写入失败抛 `ReportError`（STO.REPORT.001），由 main 记录 ERROR 后降级退出
- 异常扩展（`job_assistant/errors.py`）：新增 `StorageError`（STO.RUN.001 兜底）及子类 `JobSaveError`（STO.SAVE.001，单条归档失败标记不阻断）、`ReportError`（STO.REPORT.001，日报生成失败降级）
- 入口接入（`job_assistant/main.py`）：链路扩展为 模块一 → 二 → 四 → 五 → 六；打分成功后 `repository.save_job(match)` 全量归档；`finally` 中停止监听后生成当日日报
- 测试：`tests/test_module6_storage.py`（48 项：归档/去重/索引重建/文件名/读取/日报/空日报/错误码）

### Bug 修复

- 无（新增模块，无存量问题）

### Breaking Changes

- 无（配置无新增项，复用既有 `data.jobs` / `data.report` 目录配置，向后兼容）
- 行为变化提示：main 入口链路由「模块一 → 二 → 四 → 五」扩展为「模块一 → 二 → 四 → 五 → 六」；运行结束会在 `./data/report/{当日日期}.md` 生成日报；`./data/jobs` 目录出现全量岗位 JSON 存档（打分成功即落盘）

---

## [v0.6.1] - 2026-09-18

### 新增功能

**全模块开发完毕后的文档补齐与规范统一（基于 ARCHITECTURE.md v1.5 / 产品方案 V1.4）**

- README.md 补齐重写（文档版本 v1.5）
  - 新增真实「项目结构」目录树与模块 → 代码包对应关系；核心特性对齐 v0.6.0 全链路现状（含阈值过滤、去重、日报、统一错误码体系）
  - 「快速开始」整理为 7 步并逐项实测：环境准备（NapCat/AstrBot 要求）→ 依赖安装（含可选 OCR）→ 复制配置 + **配置项核对表** → 启动顺序 → 停止与产物查看 → 8 个测试文件自测（附 PowerShell 一键命令）→ 常见问题表（错误码现象 → 处理）
  - 「文档索引」按根目录 / docs 分组，每篇标注内容与适用场景，新增推荐阅读路径；补充 data/ 六层产物目录说明与风控部署章节
- docs/错误码体系.md
  - 登记基类保留码 `GENERAL.UNKNOWN.000`（仅兜底、禁止直接抛出），与 `errors.py` 建立完整一一对应
  - 新增「异常类 ↔ 错误码对照总表」：17 行覆盖全部异常类、错误码、所属域、可重试性、抛出/处理位置；登记规范新增保留码禁用、兜底码使用限制、错误码与异常指南一致性两条
- docs/ 文档索引互通：错误码体系 / 异常处理指南 / 日志规范 / 常用工具类说明 4 篇文档头部统一增加「文档索引」回链（产品方案.md 按 rule.md 要求不做修改）
- 入口统一异常捕获（`job_assistant/main.py`）：`__main__` 启动块统一捕获 `JobAssistantError`，只输出一行带错误码的中文提示（如 `[CFG.LOAD.001] 配置文件不存在: ...`）后以**退出码 1**退出，不向使用方暴露原始堆栈，落实《异常处理指南.md》二.3 的全局异常处理器约定

### Bug 修复

- `config/settings.py` `load_config` 文档串引用了从未定义的错误码 `CFG.VALID.002`，更正为实际唯一配置错误码 `CFG.LOAD.001`，与 `errors.py`、《错误码体系.md》统一
- `errors.py` 基类注释补充 `GENERAL.UNKNOWN.000` 保留码语义说明（仅文档注释，无行为变化）

### 验证

- 8 个测试文件全量回归通过（模块一/二/三/四/五/六单元 + 模块一/二集成，全部退出码 0）
- 启动链路冒烟：默认 `python -m job_assistant.main` 与 `--config` 两种方式均可用；空群白名单安全退出（退出码 0）；配置文件缺失时单行错误提示 + 退出码 1（无 Traceback）

### Breaking Changes

- 无（文档补齐与注释修正；配置错误的输出形式由原始堆栈收敛为单行中文提示，退出码固定为 1，供脚本编排判定失败）

---

## [v0.6.2] - 2026-09-19

### 变更

**LLM 服务由 AstrBot 切换为 One API（OpenAI 兼容网关，上游 DeepSeek）**

- 背景：AstrBot 4.x 对外仅提供自有 API 格式（`/api/v1/chat`，Agent 对话语义，需 username、SSE 流式），**不提供**标准 OpenAI 兼容 `/chat/completions` 端点（实测 405），与 `HttpLLMClient` 的调用协议不匹配；切换为 One API 后直接暴露标准 OpenAI 兼容接口，`llm_client.py` 零代码改动
- 配置变更：`config.example.yaml` 的 `llm.base_url` 默认值由 `http://127.0.0.1:8000/v1` 改为 `http://127.0.0.1:3000/v1`；`llm.api_key` 填 One API 创建的调用令牌、`llm.model` 填上游模型名（如 `deepseek-chat`）
- 代码：`config/settings.py` LLM 配置默认 `base_url` 同步更新为 `http://127.0.0.1:3000/v1`；`parser/llm_client.py` 文档注释更新
- 文档：README / ARCHITECTURE / 产品方案（V1.5）/ 错误码体系 / 异常处理指南 / 常用工具类说明 全部同步为 One API；V2.0 Web UI 技术方案移除 AstrBot 插件依赖
- 测试：`tests/test_module4_parser.py` 中缺省 base_url 断言由 `http://127.0.0.1:8000/v1` 同步为 `http://127.0.0.1:3000/v1`

### Bug 修复

- 无

### Breaking Changes

- 外部 LLM 服务由 AstrBot 替换为 One API：需先部署 One API（`one-api.exe --port 3000`），在 WebUI（`http://localhost:3000`）配置 DeepSeek 渠道并创建调用令牌，再启动主程序；`llm.base_url` 必须指向 One API 地址（默认 `http://127.0.0.1:3000/v1`），`llm.api_key` 填令牌而非 DeepSeek 原生 Key

---

## [v0.6.3] - 2026-09-19

### 新增功能

**V2.0 Web 前台 UI（查看侧提前落地：岗位看板 / 日报预览 / 实时日志 / 状态与配置）**

- 新增 `job_assistant/web/` 模块：轻量本地 Web 服务（Python 标准库 `http.server`，零第三方依赖），不引入外部 Web 框架，复用 V1.0 全部后端逻辑与文件存储（产品方案第七章技术方案）
- 启动：`python -m job_assistant.web.server --port 8080`（默认 `http://127.0.0.1:8080`）
- 子页面：
  1. **岗位看板**：统计卡片（岗位总数 / 推荐数 / 涉及公司 / 日报数）+ ECharts 匹配分数分布 + 岗位表格（关键词搜索、推荐·仅存档筛选、分数·时间排序），点击行查看完整详情（含 AI 打分理由与字段溯源）
  2. **日报预览**：日期选择 + Markdown 在线渲染 + 一键复制
  3. **实时日志**：每 2.5s 自动刷新、级别高亮（INFO/WARN/ERROR/DEBUG）、暂停 / 继续 / 清屏
  4. **状态与配置**：服务状态（监听群 / LLM 网关 / 日志大小）+ config.yaml 只读回显
- 接口：`GET /api/status`、`/api/jobs`、`/api/reports`、`/api/report?date=`、`/api/logs`、`/api/config`；日志读取 UTF-8/GBK 自动探测（Windows 中文环境兼容）
- 容错：ECharts CDN 加载失败自动降级为纯表格；无数据时展示引导文案；单条 JSON 损坏跳过不影响列表
- 前端：原生 HTML + 少量 JS（单文件自包含），hash 路由，深色控制台风格，桌面 / 移动端响应式

### Bug 修复

- 无

### Breaking Changes

- 无（V2.0 配置表单热加载仍按规划后续实现；当前配置页为只读回显，修改配置仍走 `config.yaml` + 重启主程序）

---

## [v0.6.4] - 2026-09-19

### 新增功能

**历史消息补拉（覆盖电脑休眠 / 程序重启期间错过的群消息）**

- 背景：NapCat 正向 WebSocket 只推送启动后的实时事件，不补历史；电脑休眠 / 程序重启期间群里的招聘消息会全部错过（无历史可查、睡眠挂起丢消息、重启不补发）
- 新增 `job_assistant/listener/history_backfill.py`（模块一扩展）
  - `fetch_group_history`：调用 NapCat HTTP 扩展接口 `POST /get_group_msg_history` 拉取群历史消息（`message_seq=0` 从最新往前取，兼容 `{"data":{"messages":[...]}}` 与 `{"data":[...]}` 两种返回结构）
  - `history_to_message`：历史记录补齐 `post_type` / `message_type` 后走 `filter_message`，与实时消息同一套白名单过滤与文本清洗
  - `scan_seen_ids`：扫描 `raw_msg` 目录文件名（`g{群号}_m{消息ID}.txt`）构建已处理消息索引
  - `run_backfill`：对全部监听群执行「拉取 → 去重 → 逐条回调」，单群失败 WARN 不阻断、单条消息处理失败隔离不中断；返回统计（拉取 / 进入链路 / 跳过已处理 / 失败群）
- 安全设计（对齐"只读优先，风控第一"）：补拉**仅拉取历史消息，不发送任何内容**；每次连接成功只补拉一次、每群条数上限可配（默认 50）；按 `(群号, 消息ID)` 去重，已处理消息不再重复消耗 LLM 调用
- 接入（`job_assistant/main.py`、`job_assistant/listener/napcat_client.py`）
  - `NapCatListener` 新增 `on_connected` 回调：连接成功（含断线自动重连）后在独立守护线程触发补拉，不阻塞事件循环
  - `_make_backfill` 装配补拉回调，复用现有 `on_message` 全链路（多模态 → AI 解析 → 打分 → 归档）
- 配置扩展（`config/settings.py`、`config.example.yaml`、`config.yaml`）
  - `napcat.history_backfill`（默认 `true`）：补拉总开关
  - `napcat.backfill_count`（默认 `50`）：每群补拉条数上限
  - `napcat.backfill_http_url`（默认 `http://127.0.0.1:3002`）：NapCat HTTP 服务地址（需在 NapCat WebUI / `onebot11_*.json` 的 `httpServers` 开启 HTTP 服务）
- NapCat 侧：`onebot11_2644081990.json` 的 `httpServers` 开启 `127.0.0.1:3002`（元素必须含 `name` 唯一标识，否则 NapCat 忽略该配置）；已实测 `POST /get_group_msg_history` 返回 200 与完整消息结构
- 测试：`tests/test_module1_backfill.py`（18 项：历史记录转换 / 已处理索引 / 拉取去重回调 / 开关关闭 / 失败隔离 / 同批重复 / 白名单外）；全量回归 233 项通过

### Bug 修复

- `web_fetcher.fetch_url_bytes`：URL 含中文等非 ASCII 字符时，`urllib` 直接请求抛 `UnicodeEncodeError` 导致整条消息处理中断（QQ 群链接常带中文路径）；新增 `_ascii_url` 在请求前按 RFC 3986 保留字符百分号编码，中文 URL 可正常抓取或按策略跳过
- `run_backfill`：补拉回调单条消息处理异常不再中断整批（WARN 记录后继续）

### Breaking Changes

- 需在 NapCat 侧开启 HTTP 服务（端口 3002，配置示例见上）；未开启时补拉静默跳过（`backfill_http_url` 不可达按失败群 WARN 记录），不影响实时监听
- 配置为增量新增，旧配置缺省 `history_backfill=true`、`backfill_count=50`、`backfill_http_url="http://127.0.0.1:3002"`，向后兼容

---


## [v0.6.6] - 2026-09-19

### 新增功能

**问题修复（缺陷&需求报告 问题1/2/3/4/5 全量落地）**

- **问题1 · 宣讲会地点误填工作地点（业务层字段区分校验）**
  - `parser/extractor.py` 抽取后质量校验 `_apply_quality_checks` 增强为两层：
    - 值级：`location` 字段命中宣讲/场地特征词（宣讲会、双选会、校园招聘、体育馆、报告厅、校区等）→ 置 `review_reason`，标记【待人工复核】，不进入打分
    - 上下文：原文含宣讲类关键词，且 `location` 值出现在宣讲信息同段（±20 字符）→ 判定该地点为宣讲/招聘场地，标记复核
  - 边界：校验逻辑在业务插件层实现，与 LLM 网关无关

- **问题2 · 前端面板交互修复（日志筛选 / 日报详情 / 配置在线修改）**
  - 实时日志：`GET /api/logs?level=INFO|WARN|ERROR|DEBUG` 服务端等级过滤（WARNING 按 `[WARN]` 标记匹配）；前端日志等级按钮组接线（切换后强制重新加载）
  - 日报预览：日报视图新增「当日岗位明细」表格（数据源 `GET /api/jobs?date=`），点击行展开单条岗位详情弹窗；残缺/待复核岗位显示标记
  - 配置面板：表单填充（监听群 / 分数阈值 / 简历 / 意向 / OCR / 网页开关）+ `PUT /api/config` 在线保存（保存前自动备份 `config.yaml.bak`，重启主程序后生效）；保存成功后回显刷新
  - 问题2 前端原本只读静态：HTML 已有表单与按钮但 JS 未接线，本次补齐全部事件绑定

- **问题3 · 图片 OCR 与网页链接内容读取**
  - OCR 依赖确定：paddleocr **2.7.3** + paddlepaddle **2.6.2**（3.x 会连带安装 torch，Windows 实测 `fbgemm.dll` 加载失败 WinError 126，已弃用）；`requirements.txt` 注明安装与 numpy<2 降级步骤；真实环境（anaconda3 Python 3.11）已实测 OCR 初始化与识别通过
  - 网页抓取反爬能力（重试 / 备用 UA / 3 次重定向上限 / 4xx 不重试）与失败降级（OCR 失败保留原图、网页失败保留原始链接，标注人工复核）已在 `multimodal/` 内置，本次验证确认
  - 真实图片实测：识别"西安电子科技大学-吉利控股27届秋招答疑群 使用微信或企业微信扫码加入 该二维码7天内有效"等招聘海报内容

- **问题4 · 仅公司名称误判信息充足（联网搜索自动补全 + 公司风评）**
  - 用户更正要求：缺公司名 / 岗位名时**不再直接跳过**，改为依靠联网搜索自动补全缺失字段，同时获取公司风评
  - `research/company_research.py` 新增 `enrich_missing_fields`：缺公司名 / 岗位名任一核心字段 → 用已有字段 + 工作地点构造检索词（已有字段缺哪个补哪个）→ 联网搜索 → LLM 依据搜索资料抽取缺失字段（只依据资料下结论、不得编造）→ 补全成功就地更新字段并清空残缺标记 → 无论成败均把搜索资料（含公司信息/风评）带回打分输入
  - 检索源：神马搜索（主源，移动端反爬弱、分词准确、结果 JSON 结构化）→ 百度（兜底）→ 全部失败保持【基础信息残缺】标记，不污染数据；多搜索源统一进程级节流（5 秒间隔）防验证码拦截
  - 字段归一化：LLM 输出「信息未提及 / 未知 / 无」等变体一律归一为【信息未提及】，防止无效值被当有效岗位信息进入打分
  - 打分输入：残缺消息经补全后（无论成败）均拼接搜索资料与 `research_company` 风评检索（Bing + 百度百科），符合"同时获取公司风评"要求
  - 兜底不变：补全失败仍标记【基础信息残缺】、跳过打分、日报独立板块展示并附检索线索

- **问题5 · 打分缺少联网检索补全（公司口碑/WLB/经营信息）**
  - 新增 `research/company_research.py`（模块五打分输入补全）：
    - 主来源：Bing 网页搜索「公司名 岗位名 怎么样 加班 工作体验 评价」，解析前 6 条（标题+摘要+来源链接），过滤公司官网/官方商城等不含员工风评的页面
    - 补充来源：百度百科词条（公司业务/规模/经营简介）
    - 失败自动降级：全部检索失败返回空串，打分侧标注「未自动联网核验，WLB 需人工自查」，该维度不计入自动打分；日报保留一键复制搜索关键词
    - 控制成本：`company_research.max_chars`（默认 1500 字符）截断提交给模型的资料长度
  - `main.py` 打分前调用 `research_company(company, job_title=...)`，检索结果并入模型输入（`scorer._build_prompt` 的【公司外部检索资料】段），80 分以上打分理由需写明联网检索到的 WLB/风评信息
  - 实测：`research_company('华为', job_title='软件开发')` 返回 800+ 字符多来源摘要（含来源链接）

- **测试与链路验证**
  - 新增 `tests/test_module7_problem_fixes.py`（39 项断言：地点值级/上下文/不误标、残缺标记、scorer 残缺/复核跳过 LLM、Bing/神马/百度解析纯函数、检索降级与截断、enrich 补全 6 用例【补岗位名/补公司名/无结果保持残缺/LLM 未知保持残缺/齐全不触发/LLM 故障保持残缺】、DeepSeek 直连配置）
  - 全量回归：10 个测试文件 260+ 项断言 0 失败（anaconda3 Python 3.11）
  - 前端 API 实测：`/api/config` 读写、`/api/logs?level=` 过滤、`/api/jobs?date=` 明细均验证通过

### Bug 修复

- `server.py /api/logs` 无等级过滤 → 新增 `?level=` 参数
- `index.html` 日志等级按钮、配置表单、保存按钮无 JS 事件 → 全部接线
- `extractor` 地点校验仅有值级关键词 → 增加原文上下文识别（±20 字符窗口，防误标）
- `research_company` 仅单一百度百科来源 → 增加 Bing 搜索主源 + 官方站过滤

### Breaking Changes

- 配置新增 `company_research` 段（`enable` / `timeout` / `max_chars`），旧配置缺省时走默认值（enable=true、timeout=8.0、max_chars=1500），向后兼容
- OCR 依赖固定为 paddleocr 2.7.3 + paddlepaddle 2.6.2（3.x 路线在 Windows 不可用）

---
## [v0.6.5] - 2026-09-19

### 变更

**LLM 服务由 One API 网关切换为 DeepSeek 官方 API 直连（移除中间网关）**

- 背景：One API 作为中间网关时，程序侧只能看到"请求返回 200"，无法直接验证调用是否真实到达 DeepSeek 上游、Token 消耗口径不透明；为做到**调用去向可审计、链路可验证**，移除 One API，由 `HttpLLMClient` 直连 DeepSeek 官方 OpenAI 兼容 API（OpenAI 兼容协议不变，`llm_client.py` 零代码改动）
- 配置变更：`config.yaml` / `config.example.yaml` 的 `llm.base_url` 由 `http://127.0.0.1:3000/v1` 改为 `https://api.deepseek.com/v1`；`llm.api_key` 改填 DeepSeek 官方 API Key（platform.deepseek.com 创建）
- 代码：`config/settings.py` LLM 配置默认 `base_url` 同步更新为 `https://api.deepseek.com/v1`；`parser/llm_client.py` 文档注释更新为 DeepSeek 官方直连
- 文档：README / ARCHITECTURE（v1.7）/ 产品方案（V1.8）/ 错误码体系 / 异常处理指南 / 常用工具类说明 / tools 服务端工具说明 全部同步为 DeepSeek 直连
- 测试：`tests/test_module4_parser.py` 中缺省 base_url 断言由 `http://127.0.0.1:3000/v1` 同步为 `https://api.deepseek.com/v1`

### Bug 修复

- 无

### Breaking Changes

- 外部 LLM 服务由 One API 替换为 DeepSeek 官方直连：**无需再部署 / 启动 One API**；`llm.base_url` 必须指向 `https://api.deepseek.com/v1`，`llm.api_key` 必须填 DeepSeek 官方 API Key（原 One API 调用令牌不再有效）

---

## [v0.7.0] - 2026-09-20

### 新增功能

**真实用户画像与筛选阈值落地（v0.6.5 之后的配置交付）**

- `config.yaml` 写入真实简历与求职意向：`user_resume`（1185 字：西安电子科技大学 211、数据科学与大数据技术 2027 届本科、购物商城 Spring Boot/Redis/RocketMQ 项目、Android 环境监测 App 项目）、`user_intention`（562 字：后端开发工程师、意向城市西安/上海/成都/深圳、行业不限、硬排销售/客服/倒班/教培、年薪底线税前 ≥10 万、偏好小而美外企与 WLB）
- 意向口径约束：AI 辅助开发 / AI Agent 应用后端岗位可接受，但岗位核心语言仍按技术栈严格打分；**Python / Go / Kafka 为"了解但无实战"：岗位要求时扣分、作主要开发语言时扣分**（打分提示词按此执行）
- `recommend_score_threshold` 80 → 75 → 50：推荐线下调后推荐岗位从 7 → 13（当前 36 条岗位存档 / 13 条推荐）

**Web 手动录入就业信息（POST /api/jobs + 前端弹窗）**

- `server.py` 新增 `save_manual_job`：手动录入走与群消息**完全一致**的链路（组装 JobPosting → JobScorer LLM 打分 → JobRepository 去重落盘，文件名 `g0_m0` 前缀区分），LLM 不可用时降级为未打分记录（score=0）绝不丢失录入；支持扩展字段 salary / note
- `index.html` 新增「＋ 手动录入」弹窗：公司/岗位必填 + 地点/学历/技术栈/薪资/投递/截止/备注，提交后展示打分结果与推荐状态，重复录入提示去重

**Web 手动删除岗位（DELETE /api/jobs?file= + 前端删除按钮）**

- 用户诉求：投完的岗位从看板移除。`server.py` 新增 `do_DELETE` 与 `delete_job`：**删除 = 移入 `data/jobs_deleted_backup_<时间戳>/` 备份目录（不物理删除，可恢复）**；文件名 basename 白名单 + `.json` 后缀校验，杜绝路径穿越
- `index.html` 岗位表格新增「操作」列与删除按钮：confirm 确认 → DELETE → 列表/统计/图表自动刷新；端到端实测（造测试岗位→删除→备份目录落盘→列表消失）通过

**推荐阈值在线修改全量重算（改一次阈值重新比对全部岗位）**

- 用户诉求："每次修改推荐评分数值，就将全部岗位分数和新的阈值重新比对一次"
- `repository.py` 新增模块级 `recompute_recommended(jobs_dir, threshold)`：全量扫描已存档 JSON，`recommended = (score >= threshold)` 与打分时判定逻辑一致，threshold 字段同步更新，只改标记不改分数；单文件损坏跳过、幂等可重复
- 触发点 ① `server.py save_config`：Web 配置页保存阈值 → 自动重算并返回统计（实测"扫描=36 更新=0 失败=0 阈值=50"）；② `main.py` 启动钩子：程序启动即按当前阈值重算（实测同）

**岗位自动去重（归一化 + 启动钩子，华为系/普渡多条实测合并）**

- 用户诉求："小助手自己去重"——已出现 3 条普渡机器人、华为/华为半导体/华为海思实为同一家
- `repository.py` 增强：
  - `normalize_company_name`：公司别名归一化表（华为半导体业务部/华为海思/海思→华为，普渡科技/普渡机器人→普渡，龙旗科技/龙旗等）+ 去公司后缀/集团/股份等冗余
  - `normalize_job_title`：去 J 编号/括号内容/【信息未提及】等噪音，保留语义主体
  - `_is_generic_recruitment`：泛校招（软件开发工程师/校招/秋招等无具体方向）与半导体通用岗归入 `\x1fgeneric`，同一公司只保留一条
- `dedupe_jobs(jobs_dir)` 模块级函数：按归一化键分组，每组保留最优（推荐优先 → 分数高 → 字段完整 → 文件名新），重复文件**移入 `data/jobs_dedup_backup_<时间戳>/` 不删除**；缺公司名/损坏文件不参与去重（不丢任何岗位信息）
- `main.py` 启动钩子：监听前自动执行（实测启动日志"岗位自动去重完成: 扫描=36 重复组=0 保留=24 合并移动=0"）；手工存量清理脚本 `_dedupe_jobs.py` 43→36 已执行

**残缺岗位浏览器自动补全（全自动链路，普渡/纵维立方实测通过）**

- 用户诉求："只有公司名的岗位由小助手自己用浏览器补全剩余信息"
- `research/company_research.py` `enrich_missing_fields` 升级为全自动链路：
  1. DeepSeek 按已知字段生成检索词（失败回退规则词 + 追加 `site:edu.cn` 高校就业网专项 + 公告词）
  2. 多源搜索合并去重：Bing → 神马 → 百度（**全部检索词都执行**，修复早期前两个 query 凑满即 break 导致 `site:edu.cn` 从不执行的问题）
  3. 按详情页价值排序（edu.cn/zhiye 优先）→ HTTP 抓详情页
  4. HTTP 失败降级 **Playwright 无头浏览器渲染**（新增 `research/browser_fetch.py`：`browser_fetch_texts` 渲染抓正文 + `browser_fetch_links` 挖掘官网校招入口，_LINK_HINTS=campus/career/recruit/zhiye/job/jobs/join/talent）
  5. 挖掘 zhiye（北森）校招页 → DeepSeek 抽取 7 字段（company/job_title/location/education/tech_stack/apply_method/deadline，允许原始消息作依据，不编造）
- 端到端实测：普渡机器人自动补出"后端开发工程师"（pudutech.zhiye.com/campus/jobs 渲染 1285 字生效）；纵维立方自动补出"深圳 / 本科硕士博士"；修正 DeepSeek 幻觉 URL（pudurobotics.zhiye.com 404 → 抓取校验后走正确页面）
- 官网"关于我们"页含"人才招聘"导航字样被误判为岗位页 → 加强 `_looks_like_job_page` 判定（岗位/职位/薪资/校招对象/投递/网申强特征 + 长度 ≥300）

**牛客员工评价风评检索（WLB 判断依据，普渡实测命中面经）**

- 用户诉求："WLB 这种要求需要员工风评，让小助手搜索牛客网、小红书获得"
- `research_company` 风评检索增强：
  - 多路检索词（`"{公司}" 怎么样/加班/工作体验/评价` + `site:nowcoder.com` + 小红书语境词）+ 多源搜索（Bing/神马/百度）
  - `_platform_of` 平台标记：牛客 → nowcoder.com、小红书 → xiaohongshu.com/xhslink.com，命中平台来源优先展示
  - `_fetch_nowcoder`：搜索结果中的牛客页 HTTP 抓正文（员工评价/WLB 直接来源）
  - **`_browser_nowcoder` 浏览器渲染兜底**：搜索引擎不索引牛客内容（实测 Bing/百度/神马对"公司 牛客"零命中）时，Playwright 直接渲染 `nowcoder.com/search/all?query=公司名` 抓面经/员工评价/offer 投票；**同公司结果缓存复用**（`_NOWCODER_CACHE` 上限 200 条，同一公司多条岗位只渲染一次）
- 实测：普渡机器人检索到牛客真实内容（深圳-普渡-一面技术面、C++ 工程师一面、自动化测试校招一面、offer 帮选投票等），并入打分提示词
- **小红书确认登录墙**：无头浏览器访问 xiaohongshu.com/search_result 被"手机号登录"拦截（HTTP 与浏览器均拿不到内容），属平台限制；当前策略保留小红书语境检索词（能命中摘要即纳入），完整笔记内容需登录态（待用户配合接入）

**群消息兼容修复（651677681 群 0 消息 Bug）**

- 现象：某群消息全部显示为 `post_type="message_sent"`（NapCat 对登录 QQ 自己发送/群内历史同步的消息标记），`filter_message` 仅放行 `post_type="message"` 导致该群 0 条进入链路
- 修复：`group_filter.py` 放行 `("message", "message_sent")` 两种事件类型；历史补拉 `history_to_message` 同步受益

**网页抓取上限提升（公众号链接抓取修复）**

- 现象：公众号推文链接 1MB 内全部抓取失败（页面大小上限 1MB 过小）
- 修复：`web_max_bytes` 1,048,576 → **4,194,304（4MB）**，公众号长文可正常抓取

**运行日志规范化（启动脚本 + data/logs）**

- 新增 `tools/scripts/start_all.ps1` / `stop_all.ps1`：一键启动/停止主程序 + Web 控制台，日志统一输出 `data/logs/`（main.log / main_err.log / web.log / web_err.log），不再散落项目根目录
- 根目录历史日志归档至 `data/logs_archive_20260920/`，运维脚本归入 `tools/scripts/`

### Bug 修复

- `group_filter` 只处理 `post_type="message"` → 兼容 `"message_sent"`（自发送/群内同步消息不再被丢弃）
- `research` 多源检索前两个 query 命中即 break → 全部检索词均执行（`site:edu.cn` 高校就业网专项此前从不生效）
- `_looks_like_job_page` 将官网"关于我们"含招聘导航字样误判为岗位页 → 强特征 + 长度双重判定
- DeepSeek 生成的校招 URL 幻觉（pudurobotics.zhiye.com 返回 404 北森页）→ 抓取后校验正文再采纳
- Web 端修改推荐阈值不生效（用户"改了 50 为什么没改"）→ `save_config` 自动全量重算 + 启动重算双保险
- Web 岗位列表无删除能力（用户"投完了就不需要了"）→ 新增 DELETE 接口与前端删除按钮

### Breaking Changes

- 无（全部为增量新增/增强；`recommend_score_threshold` 当前配置 50，旧配置缺省行为不变）
- 行为变化提示：① 主程序启动时自动执行去重 + 推荐重算（重复岗位移入 `jobs_dedup_backup_`，不影响正常岗位）；② 残缺岗位打分前自动浏览器补全（每条残缺岗位多耗一次检索 + 可选浏览器渲染，超时兜底不阻断）；③ 打分前风评检索增强为多源 + 牛客浏览器（同公司缓存复用，控制调用量）；④ Web 面板支持手动录入 / 删除岗位（删除移入 `jobs_deleted_backup_` 可恢复）

---

## 变更记录说明

- 每次新增功能必须同步更新本文件（rule.md 开发规则 3）
- 错误码、异常、日志、工具类规范详见 `docs/` 目录
