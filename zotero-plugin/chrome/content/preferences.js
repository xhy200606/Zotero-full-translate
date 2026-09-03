var ZFT_Preferences = {
    _initialized: false,
    _retryTimer: null,
    _retryCount: 0,
    PREF: "extensions.zotero.zft.",
    get(path, fallback = "") {
        try {
            const v = Zotero.Prefs.get(this.PREF + path, true);
            return v === undefined || v === null ? fallback : v;
        }
        catch (_) {
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
        if (pane.dataset.zftBound === "true")
            return;
        pane.dataset.zftBound = "true";
        this._initialized = true;
        this.bindPrefs(pane);
        this.bindTabs(pane);
        this.bindActions(pane);
        this.updateCloudProviderVisibility(pane);
        this.refreshCloudClientID();
        if (this.get("cloud.baseURL", "") && this.get("cloud.apiKey", ""))
            this.refreshCloudProviderPool({ silent: true });
    },
    bindPrefs(pane) {
        pane.querySelectorAll("[data-pref]").forEach((el) => {
            if (el.dataset.zftPrefBound === "true")
                return;
            el.dataset.zftPrefBound = "true";
            const key = el.dataset.pref;
            const current = this.get(key, el.type === "checkbox" ? false : "");
            if (el.type === "checkbox")
                el.checked = !!current;
            else
                el.value = current;
            const localName = String(el.localName || el.tagName || "").toLowerCase();
            const eventName = localName === "select" || el.type === "checkbox" ? "change" : "input";
            el.addEventListener(eventName, () => {
                let value;
                if (el.type === "checkbox")
                    value = el.checked;
                else if (el.type === "number")
                    value = Number(el.value);
                else
                    value = el.value;
                this.set(key, value);
                if (key === "translationProvider") {
                    this.updateProviderVisibility(pane);
                }
                if (key === "cloud.providerMode") {
                    this.updateCloudProviderVisibility(pane);
                    if (String(value) === "custom")
                        this.refreshCloudProviderPool({ silent: true });
                    else {
                        const pool = Zotero.ZoteroFulltextTranslator?.cloudProviderPoolSnapshot?.();
                        if (pool) this.updateCloudProviderStatus(pool);
                    }
                }
                if (key === "cloud.providerStrategy") {
                    const pool = Zotero.ZoteroFulltextTranslator?.cloudProviderPoolSnapshot?.();
                    if (pool) this.updateCloudProviderStatus(pool);
                }
                if (["translationProvider", "pdftranslateService", "gpt.model", "quota.enabled", "quota.warnPercent"].includes(key)) {
                    this.refreshQuotaUI();
                }
            });
        });
    },
    bindTabs(pane) {
        pane.querySelectorAll("[data-tab-button]").forEach((btn) => {
            if (btn.dataset.zftTabBound === "true")
                return;
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
        if (!pane)
            return;
        pane.querySelectorAll("[data-tab-button]").forEach((x) => {
            x.dataset.active = x.dataset.tabButton === tab ? "true" : "false";
            x.setAttribute("aria-selected", x.dataset.tabButton === tab ? "true" : "false");
        });
        pane.querySelectorAll("[data-tab]").forEach((x) => {
            x.dataset.active = x.dataset.tab === tab ? "true" : "false";
        });
    },
    updateCloudProviderVisibility(pane = this.pane()) {
        if (!pane)
            return;
        const custom = String(this.get("cloud.providerMode", "server-default")) === "custom";
        const box = pane.querySelector("#zft-cloud-provider-custom");
        if (box)
            box.hidden = !custom;
    },
    selectedCloudProviderIDs() {
        try {
            const raw = JSON.parse(String(this.get("cloud.providerIDs", "[]") || "[]"));
            return Array.isArray(raw) ? [...new Set(raw.map((x) => String(x || "").trim()).filter(Boolean))] : [];
        }
        catch (_) {
            return [];
        }
    },
    saveCloudProviderIDs(ids) {
        const clean = [...new Set((ids || []).map((x) => String(x || "").trim()).filter(Boolean))];
        this.set("cloud.providerIDs", JSON.stringify(clean));
        return clean;
    },
    renderCloudProviderPool(pool) {
        this.renderCloudQuota(pool);
        const list = document.getElementById("zft-cloud-provider-list");
        if (!list)
            return;
        list.replaceChildren();
        const items = Array.isArray(pool?.items) ? pool.items : [];
        const available = new Set(items.map((x) => String(x.id || "")));
        let selected = this.selectedCloudProviderIDs().filter((id) => available.has(id));
        if (!selected.length) {
            const defaults = Array.isArray(pool?.default_provider_ids) ? pool.default_provider_ids.filter((id) => available.has(String(id))) : [];
            selected = defaults.length ? defaults.map(String) : items.map((x) => String(x.id));
            this.saveCloudProviderIDs(selected);
        }
        if (!items.length) {
            const empty = document.createElement("div");
            empty.className = "engine-empty";
            empty.textContent = "Cloud 当前没有已启用且已配置的 API 实例";
            list.append(empty);
            return;
        }
        const duplicateNames = new Map();
        for (const item of items) {
            const name = String(item.display_name || item.kind || item.id || "API");
            duplicateNames.set(name, (duplicateNames.get(name) || 0) + 1);
        }
        for (const item of items) {
            const id = String(item.id || "");
            const label = document.createElement("label");
            label.className = "engine-option";
            const input = document.createElement("input");
            input.type = "checkbox";
            input.checked = selected.includes(id);
            const check = document.createElement("span");
            check.className = "engine-check";
            const copy = document.createElement("span");
            copy.className = "engine-copy";
            const name = document.createElement("span");
            name.className = "engine-name";
            name.textContent = String(item.display_name || item.kind || id);
            const meta = document.createElement("span");
            meta.className = "engine-meta";
            const duplicate = (duplicateNames.get(name.textContent) || 0) > 1;
            const vendor = String(item.vendor || item.kind || "API");
            const suffix = duplicate ? ` · ${id.slice(-6)}` : "";
            meta.textContent = `${vendor}${suffix} · QPS ${Number(item.qps || 0).toLocaleString("zh-CN")}`;
            copy.append(name, meta);
            input.addEventListener("change", () => {
                const current = new Set(this.selectedCloudProviderIDs());
                if (input.checked)
                    current.add(id);
                else
                    current.delete(id);
                this.saveCloudProviderIDs([...current]);
                this.updateCloudProviderStatus(pool);
            });
            label.append(input, check, copy);
            list.append(label);
        }
    },
    formatQuotaChars(value) {
        const n = Number(value);
        if (!Number.isFinite(n) || n < 0)
            return "—";
        if (n >= 100000000)
            return `${(n / 100000000).toFixed(n >= 1000000000 ? 1 : 2).replace(/\.0+$/, "")}亿`;
        if (n >= 10000)
            return `${(n / 10000).toFixed(n >= 100000 ? 1 : 2).replace(/\.0+$/, "")}万`;
        return Math.round(n).toLocaleString("zh-CN");
    },
    quotaLevel(item) {
        const status = String(item?.quota_status || "").toLowerCase();
        if (["exhausted", "unavailable"].includes(status))
            return "critical";
        const pct = Number(item?.quota_remaining_percent);
        const low = Number(item?.quota_low_percent ?? 10);
        if (status === "low" || (Number.isFinite(pct) && Number.isFinite(low) && pct <= low))
            return "low";
        return "normal";
    },
    renderCloudQuota(pool) {
        const list = document.getElementById("zft-cloud-quota-list");
        if (!list)
            return;
        list.replaceChildren();
        const items = Array.isArray(pool?.items) ? pool.items : [];
        if (!items.length) {
            const empty = document.createElement("div");
            empty.className = "quota-empty";
            empty.textContent = "无已启用 API";
            list.append(empty);
            return;
        }
        for (const item of items) {
            const card = document.createElement("div");
            card.className = "quota-card";
            card.dataset.level = this.quotaLevel(item);
            const head = document.createElement("div");
            head.className = "quota-head";
            const name = document.createElement("span");
            name.className = "quota-name";
            name.textContent = String(item.display_name || item.kind || item.id || "API");
            const value = document.createElement("span");
            value.className = "quota-value";
            const total = item.quota_total_chars == null ? NaN : Number(item.quota_total_chars);
            const remaining = item.quota_remaining_chars == null ? NaN : Number(item.quota_remaining_chars);
            const pct = item.quota_remaining_percent == null ? NaN : Number(item.quota_remaining_percent);
            const hasTotal = Number.isFinite(total) && total > 0 && Number.isFinite(remaining);
            if (item.quota_enabled === false)
                value.textContent = "额度统计关闭";
            else if (hasTotal)
                value.textContent = `剩余 ${this.formatQuotaChars(remaining)}`;
            else
                value.textContent = "未设置额度";
            head.append(name, value);
            card.append(head);
            if (hasTotal) {
                const bar = document.createElement("div");
                bar.className = "quota-bar";
                const fill = document.createElement("i");
                fill.style.width = `${Math.max(0, Math.min(100, Number.isFinite(pct) ? pct : (remaining / total * 100)))}%`;
                bar.append(fill);
                const meta = document.createElement("div");
                meta.className = "quota-meta";
                const totalNode = document.createElement("span");
                totalNode.textContent = `总额 ${this.formatQuotaChars(total)}`;
                const pctNode = document.createElement("span");
                const shownPct = Number.isFinite(pct) ? pct : (remaining / total * 100);
                pctNode.textContent = `${Math.max(0, shownPct).toFixed(shownPct < 10 ? 1 : 0)}%`;
                meta.append(totalNode, pctNode);
                card.append(bar, meta);
            }
            list.append(card);
        }
    },
    updateCloudProviderStatus(pool) {
        const items = Array.isArray(pool?.items) ? pool.items : [];
        const selected = new Set(this.selectedCloudProviderIDs());
        const active = items.filter((x) => selected.has(String(x.id))).length;
        const strategy = String(this.get("cloud.providerStrategy", pool?.default_provider_strategy || "balanced"));
        const mode = String(this.get("cloud.providerMode", "server-default"));
        const strategyText = strategy === "failover" ? "主备切换" : "负载均衡";
        const text = mode === "custom" ? `已同步 ${items.length} 个 API 实例 · 已选择 ${active} 个 · ${strategyText}` : `已同步 ${items.length} 个 API 实例 · 使用 Cloud 默认 API 池`;
        this.status("zft-cloud-provider-status", text, false);
    },
    async refreshCloudProviderPool({ silent = false } = {}) {
        const addon = Zotero.ZoteroFulltextTranslator;
        if (!addon?.refreshCloudProviderPool)
            return null;
        if (!silent)
            this.status("zft-cloud-provider-status", "正在同步 Cloud API 池…", false);
        try {
            const pool = await addon.refreshCloudProviderPool(true);
            this.renderCloudProviderPool(pool);
            this.updateCloudProviderStatus(pool);
            return pool;
        }
        catch (e) {
            this.status("zft-cloud-provider-status", `API 池同步失败：${e.message || e}`, true);
            if (!silent)
                throw e;
            return null;
        }
    },
    refreshCloudClientID() {
        const el = document.getElementById("zft-cloud-client-id");
        if (!el)
            return;
        try {
            el.value = Zotero.ZoteroFulltextTranslator?.cloudClientID?.() || "";
        }
        catch (_) {
            el.value = "";
        }
    },
    versionAtLeast(actual, required) {
        const parse = (value) => String(value || "0").split(/[.+-]/)[0].split(".").map((x) => Number(x) || 0);
        const a = parse(actual), r = parse(required);
        for (let i = 0; i < Math.max(a.length, r.length, 3); i++) {
            const av = a[i] || 0, rv = r[i] || 0;
            if (av > rv)
                return true;
            if (av < rv)
                return false;
        }
        return true;
    },
    async refreshCloudStatus() {
        this.status("zft-pdf2zh-cloud-status", "连接中…");
        try {
            const result = await Zotero.ZoteroFulltextTranslator.testCloudPDF2ZH();
            const account = result.account || null;
            if (account?.user) {
                const scopes = Array.isArray(account.scopes) ? account.scopes.join(", ") : "";
                const expiry = account.expires_at ? new Date(account.expires_at).toLocaleDateString("zh-CN") : "不过期";
                this.status("zft-pdf2zh-cloud-status", `已认证：${account.user.display_name || account.user.username} · Cloud v${result.version || "?"} · ${result.elapsedMs} ms\nAPI Key：${account.key_prefix || "zftk"}… · 到期：${expiry}${scopes ? ` · 权限：${scopes}` : ""}`);
                await this.refreshCloudProviderPool({ silent: true });
            }
            else {
                this.status("zft-pdf2zh-cloud-status", `服务器正常：Cloud v${result.version || "?"} · 未解析到账户`, false);
                this.status("zft-cloud-provider-status", "插件 0.4.2 需要 Cloud 2.5.2 账户 API Key。", false);
            }
            return result;
        }
        catch (e) {
            this.status("zft-pdf2zh-cloud-status", `失败：${e.message || e}`, true);
            this.status("zft-cloud-provider-status", "无法读取云端账户状态", true);
            throw e;
        }
    },
    updateProviderVisibility(pane = this.pane()) {
        if (!pane)
            return;
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
        if (!el)
            return;
        el.textContent = text;
        el.style.color = error ? "#b42318" : "";
    },
    loadTranslateServices() {
        const list = document.getElementById("zft-service-list");
        if (!list)
            return [];
        list.replaceChildren();
        let services = [];
        try {
            services = Zotero.ZoteroFulltextTranslator?.getAvailableTranslateServices?.() || [];
        }
        catch (_) { }
        for (const svc of services) {
            if (svc?.type && svc.type !== "sentence")
                continue;
            const opt = document.createElement("option");
            opt.value = svc.id || "";
            opt.label = svc.name ? `${svc.id} — ${svc.name}` : svc.id || "";
            list.append(opt);
        }
        this.status("zft-pdftranslate-status", services.length ? `检测到 ${services.length} 个服务` : "未检测到 Translate for Zotero；可切换到 GPT 直接配置 API", false);
        this.refreshQuotaUI();
        return services;
    },
    quotaServiceID() {
        try {
            return Zotero.ZoteroFulltextTranslator?.currentQuotaServiceID?.() || "pdftranslate-default";
        }
        catch (_) {
            return "pdftranslate-default";
        }
    },
    refreshQuotaUI() {
        const addon = Zotero.ZoteroFulltextTranslator;
        if (!addon?.getQuotaSnapshot)
            return;
        let q;
        try {
            q = addon.getQuotaSnapshot(this.quotaServiceID());
        }
        catch (_) {
            return;
        }
        const setValue = (id, value) => { const el = document.getElementById(id); if (el)
            el.value = value ?? ""; };
        setValue("zft-quota-period", q.period || "month");
        setValue("zft-quota-chars", Number(q.charsLimit) || 0);
        setValue("zft-quota-requests", Number(q.requestsLimit) || 0);
        setValue("zft-quota-qps", Number(q.qps) || 0);
        setValue("zft-quota-maxchars", Number(q.maxChars) || 0);
        const engine = document.getElementById("zft-quota-engine");
        if (engine)
            engine.textContent = `${q.name || q.serviceID} · ${q.serviceID}${q.overridden ? " · 自定义" : q.preset ? " · 内置预设" : ""}`;
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
        }
        catch (e) {
            this.status(statusID, `失败：${e.message || e}`, true);
        }
    },
    async clearCache() {
        this.status("zft-cache-status", "清理中…");
        try {
            const dir = Zotero.ZoteroFulltextTranslator.cacheDir();
            if (await IOUtils.exists(dir))
                await IOUtils.remove(dir, { recursive: true });
            this.status("zft-cache-status", "已清空");
        }
        catch (e) {
            this.status("zft-cache-status", `失败：${e.message || e}`, true);
        }
    },
    bindActions(pane) {
        const on = (id, handler) => {
            const el = pane.querySelector(`#${id}`);
            if (!el || el.dataset.zftActionBound === "true")
                return;
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
            }
            catch (e) {
                this.status("zft-pdftranslate-status", `失败：${e.message || e}`, true);
            }
        });
        on("zft-test-gpt", async () => {
            this.status("zft-gpt-status", "测试中…");
            try {
                const result = await Zotero.ZoteroFulltextTranslator.translateWithGPT("Academic translation connection test.");
                this.status("zft-gpt-status", `正常：${String(result).slice(0, 80)}`);
            }
            catch (e) {
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
            }
            catch (e) {
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
            }
            catch (e) {
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
                }
                catch (e) {
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
            }
            catch (e) {
                this.status("zft-mineru-api-status", `失败：${e.message || e}${e.zftReport ? `\n${e.zftReport}` : ""}`, true);
            }
        });
        on("zft-test-mineru", () => this.testProcess("mineru.command", "mineru", ["--version"], "zft-mineru-status"));
        on("zft-test-doc2x", () => this.testProcess("doc2x.command", "doc2x", ["--version"], "zft-doc2x-status"));
        on("zft-cloud-login", async () => {
            this.status("zft-pdf2zh-cloud-status", "正在验证账户 API Key…");
            try {
                const result = await Zotero.ZoteroFulltextTranslator.cloudVerifyApiKey();
                this.status("zft-pdf2zh-cloud-status", `连接成功：${result?.account?.user?.display_name || result?.account?.user?.username || "Cloud 账户"}`);
                await this.refreshCloudStatus();
            }
            catch (e) {
                this.status("zft-pdf2zh-cloud-status", `验证失败：${e.message || e}`, true);
            }
        });
        on("zft-cloud-logout", async () => {
            await Zotero.ZoteroFulltextTranslator.cloudLogout();
            const keyInput = pane.querySelector('[data-pref="cloud.apiKey"]');
            if (keyInput)
                keyInput.value = "";
            this.status("zft-pdf2zh-cloud-status", "已清除本机 Cloud API Key。文献绑定仍保存在 Cloud 账户中。");
        });
        on("zft-test-pdf2zh-cloud", async () => { try {
            await this.refreshCloudStatus();
        }
        catch (_) { } });
        on("zft-cloud-refresh", async () => { try {
            await this.refreshCloudStatus();
        }
        catch (_) { } });
        on("zft-cloud-console", () => Zotero.ZoteroFulltextTranslator.openCloudConsole());
        on("zft-cloud-provider-refresh", async () => { try {
            await this.refreshCloudProviderPool();
        }
        catch (_) { } });
        on("zft-cloud-recover", async () => {
            this.status("zft-cloud-recover-status", "正在检查云端任务…");
            try {
                const result = await Zotero.ZoteroFulltextTranslator.restoreCloudJobs({ notify: false });
                this.status("zft-cloud-recover-status", `已检查 ${result.total || 0} 个任务 · ${result.active || 0} 个进行中 · ${result.completed || 0} 个待导入`);
            }
            catch (e) {
                this.status("zft-cloud-recover-status", `失败：${e.message || e}`, true);
            }
        });
        on("zft-test-pdf2zh", () => this.testProcess("pdf2zh.command", "pdf2zh_next", ["--version"], "zft-pdf2zh-status"));
        on("zft-clear-cache", () => this.clearCache());
    }
};
window.ZFT_Preferences = ZFT_Preferences;
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => ZFT_Preferences.init(), { once: true });
}
else {
    setTimeout(() => ZFT_Preferences.init(), 0);
}
