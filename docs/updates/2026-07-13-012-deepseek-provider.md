# DeepSeek 模型供应商接入

日期：2026-07-13

## 目标

让证据约束的基金投资 Agent 直接调用 DeepSeek 官方 API，同时保持现有真实数据、结构化输出、风险门禁、Evidence 和审计链契约。模型不可用时只报告不可用，不允许回退到模板或模拟建议。

## 接入边界

- 供应商：`deepseek`
- Base URL：`https://api.deepseek.com`
- API 风格：`chat_completions`
- 默认部署模型：`deepseek-v4-flash`
- 可选深度模型：`deepseek-v4-pro`
- 专用密钥变量：`DEEPSEEK_API_KEY`
- 结构化输出：`response_format={"type":"json_object"}`，随后继续通过本项目 Pydantic Schema 和业务门禁校验
- 模型仍然不能直接调用行情、新闻、持仓或交易工具，只能合成已经持久化并带 Evidence ID 的上下文

官方参考：

- [DeepSeek API 快速开始](https://api-docs.deepseek.com/)
- [JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)
- [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)
- [模型与价格](https://api-docs.deepseek.com/quick_start/pricing?article_id=article_1779470751466_8)

## 可靠性处理

1. 网关确保 Chat Completions 的 system prompt 明确包含 JSON 输出要求。
2. DeepSeek 默认发送 `thinking.type=disabled`，降低批量任务延迟并提高结构化输出稳定性。
3. 可显式配置 `LLM_THINKING_MODE=enabled`，并用 `LLM_REASONING_EFFORT=high|max` 调整推理强度。
4. HTTP 429、5xx、连接异常、非 JSON 响应以及 HTTP 200 但内容为空时，在 `LLM_RETRY_COUNT` 上限内重试。
5. 重试耗尽后保存真实错误状态，不生成替代建议。
6. 返回内容仍需经过 JSON 解析、Pydantic Schema、Evidence ID、动作一致性、利润承诺和提示注入检查。

## 安全与隐私

- API Key 只能存在于服务器权限为 `600` 的环境文件中。
- 状态接口只返回供应商、模型、端点主机、处理地域和配置完整性，不返回 Key。
- Run、Step、Evidence、Claim 和 Audit 不保存 Key 或 Authorization Header。
- 在登录、租户隔离和用户同意完整上线前，保持 `LLM_PRIVATE_CONTEXT_ENABLED=false`。
- 用户姓名、账户号、截图原文和原始持仓流水不得发送给外部模型。

## 服务器配置

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=在服务器本机填写
LLM_THINKING_MODE=disabled
LLM_DATA_REGION=cn
LLM_PRIVATE_CONTEXT_ENABLED=false
```

```bash
sudo chmod 600 /etc/stock-assistant/stock-assistant.env
sudo systemctl restart stock-assistant-api
curl -s http://127.0.0.1:8000/api/v1/agent/model/status
```

状态接口返回 `configured=true` 只证明配置完整。还必须创建一笔真实基金研究 Run，并确认模型结果、响应 ID、Token 用量、Evidence 引用和审计链均可核验，才能判定接入完成。

## 验收标准

- `DEEPSEEK_API_KEY` 能被专用变量读取，且不出现在公共状态中。
- 请求发送至官方 `/chat/completions` 端点。
- 请求包含 JSON Output 和明确 JSON 提示。
- DeepSeek 思考模式配置可控，非法值阻止启动模型调用。
- 第一次返回空内容、第二次返回有效内容时调用成功且记录真实第二次用量。
- 空响应重试耗尽、鉴权失败、限流或上游故障时明确失败，不回退模拟数据。
- 现有 OpenAI、DashScope、Agent Run、批次和确定性基金分析测试继续通过。
