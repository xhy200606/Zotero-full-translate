# Zotero-full-translate Cloud

Cloud 是 Zotero-full-translate 的服务器组件，负责 PDF 任务管理、BabelDOC 排版、翻译 Provider 调度、历史复用、Translation Memory 和结果下载。

## 部署

```bash
cp .env.example .env
openssl rand -hex 32
openssl rand -hex 32
# 将两个不同的值分别填入 ZFT_API_KEY 与 ZFT_CONFIG_SECRET
docker compose up -d --build
```

默认端口为 `3005`。浏览器打开 `http://<服务器IP>:3005` 配置百度、腾讯、火山、阿里或 OpenAI-compatible Provider。

健康检查：

```bash
curl http://localhost:3005/health
```

安全重建：

```bash
sudo ./scripts/rebuild.sh
```

## 数据目录

Docker named volume `zft_data` 保存 SQLite、任务文件、翻译历史和 Translation Memory。除非明确需要清空数据，不要执行 `./scripts/rebuild.sh --reset-data`。

## 配置

所有可提交配置见 `.env.example`。实际 `.env` 已被 `.gitignore` 排除。

## API

接口摘要见 `API.md`；整体结构见 `ARCHITECTURE.md`。

## License

本目录采用 AGPL-3.0-only。BabelDOC 为单独的第三方依赖，其许可证与版权归上游项目所有。
