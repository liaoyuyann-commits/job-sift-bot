# README.md

# AI 求职信息智能筛选助手

> 文档版本：v1.9（对齐《ARCHITECTURE.md》v1.8、《docs/产品方案.md》V1.9、代码 v0.7.0）
> 更新日期：2026-09-20
> 适用规范：根目录《rule.md》

---

## 一、项目简介

面向**应届秋招学生**的本地轻量化、**只读式、低风险**求职信息自动化筛选工具：监听 1~N 个 QQ 求职群，自动采集**文本 / 图片海报 / 外部网页**三种载体的岗位信息，调用 AI 完成消息分类、结构化抽取与「岗位 × 简历 × 意向」匹配打分，所有岗位全量本地归档，并在每次运行结束时生成仅含推荐岗位的 Markdown 日报。

**业务场景**：求职信息分散在多个 QQ 求职群，本工具针对五大痛点逐一解决——

| 痛点 | 对应能力 |
| --- | --- |
| 信息爆炸冗余（闲聊广告混杂） | LLM 消息分类闸门：宣讲/招聘/内推才进入抽取，闲聊广告/诈骗直接过滤 |
| 群动态变化（不能写死群 ID） | `monitor_group_ids` 白名单数组配置，1~N 个群，不在列表直接丢弃 |
| 信息载体多样 | 文本直传 + 图片本地 OCR + URL 网页正文抓取，三分支合并 |
| 画像因人而异 | 简历全文 + 求职意向 YAML 配置，LLM 动态打分，不预置标签权重 |
| 信息无法沉淀 | 公司+岗位双维度去重，JSON 全量永久存档 + 每日 Markdown 日报 |

**运行形态**：纯本地单机运行；NapCat 纯只读接收、代码层面不提供任何发送能力；手动启停、间歇运行，规避账号风控。

## 二、技术栈清单

| 类别 | 技术 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.10+ | 标准库实现网页抓取/HTML 清洗，零额外爬虫依赖 |
| 消息接入 | NapCat（OneBot v11 正向 WebSocket） | `websockets` 库；纯只读，断线自动重连 |
| 配置 | PyYAML | 监听群、简历意向、开关、LLM、阈值全部配置驱动 |
| 多模态 | PaddleOCR（**可选依赖**） | 图片海报本地 OCR，含超时控制；不安装时该分支标记失败不阻断 |
| AI 能力 | LLM（DeepSeek 官方 OpenAI 兼容 API `/chat/completions` 直连，无中间网关） | 消息分类、岗位结构化抽取、匹配打分共用同一客户端 |
| 存储 | 本地分目录 JSON / Markdown | 六个数据目录隔离，岗位记录字段只增不减 |
| 测试 | 标准库 unittest | 8 个测试文件，无需第三方框架、无需联网即可直接运行 |

## 三、✨ 核心特性

**当前已落地（代码 v0.7.0，模块一 ~ 模块六全部开发完毕 + V2.0 Web UI 查看侧 + 历史消息补拉 + LLM 直连 DeepSeek 官方 + 自动补全/自动去重/风评检索）**

- **群 ID 白名单**：1~N 个群数组配置，空数组安全兜底退出；不在列表的群消息直接丢弃；兼容 `post_type="message_sent"`（NapCat 自发送/群内同步消息，v0.7.0）
- **NapCat 纯只读监听**：代码层面不提供任何发送能力（风控第一道防线）；断线按间隔自动重连、手动启停、运行时长与收发统计
- **历史消息补拉（v0.6.4）**：连接成功（含断线重连）后通过 NapCat 扩展 API `get_group_msg_history` 低频补拉最近群消息（默认每群 50 条，开关可配），按 `(群号, 消息ID)` 去重后走正常 AI 链路——**电脑休眠 / 程序重启期间错过的招聘消息不再丢失**
- **多模态解析**：文本直传 / 图片下载 + 本地 OCR / URL 自提取 + 网页抓取 + HTML 正文清洗，合并为完整文本；`web_max_bytes` 已上调至 4MB（公众号长文可抓取，v0.7.0）
- **稳定性兜底**：抓取设超时与响应体大小上限、OCR 设超时；显式识别并跳过需登录、JS 强渲染页面（残缺补全链路例外：支持 Playwright 渲染）；**单条分支失败标记异常、不阻断整体**
- **配置驱动无硬编码**：监听群、NapCat 地址、OCR/抓取开关与上限、数据目录、LLM 接入、推荐阈值全部来自 `config.yaml`
- **用户画像配置**：YAML 粘贴简历全文 + 求职意向，程序不预置固定标签与权重；敏感内容日志只记长度。**已接入真实画像**（西电 2027 届本科 · 后端开发工程师 · 四城意向 · 硬排销售/客服/倒班/教培 · 年薪底线 10 万；Python/Go/Kafka 按"了解无实战"扣分口径）
- **AI 消息分类**：宣讲通知 / 正式招聘 / 内推信息 / 闲聊广告 / 诈骗信息（无法识别归 OTHER 兜底），非招聘相关不消耗抽取调用
- **AI 结构化抽取**：公司 / 岗位 / 地点 / 学历 / 技术栈 / 投递方式 / 截止时间 / 信息来源 8 字段，缺失统一标【信息未提及】；存档字段一次定义完整、兼容 V2.0 Web UI
- **残缺岗位自动补全（v0.7.0）**：缺公司名/岗位名的岗位在打分前自动走**浏览器补全链路**——DeepSeek 生成检索词 → 多源搜索（Bing/神马/百度）→ HTTP 抓详情页 → 失败降级 Playwright 无头浏览器渲染（含官网校招入口挖掘、zhiye 校招页抽取）→ LLM 抽取完整字段；实测普渡机器人自动补出"后端开发工程师"、纵维立方补出"深圳/本科硕士博士"
- **AI 匹配打分**：岗位 + 简历 + 意向 → **0~100 分 + 简短理由**，完全由用户画像驱动；打分前自动联网检索公司风评/WLB（v0.6.6 起）
- **牛客员工评价风评检索（v0.7.0）**：多路检索词 + 平台标记（牛客/小红书）+ 牛客页面 HTTP 抓取；搜索引擎不索引牛客内容时自动**浏览器渲染牛客搜索页**兜底（面经/员工评价/offer 投票，同公司缓存只查一次）；实测普渡命中多篇真实面经。小红书确认登录墙（平台限制，需登录态）
- **阈值过滤（F6）**：`recommend_score_threshold` 当前配置 50；**≥ 阈值**为推荐岗位（控制台 ★ 完整输出 + 写入日报），**< 阈值**仅 JSON 存档 + 一行极简日志；**修改阈值自动全量重算全部已存档岗位**（v0.7.0，Web 保存或程序启动时触发）
- **容错解析**：LLM 输出格式非法自动重试一次；分数越界容错截断到 [0,100]（WARN）；画像未配置前置跳过打分；**单条消息任一环节失败标记异常、不阻断整体**
- **自动去重归档（F7，v0.7.0 增强）**：公司别名归一化（华为半导体业务部/华为海思→华为）+ 岗位名归一化 + 泛校招同公司归并；启动时自动清理重复岗位（保留最优，重复移入 `jobs_dedup_backup_` 备份不删除）；实测华为系 3 条/普渡 3 条合并，43→36 清理完成
- **每日日报（F8）**：运行结束自动生成 `data/report/YYYY-MM-DD.md`，仅含当日推荐岗位、按分数降序，附分数、打分理由、信息来源与群/消息溯源；空日报兜底落盘
- **统一错误码与异常体系**：`{业务域}.{模块}.{三位数字}`，6 个业务域共 16 个业务错误码 + 1 个基类保留码（详见对照总表），异常类与错误码一一对应；入口统一捕获业务异常，只输出带错误码的中文提示（退出码 1），不暴露原始堆栈
- **V2.0 Web 控制台（v0.6.3 落地，v0.7.0 增强）**：`python -m job_assistant.web.server --port 8099` 启动本地 Web UI，岗位看板（统计卡片 + 分数分布图 + 表格搜索/筛选/排序 + 详情弹窗 + **手动录入** + **手动删除（投完归档）**）、日报预览（Markdown 渲染 + 一键复制）、实时日志（2.5s 自动刷新 + 级别高亮）、状态与配置（在线修改阈值/群/画像，保存自动备份 + 阈值全量重算）；Python 标准库实现、零第三方依赖，ECharts 离线自动降级

**规划中（V2.0 后续，在本项目内追加开发，不新建项目，见《docs/产品方案.md》第七章）**

- 配置表单热加载（改群列表/简历免重启）、关键词预警、投递台账、打分模式切换（LLM 自由 / 用户权重）

## 四、项目结构

```
AI 求职信息智能筛选助手/
├─ README.md                 # 项目门面（本文件）
├─ ARCHITECTURE.md           # 架构文档：三层架构、6 模块、流程图、存储设计
├─ CHANGELOG.md              # 版本变更记录（v0.1.0 ~ v0.7.0）
├─ rule.md                   # 开发规则：文档结构与开发约束（勿随意修改）
├─ requirements.txt          # Python 核心依赖（websockets / PyYAML）
├─ docs/                     # 产品与工程规范文档（6 个文件）
│  ├─ 产品方案.md            # 完整产品策划（V1.9，需求基线，修改需经用户授权）
│  ├─ 错误码体系.md          # 错误码格式、17 码明细、异常类对照总表
│  ├─ 异常处理指南.md        # 可重试/不可重试分类、抛出原则、案例表
│  ├─ 日志规范.md            # 日志级别、打印规范、各模块日志点清单
│  ├─ 常用工具类说明.md      # 全部公开类/函数：作用、示例、注意事项
│  └─ 画像配置与打分链路.html # 画像填法 + 打分链路示意图
├─ job_assistant/            # 主程序包
│  ├─ main.py                # 入口：配置校验、启动去重/重算钩子、全链路装配、结束时生成日报
│  ├─ errors.py              # 异常基类 + 6 域全部异常类与错误码
│  ├─ config/                # 配置加载与校验 + 用户画像（模块三）
│  ├─ listener/              # 模块一：NapCat 只读监听 + 群白名单过滤 + 历史补拉
│  ├─ multimodal/            # 模块二：文本 / 图片 OCR / 网页抓取三分支
│  ├─ parser/                # 模块四：LLM 分类 + 结构化抽取
│  ├─ matcher/               # 模块五：LLM 匹配打分 + 阈值过滤 + 降序排序
│  ├─ research/              # 业务插件层（v0.6.6 起）：残缺岗位搜索补全 + 公司风评/WLB 检索
│  │  ├─ company_research.py #   检索词生成 / 多源搜索 / 详情页抓取 / 牛客浏览器兜底 / LLM 字段抽取
│  │  └─ browser_fetch.py    #   Playwright 无头浏览器渲染（正文抓取 + 校招入口链接挖掘）
│  ├─ storage/               # 模块六：去重归档仓储 + 自动去重 + 阈值重算 + Markdown 日报
│  └─ web/                   # V2.0 Web 前台 UI（手动录入 / 删除 / 配置在线保存 / 岗位看板）
├─ tests/                    # 10 个测试文件（单元 + 集成，直接 python 运行）
├─ tools/                    # 服务端工具（NapCat 安装包与说明 + 运维脚本 scripts/）
│  ├─ README.txt             #   工具安装说明
│  ├─ NapCat/                #   NapCat.Shell.zip（QQ 消息监听，只读）
│  └─ scripts/               #   运维脚本：_browser_enrich.py（人工浏览器补全重打分）/ _dedupe_jobs.py（手工去重）/ _recalc_threshold.py（阈值重算）/ _debug_enrich.py
└─ data/                     # 首次运行自动生成（见第八章产物说明）
```

模块与代码对应关系：模块一 `listener/` ｜ 模块二 `multimodal/` ｜ 模块三 `config/profile.py` ｜ 模块四 `parser/` ｜ 模块五 `matcher/` ｜ 模块六 `storage/`。

## 五、快速开始

> 以下命令均在**项目根目录**（`README.md` 所在目录）执行；已在 Windows + Python 3.10~3.14 环境验证。

### 1. 环境准备

| 依赖 | 要求 | 用途 |
| --- | --- | --- |
| Python | 3.10 及以上 | 运行主程序与测试 |
| NapCat | 本机运行，登录 QQ，启用**正向 WebSocket**（OneBot v11 事件推送，默认 `ws://127.0.0.1:3001`）与 **HTTP 服务**（默认 `127.0.0.1:3002`，历史补拉用） | 消息来源；在 NapCat 侧关闭全部发送接口/自动回复 |
| DeepSeek 开放平台 | 注册 platform.deepseek.com，创建 API Key（sk-...，按量计费） | 模块四分类抽取、模块五打分（官方 API 直连，无中间网关） |

### 2. 安装依赖

```powershell
# 核心依赖（websockets + PyYAML）
python -m pip install -r requirements.txt

# 可选：需要识别图片海报再装（体积较大）；不装也能跑，图片分支会标记失败但不阻断
# 注意：paddleocr 3.x 会连带安装 torch（Windows 下常报 fbgemm.dll 加载失败），
#       请使用 2.x 稳定路线；若 numpy 被升级到 2.x（paddle 2.6 不兼容）再降回 1.x
python -m pip install "paddlepaddle==2.6.2" "paddleocr==2.7.3"
python -m pip install "numpy<2"
```

暂不需要图片识别时，也可以先把配置里的 `multimodal.enable_ocr` 改为 `false`，跳过本步。

### 3. 创建并填写配置

```powershell
# Windows PowerShell
Copy-Item "job_assistant/config/config.example.yaml" "job_assistant/config/config.yaml"
# Windows CMD 用：copy job_assistant\config\config.example.yaml job_assistant\config\config.yaml
# macOS / Linux / Git Bash 用：cp job_assistant/config/config.example.yaml job_assistant/config/config.yaml
```

编辑 `job_assistant/config/config.yaml`，**逐项核对**：

| 配置项 | 填写内容 | 不填/填错的后果 |
| --- | --- | --- |
| `napcat.ws_url` | NapCat 正向 WS 实际地址 | 连不上时按 `reconnect_interval` 自动重连（ERROR 日志） |
| `napcat.history_backfill` / `backfill_count` / `backfill_http_url` | 补拉开关（默认 `true`）/ 每群条数上限（默认 `50`）/ NapCat HTTP 地址（默认 `http://127.0.0.1:3002`） | 开关关闭不补拉；HTTP 地址不可达时补拉按失败群 WARN，不影响实时监听 |
| `monitor_group_ids` | 要监听的真实 QQ 群号（正整数列表，1~N 个） | 空数组 `[]` 程序提示后安全退出 |
| `llm.base_url` / `api_key` / `model` | DeepSeek 官方 OpenAI 兼容地址（默认 `https://api.deepseek.com/v1`）、官方 API Key、模型名（如 `deepseek-chat`） | Key 无效/网络不通时每条消息解析/打分标记 WARN，不阻断采集 |
| `user_resume` | 简历全文（YAML 块文本 `|`） | 为空时打分前置跳过并 WARN 提示 |
| `user_intention` | 意向岗位/城市/行业/硬性排除 | 同上 |
| `recommend_score_threshold` | 0~100 整数，默认 80（当前实例配置 50） | 浮点/字符串/越界启动即报 CFG.LOAD.001；**修改后 Web 保存或重启自动全量重算全部岗位推荐标记** |
| `multimodal.enable_ocr` / `enable_web_fetch` | true / false 开关 | OCR 开启但未装 paddleocr 时图片分支标记失败 |

> V1.0 不支持热加载：**修改配置后需重启程序生效**。

### 4. 启动（顺序：先外部服务，后本程序）

1. 启动 NapCat 并确认 QQ 已登录、正向 WS 服务已开启；
2. 确认 `config.yaml` 已填 DeepSeek 官方 API Key（`llm.api_key`）、`llm.base_url=https://api.deepseek.com/v1`；
3. 启动本程序：

```powershell
# 方式一（推荐）：一键启动主程序 + Web 控制台（日志输出 data/logs/）
powershell -File tools\scripts\start_all.ps1
# 一键停止
powershell -File tools\scripts\stop_all.ps1

# 方式二：手动启动
# 主程序（默认配置路径 job_assistant/config/config.yaml）
python -m job_assistant.main
# 或显式指定配置文件
python -m job_assistant.main --config job_assistant/config/config.yaml
# Web 控制台（岗位看板 / 手动录入删除 / 日报预览 / 实时日志 / 状态配置）
python -m job_assistant.web.server --port 8099    # 浏览器访问 http://127.0.0.1:8099
```

> 端口说明：`server.py` 默认端口 8080，当前实际使用 8099（`--port` 显式指定，避免与开发期其他服务冲突）；访问地址以启动命令为准。

启动成功标志：日志依次出现 `监听已启动` → `已连接 NapCat: ws://...` → `历史消息补拉完成: 群数=... 拉取=... 进入链路=...`（补拉最近群消息，覆盖休眠/重启期间错过）；命中群消息后出现 `消息处理完成`，推荐岗位以 `★ 推荐岗位` 整块打印到控制台。

### 5. 停止与查看结果

- **Ctrl+C** 优雅停止（自动断开 WS）；程序在 `finally` 阶段自动生成**当日日报**，日志出现 `日报生成完成: YYYY-MM-DD.md 推荐=N 当日全量=M`。
- 查看入口（V2.0 起提供 Web 控制台，共 4 个）：
  - **Web 控制台**：`python -m job_assistant.web.server --port 8099` 后浏览器访问 `http://127.0.0.1:8099`——岗位看板（统计 + 分数分布 + 搜索筛选 + 详情 + 手动录入 + 手动删除）、日报预览（一键复制）、实时日志（2.5s 自动刷新）、状态与配置（在线修改阈值/群/画像，保存即全量重算推荐）；
  - 控制台：推荐岗位实时完整输出（仅 ≥ 阈值）；
  - `data/report/YYYY-MM-DD.md`：当日推荐岗位日报（按分数降序）；
  - `data/jobs/*.json`：全部岗位结构化存档（含分数、理由、推荐标记，低分岗位也在）。

### 6. 自测（无需 NapCat / DeepSeek API / 联网）

```powershell
python tests/test_module1_listener.py      # 模块一单元：白名单过滤 / 配置校验
python tests/test_module1_backfill.py      # 模块一扩展：历史消息补拉（去重 / 隔离 / 统计）
python tests/test_module1_integration.py   # 模块一集成：模拟 NapCat 全链路
python tests/test_module2_multimodal.py    # 模块二单元：清洗 / 抓取 / 下载 / 编排
python tests/test_module2_integration.py   # 模块二集成：模拟 NapCat + 本地站点
python tests/test_module3_profile.py       # 模块三单元：简历 & 求职意向
python tests/test_module4_parser.py        # 模块四单元：分类 / 抽取 / 重试 / llm 配置
python tests/test_module5_matcher.py       # 模块五单元：打分 / 阈值边界 / 排序
python tests/test_module6_storage.py       # 模块六单元：去重归档 / 日报 / STO 错误码
```

一键跑完（PowerShell）：

```powershell
Get-ChildItem tests/test_module*.py | ForEach-Object { python $_.FullName }
```

### 7. 常见问题（现象 → 处理）

| 现象 | 原因/处理 |
| --- | --- |
| 启动即退出并打印 `[CFG.LOAD.001] ...`（退出码 1） | 配置文件缺失 / YAML 语法错 / 群号非法 / ws_url、llm.base_url 协议非法 / 阈值非 0~100 整数；按中文提示改 config 后重启 |
| 日志反复 `NapCat 连接失败/断开 ... 秒后重连` | NapCat 未启动或 `napcat.ws_url` 不一致；程序会自动重连，无需重启本程序 |
| 电脑休眠/重启后群消息错过 | 已内置历史补拉：程序启动或 WS 重连后自动拉取最近 `backfill_count` 条历史消息并去重处理；需 NapCat 侧开启 HTTP 服务（`httpServers` 元素须含 `name` 字段），否则补拉 WARN 跳过、不影响实时监听 |
| 日志出现 `历史消息补拉完成: ... 失败群=1` | 某群补拉失败（HTTP 地址不通/群不可达），WARN 记录不阻断；检查 `napcat.backfill_http_url` 与 NapCat HTTP 服务状态 |
| `消息 AI 解析失败 [PAR.LLM.001]` | DeepSeek API Key 无效 / 网络不通 / `llm.base_url` 错误；单条消息标记跳过，不影响监听与后续消息 |
| `OCR 识别失败 [MUL.OCR.001]` | 未装 paddleocr / 超时；安装依赖、调大 `ocr_timeout` 或关闭 `enable_ocr` |
| `打分跳过（画像未配置）` | `user_resume` / `user_intention` 为空；补填后重启 |
| 控制台只有岗位没有 ★ 整块输出 | 分数低于阈值，属仅存档岗位；完整信息在 `data/jobs/` JSON 中 |
| 触发人脸核验 | **立即关停程序**（风控要求，见第九章） |

## 六、文档索引

### 根目录文档

| 文档 | 内容 | 适合什么时候读 |
| --- | --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 三层架构、6 模块划分与依赖、端到端流程图、关键设计决策、数据存储与风控设计 | 想理解系统怎么串起来 |
| [CHANGELOG.md](CHANGELOG.md) | v0.1.0~v0.7.0 每版新增功能、修复与行为变化（面试重点） | 想了解迭代过程与新增能力 |
| [rule.md](rule.md) | 项目文档结构约定与 6 条开发规则 | 参与开发/改代码前必读 |

### docs/ 文档

| 文档 | 内容 | 适合什么时候读 |
| --- | --- | --- |
| [docs/产品方案.md](docs/产品方案.md) | 完整产品策划 V1.9：痛点、12 项功能需求、非功能需求、迭代规划（**需求基线，修改需经用户授权**） | 想搞清"为什么做、做什么" |
| [docs/错误码体系.md](docs/错误码体系.md) | 错误码格式、6 域 16 个业务码 + 保留码明细、异常类 ↔ 错误码对照总表、登记规范 | 看到 `[XXX.YYY.001]` 报错、新增异常时 |
| [docs/异常处理指南.md](docs/异常处理指南.md) | 可重试/不可重试/策略性跳过三分类、抛出原则、20+ 常见异常案例处理表 | 排查失败分支、设计容错时 |
| [docs/日志规范.md](docs/日志规范.md) | ERROR/WARN/INFO/DEBUG 使用规范、敏感信息红线、模块一~六日志点清单 | 查日志含义、新增日志点时 |
| [docs/常用工具类说明.md](docs/常用工具类说明.md) | 全部公开类/函数清单（20 组）：作用、可运行示例、注意事项 | 二次开发调用各模块 API 时 |
| [docs/画像配置与打分链路.html](docs/画像配置与打分链路.html) | 画像配置填法 + 打分链路示意图（阈值语义/硬排/无实战扣分口径） | 想快速理解 config.yaml 怎么填、分数怎么来 |

**推荐阅读路径**：第一次使用 → 本文「快速开始」；理解设计 → ARCHITECTURE → 产品方案；参与开发 → rule.md → 常用工具类说明 → 错误码/异常/日志三件套。

## 七、配置与数据说明

- 配置文件仅一份：`job_assistant/config/config.yaml`（不入库、由 example 复制）；全部配置项含义见 `config.example.yaml` 行内注释与《ARCHITECTURE.md》。
- 岗位存档记录 = `JobPosting.to_dict()` 11 字段（8 业务字段 + category + 群号/消息 ID）+ 打分字段（score / reason / threshold / recommended）+ `recorded_at` 时间戳，**字段只增不减**，V2.0 Web UI 直接复用。
- 岗位文件名：`{YYYY-MM-DD}_g{群号}_m{消息ID}_{公司}_{岗位}.json`，日期前缀即日报"当日新增"的聚合依据。

## 八、运行产物（data/ 目录）

程序首次运行自动创建 `data/`，按 ARCHITECTURE.md 第六章分目录隔离：

| 目录 | 内容 | 写入时机 |
| --- | --- | --- |
| `data/raw_msg/` | 原始消息文本 | 每条命中白名单消息实时写入 |
| `data/image_raw/` | 下载的图片原始素材（OCR 输入，文件名带群/消息前缀防覆盖） | 消息含图片时 |
| `data/ocr_result/` | 图片 OCR 识别结果 | OCR 成功时 |
| `data/web_content/` | 抓取网页正文 | URL 分支成功时 |
| `data/jobs/` | 岗位结构化 JSON（推荐 + 仅存档全量，公司+岗位去重） | 打分成功即永久存档 |
| `data/report/` | 每日 Markdown 日报（仅推荐岗位，分数降序） | 每次运行结束生成，同日多次运行覆盖最新 |
| `data/jobs_backup_*/` | 手工/脚本备份的岗位快照（如 `jobs_backup_20260920_023626`，43 条） | 存量清理前人工备份 |
| `data/jobs_dedup_backup_*/` | **自动去重移动的重复岗位**（保留最优，重复不删除只归档） | 主程序启动自动去重 / `_dedupe_jobs.py` 手工清理时 |
| `data/jobs_deleted_backup_*/` | **Web 手动删除的岗位**（投完归档，可恢复） | Web 面板点"删除"时 |
| `data/logs_archive_*/` | **历史运行日志归档**（早期主程序/Web 的 stdout/stderr 重定向产物） | 2026-09-20 整理：run.log / run.err.log / web.log / web.err.log |

## 九、部署与风控说明

- **运行形态**：本地单机运行，**禁止部署到云服务器**；手动启停、间歇运行，不要 24 小时挂机。
- **账号安全**：NapCat 关闭全部发送接口与自动回复，纯消息接收；一旦触发人脸核验，**立即关停程序**。
- **数据安全**：消息、图片、OCR 结果、网页正文、岗位 JSON、日报全部本地留存，不上传第三方服务器；`llm.api_key` 仅用于请求头，日志禁止打印；简历/意向与消息全文不进日志（只记长度/数量摘要）。
- **抓取安全**：网页抓取设超时（`web_timeout`）与页面大小上限（`web_max_bytes`）；不处理需登录、JS 强渲染页面；不需要时用 `enable_web_fetch` / `enable_ocr` 关闭。
- **故障隔离原则**：单条消息的下载/OCR/抓取/解析/打分/归档失败一律标记后继续，不阻断当日整体流程；唯独日报写入失败（STO.REPORT.001）记录 ERROR 后降级退出，不影响已归档数据。

---

## 开发规范（摘自 rule.md）

1. 一次只实现一个模块，完成后再进入下一模块；
2. 代码附带精简注释，重要设计决策写入对应 docs 文档；
3. 每次新增功能同步更新根目录 CHANGELOG.md；
4. 架构、错误码、异常、工具相关说明放到 docs/；
5. 输出文件第一行标明文件名；
6. 严禁智能体修改 rule.md；产品方案.md 的修改需经用户授权（2026-09-20 起用户已授权按版本迭代同步）。
