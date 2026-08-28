# Zotero-full-translate Zotero 插件

这是 Zotero 10 客户端插件源码。它作为 Cloud thin client，负责：

- 提交 PDF 翻译任务并显示进度；
- 任务取消、恢复、历史复用与强制重新翻译；
- 下载译文 PDF 并挂载为 Zotero 附件；
- 原文/译文左右对照与联动滚动。

## 构建

```bash
chmod +x build.sh
./build.sh
```

生成文件位于 `dist/`。

## 安装

Zotero → 工具 → 插件 → 齿轮 → 从文件安装插件，然后选择 `.xpi`。

安装后在设置页填写 Cloud API 地址与 API Key。打开 PDF，点击工具栏中的“译”。

## 兼容性说明

为避免破坏已有用户配置，本项目公开名称已改为 Zotero-full-translate，但插件 ID、`extensions.zotero.zft.*` 首选项以及内部对象名暂时保持不变。

调试日志默认关闭。如需诊断，可将 `extensions.zotero.zft.debug` 设为 `true`。

## License

MIT License。详见 `LICENSE`。
