var ZFT_Preferences = {
  _initialized: false,
  _retryTimer: null,
  _retryCount: 0,
  PREF: "extensions.zotero.zft.",

  get(path, fallback = "") {
    try {
      const v = Zotero.Prefs.get(this.PREF + path, true);
      return v === undefined || v === null ? fallback : v;
    } catch (_) {
      return fallback;
    }
  },

  set(path, value) {
    Zotero.Prefs.set(this.PREF + path, value, true);
  },

  pane() {
    return document.getElementById("zft-prefpane");
  },

  init() {
    const pane = this.pane();
    if (!pane) {
      if (this._retryCount++ < 30) {
        clearTimeout(this._retryTimer);
        this._retryTimer = setTimeout(() => this.init(), 50);
      }
      return;
    }

    // Preference panes can be reinserted without recreating the script global.
    // Use a DOM marker instead of only a JS flag so every inserted pane binds once.
    if (pane.dataset.zftBound === "true") return;
    pane.dataset.zftBound = "true";
    this._initialized = true;

    this.bindPrefs(pane);
    this.bindTabs(pane);
    this.bindActions(pane);
    this.updateProviderVisibility(pane);
    this.updateCloudProviderVisibility(pane);
    this.refreshCloudClientID();
    this.loadTranslateServices();
    this.refreshQuotaUI();
  },

  bindPrefs(pane) {
    pane.querySelectorAll("[data-pref]").forEach((el) => {
      if (el.dataset.zftPrefBound === "true") return;
      el.dataset.zftPrefBound = "true";

      const key = el.dataset.pref;
      const current = this.get(key, el.type === "checkbox" ? false : "");
      if (el.type === "checkbox") el.checked = !!current;
      else el.value = current;

      const localName = String(el.localName || el.tagName || "").toLowerCase();
      const eventName = localName === "select" || el.type === "checkbox" ? "change" : "input";
      el.addEventListener(eventName, () => {
        let value;
        if (el.type === "checkbox") value = el.checked;
        else if (el.type === "number") value = Number(el.value);
        else value = el.value;
        this.set(key, value);

        if (key === "translationProvider") {
          this.updateProviderVisibility(pane);
        }
        if (key === "cloud.providerMode") {
          this.updateCloudProviderVisibility(pane);
        }
        if (["translationProvider", "pdftranslateService", "gpt.model", "quota.enabled", "quota.warnPercent"].includes(key)) {
          this.refreshQuotaUI();
        }
      });
    });
  },

  bindTabs(pane) {
    pane.querySelectorAll("[data-tab-button]").forEach((btn) => {
      if (btn.dataset.zftTabBound === "true") return;
      btn.dataset.zftTabBound = "true";
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.activateTab(btn.dataset.tabButton);
      });
    });
  },

  activateTab(tab) {
    const pane = this.pane();
    if (!pane) return;
    pane.querySelectorAll("[data-tab-button]").forEach((x) => {
      x.dataset.active = x.dataset.tabButton === tab ? "true" : "false";
      x.setAttribute("aria-selected", x.dataset.tabButton === tab ? "true" : "false");
    });
    pane.querySelectorAll("[data-tab]").forEach((x) => {
      x.dataset.active = x.dataset.tab === tab ? "true" : "false";
    });
  },


  updateCloudProviderVisibility(pane = this.pane()) {
    if (!pane) return;
    const custom = String(this.get("cloud.providerMode", "server-default")) === "custom";
    const box = pane.querySelector("#zft-cloud-provider-custom");
    if (box) box.hidden = !custom;
  },

  refreshCloudClientID() {
    const el = document.getElementById("zft-cloud-client-id");
    if (!el) return;
    try { el.value = Zotero.ZoteroFulltextTranslator?.cloudClientID?.() || ""; }
    catch (_) { el.value = ""; }
  },

  versionAtLeast(actual, required) {
    const parse = (value) => String(value || "0").split(/[.+-]/)[0].split(".").map((x) => Number(x) || 0);
    const a = parse(actual), r = parse(required);
    for (let i = 0; i < Math.max(a.length, r.length, 3); i++) {
      const av = a[i] || 0, rv = r[i] || 0;
      if (av > rv) return true;
      if (av < rv) return false;
    }
    return true;
  },

  async refreshCloudStatus() {
    this.status("zft-pdf2zh-cloud-status", "连接中…");
    try {
      const result = await Zotero.ZoteroFulltextTranslator.testCloudPDF2ZH();
      const providers = Array.isArray(result.providers) ? result.providers : [];
      const enabled = providers.filter((x) => x.enabled && x.configured);
      const names = enabled.map((x) => {
        const q = x.quota || {};
        const remain = q.remaining_chars === null || q.remaining_chars === undefined ? "额度未知" : `剩余 ${Number(q.remaining_chars).toLocaleString()} 字符`;
        return `${x.display_name || x.id}（${remain}${q.status ? ` · ${q.status}` : ""}）`;
      }).join("、") || "无可用翻译引擎";
      const runtime = result.runtime || {};
      const pool = Array.isArray(runtime.default_provider_ids) ? runtime.default_provider_ids.join(", ") : "";
      const compat = this.versionAtLeast(result.version, "1.4.1");
      const compatNote = compat ? "" : "\n⚠ 腾讯 TMT 与火山兼容修复要求 Cloud ≥ 1.4.1，请先升级服务器。";
      this.status("zft-pdf2zh-cloud-status", `正常：${result.service || "Zotero-full-translate Cloud"} v${result.version || "?"} · ${result.elapsedMs} ms\n可用引擎：${names}${pool ? `\n服务器默认池：${pool} · ${runtime.default_provider_strategy || "balanced"}` : ""}${compatNote}`, !compat);
      this.status("zft-cloud-provider-status", enabled.length ? `服务器可用 ${enabled.length} 个引擎：${names}${compat ? "" : "；腾讯/火山请先升级 Cloud 1.4.1"}` : "服务器没有已启用且配置完成的翻译引擎", !enabled.length || !compat);
      return result;
    } catch (e) {
      this.status("zft-pdf2zh-cloud-status", `失败：${e.message || e}`, true);
      this.status("zft-cloud-provider-status", "无法读取云端引擎状态", true);
      throw e;
    }
  },

  updateProviderVisibility(pane = this.pane()) {
    if (!pane) return;
    const provider = String(this.get("translationProvider", "pdftranslate"));
    pane.querySelectorAll("[data-provider-panel]").forEach((panel) => {
      const show = panel.dataset.providerPanel === provider || panel.dataset.providerPanel === "all";
      panel.hidden = !show;
      panel.style.display = show ? "" : "none";
    });
    const summary = pane.querySelector("#zft-provider-summary");
    if (summary) {
      summary.textContent = provider === "gpt"
        ? "当前：GPT / OpenAI-compatible。请在下方填写 API Base URL、API Key 与模型。"
        : "当前：Translate for Zotero。可继承其已配置的多种翻译服务；也可以切换到 GPT 直接填写 API。";
    }
  },

  status(id, text, error = false) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.style.color = error ? "#b42318" : "";
  },

  loadTranslateServices() {
    const list = document.getElementById("zft-service-list");
    if (!list) return [];
    list.replaceChildren();
    let services = [];
    try {
      services = Zotero.ZoteroFulltextTranslator?.getAvailableTranslateServices?.() || [];
    } catch (_) {}
    for (const svc of services) {
      if (svc?.type && svc.type !== "sentence") continue;
      const opt = document.createElement("option");
      opt.value = svc.id || "";
      opt.label = svc.name ? `${svc.id} — ${svc.name}` : svc.id || "";
      list.append(opt);
    }
    this.status(
      "zft-pdftranslate-status",
      services.length ? `检测到 ${services.length} 个服务` : "未检测到 Translate for Zotero；可切换到 GPT 直接配置 API",
      false
    );
    this.refreshQuotaUI();
    return services;
  },

  quotaServiceID() {
    try { return Zotero.ZoteroFulltextTranslator?.currentQuotaServiceID?.() || "pdftranslate-default"; }
    catch (_) { return "pdftranslate-default"; }
  },

  refreshQuotaUI() {
    const addon = Zotero.ZoteroFulltextTranslator;
    if (!addon?.getQuotaSnapshot) return;
    let q;
    try { q = addon.getQuotaSnapshot(this.quotaServiceID()); } catch (_) { return; }
    const setValue = (id, value) => { const el = document.getElementById(id); if (el) el.value = value ?? ""; };
    setValue("zft-quota-period", q.period || "month");
    setValue("zft-quota-chars", Number(q.charsLimit) || 0);
    setValue("zft-quota-requests", Number(q.requestsLimit) || 0);
    setValue("zft-quota-qps", Number(q.qps) || 0);
    setValue("zft-quota-maxchars", Number(q.maxChars) || 0);
    const engine = document.getElementById("zft-quota-engine");
    if (engine) engine.textContent = `${q.name || q.serviceID} · ${q.serviceID}${q.overridden ? " · 自定义" : q.preset ? " · 内置预设" : ""}`;
    const summary = document.getElementById("zft-quota-summary");
    if (summary) {
      const f = (n) => addon.formatQuotaNumber?.(n) || String(n || 0);
      const lines = [
        q.charsLimit ? `字符：${f(q.chars)} / ${f(q.charsLimit)} · 剩余约 ${f(q.charsRemaining)} · ${Math.round(q.charsPercent)}%` : `字符：已统计 ${f(q.chars)}（未设置上限）`,
        q.requestsLimit ? `请求：${f(q.requests)} / ${f(q.requestsLimit)} · 剩余约 ${f(q.requestsRemaining)} · ${Math.round(q.requestsPercent)}%` : `请求：已统计 ${f(q.requests)} 次（未设置上限）`,
        q.qps ? `QPS：${q.qps}${q.maxChars ? ` · 单次最大 ${q.maxChars} 字符` : ""}` : (q.maxChars ? `单次最大：${q.maxChars} 字符` : ""),
        q.note || "",
        `统计周期：${q.period === "account" ? "账号总额度" : q.periodKey}`,
      ].filter(Boolean);
      summary.textContent = lines.join("\n");
    }
    const meter = document.getElementById("zft-quota-meter");
    if (meter) {
      const pct = Math.max(0, Math.min(100, Number(q.percent) || 0));
      meter.style.width = `${pct}%`;
      meter.style.opacity = pct >= 90 ? "1" : pct >= 75 ? ".82" : ".6";
    }
  },

  saveQuotaOverride() {
    const addon = Zotero.ZoteroFulltextTranslator;
    const serviceID = this.quotaServiceID();
    const val = (id) => Number(document.getElementById(id)?.value || 0) || 0;
    const period = document.getElementById("zft-quota-period")?.value === "account" ? "account" : "month";
    addon.setQuotaOverride(serviceID, { period, charsLimit: val("zft-quota-chars"), requestsLimit: val("zft-quota-requests"), qps: val("zft-quota-qps"), maxChars: val("zft-quota-maxchars") });
    this.refreshQuotaUI();
  },

  async testProcess(prefKey, fallback, args, statusID) {
    const command = String(this.get(prefKey, fallback) || fallback);
    this.status(statusID, "检测中…");
    try {
      const result = await Zotero.ZoteroFulltextTranslator.runProcess(command, args);
      const out = (result.stdout || result.stderr || "可执行").trim().split("\n")[0];
      this.status(statusID, `正常：${out.slice(0, 100)}`);
    } catch (e) {
      this.status(statusID, `失败：${e.message || e}`, true);
    }
  },

  async clearCache() {
    this.status("zft-cache-status", "清理中…");
    try {
      const dir = Zotero.ZoteroFulltextTranslator.cacheDir();
      if (await IOUtils.exists(dir)) await IOUtils.remove(dir, { recursive: true });
      this.status("zft-cache-status", "已清空");
    } catch (e) {
      this.status("zft-cache-status", `失败：${e.message || e}`, true);
    }
  },

  bindActions(pane) {
    const on = (id, handler) => {
      const el = pane.querySelector(`#${id}`);
      if (!el || el.dataset.zftActionBound === "true") return;
      el.dataset.zftActionBound = "true";
      el.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await handler(event);
      });
    };

    on("zft-open-engine-settings", () => this.activateTab("engine"));
    on("zft-refresh-services", () => this.loadTranslateServices());
    on("zft-test-pdftranslate", async () => {
      this.status("zft-pdftranslate-status", "测试中…");
      try {
        const result = await Zotero.ZoteroFulltextTranslator.translateWithPDFTranslate("Academic translation connection test.", -1);
        this.status("zft-pdftranslate-status", `正常：${String(result).slice(0, 80)}`);
      } catch (e) {
        this.status("zft-pdftranslate-status", `失败：${e.message || e}`, true);
      }
    });
    on("zft-test-gpt", async () => {
      this.status("zft-gpt-status", "测试中…");
      try {
        const result = await Zotero.ZoteroFulltextTranslator.translateWithGPT("Academic translation connection test.");
        this.status("zft-gpt-status", `正常：${String(result).slice(0, 80)}`);
      } catch (e) {
        this.status("zft-gpt-status", `失败：${e.message || e}`, true);
      }
    });

    on("zft-quota-refresh", () => this.refreshQuotaUI());
    on("zft-quota-save", () => this.saveQuotaOverride());
    on("zft-quota-preset", () => { Zotero.ZoteroFulltextTranslator.clearQuotaOverride(this.quotaServiceID()); this.refreshQuotaUI(); });
    on("zft-quota-reset", () => { Zotero.ZoteroFulltextTranslator.resetQuotaUsage(this.quotaServiceID()); this.refreshQuotaUI(); });

    on("zft-test-parser-env", async () => {
      const parser = String(this.get("parser", "zotero"));
      this.status("zft-parser-test-status", `正在检测 ${parser}…`);
      try {
        const result = await Zotero.ZoteroFulltextTranslator.testParserEnvironment(parser);
        this.status("zft-parser-test-status", `✓ ${result.message} · ${result.elapsedMs} ms`);
      } catch (e) {
        this.status("zft-parser-test-status", `✗ ${e.message || e}${e.zftReport ? `\n\n${e.zftReport}` : ""}`, true);
      }
    });
    on("zft-test-parser-pdf", async () => {
      const parser = String(this.get("parser", "zotero"));
      this.status("zft-parser-test-status", `正在用当前 PDF 测试 ${parser}…`);
      try {
        const result = await Zotero.ZoteroFulltextTranslator.testParserWithCurrentPDF(parser, (progress) => {
          const pct = Number.isFinite(progress?.progress) ? ` · ${Math.round(progress.progress)}%` : "";
          this.status("zft-parser-test-status", `${progress?.text || "解析中…"}${pct}`);
        });
        const extra = result.layoutBlocks ? ` · 布局块 ${result.layoutBlocks}` : "";
        this.status("zft-parser-test-status", `✓ ${result.file} · ${result.pages} 页${extra} · ${result.chars} 字符 · ${(result.elapsedMs / 1000).toFixed(1)} 秒`);
      } catch (e) {
        this.status("zft-parser-test-status", `✗ ${e.message || e}${e.zftReport ? `\n\n${e.zftReport}` : ""}`, true);
      }
    });
    on("zft-test-parser-all", async () => {
      const parsers = ["zotero", "mineru-api", "mineru-local", "doc2x-md"];
      const labels = { zotero: "Zotero PDFWorker", "mineru-api": "MinerU API", "mineru-local": "MinerU CLI", "doc2x-md": "Doc2X CLI" };
      const lines = [];
      for (const parser of parsers) {
        this.status("zft-parser-test-status", [...lines, `… ${labels[parser]} 检测中`].join("\n"));
        try {
          const result = await Zotero.ZoteroFulltextTranslator.testParserEnvironment(parser);
          lines.push(`✓ ${labels[parser]}：${result.message}`);
        } catch (e) {
          lines.push(`✗ ${labels[parser]}：${e.message || e}${e.zftReport ? `\n${e.zftReport}` : ""}`);
        }
      }
      const hasError = lines.some((line) => line.startsWith("✗"));
      this.status("zft-parser-test-status", lines.join("\n"), hasError);
    });
    on("zft-test-mineru-api", async () => {
      this.status("zft-mineru-api-status", "检测中…");
      try {
        const result = await Zotero.ZoteroFulltextTranslator.testParserEnvironment("mineru-api");
        this.status("zft-mineru-api-status", `正常：${result.message}`);
      } catch (e) {
        this.status("zft-mineru-api-status", `失败：${e.message || e}${e.zftReport ? `\n${e.zftReport}` : ""}`, true);
      }
    });
    on("zft-test-mineru", () => this.testProcess("mineru.command", "mineru", ["--version"], "zft-mineru-status"));
    on("zft-test-doc2x", () => this.testProcess("doc2x.command", "doc2x", ["--version"], "zft-doc2x-status"));
    on("zft-test-pdf2zh-cloud", async () => { try { await this.refreshCloudStatus(); } catch (_) {} });
    on("zft-cloud-refresh", async () => { try { await this.refreshCloudStatus(); } catch (_) {} });
    on("zft-cloud-console", () => Zotero.ZoteroFulltextTranslator.openCloudConsole());
    on("zft-cloud-recover", async () => {
      this.status("zft-cloud-recover-status", "正在检查云端任务…");
      try {
        const result = await Zotero.ZoteroFulltextTranslator.restoreCloudJobs({ notify: false });
        this.status("zft-cloud-recover-status", `已检查 ${result.total || 0} 个任务 · ${result.active || 0} 个进行中 · ${result.completed || 0} 个待导入`);
      } catch (e) {
        this.status("zft-cloud-recover-status", `失败：${e.message || e}`, true);
      }
    });
    on("zft-test-pdf2zh", () => this.testProcess("pdf2zh.command", "pdf2zh_next", ["--version"], "zft-pdf2zh-status"));
    on("zft-clear-cache", () => this.clearCache());
  }
};

// Zotero inserts plugin preference panes into an already-loaded preferences window,
// so relying on window.load alone is incorrect. The pane's onload calls init(), and
// these fallbacks cover alternate insertion orders and future Zotero changes.
window.ZFT_Preferences = ZFT_Preferences;
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => ZFT_Preferences.init(), { once: true });
} else {
  setTimeout(() => ZFT_Preferences.init(), 0);
}
