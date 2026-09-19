# 服务端工具安装说明（2026-09-19 更新：LLM 已由 One API 网关切换为 DeepSeek 官方 API 直连，One API 下线；新增历史补拉 NapCat HTTP 服务）

## NapCat v4.18.28（QQ 消息监听，只读）
> 注意：`tools/NapCat/NapCat.Shell.zip` 是第三方二进制（约 28MB），未纳入 git 仓库，需自行下载。
- 官方发布页：https://github.com/NapNeko/NapCatQQ/releases
  - Windows：下载 `NapCat.Shell.zip`（即本机使用的版本）
  - macOS / Linux：**不要用 Windows 的 Shell.zip**，推荐用官方 Docker 镜像（见 https://napcat.apifox.cn/ ），或在 Linux 上使用终端版
  - 前置条件：本机已装 QQ（当前 9.9.21.38711，满足最低要求 29271+；若启动异常，官方推荐 QQ 9.9.26.44343）
- 解压位置：C:\Users\26440\NapCat （官方要求：路径不能含中文/空格，故放在 C 盘根用户目录）
- 启动：双击 C:\Users\26440\NapCat\launcher.bat
  （或双击 NapCatWinBootMain.exe；Docker 部署则按官方文档用 docker compose 启动）
- 登录：启动后需扫码/登录 QQ；WebUI 默认随机密码（在控制台查看）
- 安全：登录后请在 WebUI 中关闭所有发送接口/自动回复，纯只读模式
- **历史补拉 HTTP 服务（v0.6.4 起）**：编辑 C:\Users\26440\NapCat\config\onebot11_2644081990.json 的 "httpServers" 数组，
  元素**必须包含 "name" 唯一标识**（如 {"name":"http-backfill","enable":true,"host":"127.0.0.1","port":3002,...}），
  保存后重启 NapCat 生效；本机已配置并验证（端口 3002，get_group_msg_history 接口返回 200）

## DeepSeek 官方 API（OpenAI 兼容 LLM 直连，无中间网关）
- 注册/登录：platform.deepseek.com（DeepSeek 开放平台），按量计费
- 创建 API Key：「API Keys」页面 → 创建 → 复制 sk-...（仅创建时完整显示一次）
- 官方 OpenAI 兼容地址：https://api.deepseek.com/v1（求职助手 llm.base_url 指向此处）
- 模型名：deepseek-chat（通用对话）/ deepseek-reasoner（深度推理）
- 核验用量：DeepSeek 开放平台 →「用量信息」可查看 Token 消耗，与程序日志、调用埋点三方对齐

## 已弃用：AstrBot / One API
- AstrBot 4.x 不提供标准 OpenAI 兼容 /chat/completions 端点（实测 405），已弃用；虚拟环境仍在 C:\Users\26440\AppData\Local\astrbot\venv，如不再需要可删除
- One API 网关（v0.6.10）已下线：为避免中间层调用去向不可直接验证，LLM 改为直连 DeepSeek 官方 API（v0.6.5）；可执行文件仍在 C:\Users\26440\OneAPI\one-api.exe，如不再需要可删除（含其数据目录）

## 对接求职助手
1. 启动 NapCat（QQ 登录）→ 配置正向 WebSocket（OneBot v11，默认 ws://127.0.0.1:3001）+ HTTP 服务（端口 3002，历史补拉用）
2. 在 platform.deepseek.com 创建 DeepSeek API Key
3. 编辑 job_assistant/config/config.yaml：llm.base_url=https://api.deepseek.com/v1，llm.api_key=DeepSeek 官方 API Key，llm.model=deepseek-chat
4. 运行：python -m job_assistant.main（在项目根目录）
5. 启动后日志出现「历史消息补拉完成: 群数=... 拉取=... 进入链路=...」即代表休眠/重启期间错过的群消息已补齐

## V2.0 Web 控制台（v0.6.3 起，查看岗位/日报/日志）
- 启动：python -m job_assistant.web.server（在项目根目录，默认 http://127.0.0.1:8080）
- 页面：岗位看板（统计 + 分数分布图 + 表格 + 详情弹窗）、日报预览（一键复制）、实时日志（2.5s 自动刷新）、状态与配置（只读）
- 停止：Ctrl+C，或任务管理器结束对应 python 进程
