# 升级到 Cloud 2.5.0 / Zotero 0.3.7

2.3 的核心变化：DOI-only 文献身份、TranslationVersion 原子绑定、API Key scope/expiry/rotation、HttpOnly Web session、自定义 Provider Profile、Alembic 和 SQLite WAL。

## 1. 先备份 `/data`

不要执行：

```bash
docker compose down -v
```

正常升级必须保留持久 volume。`scripts/update.sh` 会在运行中的旧容器内使用 SQLite backup API 自动创建数据库备份，但首次从旧版迁移前仍建议额外做完整 `/data` 备份。

## 2. 保留真实 `.env`

不要用 `.env.example` 覆盖生产 `.env`。

必须保持 `ZFT_CONFIG_SECRET` 不变，否则数据库中已有 Provider secret 无法解密。`ZFT_API_KEY` 只是旧服务兼容凭据，不是用户 Zotero Key。

## 3. 本次首次升级会 build

2.3 新增 Alembic 依赖并修改 Dockerfile，因此从 2.2.x → 2.3.0 首次升级需要一次 cached build：

```bash
cd cloud
./scripts/update.sh
```

后续如果只改 `backend/app` 或 Alembic migration，脚本可以直接 restart，不需要完整 build。

## 4. 数据库 migration

容器启动时会运行 Alembic upgrade head。目标 revision：

```text
0001_cloud_23
```

新增 DOI、TranslationVersion、API Key 生命周期字段、Provider Profile 字段和查询索引。SQLite 同时启用 WAL、foreign keys 和 busy timeout。

## 5. Web 登录变化

3005/3006 登录现在使用 HttpOnly Session Cookie。旧浏览器 localStorage 中如果残留 `zft_*_token` 不再作为正常 Web 登录凭据；重新登录一次即可建立 Cookie session。

入口仍为：

- `http://SERVER:3005`：普通用户中心
- `http://SERVER:3006`：管理员后台

生产建议通过 HTTPS 反代。

## 6. Zotero API Key

Zotero `0.3.7` 继续只配置：

```text
Cloud 地址
账户 API Key
```

可在 3005 新建 Key，设置 scopes/过期时间；支持轮换。一个 Key 可用于多台设备，插件自动管理随机 Device UUID。

旧 `zftk_...` Key 在迁移后仍可使用；新增字段会获得兼容默认值。

## 7. 文献身份从源 PDF hash 切换为 DOI

2.3 不再计算源 PDF SHA-256。插件读取 Zotero 父条目的 DOI，并按：

```text
user + DOI + 翻译参数
```

解析当前译文。

因此：

- 有 DOI：支持账户跨设备当前版本锁定和 DOI 缓存复用。
- 无 DOI：仍可翻译，但不能自动通过 DOI 跨设备配对。
- 旧数据库中的 `source_sha256` 字段保留为 legacy 兼容数据，但新任务不依赖它。
- DOI 是公开元数据，并不能验证 PDF 内容；如果你的部署必须严格证明文件内容相同，DOI-only 模式不具备这种保证。

Cloud 仍保存**译文结果 SHA-256**，用于设备 B 检测第三方同步得到的译文是否就是当前版本。

## 8. Provider

每个普通用户继续在 3005 配置自己的 Provider。2.3 可以增加自定义 Profile，并使用 single/balanced/failover 池。管理员 3006 不统一管理普通用户 secret。

## 9. 验证

升级后至少检查：

```bash
curl http://127.0.0.1:3005/health
```

然后：

1. 3005 Web 登录正常且刷新页面仍保持会话。
2. 创建/验证一个 `zftk_...` Key。
3. Zotero 打开一个有 DOI 的条目并执行 lookup/翻译。
4. 第二台设备用同账户验证能解析当前版本。
5. 如果译文已通过第三方同步存在，确认结果 SHA-256 匹配时不会重复下载。
6. 3006 只能进行用户管理和统计。

## 10. 不要清 Docker 缓存

正常升级不要运行：

```text
docker builder prune
docker system prune -a
docker image prune -a
docker compose build --no-cache
```

这些命令会导致 Debian/pip/npm 依赖重新下载。只有明确故障恢复时才使用 `./scripts/rebuild.sh --fresh`。
## 8. Cloud 2.5 公网安全与 Provider 目录

2.5 增加 Provider 模板目录、百度机器/大模型/领域文本翻译、翻译 API 卡片式配置、Zotero 客户端合并页面和浏览器 favicon。升级后建议在生产 `.env` 增加：

```env
ZFT_ADMIN_BIND=127.0.0.1
ZFT_PUBLIC_HARDENING=true
ZFT_ALLOWED_HOSTS=translate.example.com
ZFT_EXPOSE_API_DOCS=false
ZFT_MAX_UPLOAD_MB=200
ZFT_ALLOW_PRIVATE_PROVIDER_ENDPOINTS=false
ZFT_ALLOW_INSECURE_PROVIDER_HTTP=false
```

如果使用反向代理，请把 `ZFT_CORS_ORIGINS` 改为真实 HTTPS Origin。若历史部署直接把 3006 暴露公网，升级后默认只绑定 localhost；请通过反向代理转发管理域名，或在明确理解风险时修改 `ZFT_ADMIN_BIND`。

