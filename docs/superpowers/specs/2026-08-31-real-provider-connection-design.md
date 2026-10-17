# 真实模型连接设计

**日期：** 2026-08-31  
**状态：** 已用户确认，作为下一阶段实施依据  
**平台：** Windows 本机  
**对象：** 单用户、多智能体协作桥梁

## 1. 目的与范围

本设计把当前 Fake 第一纵向切片扩展为第一条真实模型连接：通过桌面端配置一个 ChatGPT OpenAI 兼容中转站，执行连接测试，并让真实模型请求沿用现有 M2 网关、M5 持久化、M6 本地通信和 M7 工作台链路。

本阶段只实现 OpenAI 兼容协议。Claude 的 Anthropic Messages 适配器和 Z.AI 适配器预留同一 Provider 注册边界，暂不在本阶段实现。真实文件写入、Shell、Git、下载、外部内容执行和完整多智能体编排仍保持关闭。

## 2. 已确认的产品决策

- 第一条真实连接是用户正在使用的 ChatGPT 中转站。
- 中转站按 OpenAI 兼容接口接入，支持自定义 Base URL。
- 流式能力未知，由连接测试自动探测；同时兼容流式和非流式响应。
- 支持多个命名连接，例如“ChatGPT 中转站”“Claude 中转站”“未来官方账号”。
- API Key 保存到 Windows 凭据管理器，不保存到明文文件、SQLite、事件或日志。
- 连接配置、模型列表和连接测试使用 `LOCAL_CONFIG` 作用域。
- 真实模型请求继续使用现有 `TASK` 作用域、`ModelProvider` 和 `ModelGateway`。
- 用户只使用本地桌面软件；不读取或控制 ChatGPT、Claude、Zcode 的桌面窗口，不读取 Cookie 或客户端凭据。

## 3. 调研与技术选择

本轮检查了成熟开源项目的许可证、维护状态和适配成本：

| 项目 | 用途判断 | 结论 |
|---|---|---|
| `openai/openai-python` | OpenAI 请求和流式协议参考 | Apache-2.0，维护活跃；可作为协议参考，不把核心边界锁定到 SDK |
| `encode/httpx` | Python 异步 HTTP、超时和流式传输参考 | BSD-3-Clause，维护活跃；实施阶段可作为轻量 HTTP 传输依赖 |
| `BerriAI/litellm` | 多供应商统一网关 | 覆盖广但依赖和运行复杂度明显更高，本阶段不作为核心依赖 |
| `CherryHQ/cherry-studio` | 桌面交互参考 | AGPL-3.0，产品范围较大，只参考交互，不嵌入核心 |
| `open-webui/open-webui` | 完整模型工作台参考 | 范围远超本项目，只参考连接管理思路 |

推荐保持现有 Provider 抽象，在 Python 内实现轻量 OpenAI 兼容适配器。具体依赖版本、许可证和 Windows 兼容性在实施前再次核对并锁定。

## 4. 总体架构

```text
Electron renderer
    | 仅发送非敏感配置命令、接收脱敏状态
    v
Electron main / 本地 M6 连接
    v
Python LocalConfigService
    |-- ProviderProfileStore -> SQLite 元数据与 LOCAL_CONFIG 事件
    |-- CredentialStore ------> Windows 凭据管理器
    |-- ProviderRegistry
              v
    OpenAICompatibleProvider
              v
        ModelGateway (M2)
          |             |
          v             v
    临时模型帧(M6)   正式候选事件 -> M5 -> M6 -> M7
```

职责边界：

- `LocalConfigService` 只处理连接元数据、凭据操作请求、模型列表和连接测试。
- `ProviderProfileStore` 保存非敏感连接元数据，并通过 M5 保存配置事实。
- `CredentialStore` 负责 Windows 凭据管理器的写入、读取和删除。
- `ProviderRegistry` 根据 `profile_id` 选择适配器，不让上层依赖供应商细节。
- `OpenAICompatibleProvider` 只负责 HTTP 请求组装、响应解析、取消和 Provider 错误转换。
- `ModelGateway` 继续负责统一事件、临时帧、取消门、三段超时、有限重试和脱敏。
- M6 只负责本地认证、基础格式校验、命令登记、转交和发布。
- M7 只显示配置状态、流帧和正式事件，不直接访问供应商或凭据。

## 5. 连接配置模型

每个连接配置包含以下字段：

| 字段 | 是否敏感 | 说明 |
|---|---:|---|
| `profile_id` | 否 | 稳定唯一 ID |
| `display_name` | 否 | 用户可读名称 |
| `protocol` | 否 | 第一阶段固定为 `openai_compatible` |
| `base_url` | 否 | 中转站地址，规范化去除末尾 `/` |
| `model_name` | 否 | 默认模型，可为空以便先做基础测试 |
| `enabled` | 否 | 是否允许任务选择 |
| `credential_ref` | 否 | 指向 Windows 凭据项的不可逆或不透明引用 |
| `credential_status` | 否 | `UNCONFIGURED`、`CONFIGURED`、`UNAVAILABLE` |
| `capabilities` | 否 | 模型列表、流式能力及探测时间 |
| `last_test_status` | 否 | `NEVER`、`PASSED`、`FAILED` |
| `last_test_error` | 否 | 脱敏后的错误类别和简短说明 |
| `created_at` / `updated_at` | 否 | 配置时间 |

API Key 不属于 ProviderProfile 的可序列化字段。Windows 凭据项的命名必须由 `profile_id` 稳定派生，删除连接时同时删除该项。

第一阶段只使用标准 `Authorization: Bearer` 认证，不允许把任意自定义密钥头作为普通配置保存。未来如有必要，应增加显式认证模式并单独评审其安全边界。

## 6. 凭据安全边界

- API Key 由 Windows 凭据管理器托管。
- 页面只收到连接名称、掩码状态和成功/失败结果。
- API Key 不进入 React 状态、浏览器持久化、SQLite、`CommandEnvelope`、`EventEnvelope`、日志或错误消息。
- 密钥录入使用主进程触发的系统安全输入通道；实现可以使用 Windows 原生安全凭据 UI 或等价的一次性受控通道，但必须满足“renderer 不可读取、不回显、不持久化”的不变量。
- Provider 只在发送边界按 `profile_id` 读取凭据，发送完成后释放内存引用。
- 凭据管理器不可用或读取失败时安全失败，禁止退回明文文件、环境变量或数据库。
- 所有供应商错误和连接测试输出先执行脱敏，再进入 UI、事件或日志。

## 7. LOCAL_CONFIG 命令

以下命令经过 M6 命令登记、幂等检查和 M5 持久化：

- `LIST_PROVIDER_PROFILES`
- `UPSERT_PROVIDER_PROFILE`
- `DELETE_PROVIDER_PROFILE`
- `SET_PROVIDER_CREDENTIAL`
- `CLEAR_PROVIDER_CREDENTIAL`
- `LIST_PROVIDER_MODELS`
- `TEST_PROVIDER_CONNECTION`
- `SET_ACTIVE_PROVIDER_PROFILE`

命令载荷只包含 `profile_id`、名称、协议、Base URL、模型名和测试选项等非敏感内容。`SET_PROVIDER_CREDENTIAL` 只携带目标连接 ID 和一次性录入请求，不携带 API Key 原文。

配置命令是配置事实，不是任务事实：

- 不要求 `task_id` 或 `run_id`。
- 不得改变任务状态机。
- 不得获得项目文件、Shell、Git、审批或 WriteLease 权限。
- 相同幂等键重放返回既有命令结果。
- 相同幂等键但摘要不同必须返回冲突，不得覆盖已有配置。

## 8. OpenAI 兼容 Provider

### 8.1 请求

- 支持根地址和带 `/v1` 的 Base URL。
- Provider 负责规范化 `/models` 和 `/chat/completions` 路径，避免重复 `/v1`。
- 请求头包含标准 `Authorization`、`Content-Type` 和按模式需要的 `Accept`。
- `ModelRequest` 的消息、系统提示词、模型名和超时策略转换为 OpenAI 兼容请求。
- Provider 不创建正式事件，不写数据库，不访问项目文件。

### 8.2 流式响应

- 使用 `stream=true` 请求并解析 SSE 的 `data:` 行。
- 从 `choices[0].delta.content` 提取文本增量。
- 解析 `finish_reason`、可选 usage 和 `[DONE]`。
- 角色切换、工具字段和空增量不伪造成文本。
- 响应格式不符合预期时产生 `PROTOCOL` 类 `ProviderError`。

### 8.3 非流式响应

- 使用 `stream=false` 兼容只支持完整响应的中转站。
- 从 `choices[0].message.content` 取得完整文本。
- 转换为一个统一 `DELTA`，然后产生 `COMPLETED`。
- 上层只消费统一 `ProviderEvent`，不感知供应商的流式差异。

### 8.4 取消、超时和重试

- `request_cancel(operation_id)` 取消对应 HTTP 请求。
- M2 网关确认取消后，后续增量全部丢弃。
- 连接、首字节和流式空闲超时复用现有 `TimeoutPolicy`。
- 只在收到有效内容前进行有限重试。
- 收到内容后失败不自动重发完整请求。

### 8.5 错误归一化

| 情况 | 类别 | 默认是否可重试 |
|---|---|---:|
| 401、403 | `AUTH` | 否 |
| 429 | `RATE_LIMIT` | 是 |
| 连接失败、DNS、断线 | `NETWORK` | 是 |
| 连接或读取超时 | `TIMEOUT` | 是 |
| 400、响应结构错误 | `PROTOCOL` | 否 |
| 500、502、503、504 | `SERVER` | 是 |
| 用户取消 | `CANCELLED` | 否 |
| 其他未识别情况 | `UNKNOWN` | 否 |

错误载荷只保留类别、可重试性、超时阶段和脱敏说明，不保存完整响应体、请求头或供应商凭据。

## 9. 连接测试

应用启动时不自动访问供应商。所有网络动作必须由用户主动触发。

### 9.1 基础连接测试

- 检查 Base URL 格式和协议。
- 检查 Windows 凭据是否存在。
- 尝试 `GET /models`。
- 成功时返回模型 ID 列表。
- 中转站不支持模型列表时，不把该情况判为连接失败，允许手动填写模型名。

### 9.2 模型能力测试

- 要求已有模型名。
- 使用极小请求验证模型可用性。
- 默认限制为 `max_tokens=1`，并请求 `stream=true`。
- 记录是否收到有效 SSE 增量、首字节耗时和总耗时。
- UI 明确提示该测试可能产生极少量模型用量。

测试结果只保存状态元数据：认证是否成功、模型是否可用、流式能力、延迟、时间和脱敏错误。测试请求及响应正文不保存，不创建 TASK，不触发工具或审批。

## 10. 故障与重启

- 认证错误、协议错误不自动重试。
- 限流、网络、服务端错误和超时沿用 M2 有限重试。
- 模型失败产生模型操作失败事件，不直接把任务切换为恢复态；由 M1 决定重试、继续或请求用户介入。
- 软件重启后只恢复连接元数据；密钥仍按需从 Windows 凭据管理器读取。
- 未完成模型操作标记为未知或失败，不在启动时自动重发。
- 删除连接后，后续新请求立即拒绝；正在进行的请求按照取消或自然结束规则收尾。

## 11. 桌面交互

连接设置页支持连接列表、新增、编辑、删除、密钥配置、清除密钥、获取模型、手动填写模型、连接测试、启用/停用和设置默认连接。

页面展示：

- 连接名称、协议、Base URL、模型名。
- 凭据状态：未配置、已配置、读取失败。
- 模型列表支持状态。
- 流式能力：支持、不支持、未知。
- 最近测试结果和脱敏错误。

页面不展示 API Key 原文，不把密钥写入 URL、开发者日志、剪贴板、浏览器存储或导出配置。

## 12. 测试与验收

### 12.1 Provider 单元测试

- SSE 增量、`[DONE]`、完成原因和 usage。
- 非流式响应转换。
- 空增量、异常 JSON 和错误响应。
- 401、403、429、5xx、网络失败和三段超时分类。
- 取消后丢弃后续增量。
- 错误脱敏。
- `/models` 成功、不支持和异常格式。

### 12.2 配置服务测试

- 多连接新增、编辑、删除、启用和默认连接。
- 幂等重放与摘要冲突。
- SQLite 不出现 API Key。
- 凭据管理器读写失败时安全失败。
- 删除连接同步清除凭据。

### 12.3 跨模块和桌面测试

- `LOCAL_CONFIG` 命令经过 M6 登记后到达配置服务。
- 配置事实由 M5 先持久化再发布。
- 连接测试不创建 TASK 或工具事件。
- 真实 Provider 经过 M2、M5、M6 后由 M7 显示。
- 重启后元数据恢复且密钥不进入页面。
- 页面、日志、事件和数据库扫描不到测试密钥。
- 现有 Fake 任务、Fake 工具、断线补发和全部回归测试继续通过。

### 12.4 真实人工验收

1. 创建一个命名连接。
2. 通过 Windows 安全凭据录入 API Key。
3. 执行基础连接测试。
4. 获取模型列表或手动填写模型名。
5. 执行模型能力测试。
6. 运行一次真实模型任务。
7. 验证流式显示、正式事件、重连恢复和错误提示。
8. 删除连接并确认后续请求被拒绝。

## 13. 完成定义

本阶段只有同时满足以下条件才可标记完成：

- OpenAI 兼容中转站能从桌面端配置并完成一次真实模型请求。
- 流式和非流式响应均能转换为现有统一 Provider 事件。
- API Key 不出现在页面状态、数据库、日志、事件、请求错误或导出配置。
- 认证、限流、网络、超时和协议错误可以区分。
- 现有后端和桌面端回归测试全部通过。
- README、`docs/current-status.md` 和 Provider 模块说明已同步。
- 真实工具执行仍保持关闭。

## 14. 明确不在本阶段

- Anthropic Messages 和 Z.AI 真实适配器。
- 读取或控制 ChatGPT、Claude、Zcode 桌面客户端。
- 自动多智能体分工、公开群聊、互证和裁决工作流。
- 真实文件、Shell、Git、下载、安装依赖和外部内容执行。
- Windows 安装包、升级、卸载和远程服务。
