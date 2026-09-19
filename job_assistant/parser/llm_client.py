# job_assistant/parser/llm_client.py

"""LLM 调用封装（模块四/五共用）：DeepSeek 官方 OpenAI 兼容 HTTP API。

设计决策（对应 ARCHITECTURE.md 各层职责"LLM（DeepSeek 官方直连）"）：
    - 统一 complete() 接口（prompt + system + temperature），分类/抽取/打分复用
    - 默认实现 HttpLLMClient 走 OpenAI 兼容 /chat/completions（DeepSeek 官方 API，无中间网关）
    - 测试通过注入替身（duck typing，满足 complete 签名即可），不依赖真实服务
    - base_url / api_key / model / timeout 全部来自配置（llm 段），无硬编码
    - api_key 属敏感信息：日志禁止打印（docs/日志规范.md）
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..errors import LLMCallError

logger = logging.getLogger(__name__)

# LLM 输出的默认采样温度：分类/抽取用低温度提高确定性
_DEFAULT_TEMPERATURE = 0.2


class LLMClient:
    """LLM 客户端协议（抽象基类）。

    子类需实现 complete()；测试可直接注入满足同一签名的替身对象。
    """

    def complete(
        self,
        prompt: str,
        system: str = "",
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> str:
        """执行一次 LLM 补全。

        Args:
            prompt: 用户侧提示（待分类/抽取的消息文本等）。
            system: 系统提示（角色与输出约束）。
            temperature: 采样温度（越低越确定，分类/抽取默认 0.2）。

        Returns:
            LLM 返回的文本内容。

        Raises:
            LLMCallError: 网络错误、超时、HTTP 非 2xx、响应结构非法（PAR.LLM.001）。
        """
        raise NotImplementedError("LLMClient 子类必须实现 complete()")


class HttpLLMClient(LLMClient):
    """基于 OpenAI 兼容 HTTP API 的 LLM 客户端（默认实现）。

    调用形态：POST {base_url}/chat/completions
        - model 未配置时不发送 model 字段（由服务端使用默认模型）
        - api_key 非空时携带 Authorization: Bearer <key>；为空不携带
    """

    def __init__(self, config) -> None:
        self._config = config
        # 统一去掉末尾斜杠，拼接 chat/completions 端点
        self._endpoint = config.base_url.rstrip("/") + "/chat/completions"

    def complete(
        self,
        prompt: str,
        system: str = "",
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
        }
        if self._config.model:
            payload["model"] = self._config.model

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self._config.api_key:
            request.add_header("Authorization", f"Bearer {self._config.api_key}")

        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout) as resp:
                response = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 4xx/5xx：HTTP 错误（可重试类，含状态码便于定位）
            raise LLMCallError(
                f"LLM 接口返回 HTTP {e.code}: {self._endpoint}"
            ) from e
        except urllib.error.URLError as e:
            # 连接失败 / 超时 / DNS 错误（可重试类）
            raise LLMCallError(f"LLM 调用失败（网络/超时）: {e.reason}") from e
        except json.JSONDecodeError as e:
            # 服务端返回非 JSON（响应结构非法）
            raise LLMCallError(f"LLM 响应不是合法 JSON: {e}") from e
        except TimeoutError as e:
            raise LLMCallError(f"LLM 调用超时（timeout={self._config.timeout}s）") from e

        # 解析 choices[0].message.content；结构异常同样按调用失败处理
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMCallError(f"LLM 响应缺少 choices/message/content 字段: {response}") from e

        text = str(content).strip()
        if not text:
            raise LLMCallError("LLM 返回空内容")
        logger.info("LLM 调用完成: endpoint=%s len=%d", self._endpoint, len(text))
        return text
