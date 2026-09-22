# 外部集成说明

## 吉客云（MCP）
- 传输：Streamable HTTP，POST JSON-RPC 2.0 到 `JACKYUN_MCP_URL`（`/mcp/messages`）
- 认证：`Authorization: <JACKYUN_MCP_TOKEN>`（吉客云 MCP 实测为裸 Token，不能加 `Bearer`），会话经 `Mcp-Session-Id` 保持
- 白名单：仅调用已确认订阅的 14 个 method（见 `docs/jackyun-mapping.md`），禁止发明 API
- 安全：Token 只存服务器 .env；此前暴露的旧 Token 必须重置作废
- 行为：首次响应存 `raw_api_payloads`；限流 0.35s/次；失败写 `sync_logs` + 异常中心

## 吉客云销售订单三通道

- 统一入口：`GET /api/v1/jky-orders/status`、`POST /api/v1/jky-orders/sync`、`GET /api/v1/jky-orders/jobs`
- 默认优先级：Web 网页同步 → Windows 桌面 RPA 导出 → 吉客云 OpenAPI/MCP；由 `JKY_ORDER_PROVIDER_PRIORITY` 调整。
- 三个通道只返回原始订单行，统一经过规范化、身份识别、去重和幂等 upsert，最终写入 `sales_orders`；订单来源与来源历史保存在订单记录中。
- Web 通道需要签名密钥与已保存的网页登录态；RPA 通道需要 Windows Agent 的内网 URL 和 Token；API/MCP 通道复用服务端吉客云 MCP 凭证。
- 通道未配置、登录过期、需要人工验证或业务权限拒绝时如实记录并自动尝试下一通道，不生成假订单；只有成功且通过低数量/身份校验的通道才推进同步断点。
- RPA Agent 约定：`GET /health`，`POST /sync`，请求包含 `startTime`、`endTime`、`reportType=sales_orders`、`format=xlsx`；响应可为订单 JSON、Base64 文件或下载地址。
- 当前生产 `.env` 保持 `JACKYUN_SYNC_MODE=manual` 时，Beat 不会自动触发吉客云任务；可通过“自动化 → 立即同步订单”或 API 手动验证。不要把“已排队”当成“已同步成功”。

## 1688 开放平台（Phase 4）
- OAuth 只读授权买家订单；每天同步 1 次 + 手动立即同步
- 不下单、不付款；未配置时页面显示"等待 1688 开放平台配置"
- 真实 method 以应用创建后的实际权限列表为准，不假定发票接口存在

## 浙江农信（文件导入）
- 不做 API 直联、不做网页 RPA；每月人工上传 XLSX/PDF/ZIP
- 落盘 `{DATA_DIR}/finance/{公司}/{年}/{月}/original/{类别}/`，SHA256 + 版本化，同名不覆盖

## 财务邮件（Phase 6）
- SMTP 人工确认后发送；月度 ZIP 原样交付；first/resent 发送记录分离

## 税务系统发票清单（文件导入）
- 来源：用户从已登录的税务系统下载的官方 `.xlsx` / `.csv` 清单；不做绕过登录、验证码或网页 RPA。
- 入口：`POST /api/v1/tax-invoices/imports`，前端页面 `/tax-invoices`。
- 保留：`tax_invoice_imports` 保存文件指纹、原件路径、表头和 mapping；`tax_invoice_import_records` 保存逐行原始值；`tax_invoices` 保存标准字段；`tax_invoice_links` 保存可审计业务关联。
- 识别：发票代码/号码、类型、开票日期、购销方和税号、金额/税额/价税合计、进销项、状态；未知字段和无法确认的行进入待核对，不丢弃。
- 匹配：仅按官方清单明确的关联单号匹配 1688 采购单/吉客云采购单/销售单；不按金额或名称模糊猜测。
