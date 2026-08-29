# Zotero-full-translate Cloud 2.5.0 API

## 认证模型

### Web session

3005/3006 使用用户名/密码。登录/注册成功后服务端设置 HttpOnly Session Cookie；响应返回用户/到期信息，但不把可供前端持久化的原始 bearer token放入 JSON。

常用接口：

```text
GET  /api/v1/auth/capabilities
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/change-password
```

用户端/管理员端请求使用浏览器 Cookie。管理员端 3006 会拒绝普通 user 登录管理界面。

### Zotero client API Key

用户在 3005 创建 `zftk_...` Key。Zotero 请求：

```http
Authorization: Bearer zftk_...
X-ZFT-Device-ID: <persistent-random-uuid>
X-ZFT-Client-Version: 0.3.7
```

验证当前 Key：

```text
GET /api/v1/auth/client
```

返回账户公开信息、Key id/prefix/scopes/expiry 和当前 Device 信息。

支持 scopes：

```text
translate
lookup
download
account:read
```

Job 创建/重试/取消要求 `translate`；lookup/history/job读取要求 `lookup`；结果下载要求 `download`。

## Zotero API Key 管理（Web session）

```text
GET    /api/v1/account/api-keys
POST   /api/v1/account/api-keys
POST   /api/v1/account/api-keys/{id}/rotate
DELETE /api/v1/account/api-keys/{id}
```

创建示例：

```json
{
  "label": "Zotero",
  "scopes": ["translate", "lookup", "download", "account:read"],
  "expires_in_days": 365
}
```

创建/轮换响应包含一次性的完整 `api_key`。列表只显示安全元数据。普通轮换未指定新 `expires_in_days` 时保留旧 Key 的原过期时间。

## Device UUID

同一 API Key 可以由多个 Device UUID 使用。Device UUID 是随机安装实例 ID，不是硬件机器码。

```text
GET    /api/v1/account/devices
PATCH  /api/v1/account/devices/{id}
DELETE /api/v1/account/devices/{id}
```

撤销某个实例不会撤销同一 API Key 的其他实例。被撤销 UUID 不会自动复活。

## 用户 Provider

这些接口要求 Web session，且只操作当前用户：

```text
GET    /api/v1/account/providers
GET    /api/v1/account/providers/catalog
POST   /api/v1/account/providers
PUT    /api/v1/account/providers/{provider_id}
DELETE /api/v1/account/providers/{provider_id}
POST   /api/v1/account/providers/{provider_id}/test
GET    /api/v1/account/providers/{provider_id}/quota
POST   /api/v1/account/providers/{provider_id}/quota/reset
GET    /api/v1/account/providers/settings/default
PUT    /api/v1/account/providers/settings/default
```

`/catalog` 返回可创建模板的 `template_id`、厂商标识、说明、凭据入口和默认配置。创建时推荐提交 `template_id`，例如 `baidu_general`、`baidu_machine`、`baidu_llm`、`baidu_domain`、`tencent_tmt`、`volcengine_mt`、`aliyun_general` 或 `custom_openai_compatible`。

用户可以维护内置或自定义 Profile。配置按 `user_id + provider_id` 隔离；Provider secret 加密保存。默认设置支持 single、balanced、failover。Provider 返回实时 QPS、最近 60 秒请求/错误、今日翻译字符和额度状态；额度计数可以由当前用户重置。测试接口先持久化当前配置，再执行真实短文本翻译请求。自定义 endpoint 默认必须是 HTTPS 公网地址。

## DOI 文献 lookup

Cloud 2.3 不接受/计算源 PDF SHA-256 作为文献身份。lookup 使用 DOI：

```text
POST /api/v1/jobs/lookup
```

示例：

```json
{
  "document_doi": "10.1038/s41586-024-00000-0",
  "lang_in": "en",
  "lang_out": "zh-CN",
  "pages": null,
  "output_mode": "mono",
  "client_id": "zotero",
  "client_request_id": "..."
}
```

服务端规范化 DOI 后先解析当前账户 `UserDocumentBinding`，再查当前账户同 DOI 完成任务。跨账户裸 DOI lookup 不返回其他用户私有 Job。

没有 DOI 时不应调用 DOI lookup；仍可直接创建翻译 Job。

## 创建翻译任务

```text
POST /api/v1/jobs
Content-Type: multipart/form-data
```

主要字段：

```text
file=<PDF>
document_doi=10.xxxx/...
lang_in=en
lang_out=zh-CN
pages=
output_mode=mono|dual
provider_id=...
provider_ids=...
provider_strategy=single|balanced|failover
force_retranslate=false
```

`document_doi` 可缺省；缺省时任务仍可翻译，但不会进入 DOI binding/共享 DOI 复用。

当 `force_retranslate=false` 且 DOI 有可复用全局结果时，Cloud 可以在 Provider 可用性检查前生成当前用户的缓存复用 Job。DOI 是公开元数据，服务端不会验证上传 PDF 字节确实对应该 DOI；这是 DOI-only 策略的已知取舍。

`force_retranslate=true` 绕过文档级可复用结果、Translation Memory 读取和 BabelDOC cache；成功后才更新当前用户 binding。

## Job 与结果

```text
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/timeline
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
DELETE /api/v1/jobs/{job_id}/history
GET  /api/v1/jobs/{job_id}/result/{mono|dual}
```

Job JSON 包含 `document_doi`、结果存在标记及 `mono_sha256` / `dual_sha256`（完成时）。下载响应包含：

```http
X-ZFT-Result-SHA256: <translated-pdf-sha256>
ETag: "<translated-pdf-sha256>"
```

结果 SHA-256 只用于译文完整性/本地复用，不用作源论文身份。

`DELETE /history` 只允许删除终态任务。删除时会清除当前用户对应的文献绑定、TranslationVersion、JobEvent 和 Job；UsageEvent 保留用于历史用量统计。源文件/译文文件只有在没有其他 Job 引用时才物理删除。仍被其他账户 binding 引用的版本拒绝删除。

## TranslationVersion / Binding

完成任务会创建不可变 TranslationVersion。账户 current binding 按：

```text
user_id + document_doi + lang_in + lang_out + pages + output_mode
```

定位。完成 Job、Version 创建和 binding 切换必须在同一 DB transaction 中提交。

## History

```text
GET /api/v1/history/documents
```

返回当前账户可见的 DOI-keyed 完成译文历史，需要 `lookup` 能力。

管理员 Translation Memory 检查：

```text
GET /api/v1/history/translation-memory
```

需要 admin 权限。

## 用户与管理员统计

用户：

```text
GET /api/v1/account/summary
GET /api/v1/account/usage
```

管理员：

```text
GET /api/v1/admin/summary
GET /api/v1/admin/users
GET /api/v1/admin/users/{id}
PATCH /api/v1/admin/users/{id}
```

3006 只管理用户和统计；普通用户 Provider secret 不通过管理员接口统一返回或编辑。
