(function () {
    const STATE_ID = "zft-cloud-connection-state";
    const STYLE_ID = "zft-cloud-connection-state-style";
    let verifyTimer = null;
    let installed = false;

    function prefs() {
        return globalThis.ZFT_Preferences || window.ZFT_Preferences || null;
    }

    function value(key) {
        try {
            return String(prefs()?.get?.(key, "") || "").trim();
        }
        catch (_) {
            return "";
        }
    }

    function configured() {
        return !!value("cloud.baseURL") && !!value("cloud.apiKey");
    }

    function ensureStyle() {
        if (document.getElementById(STYLE_ID))
            return;
        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = `
#${STATE_ID}{display:inline-flex;align-items:center;gap:7px;min-height:26px;padding:0 10px;border-radius:999px;border:1px solid color-mix(in srgb,currentColor 16%,transparent);background:var(--material-background,#fff);font-size:11px;font-weight:680;white-space:nowrap}
#${STATE_ID} .zft-connection-dot{width:8px;height:8px;border-radius:50%;background:#b42318;box-shadow:0 0 0 2px color-mix(in srgb,#b42318 13%,transparent)}
#${STATE_ID}[data-state="connected"]{color:#1a7f37}
#${STATE_ID}[data-state="connected"] .zft-connection-dot{background:#1a7f37;box-shadow:0 0 0 2px color-mix(in srgb,#1a7f37 13%,transparent)}
#${STATE_ID}[data-state="disconnected"]{color:#b42318}
#${STATE_ID}[data-state="checking"]{opacity:.66}
#${STATE_ID}[data-state="checking"] .zft-connection-dot{background:currentColor;box-shadow:none;animation:zft-cloud-state-pulse 1s ease-in-out infinite}
@keyframes zft-cloud-state-pulse{50%{opacity:.35;transform:scale(.82)}}
.zft-cloud-heading-with-state{display:flex!important;align-items:center;justify-content:space-between;gap:12px}
.zft-cloud-heading-with-state>h2{margin:0!important}
`;
        (document.head || document.documentElement).append(style);
    }

    function ensureIndicator() {
        let el = document.getElementById(STATE_ID);
        if (el)
            return el;
        const status = document.getElementById("zft-pdf2zh-cloud-status");
        const section = status?.closest?.(".section");
        const heading = section?.querySelector?.("h2");
        if (!section || !heading)
            return null;
        const row = document.createElement("div");
        row.className = "zft-cloud-heading-with-state";
        heading.parentNode.insertBefore(row, heading);
        row.append(heading);
        el = document.createElement("span");
        el.id = STATE_ID;
        el.setAttribute("role", "status");
        el.setAttribute("aria-live", "polite");
        const dot = document.createElement("span");
        dot.className = "zft-connection-dot";
        const label = document.createElement("span");
        label.className = "zft-connection-label";
        el.append(dot, label);
        row.append(el);
        return el;
    }

    function setState(state) {
        ensureStyle();
        const el = ensureIndicator();
        if (!el)
            return;
        const normalized = state === "connected" ? "connected" : state === "checking" ? "checking" : "disconnected";
        el.dataset.state = normalized;
        const label = el.querySelector(".zft-connection-label");
        const text = normalized === "connected" ? "已连接" : normalized === "checking" ? "验证中" : "断开连接";
        if (label)
            label.textContent = text;
        el.setAttribute("aria-label", text);
    }

    async function verify() {
        if (!configured()) {
            setState("disconnected");
            return false;
        }
        const p = prefs();
        if (!p?.refreshCloudStatus) {
            setState("disconnected");
            return false;
        }
        setState("checking");
        try {
            const result = await p.refreshCloudStatus();
            const ok = !!result?.account?.user;
            setState(ok ? "connected" : "disconnected");
            return ok;
        }
        catch (_) {
            setState("disconnected");
            return false;
        }
    }

    function schedule(delay = 700) {
        if (verifyTimer)
            clearTimeout(verifyTimer);
        if (!configured()) {
            setState("disconnected");
            return;
        }
        setState("checking");
        verifyTimer = setTimeout(() => {
            verifyTimer = null;
            verify();
        }, delay);
    }

    function install() {
        if (installed)
            return;
        const pane = document.getElementById("zft-prefpane");
        if (!pane) {
            setTimeout(install, 60);
            return;
        }
        installed = true;
        ensureStyle();
        ensureIndicator();

        const p = prefs();
        if (p?.refreshCloudStatus && !p.refreshCloudStatus.__zftConnectionWrapped) {
            const original = p.refreshCloudStatus.bind(p);
            const wrapped = async function (...args) {
                if (!configured()) {
                    setState("disconnected");
                    return original(...args);
                }
                setState("checking");
                try {
                    const result = await original(...args);
                    setState(result?.account?.user ? "connected" : "disconnected");
                    return result;
                }
                catch (e) {
                    setState("disconnected");
                    throw e;
                }
            };
            wrapped.__zftConnectionWrapped = true;
            p.refreshCloudStatus = wrapped;
        }

        for (const selector of ['[data-pref="cloud.baseURL"]', '[data-pref="cloud.apiKey"]']) {
            const input = pane.querySelector(selector);
            input?.addEventListener("input", () => schedule(700));
            input?.addEventListener("change", () => schedule(150));
        }

        pane.querySelector("#zft-cloud-login")?.addEventListener("click", () => schedule(900));
        pane.querySelector("#zft-cloud-logout")?.addEventListener("click", () => {
            if (verifyTimer)
                clearTimeout(verifyTimer);
            setTimeout(() => setState("disconnected"), 0);
        });

        if (configured())
            schedule(80);
        else
            setState("disconnected");
    }

    if (document.readyState === "loading")
        document.addEventListener("DOMContentLoaded", install, { once: true });
    else
        setTimeout(install, 0);
})();
