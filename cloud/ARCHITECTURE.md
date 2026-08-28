# Zotero-full-translate Cloud 架构

## 单容器控制平面

```text
┌───────────────────────────────────────────────────────────────┐
│ Zotero-full-translate Cloud container                                           │
│                                                               │
│ React / Material 3                                            │
│        │                                                      │
│     FastAPI ─── SQLite ─── /data files                       │
│        │          │                                           │
│        │          ├─ Jobs / Events                            │
│        │          ├─ Provider quota state                     │
│        │          └─ ZFT Translation Memory                   │
│        │                                                      │
│ Embedded Task Manager                                         │
│        │                                                      │
│     BabelDOC                                                  │
│        │                                                      │
│ MultiProviderTranslator                                       │
│   ├─ Baidu 10 QPS default                                     │
│   ├─ Tencent 5 QPS default                                    │
│   ├─ Volcengine 10 QPS default                                │
│   ├─ Aliyun 10 QPS default                                    │
│   └─ OpenAI-compatible                                        │
└───────────────────────────────────────────────────────────────┘
```

## 文档级复用

客户端先计算 PDF SHA-256：

```text
PDF -> SHA-256 -> /api/v1/jobs/lookup
                    ├─ hit  -> 直接返回历史完成 Job / PDF
                    └─ miss -> 上传 PDF -> 创建新 Job
```

服务端上传入口还会再次做 SHA-256 复用检查，因此旧客户端也能避免重复 BabelDOC 翻译，只是无法节省上传流量。

## 文本级 Translation Memory

BabelDOC 进入翻译阶段后：

```text
stable paragraph
      │
      ▼
ZFT Translation Memory
  ├─ hit  -> 直接返回第一次成功译文
  └─ miss -> Quota-aware MultiProviderTranslator
                │
                └─ 成功后写入 TM
```

ZFT TM 独立于具体 Provider，因此相同语言对/profile 的相同文本可以跨百度/腾讯/火山/阿里复用。BabelDOC 自身的内部 cache 仍保留，形成额外缓存层。

## 额度感知调度

每个 Provider 持久保存：

```text
local_used_chars
remote_used_chars (若有可用同步来源)
status
last_error
configured total/reserve/low threshold
```

调度权重近似：

```text
capacity = qps × quota_dispatch_weight
score    = (in_flight + 1) / capacity
```

`exhausted` / `unavailable` Provider 不进入候选池。低额度 Provider 仍可用，但权重会下降。

必须区分“官方余额”和“ZFT 估算”：不是所有云翻译产品都提供统一的剩余字符查询 API。v1.4 的 API 明确返回 `source`；如果只有用户配置的总额度 + ZFT 本地计量，则标记为 `manual_budget+local_meter`。

## 两层限速

1. BabelDOC aggregate limiter：控制整篇文档的总请求启动速率。
2. Provider gate：每个服务独立 QPS + concurrency semaphore。

多引擎理论吞吐量接近健康 Provider 的有效 QPS 之和，同时受 `aggregate_qps_cap`、`multi_pool_max_workers`、网络延迟、段落数量和厂商账号限制约束。

## Provider 熔断

```text
Baidu 54004                  -> exhausted
Volcengine unsynchronized    -> unavailable
Tencent ServiceNotActivated  -> unavailable
Auth/signature failures      -> unavailable
```

硬错误会立即交还给 MultiProviderTranslator，并转投其他健康 Provider；不会让同一段落在明确的账户错误上持续指数重试。

## 数据持久化

单 volume：`zft_data:/data`

```text
/data/zft.db
/data/files/inputs/
/data/files/results/
/data/work/
```

普通升级和 `scripts/rebuild-zft.sh` 默认保留该 volume。只有 `--reset-data` 会在明确二次确认后删除 ZFT 持久数据。
