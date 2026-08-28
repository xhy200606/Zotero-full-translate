# Zotero-full-translate Cloud API

所有 REST 请求（SSE 除外）使用：

```text
X-API-Key: <ZFT_API_KEY>
```

## PDF 历史预检（推荐客户端先调用）

`POST /api/v1/jobs/lookup`，JSON：

```json
{
  "source_sha256": "<64-char sha256>",
  "lang_in": "en",
  "lang_out": "zh-CN",
  "pages": null,
  "output_mode": "mono"
}
```

命中时返回 `found=true` 和已完成 Job。Zotero v0.2.18 会在读取/上传 PDF 正文之前先做这个查询，因此命中历史后不会重新上传、解析或翻译。

## 创建任务

`POST /api/v1/jobs`，multipart/form-data。

字段：

```text
file                PDF，必填
lang_in             默认 en
lang_out            默认 zh-CN
pages               可选
output_mode          mono | dual | both
providers            可选，逗号分隔，如 baidu,tencent,aliyun
provider_strategy    balanced | failover
client_id            可选
client_request_id    可选，和 client_id 组成幂等键
client_item_key      可选
```

如果省略 `providers` 和旧版 `provider`，服务端自动使用 `RuntimeConfig.default_provider_ids`。

即使客户端没有先调用 `/lookup`，上传后服务器仍会计算 SHA-256；若命中历史，会创建一个立即完成的复用 Job，不再执行 BabelDOC。

## Provider API

```text
GET  /api/v1/providers
PUT  /api/v1/providers/{id}
POST /api/v1/providers/{id}/test
GET  /api/v1/providers/{id}/quota
POST /api/v1/providers/{id}/quota/reset
```

内置 Provider IDs：

```text
baidu
openai_compatible
tencent
volcengine
aliyun
```

Provider 返回值包含 `quota`：

```json
{
  "status": "ok|low|exhausted|unavailable|metered|unknown",
  "source": "manual_budget+local_meter",
  "total_chars": 1000000,
  "used_chars": 420000,
  "remaining_chars": 580000,
  "remaining_percent": 58.0,
  "dispatch_weight": 0.8136,
  "last_error": null
}
```

`total_chars=0/未配置` 时，ZFT 不伪造云厂商官方余额；`remaining_chars` 会为 null，但仍记录 ZFT 自身实际成功发送的字符数。若账号有可配置套餐总量，填写后即可得到可用于调度的剩余量。

典型硬状态：

```text
Baidu 54004 / Please recharge   -> exhausted
Volcengine unsynchronized       -> unavailable
Tencent ServiceNotActivated     -> unavailable
Invalid Sign / auth failure     -> unavailable
```

修复/充值后执行 Provider “测试连接”，成功会清除硬熔断状态。

## Runtime API

```text
GET /api/v1/system/runtime
PUT /api/v1/system/runtime
```

v1.4 关键字段：

```json
{
  "default_provider_ids": ["baidu", "tencent", "volcengine"],
  "default_provider_strategy": "balanced",
  "multi_pool_max_workers": 12,
  "aggregate_qps_cap": 100,
  "quota_aware_dispatch": true
}
```

默认 Provider QPS：百度 10、腾讯 5、火山 10、阿里 10。实际值以 Web 控制台保存的 Provider config 为准。

## 翻译历史 API

```text
GET /api/v1/history/documents?limit=100
GET /api/v1/history/translation-memory?limit=50
```

前者返回可复用的完成 PDF 历史；后者返回 ZFT Translation Memory 统计和最近文本记录。

## 任务查询/取消/恢复

```text
GET    /api/v1/jobs
GET    /api/v1/jobs/{id}
DELETE /api/v1/jobs/{id}
POST   /api/v1/jobs/{id}/retry
GET    /api/v1/jobs/{id}/timeline
GET    /api/v1/jobs/{id}/events
GET    /api/v1/jobs/{id}/result/mono
GET    /api/v1/jobs/{id}/result/dual
```
