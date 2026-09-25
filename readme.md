# Codex Excel Proxy

把 ChatGPT Excel 插件（Basispoints）的上游收敛成一个本地 OpenAI Responses 兼容代理。

只做一件事：用你 Excel 插件里已经登录好的 ChatGPT 会话，转发 `/v1/responses`。没有第二个上游，没有 GitHub/Copilot 代码。

## 前提

Microsoft Excel 里装好官方 ChatGPT 插件，并且登录过一次：

- 插件名 `ChatGPT`，发布者必须是 **OpenAI, LLC**（商店里同名山寨件很多，认发布者）
- 路径：Excel → 插入 → 获取加载项 → 应用商店 → 搜 `ChatGPT`

代理从插件 WebView2 的 LocalStorage 里读 `bps_auth_tokens`，不需要另外的账号、OAuth 或 SDK。

## 启动

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\excel_proxy.py
```

- 控制台：`http://127.0.0.1:8000/ui`
- 启动时会自动从 Excel 缓存读一次凭据，并用 Windows DPAPI 加密落盘，之后重启不用重新登录

## 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/models`、`/models` | 模型清单，需要 API Key |
| POST | `/v1/responses`、`/responses` | Responses API，流式透传 + 工具调用桥接，需要 API Key |
| POST | `/v1/chat/completions`、`/chat/completions` | Chat Completions 兼容层，文本 + function 工具，需要 API Key |
| GET | `/api/config/excel-session` | 凭据状态（是否已配置、是否过期、读取方式） |
| POST | `/api/config/excel-session` | 立刻重新从 Excel 读一次凭据 |
| DELETE | `/api/config/excel-session` | 清掉本地凭据 |
| GET/POST | `/api/config/api-key` | 查看 / 重新生成 / 设置自定义 API Key |
| GET | `/`、`/ui` | 中文控制台 |

## 模型

对外模型名和上游模型名是 1:1，不做重命名，也不存在 `-excel` 后缀别名。

| 模型 | 上游 | 上下文 | 推理档位 |
| --- | --- | --- | --- |
| `gpt-6-astra`（默认） | `gpt-6-astra` | 272k | low / medium / high / xhigh |
| `gpt-5.6-sol` | `gpt-5.6-sol` | 272k | low / medium / high / xhigh |
| `gpt-5.6-luna` | `gpt-5.6-luna` | 200k | low / medium / high / xhigh |
| `gpt-5.6-terra` | `gpt-5.6-terra` | 272k | low / medium / high / xhigh |

宿主可用性（实测 `200` / `403`）：

| 模型 | Excel | Word | PowerPoint | Outlook | OneNote | Word 混搭 |
| --- | --- | --- | --- | --- | --- | --- |
| `gpt-6-astra` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `gpt-5.6-sol` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `gpt-5.6-luna` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `gpt-5.6-terra` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

以下模型上游一律返回 `403`，因此不对外暴露：`gpt-6-sol`、`gpt-6-luna`、`gpt-6-terra`、`gpt-5.5`。以后如果放开，只需在 `EXCEL_MODEL_UPSTREAMS` 里加一行。

推理档位没有 `max`，最高是 `xhigh`。

## Chat Completions 兼容

给只会说 `/v1/chat/completions` 的客户端用。代理内部仍然只发一次 Responses 请求，没有第二个上游。

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"model":"gpt-6-astra","messages":[{"role":"user","content":"你好"}]}'
```

- `system` / `developer` 消息映射到上游 `instructions`，`user` / `assistant` / `tool` 映射到 Responses 输入项
- `stream: true` 返回标准 `chat.completion.chunk`，结尾是 `data: [DONE]`
- `tools` 支持 `type: function`：调用时返回 `finish_reason: "tool_calls"`，回传 `role: "tool"` 结果即可续跑。上游走 `run_officejs` 传输通道，内部会被还原成你声明的工具名，不会泄漏成客户端可见的调用
- 图片/音频 content 直接返回 `400`，上游是纯文本，不假装支持
- `temperature`、`top_p`、`max_tokens` 不转发：Excel 的上游线格式不带这些字段
## 客户端 API Key

`/v1/models` 和 `/v1/responses` 强制校验，缺失或错误一律 `401`。

- 首次启动自动生成 `sk-ghcpexcel-...`，存放在 `%APPDATA%\ghcp_proxy\api-key.json`
- 控制台可以查看、复制、重新生成，或设置自定义 key（16-128 位可打印 ASCII）
- 客户端用 `Authorization: Bearer <key>`，也接受 `x-api-key` 头

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GHCP_EXCEL_RESPONSES_URL` | `https://bps.openai.com/basispoints/api/responses` | 上游地址 |
| `GHCP_EXCEL_UPSTREAM_MODEL` | 空 | 强制覆盖所有请求的上游模型 |
| `GHCP_EXCEL_FORWARD_PROMPT_CACHE_KEY` | `1` | 转发 `prompt_cache_key` |
| `GHCP_EXCEL_CATALOG_AT_PROMPT_END` | `0` | 工具目录放 prompt 末尾的兼容开关 |
| `GHCP_EXCEL_WEBVIEW` | 自动探测 | 手动指定 WebView2 根目录 |
| `GHCP_CONFIG_DIR` / `GHCP_STATE_DIR` / `GHCP_CACHE_DIR` | `%APPDATA%` / `%LOCALAPPDATA%` | 运行数据目录 |

## 数据文件

| 内容 | 位置 |
| --- | --- |
| API Key | `%APPDATA%\ghcp_proxy\api-key.json` |
| 加密会话 | `%LOCALAPPDATA%\ghcp_proxy\excel-session.dpapi` |
| Excel 插件凭据源 | `%LOCALAPPDATA%\Microsoft\Office\16.0\Wef\webview2\*\EBWebView\Default\Local Storage\leveldb` |

## macOS

凭据来自 Excel WebKit 的 LocalStorage，用自带脚本手动提交一次：

```sh
python3 tools/prime-excel-session-macos.py
```

## 免责声明

上游固定为 `https://bps.openai.com/basispoints/api/responses`。这是非官方兼容代理，只使用你自己的 Excel/ChatGPT 会话，并自行遵守服务条款与使用限制。
