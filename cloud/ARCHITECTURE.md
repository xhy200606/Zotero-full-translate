# Cloud 2.3 Architecture

```text
Browser ─ :3005 用户中心 ─┐
                           ├─ FastAPI ─ SQLite/WAL ─ /data
Browser ─ :3006 管理后台 ─┤        │
                           │        ├─ Account / API Keys / Usage
Zotero ── :3005 API ──────┘        ├─ DOI Binding / TranslationVersion
                                    ├─ Result Assets / TM
                                    └─ BabelDOC + 用户 Provider
```

3005/3006 只是入口职责不同，API 权限始终由服务端 principal/role/scope 判断。

## 身份层

```text
User
├─ password_hash (scrypt)
├─ Web AuthToken
├─ ClientApiKey (zftk_...)
│  ├─ scopes
│  ├─ expires_at
│  └─ rotated_from_id
└─ Device UUID(s)
```

Web Session 与 Zotero API Key 分离。浏览器通过 HttpOnly Cookie 携带 Web session；Zotero 使用 bearer client key。服务端只保存 token/key 的 SHA-256，不保存普通长期明文。

Device UUID 只描述安装实例，不参与文献身份。

## DOI 文献层

源 PDF 不再做 hash identity。规范化 DOI 是文献键：

```text
Job
  document_doi
  lang_in/lang_out/pages/output_mode
        │
        ├─ completed result assets
        │    mono_sha256 / dual_sha256
        │
        └─ TranslationVersion (immutable)
                  │
                  └─ UserDocumentBinding.current_version_id
```

`UserDocumentBinding` 按账户 + DOI + 翻译参数唯一定位当前版本。

没有 DOI 的 Job 可以存在，但没有 DOI binding。

### DOI-only 的边界

DOI 是元数据，不能证明 PDF 文件内容。跨账户裸 lookup 不暴露其他账户私有 Job，但客户端提交带 DOI 的请求时，全局缓存可按 DOI 复用。错误 DOI 可能造成错误配对，因此这不是 proof-of-possession 设计。

## 原子版本切换

正常完成/缓存克隆/重译成功的关键事务：

```text
BEGIN
  Job.status = COMPLETED
  Job.result keys / translated-result SHA-256
  INSERT TranslationVersion
  UPSERT UserDocumentBinding.current_version_id
COMMIT
```

如果任何一步失败则 rollback，旧 binding 继续有效。这样设备 A/B 永远从同一个账户 current pointer 解析版本。

## 本地/第三方同步路径

```text
Zotero 打开原文
  ↓
读取父条目 DOI
  ↓
Cloud lookup current binding
  ↓
拿到当前译文 result SHA-256
  ↓
检查 Zotero 已有附件/原文同目录候选
  ↓
path+size+mtime hash index 命中？
  ↓ 否时才计算 SHA-256
结果 hash 完全一致 → 直接绑定
否则 → Cloud 下载
```

源文献不计算 SHA-256；只有译文结果用 SHA-256 做字节级版本确认。

## Provider 隔离

```text
User
└─ UserProviderProfile(s)
   ├─ built-in profile
   ├─ custom profile
   ├─ encrypted config/secrets
   └─ default pool: single / balanced / failover
```

只有真正需要新翻译时才加载 Job 所属用户的 Provider。缓存复用路径在 Provider 配置检查之前执行。

## SQLite

单节点默认 SQLite。连接 PRAGMA：

```text
foreign_keys=ON
journal_mode=WAL
busy_timeout=30000
synchronous=NORMAL
```

任务状态更新保持短事务；高频查询索引覆盖用户、DOI、Job 时间/状态和 Usage 时间。

Schema 变更通过 Alembic。`Base.metadata.create_all()` 负责新库基础表，Alembic revision 负责正式版本迁移和旧库演进；legacy column bridge 只用于历史数据库过渡。

## 升级路径

`cloud/scripts/update.sh` 根据文件哈希决定：

```text
backend/app / alembic only
→ SQLite backup
→ restart

requirements / frontend / Dockerfile / compose changed
→ SQLite backup
→ cached build
→ recreate
```

Alembic 和 `alembic.ini` 以只读 bind mount 进入容器，因此 migration-only 更新不要求重新下载整个 runtime 依赖。

## Zotero 状态机

```text
IDLE
 ↓
RESOLVING
 ├─ LOCAL_MATCH → SWITCHING → BOUND
 └─ UPLOADING / TRANSLATING / RETRANSLATING
                    ↓
                DOWNLOADING
                    ↓
                SWITCHING
                    ↓
                  BOUND
任何失败 → ERROR → 保留既有可用绑定/附件
```

状态机用于限制错误顺序，特别是重译期间不得提前删除旧译本。
