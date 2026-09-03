# Zotero Full Translate 0.4.2

Zotero 全文 PDF 翻译客户端，配套 Zotero Full Translate Cloud 2.5.2。

## 主要功能

- 从 Zotero Reader 一键提交全文翻译任务。
- Cloud 生成固定版式译文 PDF，并统一执行翻译 API 调度。
- Reader 内显示翻译阶段、进度与任务状态，可取消或恢复任务。
- 翻译完成后自动下载并挂载译文附件。
- 支持原文 / 译文左右对照与联动滚动。
- 支持按 DOI 复用账户已有译文与跨设备绑定。
- Zotero 可读取 Cloud 当前可用的 API 实例池；同一厂商的多个账号会作为多个独立实例参与选择与调度。
- 设置页显示每个已启用 API 实例的剩余额度；Reader 工具栏仅在当前调度实例进入低额度阈值时显示警告。
- Zotero 仅保存 Cloud 地址和账户 API Key；翻译服务密钥保存在 Cloud 账户侧。

## 连接 Cloud

在 Cloud `3005` 用户中心创建 `zftk_...` API Key，然后在插件“连接”设置中填写 Cloud 地址和账户 API Key，点击“验证并连接”。

## 构建

```bash
./build.sh
```

生成：

```text
dist/Zotero-full-translate-v0.4.2.xpi
```

manifest 兼容 Zotero `9.0` 至 `10.0.*`。
