# Zotero-full-translate

Zotero-full-translate 是一个面向 Zotero 10 的全文 PDF 翻译项目，由两个独立组件组成：

- `cloud/`：FastAPI + React + BabelDOC 的单容器云端翻译服务。
- `zotero-plugin/`：Zotero 10 插件源码，负责提交任务、下载译文、挂载附件以及原文/译文左右对照。

> 为兼容已有配置，代码内部仍保留 `ZFT_*` 环境变量、`extensions.zotero.zft.*` 首选项和部分 `zft` 内部标识；这不影响项目公开名称。

## 快速开始

### 1. 部署 Cloud

```bash
cd cloud
cp .env.example .env
```

生成两个**不同**的随机值并写入 `.env`：

```bash
openssl rand -hex 32   # ZFT_API_KEY
openssl rand -hex 32   # ZFT_CONFIG_SECRET
```

然后启动：

```bash
docker compose up -d --build
```

默认控制台与 API：`http://<服务器IP>:3005`。

### 2. 安装 Zotero 插件

可直接安装 `zotero-plugin/dist/Zotero-full-translate-v0.2.22.xpi`，也可以在 `zotero-plugin/` 中运行：

```bash
./build.sh
```

Zotero 中进入“工具 → 插件 → 齿轮 → 从文件安装插件”，选择生成的 `.xpi`。

### 3. 连接 Cloud

在 Zotero 的 Zotero-full-translate 设置页填写：

- Cloud API 地址，例如 `http://192.168.1.10:3005`
- 主播给UU们提供一个测试的节点 `http://60.205.210.93:13005/`
- 与服务器 `.env` 中相同的 `ZFT_API_KEY`

打开 PDF 后点击阅读器工具栏中的“译”，即可提交全文翻译。翻译完成后可自动挂载译文 PDF，并在右侧与原文对照阅读。

## 安全

仓库**不包含实际 `.env`**。不要提交 API Key、Provider 密钥、SQLite 数据库、翻译 PDF 或 `/data` 持久卷内容。公网部署建议使用 HTTPS 反向代理。

## 许可证

本仓库采用组件级许可证：

- `cloud/`：GNU Affero General Public License v3.0 only（AGPL-3.0-only）。Cloud 直接依赖并导入 BabelDOC；BabelDOC 本身声明 AGPL-3.0。
- `zotero-plugin/`：MIT License。

详见各组件内的 `LICENSE` 和根目录 `LICENSE.md`。第三方依赖仍受各自许可证约束。

