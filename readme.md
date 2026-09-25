# Excel Responses Proxy

一个只保留 ChatGPT Excel 插件上游的本地 OpenAI Responses 代理。

## 启动

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\excel_proxy.py
```

服务地址：

- `http://127.0.0.1:8000/v1/responses`
- `http://127.0.0.1:8000/v1/models`
- `http://127.0.0.1:8000/api/config/excel-session`
- `http://127.0.0.1:8000/api/config/api-key`

启动前请在 Microsoft Excel 的 ChatGPT 插件中登录一次。代理从 Excel WebView2 本地缓存读取 session，不需要 GitHub 账号，也不安装 Copilot SDK。

## 客户端 API Key

`/v1/models` 和 `/v1/responses` 强制校验 API Key，缺失或错误一律返回 `401`。

- 首次启动自动生成 `sk-ghcpexcel-...`，保存在 `%APPDATA%\ghcp_proxy\api-key.json`。
- 控制台 `http://127.0.0.1:8000/ui` 可以查看、复制、重新生成，或设置自定义 key（16-128 位可打印 ASCII）。
- 客户端使用 `Authorization: Bearer <key>`，也接受 `x-api-key` 头。

## 模型

- `gpt-6-astra`（默认）
- `gpt-5.6-sol`
- `gpt-5.6-luna`
- `gpt-5.6-terra`

三种别名分别映射到 Basispoints 的 `gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol`。Excel Responses 的输入转换、prompt cache key、session 持久化、流式输出和客户端工具调用桥接均保留。

## 说明

上游固定为 `https://bps.openai.com/basispoints/api/responses`。这是非官方兼容代理，请只使用自己的 Excel/ChatGPT 会话并遵守服务条款和使用限制。
