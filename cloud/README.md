# Zotero-full-translate Cloud 2.5.2

Cloud 提供账户体系、HttpOnly Web session、Zotero API Key、用户级翻译 API 实例池、全文翻译任务、DOI 文献绑定、翻译版本、共享译文资产、结果下载和用量统计。

## 端口

- `3005`：普通用户注册/登录、Zotero 客户端（API Key + 客户端实例）、个人 Provider、个人统计和任务。
- `3006`：管理员登录、用户管理、全局统计和系统状态；默认发布到 `0.0.0.0:3006`。公网部署请配合主机防火墙/安全组限制来源，并优先使用 HTTPS 反向代理。

两个端口映射同一个 FastAPI 容器；服务端根据 Host/forwarded port 选择 SPA，权限仍由服务端鉴权。

## 首次部署

```bash
cp .env.example .env
openssl rand -hex 32
```

将随机值写入 `ZFT_CONFIG_SECRET`。它用于加密每个用户的 Provider secret，部署后必须保持稳定。

从旧版本升级时，`scripts/update.sh` / `scripts/rebuild.sh` 会检查该变量：若旧 `.env` 缺失或仍为示例占位值，脚本会先尝试从正在运行的旧容器恢复原值；确实没有旧值时才首次生成随机密钥并写入 `.env`。已有有效值永远不会被脚本自动轮换。

`ZFT_API_KEY` 是可选的旧服务自动化/恢复兼容密钥，不是 Zotero 普通用户 Key，可以留空。

启动：

```bash
docker compose up -d --build
```

检查：

```bash
curl http://127.0.0.1:3005/health
```

空数据库的第一位注册者自动成为管理员；之后用户继续在 3005 自助注册。

## Web 与 Zotero 认证

Web 登录成功后服务端设置 HttpOnly Cookie。用户/管理员 SPA 使用 `credentials: include`，不把 Web bearer token保存在 localStorage。

Zotero API Key 在 3005 创建，格式为 `zftk_...`。Key 支持 scope、可选过期、轮换和撤销。Cloud 只保存 SHA-256/prefix；完整明文只在创建或轮换响应显示一次。

Zotero 请求示意：

```http
Authorization: Bearer zftk_...
X-ZFT-Device-ID: <random-installation-uuid>
```

一个 Key 可以供多个 Device UUID 使用。Device UUID 只是客户端实例元数据，不使用硬件指纹，也不参与文献绑定。

## DOI 文献锁定

Cloud 2.3 不计算源 PDF hash。源文献身份使用：

```text
normalized DOI + lang_in + lang_out + pages + output_mode
```

账户 binding 额外包含 `user_id`。每个完成版本保存为不可变 `TranslationVersion`，`UserDocumentBinding.current_version_id` 指向当前版本。

没有 DOI 的任务仍能翻译，但不会建立 DOI binding，也不能跨设备自动解析同一文献。

DOI-only 是元数据级身份，不证明 PDF 字节与 DOI 对应。错误 DOI 可能命中错误缓存；参见 `SECURITY.md`。

## 重译一致性

强制重译不会预先覆盖旧 binding。新 Job 成功、结果资产写入、TranslationVersion 创建后，binding 才在同一数据库事务中切换。失败/取消继续保留旧版本。

## 结果 SHA-256 与第三方同步

Cloud 为 mono/dual 译文保存 `mono_sha256` / `dual_sha256`，下载响应返回 `X-ZFT-Result-SHA256` 和 ETag。

这些结果 hash 不用于源文献身份，只用于确认某台设备已经通过其他同步软件拿到的译文 PDF 是否就是 Cloud 当前版本，从而避免重复下载。

## 用户独立 Provider

每个普通用户在 3005 维护自己的 Provider Profile。每张 API 卡片都是一个独立实例；同一厂商、同一模板可以创建多个账号实例，并分别保存名称、凭据、QPS、额度与健康状态，同时参与负载均衡或主备调度。翻译 API 页面本身就是服务总览：每张卡片显示厂商标识、配置状态、实时 QPS、最近 60 秒请求/错误、今日字符和额度；右上角设置按钮打开配置对话框。

“新增翻译 API”从 Provider 目录选择现成模板，也可创建自定义 OpenAI-compatible Profile。当前目录包括 OpenAI Compatible、百度通用文本翻译、百度大模型文本翻译、百度领域文本翻译、腾讯 TMT/TokenHub/混元、火山机器翻译、阿里云机器翻译通用版和阿里云机器翻译专业版。阿里云专业版使用 `alimt/2018-10-12` 的 `Translate` 接口，可选择商品标题、商品描述、商品沟通、医疗、社交、金融场景。百度领域翻译可选择学术论文、生物医药、IT、金融、机械、小说、新闻、人文社科、航空航天、法律、合同等领域。

API secret 输入旁提供厂商“获取 API”入口。Provider secret 使用 `ZFT_CONFIG_SECRET` 加密保存。支持 single、balanced、failover 选择策略；管理员 3006 不统一维护普通用户 secret。Provider“测试连接”会先保存当前表单，再发起真实短文本翻译测试。

## 翻译任务管理

3005 的任务页支持按文件名/DOI 搜索、按状态筛选、查看实时进度、下载已完成译文，以及删除已完成/失败/取消的历史任务。历史删除会先解除当前账户的文献绑定；仍被其他账户引用的共享译文不会被误删。未被任何 Job 引用的本地源文件/译文文件才会物理清理。

## 公网部署基线

Cloud 2.5 默认启用 `ZFT_PUBLIC_HARDENING=true`。公网环境建议至少配置：

```env
ZFT_BIND=0.0.0.0
ZFT_ADMIN_BIND=0.0.0.0
ZFT_ALLOWED_HOSTS=translate.example.com
ZFT_CORS_ORIGINS=https://translate.example.com
ZFT_EXPOSE_API_DOCS=false
ZFT_ALLOW_PRIVATE_PROVIDER_ENDPOINTS=false
ZFT_ALLOW_INSECURE_PROVIDER_HTTP=false
ZFT_MAX_UPLOAD_MB=200
```

生产入口必须由 HTTPS 反向代理终止 TLS。应用会添加 CSP、HSTS（HTTPS 下）、frame/内容类型/referrer/permissions 等安全响应头；Web session 的跨站写请求会被拒绝；登录和注册分别限流。自定义 Provider URL 默认必须是 HTTPS，并拒绝 localhost、私网/保留地址；真正发起请求前还会重新解析 DNS 并检查解析结果，降低 SSRF/DNS rebinding 风险。PDF 上传采用有界读取，超过 `ZFT_MAX_UPLOAD_MB` 即中止。Docker 默认 `no-new-privileges` 并 drop all capabilities。

这些是应用层基线，不替代防火墙、反向代理限流、WAF、系统补丁和备份。

## SQLite 与 Alembic

Cloud 2.3 引入 Alembic。启动顺序为旧数据库兼容 bridge → Alembic upgrade head → runtime defaults。

SQLite 连接启用：

```text
foreign_keys=ON
journal_mode=WAL
busy_timeout=30000
synchronous=NORMAL
```

并为 DOI/Job/User/Usage 等高频查询增加索引。

## 升级

推荐：

```bash
./scripts/update.sh
```

> `update.sh` 只部署当前目录里已经存在的源码，不会自动下载 GitHub/Release 的新版本。升级源码包时，请先用新版本文件覆盖项目目录并保留生产 `.env` 与 `/data`，再执行该脚本。

脚本会：

1. 检测 backend/migration、依赖、前端和 Docker 配置变化。
2. 如果容器正在运行，使用 Python `sqlite3.Connection.backup()` 创建一致性数据库备份。
3. 仅 backend/migration 变化且 runtime 不变时直接 restart。
4. 只有依赖/前端/Docker 变化时执行保留缓存的 build。
5. 不执行 prune，也不使用 `--no-cache`。

真正需要清缓存故障恢复时才使用：

```bash
./scripts/rebuild.sh --fresh
```

## 测试

```bash
cd ..
ZFT_SKIP_NPM_BUILD=1 ./scripts/release_check.sh
```

GitHub CI 应执行两个真实 Vite production build。
