(function () {
  const PREF = "extensions.zotero.zft.";
  const PANEL_ID = "zft-translation-panel";
  const CLOUD_PANEL_ID = "zft-cloud-pdf-panel";
  const CLOUD_FRAME_ID = "zft-cloud-pdf-frame";
  const STYLE_ID = "zft-reader-style";
  const BUTTON_CLASS = "zft-toolbar-button";
  const TASK_HUD_ID = "zft-task-hud";
  const COMPARE_BADGE_ID = "zft-native-compare-badge";

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function asString(v, fallback = "") {
    return v === undefined || v === null ? fallback : String(v);
  }

  function escapeHTML(text) {
    return asString(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function basename(path) {
    try {
      return PathUtils.filename(path);
    } catch (_) {
      return asString(path).split(/[\\/]/).pop() || "document.pdf";
    }
  }

  function stem(path) {
    return basename(path).replace(/\.pdf$/i, "");
  }

  function fnv1a(text) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash.toString(16).padStart(8, "0");
  }

  function splitArgs(input) {
    const out = [];
    const s = asString(input).trim();
    if (!s) return out;
    let cur = "";
    let quote = null;
    let escaped = false;
    for (const ch of s) {
      if (escaped) {
        cur += ch;
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (quote) {
        if (ch === quote) quote = null;
        else cur += ch;
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
        continue;
      }
      if (/\s/.test(ch)) {
        if (cur) {
          out.push(cur);
          cur = "";
        }
      } else {
        cur += ch;
      }
    }
    if (cur) out.push(cur);
    return out;
  }

  const Addon = {
    id: "zotero-fulltext-translator@zft.local",
    version: "0.2.22",
    rootURI: "",
    toolbarHandler: null,
    viewContextHandler: null,
    states: new Map(),
    windows: new Set(),
    windowHandlers: new Map(),
    toolbarSweepTimers: new Map(),
    cloudJobs: new Map(),
    cloudMonitors: new Set(),
    cloudImporting: new Set(),
    cloudRecoveryTimer: null,

    log(...args) {
      if (!this.pref("debug", false)) return;
      try {
        Zotero.debug("[Zotero-full-translate] " + args.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" "));
      } catch (_) {}
    },

    pref(key, fallback) {
      try {
        const value = Zotero.Prefs.get(PREF + key, true);
        return value === undefined || value === null ? fallback : value;
      } catch (_) {
        return fallback;
      }
    },

    setPref(key, value) {
      Zotero.Prefs.set(PREF + key, value, true);
    },

    async init({ id, version, rootURI }) {
      this.id = id;
      this.version = version;
      this.rootURI = rootURI;
      if (this.pref("cloud.thinClient", true)) this.setPref("output.layoutEngine", "zft-cloud");
      this.cloudClientID();
      this.registerReaderHooks();
      for (const win of Zotero.getMainWindows ? Zotero.getMainWindows() : []) {
        this.onMainWindowLoad(win);
      }
      this.log(`initialized ${version} on Zotero ${Zotero.version}`);
      if (this.pref("cloud.autoRestore", true)) {
        this.cloudRecoveryTimer = setTimeout(() => {
          this.restoreCloudJobs().catch((e) => this.log("cloud restore failed", this.safeErrorMessage(e)));
        }, 1800);
      }
    },

    onMainWindowLoad(win) {
      if (!win || this.windows.has(win)) return;
      this.windows.add(win);
      this.installShortcut(win);
      this.startToolbarSweep(win);
    },

    onMainWindowUnload(win) {
      const handler = this.windowHandlers.get(win);
      if (handler) { try { win.removeEventListener("keydown", handler, true); } catch (_) {} }
      this.windowHandlers.delete(win);
      const timer = this.toolbarSweepTimers.get(win);
      if (timer) clearInterval(timer);
      this.toolbarSweepTimers.delete(win);
      this.windows.delete(win);
    },

    async shutdown() {
      try {
        if (this.toolbarHandler) Zotero.Reader.unregisterEventListener("renderToolbar", this.toolbarHandler);
      } catch (_) {}
      try {
        if (this.viewContextHandler) Zotero.Reader.unregisterEventListener("createViewContextMenu", this.viewContextHandler);
      } catch (_) {}
      if (this.cloudRecoveryTimer) clearTimeout(this.cloudRecoveryTimer);
      this.cloudRecoveryTimer = null;
      for (const state of this.states.values()) this.destroyState(state);
      this.states.clear();
      for (const [win, handler] of this.windowHandlers) { try { win.removeEventListener("keydown", handler, true); } catch (_) {} }
      this.windowHandlers.clear();
      for (const timer of this.toolbarSweepTimers.values()) clearInterval(timer);
      this.toolbarSweepTimers.clear();
      this.windows.clear();
    },

    registerReaderHooks() {
      this.toolbarHandler = (event) => this.onRenderToolbar(event);
      Zotero.Reader.registerEventListener("renderToolbar", this.toolbarHandler, this.id);

      this.viewContextHandler = (event) => {
        const { reader, append } = event;
        append({
          label: "ZFT Cloud 全文翻译（BabelDOC）",
          onCommand: () => this.exportLayoutPDF(reader, "zft-cloud").catch((e) => this.reportError(e, reader)),
        });
        append({
          label: "重新翻译（忽略历史与本地译文）",
          onCommand: () => this.exportLayoutPDF(reader, "zft-cloud", { forceRetranslate: true }).catch((e) => this.reportError(e, reader)),
        });
        append({
          label: "打开 Zotero-full-translate Cloud 控制台",
          onCommand: () => this.openCloudConsole(),
        });
        append({
          label: "左右对照打开原文 / 译文",
          onCommand: () => this.openComparisonForReader(reader).catch((e) => this.reportError(e, reader)),
        });
      };
      Zotero.Reader.registerEventListener("createViewContextMenu", this.viewContextHandler, this.id);
    },

    onRenderToolbar({ reader, doc, append }) {
      if (!this.pref("enabled", true)) return;
      this.injectReaderStyle(doc);
      // If a fallback button was injected before React's CustomSections event
      // fired, remove it and let the official event own this toolbar section.
      doc.querySelectorAll('[data-zft-toolbar-fallback="true"]').forEach((el) => el.remove());
      const button = this.createToolbarButton(reader, doc);
      append(button);
      this.trackToolbarButton(reader, doc, button);
    },

    createToolbarButton(reader, doc) {
      const button = doc.createElement("button");
      button.type = "button";
      button.className = `toolbar-button ${BUTTON_CLASS}`;
      button.dataset.zftToolbar = "true";
      button.title = "全文翻译菜单（Shift+T）";
      button.textContent = "译";
      button.setAttribute("aria-label", "全文翻译");
      button.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        this.showQuickMenu(reader, doc, ev.currentTarget);
      });
      return button;
    },

    trackToolbarButton(reader, doc, button) {
      const state = this.getState(reader, doc);
      if (!state.toolbarButtons) state.toolbarButtons = new Set();
      state.toolbarButtons.add(button);
      this.updateToolbarTaskState(state);
      return button;
    },

    ensureReaderToolbarButton(reader) {
      if (!reader || !this.pref("enabled", true)) return false;
      const doc = this.getReaderDoc(reader);
      if (!doc) return false;
      this.injectReaderStyle(doc);
      const existing = doc.querySelector(`.${BUTTON_CLASS}`);
      if (existing) {
        this.trackToolbarButton(reader, doc, existing);
        return true;
      }
      const toolbar = doc.querySelector(".toolbar");
      if (!toolbar) return false;
      const end = toolbar.querySelector(".end") || toolbar;
      const button = this.createToolbarButton(reader, doc);
      button.dataset.zftToolbarFallback = "true";
      const custom = end.querySelector(".custom-sections");
      if (custom) {
        const section = doc.createElement("div");
        section.className = "section zft-fallback-section";
        section.dataset.zftToolbarFallback = "true";
        section.append(button);
        custom.append(section);
      } else {
        end.insertBefore(button, end.querySelector("#appearance,.find") || null);
      }
      this.trackToolbarButton(reader, doc, button);
      return true;
    },

    startToolbarSweep(win) {
      if (!win || this.toolbarSweepTimers.has(win)) return;
      const tick = () => {
        try {
          const reader = this.getActiveReader(win);
          if (reader) this.ensureReaderToolbarButton(reader);
        } catch (e) {
          this.log("toolbar sweep failed", String(e));
        }
      };
      tick();
      this.toolbarSweepTimers.set(win, setInterval(tick, 1200));
    },

    injectReaderStyle(doc) {
      if (!doc || doc.getElementById(STYLE_ID)) return;
      const style = doc.createElement("style");
      style.id = STYLE_ID;
      style.textContent = `
        .zft-toolbar-wrap{display:flex;align-items:center;gap:2px;margin-inline:2px}
        .zft-toolbar-button{appearance:none;border:0;background:transparent;color:inherit;border-radius:5px;min-width:28px;height:28px;font-weight:700;cursor:pointer;padding:0 7px}
        .zft-toolbar-button:hover{background:color-mix(in srgb,currentColor 12%,transparent)}
        .zft-toolbar-small{min-width:20px;padding:0 3px;font-size:11px}
        #${PANEL_ID}{position:fixed;z-index:100050;box-sizing:border-box;background:var(--material-background,#fff);color:var(--fill-primary,#222);border-left:1px solid color-mix(in srgb,currentColor 16%,transparent);box-shadow:-2px 0 12px rgba(0,0,0,.08);display:flex;flex-direction:column;overflow:hidden;pointer-events:auto}
        #${PANEL_ID}[hidden],#${CLOUD_PANEL_ID}[hidden]{display:none!important}
        #${PANEL_ID}[data-layout="vertical"]{top:0;right:0;width:44%;height:100%}
        #${PANEL_ID}[data-layout="horizontal"]{left:0;bottom:0;width:100%;height:42%;border-left:0;border-top:1px solid color-mix(in srgb,currentColor 16%,transparent)}
        #${PANEL_ID} .zft-head{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid color-mix(in srgb,currentColor 14%,transparent);background:color-mix(in srgb,var(--material-background,#fff) 94%,currentColor 6%);flex:0 0 auto}
        #${PANEL_ID} .zft-title{font-weight:650;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        #${PANEL_ID} .zft-status{font-size:11px;opacity:.72;white-space:nowrap}
        #${PANEL_ID} .zft-close,#${PANEL_ID} .zft-action{appearance:none;border:0;background:transparent;color:inherit;border-radius:5px;padding:4px 7px;cursor:pointer}
        #${PANEL_ID} .zft-close:hover,#${PANEL_ID} .zft-action:hover{background:color-mix(in srgb,currentColor 12%,transparent)}
        #${PANEL_ID} .zft-modes{display:flex;align-items:center;gap:2px;padding:2px;border-radius:6px;background:color-mix(in srgb,currentColor 7%,transparent)}
        #${PANEL_ID} .zft-mode{appearance:none;border:0;background:transparent;color:inherit;border-radius:4px;padding:3px 6px;font-size:11px;cursor:pointer}
        #${PANEL_ID} .zft-mode[data-active="true"]{background:var(--material-background,#fff);box-shadow:0 1px 2px rgba(0,0,0,.12)}
        #${PANEL_ID} .zft-body{overflow:auto;flex:1;padding:10px 14px 40px;scroll-behavior:smooth}
        #${PANEL_ID} .zft-page{margin:0 0 18px;padding:0 0 12px;border-bottom:1px dashed color-mix(in srgb,currentColor 15%,transparent)}
        #${PANEL_ID} .zft-page-label{position:sticky;top:-10px;padding:5px 0;font-size:11px;font-weight:650;opacity:.7;background:var(--material-background,#fff);z-index:2}
        #${PANEL_ID} .zft-segment{padding:5px 7px;margin:2px -7px;border-radius:6px;line-height:1.68;cursor:pointer;white-space:pre-wrap;word-break:break-word}
        #${PANEL_ID} .zft-segment:hover{background:color-mix(in srgb,currentColor 7%,transparent)}
        #${PANEL_ID} .zft-source{font-size:.86em;opacity:.56;margin-bottom:4px}
        #${PANEL_ID} .zft-translation{font-size:1em}
        #${PANEL_ID} .zft-segment[data-role="paper-title"] .zft-translation{font-size:1.38em;font-weight:700;text-align:center;line-height:1.35}
        #${PANEL_ID} .zft-segment[data-role="author"] .zft-translation,#${PANEL_ID} .zft-segment[data-role="affiliation"] .zft-translation{text-align:center}
        #${PANEL_ID} .zft-segment[data-role="heading-1"] .zft-translation{font-size:1.22em;font-weight:700;margin-top:8px}
        #${PANEL_ID} .zft-segment[data-role="heading-2"] .zft-translation{font-size:1.13em;font-weight:650;margin-top:6px}
        #${PANEL_ID} .zft-segment[data-role="heading-3"] .zft-translation{font-size:1.06em;font-weight:600;margin-top:4px}
        #${PANEL_ID} .zft-segment[data-role="abstract-heading"] .zft-translation,#${PANEL_ID} .zft-segment[data-role="keywords-heading"] .zft-translation,#${PANEL_ID} .zft-segment[data-role="reference-heading"] .zft-translation{font-weight:700}
        #${PANEL_ID} .zft-segment[data-role="footnote"] .zft-translation,#${PANEL_ID} .zft-segment[data-role="reference-entry"] .zft-translation{font-size:.9em;line-height:1.5}
        #${PANEL_ID} .zft-asset-placeholder{box-sizing:border-box;margin-top:10px;margin-bottom:10px;padding:10px 12px;border:1px dashed color-mix(in srgb,currentColor 28%,transparent);border-radius:8px;background:color-mix(in srgb,currentColor 4%,transparent);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;overflow:hidden}
        #${PANEL_ID} .zft-asset-label{font-weight:650}
        #${PANEL_ID} .zft-asset-note{font-size:.78em;opacity:.58;margin-top:3px}
        #${PANEL_ID} .zft-error{color:#b42318;background:rgba(180,35,24,.08);padding:8px;border-radius:6px}
        #${PANEL_ID} .zft-progress{height:2px;background:color-mix(in srgb,currentColor 10%,transparent);overflow:hidden}
        #${PANEL_ID} .zft-progress>i{display:block;height:100%;background:currentColor;opacity:.65;width:0%;transition:width .2s ease}
        #${CLOUD_PANEL_ID}{position:fixed;z-index:100060;top:0;right:0;width:44%;height:100%;box-sizing:border-box;background:var(--material-background,#fff);color:var(--fill-primary,#222);border-left:1px solid color-mix(in srgb,currentColor 18%,transparent);box-shadow:-2px 0 12px rgba(0,0,0,.10);display:flex;flex-direction:column;overflow:hidden;pointer-events:auto}
        #${CLOUD_PANEL_ID} .zft-cloud-head{display:flex;align-items:center;gap:6px;padding:7px 9px;border-bottom:1px solid color-mix(in srgb,currentColor 14%,transparent);background:color-mix(in srgb,var(--material-background,#fff) 94%,currentColor 6%);flex:0 0 auto}
        #${CLOUD_PANEL_ID} .zft-cloud-title{min-width:0;flex:1;font-size:12px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        #${CLOUD_PANEL_ID} .zft-cloud-action{appearance:none;border:0;background:transparent;color:inherit;border-radius:5px;padding:4px 7px;cursor:pointer;font-size:11px}
        #${CLOUD_PANEL_ID} .zft-cloud-action:hover{background:color-mix(in srgb,currentColor 12%,transparent)}
        #${CLOUD_PANEL_ID} .zft-cloud-body{position:relative;flex:1;min-height:0;background:var(--material-background,#fff)}
        #${CLOUD_PANEL_ID} iframe{display:block;width:100%;height:100%;border:0;background:#fff}
        #${CLOUD_PANEL_ID} .zft-cloud-fallback{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;padding:28px;line-height:1.6;background:var(--material-background,#fff);color:var(--fill-primary,#222)}
        #${CLOUD_PANEL_ID}[data-load-error="true"] .zft-cloud-fallback{display:flex}
        #${CLOUD_PANEL_ID}[data-load-error="true"] iframe{display:none}
        #${COMPARE_BADGE_ID}{position:fixed;z-index:99998;right:14px;top:48px;display:flex;align-items:center;gap:7px;padding:6px 8px;border-radius:7px;background:var(--material-background,#fff);color:var(--fill-primary,#222);border:1px solid color-mix(in srgb,currentColor 16%,transparent);box-shadow:0 5px 18px rgba(0,0,0,.14);font-size:11px}
        #${COMPARE_BADGE_ID} label{display:flex;align-items:center;gap:5px;cursor:pointer}
        #${COMPARE_BADGE_ID} button{appearance:none;border:0;background:transparent;color:inherit;cursor:pointer;padding:2px 4px;border-radius:4px}
        #${COMPARE_BADGE_ID} button:hover{background:color-mix(in srgb,currentColor 10%,transparent)}
        .zft-quick-menu{position:fixed;z-index:99999;min-width:215px;background:var(--material-background,#fff);color:var(--fill-primary,#222);border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:8px;padding:5px;box-shadow:0 8px 32px rgba(0,0,0,.18)}
        .zft-quick-menu button{display:block;width:100%;text-align:left;border:0;background:transparent;color:inherit;border-radius:5px;padding:7px 9px;cursor:pointer}
        .zft-quick-menu button:hover{background:color-mix(in srgb,currentColor 10%,transparent)}
        .zft-quick-menu .zft-menu-head{padding:7px 9px 8px;font-size:12px;font-weight:650;border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent);margin-bottom:4px}
        .zft-quick-menu .zft-menu-sub{font-size:11px;font-weight:400;opacity:.68;margin-top:2px}
        .zft-quick-menu .zft-menu-sep{height:1px;background:color-mix(in srgb,currentColor 12%,transparent);margin:5px 3px}
        .zft-quick-menu button:disabled{opacity:.45;cursor:default}
        .zft-toolbar-button[data-running="true"]{min-width:48px;font-variant-numeric:tabular-nums}
        #${TASK_HUD_ID}{position:fixed;right:16px;top:52px;z-index:100000;width:min(360px,calc(100vw - 32px));background:var(--material-background,#fff);color:var(--fill-primary,#222);border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:10px;box-shadow:0 10px 34px rgba(0,0,0,.2);overflow:hidden}
        #${TASK_HUD_ID}[hidden]{display:none}
        #${TASK_HUD_ID} .zft-task-main{display:flex;align-items:center;gap:10px;padding:10px 11px 8px}
        #${TASK_HUD_ID} .zft-task-spinner{width:15px;height:15px;border:2px solid color-mix(in srgb,currentColor 20%,transparent);border-top-color:currentColor;border-radius:50%;animation:zft-spin .8s linear infinite;flex:0 0 auto}
        #${TASK_HUD_ID}[data-state="done"] .zft-task-spinner{animation:none;border:0;width:16px;height:16px}
        #${TASK_HUD_ID}[data-state="done"] .zft-task-spinner::before{content:"✓";font-weight:800}
        #${TASK_HUD_ID}[data-state="error"] .zft-task-spinner{animation:none;border:0;width:16px;height:16px;color:#b42318}
        #${TASK_HUD_ID}[data-state="error"] .zft-task-spinner::before{content:"!";font-weight:800}
        #${TASK_HUD_ID}[data-state="idle"] .zft-task-spinner{animation:none;border:0;width:16px;height:16px}
        #${TASK_HUD_ID}[data-state="idle"] .zft-task-spinner::before{content:"—";font-weight:700}
        #${TASK_HUD_ID} .zft-task-copy{min-width:0;flex:1}
        #${TASK_HUD_ID} .zft-task-title{font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        #${TASK_HUD_ID} .zft-task-status{font-size:11px;opacity:.72;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        #${TASK_HUD_ID} .zft-task-percent{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
        #${TASK_HUD_ID} .zft-task-cancel,#${TASK_HUD_ID} .zft-task-detail,#${TASK_HUD_ID} .zft-task-dismiss{appearance:none;border:0;background:transparent;color:inherit;border-radius:5px;padding:4px 7px;cursor:pointer;font-size:11px}
        #${TASK_HUD_ID} .zft-task-cancel:hover,#${TASK_HUD_ID} .zft-task-detail:hover,#${TASK_HUD_ID} .zft-task-dismiss:hover{background:color-mix(in srgb,currentColor 10%,transparent)}
        #${TASK_HUD_ID} .zft-task-progress{height:3px;background:color-mix(in srgb,currentColor 10%,transparent)}
        #${TASK_HUD_ID} .zft-task-progress>i{display:block;height:100%;background:currentColor;width:0%;transition:width .18s ease}
        @keyframes zft-spin{to{transform:rotate(360deg)}}
      `;
      doc.documentElement.append(style);
    },

    installShortcut(win) {
      const handler = (ev) => {
        const shortcut = asString(this.pref("shortcut", "Shift+T")).toLowerCase().replace(/\s+/g, "");
        const actual = [ev.ctrlKey ? "ctrl" : "", ev.altKey ? "alt" : "", ev.shiftKey ? "shift" : "", ev.metaKey ? "meta" : "", ev.key.toLowerCase()]
          .filter(Boolean)
          .join("+");
        if (actual !== shortcut) return;
        const reader = this.getActiveReader();
        if (!reader) return;
        ev.preventDefault();
        const doc = this.getReaderDoc(reader);
        if (!doc) return;
        const anchor = doc.querySelector(`.${BUTTON_CLASS}`) || null;
        this.showQuickMenu(reader, doc, anchor);
      };
      try {
        win.addEventListener("keydown", handler, true);
        this.windowHandlers.set(win, handler);
      } catch (_) {}
    },

    getActiveReader(win = null) {
      try {
        win = win || Zotero.getMainWindow?.();
        const tabID = win?.Zotero_Tabs?.selectedID;
        if (tabID) {
          const reader = Zotero.Reader.getByTabID?.(tabID);
          if (reader) return reader;
        }
        // Fallback for standalone reader windows / future Zotero builds.
        const readers = Zotero.Reader?._readers;
        if (Array.isArray(readers)) {
          return readers.find((r) => r?._iframeWindow?.document?.visibilityState !== "hidden") || readers[0] || null;
        }
        if (readers?.values) {
          const arr = [...readers.values()];
          return arr.find((r) => r?._iframeWindow?.document?.visibilityState !== "hidden") || arr[0] || null;
        }
      } catch (e) {
        this.log("getActiveReader failed", String(e));
      }
      return null;
    },

    getReaderDoc(reader) {
      try {
        return reader?._iframeWindow?.document || reader?._iframeWindow?.wrappedJSObject?.document || null;
      } catch (_) {
        return null;
      }
    },

    isLiveDocument(doc) {
      try {
        return !!(doc && doc.documentElement && doc.body && doc.defaultView && !doc.defaultView.closed);
      } catch (_) {
        return false;
      }
    },

    refreshStateDocument(state, preferredDoc = null) {
      if (!state) return null;
      let doc = this.isLiveDocument(preferredDoc) ? preferredDoc : null;
      if (!doc) {
        const fresh = this.getReaderDoc(state.reader);
        if (this.isLiveDocument(fresh)) doc = fresh;
      }
      if (!doc && this.isLiveDocument(state.doc)) doc = state.doc;
      if (!doc) return null;
      if (state.doc !== doc || !this.isLiveDocument(state.doc)) {
        state.doc = doc;
        // DOM references belong to a specific Reader document. Never carry them across reloads.
        state.panel = null;
        state.cloudPanel = null;
        state.cloudPanelFrame = null;
        state.cloudPanelTranslatedItemID = null;
        state.body = null;
        state.status = null;
        state.progress = null;
        state.taskHUD = null;
        try { this.stopNativeCompareSession(state); } catch (_) {}
        state.nativeCompare = null;
        state.overlayDoc = null;
        state.originalSplitStyle = null;
        for (const observer of state.overlayResizeObservers?.values?.() || []) {
          try { observer.disconnect(); } catch (_) {}
        }
        state.overlayResizeObservers?.clear?.();
        // Old toolbar buttons may already be Firefox dead objects; drop all references eagerly.
        state.toolbarButtons = new Set();
      }
      return doc;
    },

    safeDOMConnected(node) {
      try { return !!(node && node.isConnected); } catch (_) { return false; }
    },

    getAttachmentID(reader) {
      const readers = [reader];
      for (const r of readers) {
        if (!r) continue;
        const getters = [
          () => r.itemID,
          () => r._itemID,
          () => r._item?.id,
          () => r._reader?.itemID,
          () => r._reader?._itemID,
          () => r._iframeWindow?.wrappedJSObject?._reader?._itemID,
        ];
        for (const get of getters) {
          try {
            const n = Number(get());
            if (Number.isInteger(n) && n > 0) return n;
          } catch (_) {}
        }
      }
      return null;
    },

    layoutEngineLabel(engine) {
      if (engine === "doc2x") return "Doc2X";
      if (engine === "zft-cloud" || engine === "pdf2zh-cloud") return "Zotero-full-translate Cloud · BabelDOC";
      return "本地 pdf2zh_next";
    },

    normalizedLayoutEngine(value = null) {
      const raw = asString(value ?? this.pref("output.layoutEngine", "zft-cloud"));
      if (raw === "pdf2zh-cloud") return "zft-cloud";
      if (["doc2x", "pdf2zh", "zft-cloud"].includes(raw)) return raw;
      return "zft-cloud";
    },

    getState(reader, doc) {
      const itemID = this.getAttachmentID(reader) || `reader-${Date.now()}`;
      let state = this.states.get(itemID);
      if (!state) {
        state = {
          itemID,
          reader,
          doc: doc || this.getReaderDoc(reader),
          panel: null,
          panelDismissed: false,
          cloudPanel: null,
          cloudPanelFrame: null,
          cloudPanelTranslatedItemID: null,
          body: null,
          status: null,
          progress: null,
          running: false,
          completed: false,
          segments: [],
          layoutBlocks: [],
          translationByIndex: new Map(),
          syncTimer: null,
          cacheKey: "",
          layoutAvailable: false,
          displayMode: "",
          overlayTimer: null,
          overlayDoc: null,
          overlayScheduled: null,
          overlayResizeObservers: new Map(),
          toolbarButtons: new Set(),
          taskHUD: null,
          taskHUDDismissed: false,
          taskStatusText: "就绪",
          taskProgress: 0,
          taskStartedAt: 0,
          taskTimer: null,
          cancelRequested: false,
          currentProcess: null,
          currentAbort: null,
          remoteLayoutJobID: null,
          lastCompletedCloudJobID: null,
          taskScope: "all",
        };
        this.states.set(itemID, state);
      } else {
        state.reader = reader || state.reader;
      }
      this.refreshStateDocument(state, doc);
      return state;
    },

    destroyState(state) {
      if (!state) return;
      if (state.syncTimer) clearInterval(state.syncTimer);
      state.syncTimer = null;
      if (state.taskTimer) clearInterval(state.taskTimer);
      state.taskTimer = null;
      try { state.currentProcess?.kill?.(); } catch (_) {}
      state.currentProcess = null;
      // Cloud jobs deliberately survive Reader/Zotero shutdown. Explicit user cancellation
      // is the only path that sends DELETE to ZFT Cloud.
      this.stopOverlayLifecycle(state);
      try { this.stopNativeCompareSession(state); } catch (_) {}
      try { this.removeTranslationOverlays(state); } catch (_) {}
      try { state.panel?.remove(); } catch (_) {}
      try { state.cloudPanel?.remove(); } catch (_) {}
    },

    showQuickMenu(reader, doc, anchor) {
      if (!doc) return;
      doc.querySelectorAll(".zft-quick-menu").forEach((el) => el.remove());
      const state = this.getState(reader, doc);
      const menu = doc.createElement("div");
      menu.className = "zft-quick-menu";

      const head = doc.createElement("div");
      head.className = "zft-menu-head";
      head.textContent = state.running ? "全文翻译 · 正在运行" : "全文翻译";
      const sub = doc.createElement("div");
      sub.className = "zft-menu-sub";
      const quotaMenuText = this.pref("quota.enabled", true) ? this.quotaSummaryText() : "";
      sub.textContent = state.running
        ? `${state.taskStatusText || "处理中"} · ${Math.round(state.taskProgress || 0)}%`
        : `选择翻译范围或阅读模式${quotaMenuText ? ` · ${quotaMenuText}` : ""}`;
      head.append(sub);
      menu.append(head);

      const addAction = (label, fn, { disabled = false, danger = false } = {}) => {
        const b = doc.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.disabled = disabled;
        if (danger) b.style.color = "#b42318";
        b.addEventListener("click", () => {
          menu.remove();
          Promise.resolve(fn()).catch((e) => this.reportError(e, reader));
        });
        menu.append(b);
      };
      const sep = () => {
        const el = doc.createElement("div");
        el.className = "zft-menu-sep";
        menu.append(el);
      };

      const cloudSummary = this.cloudProviderSummary();
      addAction(`全文翻译 · ZFT Cloud · ${cloudSummary}`, () => this.exportLayoutPDF(reader, "zft-cloud"), { disabled: state.running });
      addAction("重新翻译 · 忽略历史/缓存", () => this.exportLayoutPDF(reader, "zft-cloud", { forceRetranslate: true }), { disabled: state.running });
      if (state.running) addAction("取消云端翻译", () => this.cancelTranslation(state), { danger: true });
      addAction("打开 Zotero-full-translate Cloud 控制台", () => this.openCloudConsole());
      addAction("恢复/检查云端任务", () => this.restoreCloudJobs({ notify: true }));
      const currentItem = state.itemID ? Zotero.Items.get(state.itemID) : null;
      const comparePair = currentItem ? this.findComparePair(currentItem) : null;
      if (comparePair) {
        sep();
        const comparisonOpen = !!state.nativeCompare || this.safeDOMConnected(state.cloudPanel);
        addAction(comparisonOpen ? "关闭右侧译文" : "右侧显示译文", () => {
          if (state.nativeCompare) this.stopNativeCompareSession(state);
          else if (this.safeDOMConnected(state.cloudPanel)) this.closeCloudPDFPanel(state);
          else return this.openComparisonForReader(reader);
        });
        addAction(`联动滚动：${this.pref("compare.syncScroll", true) ? "开" : "关"}`, () => {
          const next = !this.pref("compare.syncScroll", true);
          this.setPref("compare.syncScroll", next);
          try {
            const check = this.refreshStateDocument(state)?.querySelector?.(`#${COMPARE_BADGE_ID} input[type="checkbox"]`);
            if (check) check.checked = next;
          } catch (_) {}
        });
      }
      if (!this.pref("cloud.thinClient", true)) {
        sep();
        addAction(state.completed && state.taskScope === "all" ? "重新生成阅读器全文预览" : "阅读器全文翻译 · 兼容模式", () => this.translateReader(reader, doc, { scope: "all", force: state.completed && state.taskScope === "all" }), { disabled: state.running });
        addAction("翻译当前页 · 兼容模式", () => this.translateReader(reader, doc, { scope: "current-page" }), { disabled: state.running });
      }

      const rect = anchor?.getBoundingClientRect?.();
      if (rect) {
        menu.style.left = `${Math.max(8, Math.min(doc.defaultView.innerWidth - 230, rect.right - 215))}px`;
        menu.style.top = `${Math.min(doc.defaultView.innerHeight - 330, rect.bottom + 4)}px`;
      } else {
        menu.style.right = "16px";
        menu.style.top = "52px";
      }
      doc.body.append(menu);
      const close = (ev) => {
        if (!menu.contains(ev.target) && ev.target !== anchor) {
          menu.remove();
          doc.removeEventListener("mousedown", close, true);
        }
      };
      setTimeout(() => doc.addEventListener("mousedown", close, true), 0);
    },

    showQuotaDiagnostic(serviceID = null) {
      const q = this.getQuotaSnapshot(serviceID || this.currentQuotaServiceID());
      const lines = [
        `引擎：${q.name || q.serviceID} (${q.serviceID})`,
        `周期：${q.period === "account" ? "账号总额度" : q.periodKey}`,
        `已统计字符：${this.formatQuotaNumber(q.chars)}${q.charsLimit ? ` / ${this.formatQuotaNumber(q.charsLimit)}` : "（未设置上限）"}`,
        `已统计请求：${this.formatQuotaNumber(q.requests)}${q.requestsLimit ? ` / ${this.formatQuotaNumber(q.requestsLimit)}` : "（未设置上限）"}`,
        q.qps ? `QPS 建议上限：${q.qps}` : "",
        q.maxChars ? `单次字段上限：${q.maxChars} 字符` : "",
        q.note ? `备注：${q.note}` : "",
        "",
        "说明：统计由本插件在成功翻译请求后本地累计；缓存命中不计费。服务商后台可能存在其他客户端用量、套餐变化或计费规则差异。",
      ].filter(Boolean).join("\n");
      try { Services.prompt.alert(Zotero.getMainWindow?.() || null, "全文翻译 · 引擎额度", lines); }
      catch (_) { this.notify("引擎额度", lines); }
    },

    closeTranslationPanel(state, { remove = true } = {}) {
      if (!state) return;
      state.panelDismissed = true;
      const doc = this.refreshStateDocument(state);
      let panel = null;
      try { panel = doc?.getElementById(PANEL_ID) || (this.safeDOMConnected(state.panel) ? state.panel : null); } catch (_) {}
      // Restore the reader split before detaching the panel.
      try { this.applyPanelLayout(state, false); } catch (_) {}
      try {
        if (panel) {
          if (remove) panel.remove();
          else panel.hidden = true;
        }
      } catch (_) {}
      state.panel = null;
      state.body = null;
      state.status = null;
      state.progress = null;
    },

    dismissTaskHUD(state) {
      if (!state) return;
      state.taskHUDDismissed = true;
      const doc = this.refreshStateDocument(state);
      let hud = null;
      try { hud = doc?.getElementById(TASK_HUD_ID) || (this.safeDOMConnected(state.taskHUD) ? state.taskHUD : null); } catch (_) {}
      try { hud?.remove(); } catch (_) {}
      state.taskHUD = null;
    },

    bindPanelControls(state, panel) {
      if (!panel) return;
      try {
        const close = panel.querySelector(".zft-close");
        if (close) close.onclick = (ev) => {
          try { ev.preventDefault(); ev.stopPropagation(); } catch (_) {}
          this.closeTranslationPanel(state, { remove: true });
        };
        const note = panel.querySelector('[data-action="note"]');
        if (note) note.onclick = () => this.exportNote(state.reader).catch((e) => this.reportError(e, state.reader));
        const retranslate = panel.querySelector('[data-action="retranslate"]');
        if (retranslate) retranslate.onclick = () => this.retranslateCurrentReader(state.reader).catch((e) => this.reportError(e, state.reader));
        panel.querySelectorAll(".zft-mode").forEach((button) => {
          button.onclick = () => this.setReaderMode(state, button.dataset.mode);
        });
      } catch (_) {}
    },

    ensurePanel(state, title = "全文翻译") {
      const doc = this.refreshStateDocument(state);
      state.panelDismissed = false;
      if (!doc) throw new Error("无法访问 Zotero 阅读器文档。请关闭并重新打开 PDF 后再试。");
      this.injectReaderStyle(doc);
      let panel = null;
      try { panel = doc.getElementById(PANEL_ID); } catch (_) {}
      if (!panel) {
        panel = doc.createElement("section");
        panel.id = PANEL_ID;
        panel.dataset.layout = this.pref("layout", "vertical");
        panel.innerHTML = `
          <div class="zft-head">
            <div class="zft-title"></div>
            <div class="zft-status">就绪</div>
            <div class="zft-modes" title="阅读模式">
              <button class="zft-mode" data-mode="source">原</button>
              <button class="zft-mode" data-mode="translation">译</button>
              <button class="zft-mode" data-mode="bilingual">对照</button>
            </div>
            <button class="zft-action" data-action="retranslate" title="忽略历史结果并重新翻译">重译</button>
            <button class="zft-action" data-action="note" title="保存为 Zotero 笔记">笔记</button>
            <button type="button" class="zft-close" title="关闭翻译面板">×</button>
          </div>
          <div class="zft-progress"><i></i></div>
          <div class="zft-body"></div>`;
        doc.body.append(panel);
      }
      this.bindPanelControls(state, panel);
      panel.hidden = false;
      panel.dataset.layout = this.pref("layout", "vertical");
      const titleNode = panel.querySelector(".zft-title");
      if (titleNode) titleNode.textContent = title;
      state.panel = panel;
      state.body = panel.querySelector(".zft-body");
      state.status = panel.querySelector(".zft-status");
      state.progress = panel.querySelector(".zft-progress > i");
      this.applyPanelLayout(state, true);
      this.updateModeButtons(state);
      return panel;
    },

    toggleReaderPanel(reader) {
      const itemID = this.getAttachmentID(reader);
      const state = itemID ? this.states.get(itemID) : null;
      if (!state) return;
      const doc = this.refreshStateDocument(state);
      const panel = doc?.getElementById?.(PANEL_ID);
      if (!panel) {
        this.ensurePanel(state);
        return;
      }
      if (panel.hidden) {
        panel.hidden = false;
        state.panel = panel;
        state.body = panel.querySelector(".zft-body");
        state.status = panel.querySelector(".zft-status");
        state.progress = panel.querySelector(".zft-progress > i");
        this.bindPanelControls(state, panel);
        this.applyPanelLayout(state, true);
      } else {
        this.closeTranslationPanel(state, { remove: true });
      }
    },

    applyPanelLayout(state, visible) {
      const doc = this.refreshStateDocument(state);
      let split = null;
      try { split = doc?.getElementById("split-view"); } catch (_) {}
      if (!split) return;
      if (!state.originalSplitStyle) {
        try { state.originalSplitStyle = { width: split.style.width, height: split.style.height }; }
        catch (_) { state.originalSplitStyle = { width: "", height: "" }; }
      }
      try {
        if (!visible) {
          const originalWidth = asString(state.originalSplitStyle?.width || "");
          const originalHeight = asString(state.originalSplitStyle?.height || "");
          // Older builds could leave our 56%/58% inline size behind after the panel DOM vanished.
          split.style.width = originalWidth === "56%" ? "" : originalWidth;
          split.style.height = originalHeight === "58%" ? "" : originalHeight;
          return;
        }
        const layout = asString(this.pref("layout", "vertical"));
        if (layout === "horizontal") {
          split.style.width = state.originalSplitStyle.width || "";
          split.style.height = "58%";
        } else {
          split.style.height = state.originalSplitStyle.height || "";
          split.style.width = "56%";
        }
      } catch (_) {}
    },

    async retranslateCurrentReader(reader) {
      if (!reader) throw new Error("无法识别当前 Zotero 阅读器。");
      if (this.pref("cloud.thinClient", true)) {
        return this.exportLayoutPDF(reader, "zft-cloud", { forceRetranslate: true });
      }
      const doc = this.getReaderDoc(reader);
      return this.translateReader(reader, doc, { scope: "all", force: true });
    },

    fileURIForPath(path) {
      try {
        const { FileUtils: ImportedFileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
        return Services.io.newFileURI(new ImportedFileUtils.File(path)).spec;
      } catch (e) {
        throw new Error(`无法生成译文 PDF 地址：${this.safeErrorMessage(e)}`);
      }
    },

    closeCloudPDFPanel(state) {
      if (!state) return;
      const doc = this.refreshStateDocument(state);
      let panel = null;
      try { panel = doc?.getElementById?.(CLOUD_PANEL_ID) || (this.safeDOMConnected(state.cloudPanel) ? state.cloudPanel : null); } catch (_) {}
      try { panel?.remove(); } catch (_) {}
      state.cloudPanel = null;
      state.cloudPanelFrame = null;
      state.cloudPanelTranslatedItemID = null;
      try { this.applyPanelLayout(state, false); } catch (_) {}
    },

    async openCloudPDFPanel(reader, sourceItem, translatedItem) {
      if (!reader || !sourceItem?.id || !translatedItem?.id) throw new Error("原文或译文附件不可用。");
      const translatedPath = await translatedItem.getFilePathAsync();
      if (!translatedPath || !(await IOUtils.exists(translatedPath))) throw new Error("本地译文 PDF 不存在。");
      const state = this.getState(reader, this.getReaderDoc(reader));
      const doc = this.refreshStateDocument(state);
      if (!doc) throw new Error("无法访问 Zotero 阅读器文档。");
      this.injectReaderStyle(doc);
      try { this.stopNativeCompareSession(state); } catch (_) {}
      try { this.closeTranslationPanel(state, { remove: true }); } catch (_) {}
      let panel = doc.getElementById(CLOUD_PANEL_ID);
      if (!panel) {
        panel = doc.createElement("aside");
        panel.id = CLOUD_PANEL_ID;
        panel.innerHTML = `
          <div class="zft-cloud-head">
            <div class="zft-cloud-title">右侧译文</div>
            <button type="button" class="zft-cloud-action" data-action="retranslate" title="忽略历史结果并重新提交云端翻译">重译</button>
            <button type="button" class="zft-cloud-action" data-action="tab" title="在 Zotero 标签页中打开译文">标签页</button>
            <button type="button" class="zft-cloud-action" data-action="close" title="关闭右侧译文">×</button>
          </div>
          <div class="zft-cloud-body">
            <iframe id="${CLOUD_FRAME_ID}" title="译文 PDF"></iframe>
            <div class="zft-cloud-fallback">译文 PDF 无法在右侧内嵌显示。请点击上方“标签页”打开；也可以点击“重译”重新生成译文。</div>
          </div>`;
        doc.body.append(panel);
      }
      panel.dataset.loadError = "false";
      const titleNode = panel.querySelector(".zft-cloud-title");
      if (titleNode) titleNode.textContent = `译文 · ${asString(translatedItem.getField?.("title") || "PDF")}`;
      const frame = panel.querySelector(`#${CLOUD_FRAME_ID}`);
      const fileURI = this.fileURIForPath(translatedPath);
      const close = panel.querySelector('[data-action="close"]');
      if (close) close.onclick = () => this.closeCloudPDFPanel(state);
      const tab = panel.querySelector('[data-action="tab"]');
      if (tab) tab.onclick = () => Zotero.Reader.open(translatedItem.id);
      const retranslate = panel.querySelector('[data-action="retranslate"]');
      if (retranslate) retranslate.onclick = () => this.exportLayoutPDF(reader, "zft-cloud", { forceRetranslate: true }).catch((e) => this.reportError(e, reader));
      if (frame) {
        frame.onerror = () => { try { panel.dataset.loadError = "true"; } catch (_) {} };
        try {
          frame.src = fileURI;
          // A failed privileged PDF embed does not always fire `error`; detect a completely empty document.
          setTimeout(() => {
            try {
              if (!this.safeDOMConnected(frame)) return;
              const href = asString(frame.contentWindow?.location?.href || "");
              if (!href || href === "about:blank") panel.dataset.loadError = "true";
            } catch (_) {
              // Cross-principal access means the embed navigated away from about:blank, which is acceptable.
            }
          }, 1800);
        } catch (_) {
          panel.dataset.loadError = "true";
        }
      }
      panel.hidden = false;
      state.cloudPanel = panel;
      state.cloudPanelFrame = frame || null;
      state.cloudPanelTranslatedItemID = translatedItem.id;
      this.applyPanelLayout(state, true);
      return panel;
    },

    async secondaryPDFReady(view, timeoutMs = 4500) {
      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        try {
          const win = view?._iframeWindow || view?._iframe?.contentWindow;
          const app = win?.PDFViewerApplication;
          if (app?.pdfDocument && Number(app.pdfDocument.numPages) > 0) return true;
          if (app?.pdfLoadingTask?.promise) {
            try {
              await Promise.race([app.pdfLoadingTask.promise, Zotero.Promise.delay(250)]);
              if (app?.pdfDocument && Number(app.pdfDocument.numPages) > 0) return true;
            } catch (_) {}
          }
        } catch (_) {}
        await Zotero.Promise.delay(120);
      }
      return false;
    },

    setStatus(state, text, progress) {
      state.taskStatusText = asString(text, "处理中…");
      if (typeof progress === "number") state.taskProgress = Math.max(0, Math.min(100, progress));
      this.refreshStateDocument(state);
      try {
        if (this.safeDOMConnected(state.status)) state.status.textContent = state.taskStatusText;
        else state.status = null;
      } catch (_) { state.status = null; }
      try {
        if (this.safeDOMConnected(state.progress) && typeof progress === "number") state.progress.style.width = `${state.taskProgress}%`;
        else if (!this.safeDOMConnected(state.progress)) state.progress = null;
      } catch (_) { state.progress = null; }
      try { if (state.running || state.taskHUD) this.updateTaskHUD(state); } catch (_) {}
      try { this.updateToolbarTaskState(state); } catch (_) {}
      try { state.parserTestCallback?.({ text: state.taskStatusText, progress: state.taskProgress }); } catch (_) {}
    },

    ensureTaskHUD(state) {
      if (state?.taskHUDDismissed) return null;
      const doc = this.refreshStateDocument(state);
      if (!doc) return null;
      this.injectReaderStyle(doc);
      let hud = null;
      try { hud = doc.getElementById(TASK_HUD_ID); } catch (_) {}
      if (!hud) {
        hud = doc.createElement("aside");
        hud.id = TASK_HUD_ID;
        hud.innerHTML = `
          <div class="zft-task-main">
            <div class="zft-task-spinner"></div>
            <div class="zft-task-copy">
              <div class="zft-task-title">全文翻译</div>
              <div class="zft-task-status">准备中…</div>
            </div>
            <div class="zft-task-percent">0%</div>
            <button type="button" class="zft-task-detail" hidden>详情</button>
            <button type="button" class="zft-task-cancel">取消</button>
            <button type="button" class="zft-task-dismiss" title="关闭任务卡">×</button>
          </div>
          <div class="zft-task-progress"><i></i></div>`;
        doc.body.append(hud);
      }
      try {
        const cancel = hud.querySelector(".zft-task-cancel");
        if (cancel) cancel.onclick = () => this.cancelTranslation(state);
        const detail = hud.querySelector(".zft-task-detail");
        if (detail) detail.onclick = () => this.showTaskDiagnostic(state);
        const dismiss = hud.querySelector(".zft-task-dismiss");
        if (dismiss) dismiss.onclick = () => this.dismissTaskHUD(state);
      } catch (_) {}
      try { hud.hidden = false; } catch (_) {}
      state.taskHUD = hud;
      return hud;
    },

    updateTaskHUD(state, explicitState = null) {
      const hud = this.ensureTaskHUD(state);
      if (!hud) return;
      const pct = Math.round(Number(state.taskProgress) || 0);
      const elapsed = state.taskStartedAt ? Math.max(0, Math.floor((Date.now() - state.taskStartedAt) / 1000)) : 0;
      const elapsedText = elapsed >= 60 ? `${Math.floor(elapsed / 60)}分${elapsed % 60}秒` : `${elapsed}秒`;
      const status = state.taskStatusText || "处理中…";
      try {
        hud.dataset.state = explicitState || (state.running ? "running" : state.completed ? "done" : "idle");
        const title = hud.querySelector(".zft-task-title");
        const statusNode = hud.querySelector(".zft-task-status");
        const pctNode = hud.querySelector(".zft-task-percent");
        const bar = hud.querySelector(".zft-task-progress > i");
        const cancel = hud.querySelector(".zft-task-cancel");
        const detail = hud.querySelector(".zft-task-detail");
        if (title) title.textContent = state.taskScope === "current-page" ? "当前页翻译" : "全文翻译";
        if (statusNode) statusNode.textContent = state.running ? `${status} · ${elapsedText}` : status;
        if (pctNode) pctNode.textContent = `${pct}%`;
        if (bar) bar.style.width = `${pct}%`;
        if (cancel) {
          cancel.hidden = !state.running;
          cancel.textContent = state.cancelRequested ? "取消中…" : "取消";
          cancel.disabled = !!state.cancelRequested;
        }
        if (detail) detail.hidden = !state.taskErrorReport;
      } catch (_) {
        state.taskHUD = null;
      }
    },

    updateToolbarTaskState(state) {
      const pct = Math.round(Number(state?.taskProgress) || 0);
      for (const button of [...(state?.toolbarButtons || [])]) {
        try {
          if (!button || !button.isConnected) {
            state.toolbarButtons.delete(button);
            continue;
          }
          if (state.running) {
            button.dataset.running = "true";
            button.textContent = pct > 0 ? `译·${pct}%` : "译…";
            button.title = `${state.taskStatusText || "正在翻译"} · ${pct}%（点击查看菜单）`;
          } else {
            delete button.dataset.running;
            button.textContent = state.completed ? "译✓" : "译";
            button.title = state.completed ? "翻译已完成（点击打开菜单）" : "全文翻译菜单（Shift+T）";
          }
        } catch (_) {
          // Firefox/XUL marks DOM nodes from destroyed Reader documents as dead objects.
          try { state.toolbarButtons.delete(button); } catch (_) {}
        }
      }
    },

    startTask(state, scope = "all") {
      state.running = true;
      state.taskHUDDismissed = false;
      state.cancelRequested = false;
      state.taskScope = scope;
      state.targetPageIndex = scope === "current-page" ? this.getCurrentPageIndex(state) : null;
      state.taskStartedAt = Date.now();
      state.taskProgress = 0;
      state.taskErrorReport = "";
      state.taskStatusText = scope === "current-page" ? "准备翻译当前页…" : "准备翻译全文…";
      this.ensureTaskHUD(state);
      if (state.taskTimer) clearInterval(state.taskTimer);
      state.taskTimer = setInterval(() => {
        try { if (state.running) this.updateTaskHUD(state); } catch (_) {}
      }, 1000);
      this.setStatus(state, state.taskStatusText, 1);
    },

    finishTask(state, kind = "done", message = "翻译完成") {
      state.running = false;
      if (state.taskTimer) clearInterval(state.taskTimer);
      state.taskTimer = null;
      if (kind === "done") state.taskProgress = 100;
      state.taskStatusText = message;
      this.updateTaskHUD(state, kind);
      this.updateToolbarTaskState(state);
      if (kind === "done") {
        setTimeout(() => {
          try { if (this.safeDOMConnected(state.taskHUD) && !state.running) state.taskHUD.hidden = true; }
          catch (_) { state.taskHUD = null; }
        }, 3200);
      }
    },

    sanitizeDiagnosticURL(url) {
      try {
        const u = new URL(asString(url));
        return `${u.origin}${u.pathname}`;
      } catch (_) {
        return asString(url).split("?")[0];
      }
    },

    buildNetworkDiagnostic(stage, error, url = "", extra = {}) {
      const snap = this.snapshotNetworkError(error);
      const status = snap.status || snap.responseStatus || 0;
      let responseText = asString(snap.responseText || "").trim();
      if (responseText.length > 1600) responseText = responseText.slice(0, 1600) + "…";
      const lines = [
        `阶段：${stage}`,
        url ? `请求：${this.sanitizeDiagnosticURL(url)}` : "",
        status ? `HTTP 状态：${status}` : "HTTP 状态：未获得响应",
        `错误类型：${snap.name || "Error"}`,
        `错误信息：${snap.message || "未知错误"}`,
      ].filter(Boolean);
      if (extra.requestContentType !== undefined) lines.push(`请求 Content-Type：${extra.requestContentType || "<空>"}`);
      if (extra.apiCode !== undefined && extra.apiCode !== null) lines.push(`MinerU code：${extra.apiCode}`);
      if (extra.apiMessage) lines.push(`MinerU msg：${extra.apiMessage}`);
      if (extra.traceID) lines.push(`MinerU trace_id：${extra.traceID}`);
      if (responseText) lines.push(`响应：${responseText}`);
      if (extra.hint) lines.push(`建议：${extra.hint}`);
      return lines.join("\n");
    },

    makeNetworkError(stage, error, url = "", extra = {}) {
      // Build the report immediately while any XPCOM/XHR object is still alive, then keep only primitives.
      const snap = this.snapshotNetworkError(error);
      const status = snap.status || snap.responseStatus || 0;
      const baseMessage = status
        ? `${stage}失败：HTTP ${status}`
        : `${stage}失败：${snap.message || "网络请求失败"}`;
      const wrapped = new Error(baseMessage);
      wrapped.name = "ZFTNetworkError";
      wrapped.zftStage = stage;
      wrapped.zftReport = this.buildNetworkDiagnostic(stage, snap, url, extra);
      // Do not retain the original XPCOM/XHR object as cause: it may become a dead object later.
      wrapped.zftCauseText = snap.message;
      return wrapped;
    },

    showTaskDiagnostic(state) {
      const report = asString(state?.taskErrorReport || "暂无诊断信息。");
      try {
        const win = Zotero.getMainWindow?.() || null;
        Services.prompt.alert(win, "全文翻译 · 诊断信息", report);
      } catch (_) {
        this.notify("全文翻译诊断", report);
      }
    },

    cancelTranslation(state) {
      if (!state?.running || state.cancelRequested) return;
      state.cancelRequested = true;
      state.taskStatusText = "正在取消…";
      this.updateTaskHUD(state);
      this.updateToolbarTaskState(state);
      try { state.currentAbort?.(); } catch (_) {}
      state.currentAbort = null;
      try { state.currentProcess?.kill?.(); } catch (_) {}
      if (state.remoteLayoutJobID) {
        Promise.resolve(this.cancelCloudPDF2ZHJob(state)).catch((e) => this.log("remote cancel failed", this.safeErrorMessage(e)));
      }
    },

    throwIfCancelled(state) {
      if (state?.cancelRequested) {
        const error = new Error("翻译任务已取消");
        error.name = "ZFTCancelled";
        throw error;
      }
    },

    getCurrentPageIndex(stateOrReader) {
      // Accept both a normal plugin state ({ reader, ... }) and a raw Zotero
      // ReaderInstance. Side-by-side comparison owns ReaderInstances directly.
      const reader = stateOrReader?.reader || stateOrReader;
      const state = stateOrReader?.reader ? stateOrReader : { reader };
      try {
        const view = this.getPDFView(state);
        const app = view?._iframeWindow?.PDFViewerApplication || view?._iframe?.contentWindow?.PDFViewerApplication;
        const n = Number(app?.pdfViewer?.currentPageNumber);
        if (Number.isInteger(n) && n > 0) return n - 1;
      } catch (_) {}
      try {
        const n = Number(
          reader?._primaryView?._viewState?.pageIndex
          ?? reader?._internalReader?._primaryView?._viewState?.pageIndex
          ?? reader?._reader?._primaryView?._viewState?.pageIndex
        );
        if (Number.isInteger(n) && n >= 0) return n;
      } catch (_) {}
      return 0;
    },

    async translateReader(reader, doc, options = {}) {
      const state = this.getState(reader, doc);
      if (state.running) {
        this.ensureTaskHUD(state);
        return;
      }
      const scope = options.scope === "current-page" ? "current-page" : "all";
      // A new explicit translation request should be allowed after a previous completed run.
      if (state.completed && state.taskScope === "all" && scope === "all" && !options.force) {
        this.ensureTaskHUD(state);
        state.taskStatusText = "已有翻译结果，可在“译”菜单中切换阅读模式";
        state.taskProgress = 100;
        this.updateTaskHUD(state, "done");
        setTimeout(() => {
          try { if (this.safeDOMConnected(state.taskHUD) && !state.running) state.taskHUD.hidden = true; }
          catch (_) { state.taskHUD = null; }
        }, 2600);
        return;
      }
      if (scope === "current-page" || options.force) {
        state.completed = false;
        state.translationByIndex.clear();
        try { this.removeTranslationOverlays(state); } catch (_) {}
      }
      try {
        this.refreshStateDocument(state, doc);
        this.startTask(state, scope);
        const itemID = this.getAttachmentID(reader);
        if (!itemID) throw new Error("无法识别当前阅读器中的附件。");
        const item = Zotero.Items.get(itemID);
        if (!item?.isAttachment?.()) throw new Error("当前阅读器项目不是附件。");
        const path = await item.getFilePathAsync();
        if (!path || !/\.pdf$/i.test(path)) throw new Error("当前附件不是可访问的 PDF 文件。");
        const parent = item.parentID ? Zotero.Items.get(item.parentID) : null;
        const title = parent?.getField("title") || item.getField("title") || basename(path);
        this.ensurePanel(state, title);
        state.body.replaceChildren();
        this.setStatus(state, "正在解析 PDF…", 3);

        const parsed = await this.parseDocument(item, path, state);
        this.throwIfCancelled(state);
        if (Array.isArray(parsed?.layoutBlocks) && parsed.layoutBlocks.length) {
          parsed.layoutBlocks = this.annotateSemanticRoles(parsed.layoutBlocks);
        }
        state.layoutBlocks = Array.isArray(parsed?.layoutBlocks) ? parsed.layoutBlocks.slice() : [];
        let segments = this.prepareSegments(parsed, itemID);
        if (scope === "current-page") {
          const pageIndex = Number.isInteger(state.targetPageIndex) ? state.targetPageIndex : this.getCurrentPageIndex(state);
          segments = segments.filter((seg) => seg.pageIndex === pageIndex).map((seg, index) => ({ ...seg, index }));
          this.setStatus(state, `当前第 ${pageIndex + 1} 页 · ${segments.length} 段`, 7);
        }
        if (!segments.length) throw new Error(scope === "current-page" ? "当前页没有可翻译文本。" : "未能从 PDF 中提取可翻译文本。");
        state.segments = segments;
        this.enrichSegmentLayoutMetadata(state);
        state.layoutAvailable = segments.some((seg) => Array.isArray(seg.bbox) && seg.bbox.length === 4);
        state.cacheKey = this.makeCacheKey(item, segments);

        const cached = scope === "all" ? await this.loadTranslationCache(state) : null;
        if (cached?.translations?.length === segments.length) {
          cached.translations.forEach((t, i) => state.translationByIndex.set(i, t));
          this.renderAllSegments(state);
          state.completed = true;
          this.setStatus(state, `已从缓存加载 ${segments.length} 段`, 100);
          this.startPageSync(state);
          this.activatePreferredReaderMode(state);
          this.finishTask(state, "done", `已从缓存加载 ${segments.length} 段`);
          return;
        }

        this.quotaPreflight(state, segments);
        this.renderPlaceholders(state);
        const quotaText = this.pref("quota.enabled", true) ? ` · ${this.quotaSummaryText()}` : "";
        this.setStatus(state, `待翻译 ${segments.length} 段${quotaText}`, 8);
        await this.translateSegments(state, item);
        this.throwIfCancelled(state);
        state.completed = true;
        if (scope === "all") await this.saveTranslationCache(state);
        this.setStatus(state, `完成 · ${segments.length} 段`, 100);
        this.startPageSync(state);
        this.activatePreferredReaderMode(state);
        this.finishTask(state, "done", `翻译完成 · ${segments.length} 段`);
      } catch (e) {
        const errorName = asString(this.safeRead(e, "name", "Error"));
        const errorMessage = this.safeErrorMessage(e) || "未知错误";
        if (errorName === "ZFTCancelled") {
          try { this.finishTask(state, "idle", "翻译已取消"); }
          catch (_) { state.running = false; }
          return;
        }
        const zftReport = asString(this.safeRead(e, "zftReport", "")).trim();
        const zftStage = asString(this.safeRead(e, "zftStage", "")).trim();
        state.taskErrorReport = zftReport || [
          `阶段：${zftStage || state.taskStatusText || "翻译"}`,
          `错误类型：${errorName}`,
          `错误信息：${errorMessage}`,
        ].join("\n");
        try { this.finishTask(state, "error", zftStage ? `${zftStage}失败 · 点击“详情”` : `失败：${errorMessage}`); }
        catch (_) {
          state.running = false;
          if (state.taskTimer) clearInterval(state.taskTimer);
          state.taskTimer = null;
        }
        throw e;
      } finally {
        state.currentProcess = null;
      }
    },

    async parseDocument(item, path, state) {
      return this.parseDocumentWithParser(asString(this.pref("parser", "zotero")), item, path, state);
    },

    async parseDocumentWithParser(parser, item, path, state) {
      if (parser === "mineru-api") return this.parseWithMinerUAPI(item, path, state);
      if (parser === "mineru-local") return this.parseWithMinerULocal(item, path, state);
      if (parser === "doc2x-md") return this.parseWithDoc2X(item, path, state);
      return this.parseWithZotero(item, path, state);
    },

    async parseWithZotero(item, path, state) {
      const result = await Zotero.PDFWorker.getFullText(item.id);
      if (!result?.text) throw new Error("Zotero PDFWorker 未返回文本。扫描版 PDF 建议改用 MinerU。 ");
      const pages = this.pageTextsFromWorker(result.text, result.pageChars);
      return {
        source: "zotero",
        pages: pages.map((text, pageIndex) => ({ pageIndex, text })),
        markdown: null,
      };
    },

    pageTextsFromWorker(text, pageChars) {
      const raw = asString(text);
      const arr = Array.isArray(pageChars) ? pageChars.map(Number).filter((n) => Number.isFinite(n) && n >= 0) : [];
      if (!arr.length) return [raw];
      const sum = arr.reduce((a, b) => a + b, 0);
      const monotonic = arr.every((v, i) => i === 0 || v >= arr[i - 1]);
      const looksCumulative = monotonic && arr[arr.length - 1] <= raw.length * 1.05 && sum > raw.length * 1.4;
      const pages = [];
      if (looksCumulative) {
        let start = 0;
        for (const end of arr) {
          pages.push(raw.slice(start, Math.min(raw.length, end)));
          start = end;
        }
        if (start < raw.length) pages.push(raw.slice(start));
      } else {
        let cursor = 0;
        for (const len of arr) {
          pages.push(raw.slice(cursor, Math.min(raw.length, cursor + len)));
          cursor += len;
        }
        if (cursor < raw.length) pages.push(raw.slice(cursor));
      }
      return pages.length ? pages : [raw];
    },

    async parseWithMinerULocal(item, path, state) {
      const command = asString(this.pref("mineru.command", "mineru"));
      const temp = await this.makeTempDir(`zft-mineru-${item.key || item.id}`);
      const args = ["-p", path, "-o", temp];
      const extra = splitArgs(this.pref("mineru.extraArgs", ""));
      args.push(...extra);
      this.setStatus(state, "MinerU 本地解析中…", 5);
      await this.runProcess(command, args, state);
      const contentPath = await this.findFileRecursive(temp, (p) => /_content_list\.json$/i.test(p));
      const contentV2Path = contentPath ? null : await this.findFileRecursive(temp, (p) => /_content_list_v2\.json$/i.test(p));
      const mdPath = await this.findFileRecursive(temp, (p) => /(?:^|[\\/])full\.md$/i.test(p) || /\.md$/i.test(p));
      const md = mdPath ? await this.readUTF8(mdPath) : "";
      const layoutBlocks = await this.readMinerULayout(contentPath || contentV2Path, !!contentV2Path);
      if (!md && !layoutBlocks.length) throw new Error("MinerU 已运行，但未找到 Markdown 或 content_list JSON 输出。");
      return this.makeMinerUParsed("mineru-local", md, layoutBlocks);
    },

    async parseWithMinerUAPI(item, path, state) {
      const token = asString(this.pref("mineru.token", "")).trim();
      if (!token) throw new Error("请先在设置中填写 MinerU Token。");
      const base = asString(this.pref("mineru.baseURL", "https://mineru.net")).replace(/\/$/, "");
      const filename = basename(path);
      const dataID = `zft-${item.key || item.id}-${Date.now()}`;
      const requestBody = {
        files: [{ name: filename, data_id: dataID, is_ocr: !!this.pref("mineru.ocr", false) }],
        model_version: asString(this.pref("mineru.model", "vlm")),
        enable_formula: !!this.pref("mineru.formula", true),
        enable_table: !!this.pref("mineru.table", true),
      };

      const applyURL = `${base}/api/v4/file-urls/batch`;
      this.setStatus(state, "MinerU：申请上传地址…", 4);
      let applied;
      try {
        applied = await this.httpJSON("POST", applyURL, requestBody, {
          Authorization: `Bearer ${token}`,
        }, state);
      } catch (e) {
        if (state?.cancelRequested) this.throwIfCancelled(state);
        throw this.makeNetworkError("MinerU：申请上传地址", e, applyURL, {
          hint: "检查 MinerU Base URL、网络连接和 Token。主 API 请求需要 Authorization: Bearer <token> 与 application/json。",
        });
      }
      if (applied?.code !== 0) {
        const e = new Error(`MinerU 返回 code=${applied?.code}：${applied?.msg || "未知错误"}`);
        e.zftStage = "MinerU：申请上传地址";
        e.zftReport = this.buildNetworkDiagnostic(e.zftStage, e, applyURL, {
          apiCode: applied?.code,
          apiMessage: applied?.msg,
          traceID: applied?.trace_id,
          hint: "如果是鉴权错误，请在 MinerU API 管理页重新确认 Token 是否已启用。",
        });
        throw e;
      }
      const uploadURL = applied?.data?.file_urls?.[0];
      const batchID = applied?.data?.batch_id;
      if (!uploadURL || !batchID) {
        const e = new Error("MinerU 未返回上传 URL 或 batch_id。");
        e.zftStage = "MinerU：申请上传地址";
        e.zftReport = this.buildNetworkDiagnostic(e.zftStage, e, applyURL, {
          apiCode: applied?.code,
          apiMessage: applied?.msg,
          traceID: applied?.trace_id,
        });
        throw e;
      }

      // MinerU 的预签名 OSS PUT 明确要求不要发送 Content-Type。
      // 不能使用 Zotero.HTTP.request()：Zotero 当前实现会在 body 存在且
      // Content-Type 未设置时自动补 application/x-www-form-urlencoded，导致 OSS 签名不匹配。
      // 这里直接使用 Zotero 特权环境中的原生 XMLHttpRequest，并发送 Uint8Array。
      this.setStatus(state, "MinerU：上传 PDF…", 8);
      try {
        await this.putPresignedFileWithoutContentType(uploadURL, path, 180000, state);
      } catch (e) {
        if (state?.cancelRequested) this.throwIfCancelled(state);
        throw this.makeNetworkError("MinerU：上传 PDF", e, uploadURL, {
          traceID: applied?.trace_id,
          requestContentType: "<未设置>",
          hint: "MinerU 预签名 OSS PUT 必须保持 Content-Type 为空。若仍为 403/SignatureDoesNotMatch，请点击详情确认 StringToSign 的 Content-Type 行是否为空。",
        });
      }

      let resultURL = "";
      const statusURL = `${base}/api/v4/extract-results/batch/${encodeURIComponent(batchID)}`;
      for (let i = 0; i < 180; i++) {
        await this.cancellableDelay(state, i < 5 ? 1200 : 2500);
        let status;
        try {
          status = await this.httpJSON("GET", statusURL, null, {
            Authorization: `Bearer ${token}`,
          }, state);
        } catch (e) {
          if (state?.cancelRequested) this.throwIfCancelled(state);
          throw this.makeNetworkError("MinerU：查询解析进度", e, statusURL, {
            traceID: applied?.trace_id,
            hint: "上传已完成，但查询任务状态失败。检查网络连接或 MinerU 服务状态。",
          });
        }
        if (status?.code !== 0) {
          const e = new Error(`MinerU 返回 code=${status?.code}：${status?.msg || "未知错误"}`);
          e.zftStage = "MinerU：查询解析进度";
          e.zftReport = this.buildNetworkDiagnostic(e.zftStage, e, statusURL, {
            apiCode: status?.code,
            apiMessage: status?.msg,
            traceID: status?.trace_id || applied?.trace_id,
          });
          throw e;
        }
        const entry = status?.data?.extract_result?.[0];
        if (!entry) continue;
        if (entry.state === "failed") {
          const e = new Error(entry.err_msg || "MinerU 解析任务失败");
          e.zftStage = "MinerU：服务端解析";
          e.zftReport = [
            `阶段：${e.zftStage}`,
            `batch_id：${batchID}`,
            `状态：${entry.state}`,
            `错误信息：${entry.err_msg || "未知错误"}`,
            status?.trace_id ? `MinerU trace_id：${status.trace_id}` : "",
          ].filter(Boolean).join("\n");
          throw e;
        }
        if (entry.state === "done" && entry.full_zip_url) {
          resultURL = entry.full_zip_url;
          break;
        }
        const p = entry.extract_progress;
        if (p?.total_pages) {
          const pct = 10 + Math.round((p.extracted_pages / p.total_pages) * 25);
          this.setStatus(state, `MinerU：解析 ${p.extracted_pages}/${p.total_pages} 页`, pct);
        } else {
          this.setStatus(state, `MinerU：${entry.state || "排队"}…`, 12);
        }
      }
      if (!resultURL) {
        const e = new Error("MinerU 解析超时或未返回结果地址。");
        e.zftStage = "MinerU：等待解析结果";
        e.zftReport = `阶段：${e.zftStage}\nbatch_id：${batchID}\n错误信息：180 次轮询后仍未获得 full_zip_url。`;
        throw e;
      }

      this.setStatus(state, "MinerU：读取结构化结果…", 36);
      const temp = await this.makeTempDir(`zft-mineru-api-${item.key || item.id}`);
      const zipPath = PathUtils.join(temp, "result.zip");
      let resultResp;
      try {
        resultResp = await Zotero.HTTP.request("GET", resultURL, {
          responseType: "arraybuffer",
          anon: true,
          timeout: 180000,
          errorDelayMax: 0,
        });
      } catch (e) {
        throw this.makeNetworkError("MinerU：下载解析结果", e, resultURL, {
          hint: "任务已解析完成，但下载结果 ZIP 失败。可稍后重试；诊断中不会显示预签名 URL 的查询参数。",
        });
      }
      const resultBuffer = resultResp?.response;
      if (!resultBuffer) {
        const e = new Error("MinerU 结果下载响应为空。");
        e.zftStage = "MinerU：下载解析结果";
        e.zftReport = this.buildNetworkDiagnostic(e.zftStage, e, resultURL);
        throw e;
      }
      await IOUtils.write(zipPath, new Uint8Array(resultBuffer));
      const artifacts = await this.extractMinerUArtifactsFromZip(zipPath, temp);
      const md = artifacts.mdPath ? await this.readUTF8(artifacts.mdPath) : "";
      const layoutPath = artifacts.contentPath || artifacts.contentV2Path;
      const layoutBlocks = await this.readMinerULayout(layoutPath, !artifacts.contentPath && !!artifacts.contentV2Path);
      if (!md && !layoutBlocks.length) throw new Error("MinerU 结果中未找到可用的 Markdown 或 content_list JSON。");
      return this.makeMinerUParsed("mineru-api", md, layoutBlocks);
    },

    makeMinerUParsed(source, markdown, layoutBlocks) {
      if (layoutBlocks?.length) {
        const grouped = new Map();
        for (const block of layoutBlocks) {
          if (!grouped.has(block.pageIndex)) grouped.set(block.pageIndex, []);
          grouped.get(block.pageIndex).push(block.text || "");
        }
        const maxPage = Math.max(...Array.from(grouped.keys()), 0);
        const pages = [];
        for (let pageIndex = 0; pageIndex <= maxPage; pageIndex++) {
          pages.push({ pageIndex, text: (grouped.get(pageIndex) || []).filter(Boolean).join("\n\n") });
        }
        return { source, pages, markdown: markdown || null, layoutBlocks };
      }
      return { source, pages: [{ pageIndex: 0, text: markdown || "" }], markdown: markdown || null, layoutBlocks: [] };
    },

    async readMinerULayout(path, isV2 = false) {
      if (!path) return [];
      try {
        const data = JSON.parse(await this.readUTF8(path));
        return isV2 ? this.normalizeMinerUV2(data) : this.normalizeMinerUContentList(data);
      } catch (e) {
        this.log("MinerU layout parse failed", String(e));
        return [];
      }
    },

    normalizeMinerUContentList(data) {
      if (!Array.isArray(data)) return [];
      const out = [];
      for (let i = 0; i < data.length; i++) {
        const block = data[i] || {};
        const pageIndex = Number(block.page_idx);
        const bbox = this.normalizeMinerUBBox(block.bbox);
        if (!Number.isInteger(pageIndex) || !bbox) continue;
        const type = asString(block.type, "text").toLowerCase();
        let text = asString(block.text, "").trim();
        if (!text && Array.isArray(block.list_items)) text = block.list_items.map((x) => asString(x).trim()).filter(Boolean).join("\n");
        const subType = asString(block.sub_type, "").toLowerCase();
        const auxiliary = ["header", "footer", "page_number", "aside_text", "page_footnote"].includes(type);
        const protectedBlock = ["image", "table", "chart", "equation", "code"].includes(type);
        out.push({
          layoutIndex: i, pageIndex, bbox, type, subType, text,
          textLevel: Number(block.text_level) || 0,
          isReference: type === "list" && subType === "ref_text",
          auxiliary, protectedBlock,
        });
      }
      return out;
    },

    normalizeMinerUV2(data) {
      if (!Array.isArray(data)) return [];
      const out = [];
      let layoutIndex = 0;
      const pages = data.every(Array.isArray) ? data : (Array.isArray(data[0]?.content_list) ? data.map((x) => x.content_list || []) : []);
      for (let pageIndex = 0; pageIndex < pages.length; pageIndex++) {
        for (const block of pages[pageIndex] || []) {
          const bbox = this.normalizeMinerUBBox(block?.bbox);
          if (!bbox) continue;
          const type = asString(block?.type, "paragraph").toLowerCase();
          const text = this.extractMinerUV2Text(block?.content).trim();
          const auxiliary = /^(page_header|page_footer|page_number|page_aside_text|page_footnote)$/.test(type);
          const protectedBlock = /^(image|table|chart|equation|code|algorithm)/.test(type);
          out.push({
            layoutIndex: layoutIndex++, pageIndex, bbox, type, subType: "", text,
            textLevel: Number(block?.content?.level) || 0,
            isReference: type.includes("reference") || block?.content?.list_type === "ref_text",
            auxiliary, protectedBlock,
          });
        }
      }
      return out;
    },

    extractMinerUV2Text(value) {
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.map((x) => this.extractMinerUV2Text(x)).filter(Boolean).join(" ");
      if (!value || typeof value !== "object") return "";
      if (typeof value.content === "string") return value.content;
      const preferred = ["title_content", "paragraph_content", "header_content", "footer_content", "page_header_content", "page_footer_content", "page_footnote_content", "item_content"];
      const parts = [];
      for (const key of preferred) if (value[key] !== undefined) parts.push(this.extractMinerUV2Text(value[key]));
      if (Array.isArray(value.list_items)) parts.push(this.extractMinerUV2Text(value.list_items));
      return parts.filter(Boolean).join(" ");
    },

    normalizeMinerUBBox(bbox) {
      if (!Array.isArray(bbox) || bbox.length < 4) return null;
      let [x0, y0, x1, y1] = bbox.slice(0, 4).map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite)) return null;
      const max = Math.max(Math.abs(x0), Math.abs(y0), Math.abs(x1), Math.abs(y1));
      if (max <= 1.5) [x0, y0, x1, y1] = [x0, y0, x1, y1].map((v) => v * 1000);
      x0 = Math.max(0, Math.min(1000, x0));
      y0 = Math.max(0, Math.min(1000, y0));
      x1 = Math.max(0, Math.min(1000, x1));
      y1 = Math.max(0, Math.min(1000, y1));
      if (x1 <= x0 || y1 <= y0) return null;
      return [x0, y0, x1, y1];
    },

    async parseWithDoc2X(item, path, state) {
      const command = asString(this.pref("doc2x.command", "doc2x"));
      const temp = await this.makeTempDir(`zft-doc2x-${item.key || item.id}`);
      const lang = this.toDoc2XLanguage(this.pref("targetLanguage", "zh-CN"));
      const args = ["parse", path, "--to", "md", "--out", temp, "--overwrite"];
      this.setStatus(state, "Doc2X 结构化解析中…", 5);
      await this.runProcess(command, args, state);
      const mdPath = await this.findFileRecursive(temp, (p) => /\.md$/i.test(p));
      if (!mdPath) throw new Error("Doc2X 已运行，但未找到 Markdown 输出。");
      const md = await this.readUTF8(mdPath);
      return { source: "doc2x", pages: [{ pageIndex: 0, text: md }], markdown: md };
    },

    prepareSegments(parsed, itemID) {
      if (Array.isArray(parsed.layoutBlocks) && parsed.layoutBlocks.length) {
        return this.prepareLayoutSegments(parsed, itemID);
      }
      const mode = asString(this.pref("segmentMode", "paragraph"));
      const maxChars = Math.max(500, Number(this.pref("chunkChars", 3200)) || 3200);
      const ignoreRefs = !!this.pref("ignoreReferences", true);
      const ignoreSubtitles = !!this.pref("ignoreSubtitles", false);
      const segments = [];
      let stop = false;
      for (const page of parsed.pages || []) {
        if (stop) break;
        const normalized = asString(page.text).replace(/\r\n/g, "\n").replace(/\u0000/g, "");
        let pieces;
        if (parsed.markdown) {
          pieces = normalized.split(/\n{2,}/).map((x) => x.trim()).filter(Boolean);
        } else if (mode === "sentence") {
          pieces = normalized.split(/(?<=[.!?。！？])\s+(?=[A-Z0-9“"'（(])/).map((x) => x.trim()).filter(Boolean);
        } else {
          pieces = normalized.split(/\n\s*\n|(?<=\.)\s*\n(?=[A-Z])/).map((x) => x.trim()).filter(Boolean);
          if (pieces.length < 2) pieces = normalized.split(/\n+/).map((x) => x.trim()).filter(Boolean);
        }
        for (let text of pieces) {
          if (ignoreRefs && this.isReferenceHeading(text)) {
            stop = true;
            break;
          }
          if (ignoreSubtitles && this.looksLikeHeading(text)) continue;
          if (!this.shouldTranslateText(text)) continue;
          for (const chunk of this.chunkText(text, maxChars)) {
            segments.push({
              index: segments.length,
              pageIndex: Number(page.pageIndex) || 0,
              source: chunk,
              sourceType: parsed.source,
              itemID,
            });
          }
        }
      }
      return segments;
    },

    prepareLayoutSegments(parsed, itemID) {
      const maxChars = Math.max(500, Number(this.pref("chunkChars", 3200)) || 3200);
      const ignoreRefs = !!this.pref("ignoreReferences", true);
      const ignoreSubtitles = !!this.pref("ignoreSubtitles", false);
      const translateAuxiliary = !!this.pref("overlay.translateAuxiliary", false);
      const segments = [];
      let referencesStarted = false;
      for (const block of parsed.layoutBlocks) {
        if (block.protectedBlock) continue;
        if (block.auxiliary && !translateAuxiliary) continue;
        const text = asString(block.text).replace(/\u0000/g, "").trim();
        if (!text) continue;
        if (block.semanticRole === "author" && !!this.pref("structure.preserveAuthors", true)) continue;
        if (ignoreRefs && (block.isReference || this.isReferenceHeading(text))) {
          referencesStarted = true;
          continue;
        }
        if (ignoreRefs && referencesStarted) continue;
        if (ignoreSubtitles && (block.textLevel > 0 || this.looksLikeHeading(text))) continue;
        if (!this.shouldTranslateText(text)) continue;
        const chunks = this.chunkText(text, maxChars);
        const blockKey = `${block.pageIndex}:${block.layoutIndex}`;
        for (let chunkIndex = 0; chunkIndex < chunks.length; chunkIndex++) {
          segments.push({
            index: segments.length, pageIndex: block.pageIndex, source: chunks[chunkIndex], sourceType: parsed.source, itemID,
            bbox: block.bbox.slice(), layoutType: block.type, textLevel: block.textLevel || 0,
            semanticRole: block.semanticRole || "body", headingLevel: block.headingLevel || 0,
            layoutBlockKey: blockKey, layoutChunkIndex: chunkIndex, layoutChunkCount: chunks.length,
          });
        }
      }
      return segments;
    },

    semanticRoleLabel(role) {
      const labels = {
        "paper-title": "论文标题",
        author: "作者",
        affiliation: "作者单位",
        "abstract-heading": "摘要标题",
        "abstract-body": "摘要正文",
        "keywords-heading": "关键词标题",
        keywords: "关键词",
        "heading-1": "一级标题",
        "heading-2": "二级标题",
        "heading-3": "三级标题",
        body: "正文",
        "figure-caption": "图注",
        "table-caption": "表注",
        caption: "图表说明",
        footnote: "脚注",
        header: "页眉",
        footer: "页脚",
        "reference-heading": "参考文献标题",
        "reference-entry": "参考文献",
      };
      return labels[role] || role || "正文";
    },

    headingLevelFromText(text) {
      const t = asString(text).trim().replace(/^#+\s*/, "");
      const m = t.match(/^(\d+(?:\.\d+){0,5})(?:[\s、.．:：]|$)/);
      if (m) return Math.min(3, m[1].split(".").length);
      if (/^(chapter|section)\s+\d+/i.test(t)) return 1;
      return 0;
    },

    looksLikeAuthorLine(text) {
      const t = asString(text).trim();
      if (!t || t.length > 220 || /[.!?。！？]{2,}/.test(t)) return false;
      if (/\b(university|institute|department|school|laboratory|lab\b|college|hospital|academy|center|centre)\b/i.test(t)) return false;
      if (/@|https?:|doi\b/i.test(t)) return false;
      const commaNames = (t.match(/[,，;]/g) || []).length >= 1;
      const andNames = /\s(?:and|&)\s/i.test(t);
      const initials = /\b[A-Z][.\-]?\s*[A-Z]?[.\-]?\s*[A-Z][a-z]{1,}/.test(t);
      const cjkNames = /^[\u3400-\u9fff·•\s,，*†‡§1-9]+$/.test(t) && t.replace(/[^\u3400-\u9fff]/g, "").length >= 4;
      return commaNames || andNames || initials || cjkNames;
    },

    looksLikeAffiliation(text) {
      const t = asString(text).trim();
      return /\b(university|institute|department|school|laboratory|laboratories|college|hospital|academy|faculty|centre|center|key laboratory|research center)\b/i.test(t)
        || /(大学|学院|研究所|研究院|实验室|医院|中心|学部|系|课题组)/.test(t)
        || /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(t);
    },

    annotateSemanticRoles(layoutBlocks) {
      const blocks = (layoutBlocks || []).map((block) => ({ ...block }));
      if (!blocks.length || !this.pref("structure.semanticStyles", true)) return blocks;
      const ordered = blocks
        .filter((b) => Array.isArray(b.bbox))
        .slice()
        .sort((a, b) => a.pageIndex - b.pageIndex || a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0]);

      const firstPageText = ordered.filter((b) => b.pageIndex === 0 && !b.protectedBlock && !b.auxiliary && asString(b.text).trim());
      let paperTitle = null;
      let titleScore = -Infinity;
      for (const b of firstPageText) {
        const t = asString(b.text).trim();
        const type = asString(b.type).toLowerCase();
        const [x0, y0, x1, y1] = b.bbox;
        if (y0 > 360 || t.length < 5 || t.length > 420) continue;
        if (/^(abstract|摘要|keywords?|关键词)\b/i.test(t)) continue;
        const width = x1 - x0;
        const height = y1 - y0;
        let score = 0;
        if (/title/.test(type)) score += 100;
        if ((b.textLevel || 0) === 1) score += 45;
        score += Math.min(40, width / 18) + Math.min(28, height / 4) - y0 / 24;
        if (this.headingLevelFromText(t)) score -= 35;
        if (score > titleScore) { paperTitle = b; titleScore = score; }
      }

      let inAbstract = false;
      let inKeywords = false;
      let inReferences = false;
      let abstractPage = -1;
      let titleBottom = paperTitle?.bbox?.[3] ?? -1;
      for (const block of ordered) {
        const text = asString(block.text).replace(/\u0000/g, "").trim();
        const lower = text.toLowerCase().replace(/[:：.]$/, "");
        const type = asString(block.type).toLowerCase();
        let role = "body";
        let headingLevel = 0;

        if (block.protectedBlock) {
          block.semanticRole = "asset";
          continue;
        }
        if (block === paperTitle) {
          role = "paper-title";
          inAbstract = false;
        } else if (block.isReference || this.isReferenceHeading(text)) {
          role = this.isReferenceHeading(text) ? "reference-heading" : "reference-entry";
          inReferences = true;
          inAbstract = false;
          inKeywords = false;
        } else if (inReferences) {
          role = "reference-entry";
        } else if (/^(abstract|摘要)$/.test(lower)) {
          role = "abstract-heading";
          inAbstract = true;
          abstractPage = block.pageIndex;
          inKeywords = false;
        } else if (/^(abstract|摘要)\s*[:：—–-]\s*\S/i.test(text)) {
          role = "abstract-body";
          inAbstract = true;
          abstractPage = block.pageIndex;
          inKeywords = false;
        } else if (/^(keywords?|key words|关键词)$/.test(lower)) {
          role = "keywords-heading";
          inKeywords = true;
          inAbstract = false;
        } else if (/^(keywords?|key words|关键词)\s*[:：—–-]\s*\S/i.test(text)) {
          role = "keywords";
          inKeywords = false;
          inAbstract = false;
        } else if (block.auxiliary) {
          if (/footnote/.test(type)) role = "footnote";
          else if (/header/.test(type)) role = "header";
          else role = "footer";
        } else if (/author/.test(type)) {
          role = "author";
        } else if (/affiliation|institution|organization/.test(type)) {
          role = "affiliation";
        } else if (this.isCaptionLikeText(text)) {
          role = /^(table|表)\b/i.test(text) ? "table-caption" : (/^(figure|fig\.?|图)\b/i.test(text) ? "figure-caption" : "caption");
        } else {
          const explicitLevel = Math.max(0, Number(block.textLevel) || 0);
          const numericLevel = this.headingLevelFromText(text);
          const titleLike = /title|heading|section/.test(type) || explicitLevel > 0 || numericLevel > 0 || this.looksLikeHeading(text);
          if (titleLike && text.length <= 180) {
            headingLevel = Math.max(1, Math.min(3, explicitLevel || numericLevel || 1));
            role = `heading-${headingLevel}`;
            inAbstract = false;
            inKeywords = false;
          } else if (inAbstract && (block.pageIndex === abstractPage || block.pageIndex === abstractPage + 1)) {
            role = "abstract-body";
          } else if (inKeywords && text.length <= 500) {
            role = "keywords";
            inKeywords = false;
          } else if (block.pageIndex === 0 && block.bbox[1] >= titleBottom - 8 && block.bbox[1] < 520 && this.looksLikeAffiliation(text)) {
            role = "affiliation";
          } else if (block.pageIndex === 0 && block.bbox[1] >= titleBottom - 8 && block.bbox[1] < 470 && this.looksLikeAuthorLine(text)) {
            role = "author";
          } else if (/footnote/.test(type) || (block.bbox[1] > 900 && text.length < 500)) {
            role = "footnote";
          }
        }
        block.semanticRole = role;
        block.headingLevel = headingLevel || (/^heading-/.test(role) ? Number(role.slice(-1)) || 1 : 0);
      }
      return blocks;
    },

    isReferenceHeading(text) {
      const t = asString(text).trim().replace(/^#+\s*/, "").replace(/[:：]$/, "").toLowerCase();
      return /^(references|bibliography|literature cited|参考文献|参考资料)$/.test(t);
    },

    looksLikeHeading(text) {
      const t = asString(text).trim().replace(/^#+\s*/, "");
      if (t.length > 120 || /[.!?。！？]\s*$/.test(t)) return false;
      if (/^\d+(?:\.\d+)*\s+\S+/.test(t)) return true;
      if (/^[A-Z][A-Z\s\-:]{3,}$/.test(t)) return true;
      if (/^#{1,6}\s*/.test(asString(text).trim())) return true;
      return false;
    },

    shouldTranslateText(text) {
      const t = asString(text).trim();
      if (t.length < 2) return false;
      if (/^https?:\/\/\S+$/.test(t)) return false;
      if (/^[\d\s.,;:()\[\]{}+\-=×÷%<>_/\\|]+$/.test(t)) return false;
      if (/^!\[[^\]]*\]\([^)]*\)$/.test(t)) return false;
      return true;
    },

    chunkText(text, maxChars) {
      const t = asString(text).trim();
      if (t.length <= maxChars) return [t];
      const sentences = t.split(/(?<=[.!?。！？])\s+/);
      const chunks = [];
      let current = "";
      for (const s of sentences) {
        if (!current) current = s;
        else if ((current + " " + s).length <= maxChars) current += " " + s;
        else {
          chunks.push(current);
          current = s;
        }
      }
      if (current) chunks.push(current);
      const final = [];
      for (const c of chunks) {
        if (c.length <= maxChars) final.push(c);
        else {
          for (let i = 0; i < c.length; i += maxChars) final.push(c.slice(i, i + maxChars));
        }
      }
      return final;
    },

    isCaptionLikeText(text) {
      const t = asString(text).trim();
      if (!t || t.length > 420) return false;
      return /^(figure|fig\.?|table|scheme|chart|plate)\s*[0-9ivx]+[.:：\-\s]|^(图|表)\s*\d+[：:\.、\-\s]/i.test(t);
    },

    bboxHorizontalOverlapRatio(a, b) {
      if (!Array.isArray(a) || !Array.isArray(b)) return 0;
      const left = Math.max(a[0], b[0]);
      const right = Math.min(a[2], b[2]);
      const w = right - left;
      if (w <= 0) return 0;
      const base = Math.max(1, Math.min(a[2] - a[0], b[2] - b[0]));
      return w / base;
    },

    bboxVerticalGap(a, b) {
      if (!Array.isArray(a) || !Array.isArray(b)) return Infinity;
      if (a[3] <= b[1]) return b[1] - a[3];
      if (b[3] <= a[1]) return a[1] - b[3];
      return 0;
    },

    resolveCaptionAnchor(layoutBlocks, blockLike) {
      if (!blockLike || !Array.isArray(blockLike.bbox)) return null;
      const pageBlocks = (layoutBlocks || []).filter((x) => x.pageIndex === blockLike.pageIndex && x.protectedBlock && Array.isArray(x.bbox));
      if (!pageBlocks.length) return null;
      let best = null;
      for (const pb of pageBlocks) {
        const overlap = this.bboxHorizontalOverlapRatio(blockLike.bbox, pb.bbox);
        if (overlap < 0.32) continue;
        const gap = this.bboxVerticalGap(blockLike.bbox, pb.bbox);
        if (gap > 65) continue;
        const score = overlap * 100 - gap;
        if (!best || score > best.score) {
          best = { block: pb, gap, overlap, score };
        }
      }
      return best;
    },

    enrichSegmentLayoutMetadata(state) {
      const layoutBlocks = state?.layoutBlocks || [];
      if (!layoutBlocks.length || !Array.isArray(state?.segments)) return;
      const blockMap = new Map(layoutBlocks.map((b) => [`${b.pageIndex}:${b.layoutIndex}`, b]));
      for (const seg of state.segments) {
        if (!seg?.layoutBlockKey) continue;
        const layoutBlock = blockMap.get(seg.layoutBlockKey);
        if (!layoutBlock) continue;
        seg.layoutIndex = layoutBlock.layoutIndex;
        seg.layoutType = layoutBlock.type;
        seg.semanticRole = layoutBlock.semanticRole || seg.semanticRole || "body";
        seg.headingLevel = layoutBlock.headingLevel || seg.headingLevel || 0;
        const anchor = this.resolveCaptionAnchor(layoutBlocks, layoutBlock);
        const looksCaption = this.isCaptionLikeText(layoutBlock.text || seg.source) || (anchor && (anchor.gap <= 38 || anchor.overlap >= 0.68));
        if (looksCaption && anchor) {
          seg.caption = true;
          seg.captionTargetBBox = anchor.block.bbox.slice();
          seg.captionTargetType = anchor.block.type || '';
          seg.captionGap = anchor.gap;
          seg.captionOverlap = anchor.overlap;
          if (anchor.block.type === "table") seg.semanticRole = "table-caption";
          else if (["image", "chart"].includes(anchor.block.type)) seg.semanticRole = "figure-caption";
          else if (!/caption$/.test(seg.semanticRole || "")) seg.semanticRole = "caption";
        }
      }
    },

    renderPlaceholders(state) {
      const doc = this.refreshStateDocument(state);
      if (!this.safeDOMConnected(state.body)) {
        try {
          const panel = doc?.getElementById(PANEL_ID);
          if (panel && !panel.hidden) {
            state.panel = panel;
            state.body = panel.querySelector(".zft-body");
            state.status = panel.querySelector(".zft-status");
            state.progress = panel.querySelector(".zft-progress > i");
            this.bindPanelControls(state, panel);
          }
        } catch (_) {}
      }
      if (!this.safeDOMConnected(state.body)) return;
      state.body.replaceChildren();
      const showSource = !!this.pref("showSource", true);
      const showAssets = !!this.pref("panel.showAssetPlaceholders", true);
      const makePageBox = (pageIndex) => {
        const pageBox = state.doc.createElement("section");
        pageBox.className = "zft-page";
        pageBox.dataset.pageIndex = String(pageIndex);
        const label = state.doc.createElement("div");
        label.className = "zft-page-label";
        label.textContent = `第 ${pageIndex + 1} 页`;
        pageBox.append(label);
        state.body.append(pageBox);
        return pageBox;
      };
      const appendSegmentRow = (pageBox, seg) => {
        const row = state.doc.createElement("div");
        row.className = "zft-segment";
        row.dataset.index = String(seg.index);
        row.dataset.pageIndex = String(seg.pageIndex);
        row.dataset.role = seg.semanticRole || "body";
        if (seg.caption) row.dataset.caption = "true";
        if (showSource) {
          const source = state.doc.createElement("div");
          source.className = "zft-source";
          source.textContent = seg.source;
          row.append(source);
        }
        const trans = state.doc.createElement("div");
        trans.className = "zft-translation";
        trans.textContent = "翻译中…";
        row.append(trans);
        row.addEventListener("click", () => this.onSegmentClick(state, seg));
        pageBox.append(row);
      };
      if (state.layoutAvailable && Array.isArray(state.layoutBlocks) && state.layoutBlocks.length) {
        const segsByBlock = new Map();
        for (const seg of state.segments) {
          const key = seg.layoutBlockKey || `idx:${seg.index}`;
          if (!segsByBlock.has(key)) segsByBlock.set(key, []);
          segsByBlock.get(key).push(seg);
        }
        let currentPage = null;
        let pageBox = null;
        for (const block of state.layoutBlocks) {
          if (block.pageIndex !== currentPage) {
            currentPage = block.pageIndex;
            pageBox = makePageBox(currentPage);
          }
          if (showAssets && block.protectedBlock) {
            const asset = state.doc.createElement("div");
            asset.className = "zft-asset-placeholder";
            asset.dataset.assetType = block.type || "asset";
            const label = state.doc.createElement("div");
            label.className = "zft-asset-label";
            const names = { image: '图片/Figure', table: '表格/Table', chart: '图表/Chart', equation: '公式/Equation', code: '代码/Code', algorithm: '算法/Algorithm' };
            label.textContent = `保留原位：${names[block.type] || block.type || '对象'}`;
            asset.append(label);
            if (Array.isArray(block.bbox)) {
              const note = state.doc.createElement("div");
              note.className = "zft-asset-note";
              const [ax0, ay0, ax1, ay1] = block.bbox;
              const w = Math.max(1, ax1 - ax0);
              const h = Math.max(1, ay1 - ay0);
              note.textContent = `页面对象占位 ${Math.round(w)}×${Math.round(h)}（归一化坐标）`;
              asset.append(note);
              asset.style.width = `${Math.max(18, Math.min(100, w / 10))}%`;
              asset.style.marginLeft = `${Math.max(0, Math.min(82, ax0 / 10))}%`;
              asset.style.aspectRatio = `${Math.max(1, w)} / ${Math.max(1, h)}`;
              asset.style.minHeight = "58px";
              asset.style.maxHeight = "360px";
            }
            pageBox.append(asset);
          }
          const key = `${block.pageIndex}:${block.layoutIndex}`;
          const rows = segsByBlock.get(key);
          if (rows?.length) {
            for (const seg of rows) appendSegmentRow(pageBox, seg);
            segsByBlock.delete(key);
          }
        }
        for (const [_, rows] of segsByBlock) {
          const pageIndex = rows[0]?.pageIndex || 0;
          if (pageIndex !== currentPage) { currentPage = pageIndex; pageBox = makePageBox(currentPage); }
          for (const seg of rows) appendSegmentRow(pageBox, seg);
        }
        return;
      }
      let currentPage = null;
      let pageBox = null;
      for (const seg of state.segments) {
        if (seg.pageIndex !== currentPage) {
          currentPage = seg.pageIndex;
          pageBox = makePageBox(currentPage);
        }
        appendSegmentRow(pageBox, seg);
      }
    },

    renderAllSegments(state) {
      this.renderPlaceholders(state);
      for (let i = 0; i < state.segments.length; i++) this.updateSegmentDOM(state, i);
    },

    updateSegmentDOM(state, index) {
      const row = state.body?.querySelector(`.zft-segment[data-index="${index}"]`);
      if (!row) return;
      const trans = row.querySelector(".zft-translation");
      trans.textContent = state.translationByIndex.get(index) || "";
    },

    onSegmentClick(state, seg) {
      const action = asString(this.pref("clickAction", "jump"));
      if (action === "copy") {
        const value = `${seg.source}\n\n${state.translationByIndex.get(seg.index) || ""}`;
        try {
          new state.doc.defaultView.ClipboardEvent("copy");
          state.doc.defaultView.navigator.clipboard.writeText(value);
        } catch (_) {}
        return;
      }
      try {
        Zotero.Reader.open(state.itemID, { pageIndex: seg.pageIndex });
      } catch (e) {
        this.log("jump failed", String(e));
      }
    },

    quotaPresets() {
      // These presets are intentionally editable in Preferences. Provider policies can change.
      return {
        niutrans: { name: "小牛翻译", period: "account", charsLimit: 1000000, requestsLimit: 0, qps: 5, maxChars: 5000, note: "新注册总额度约 100 万字符（非每月重置）" },
        baidu: { name: "百度翻译", period: "month", charsLimit: 1000000, requestsLimit: 0, qps: 10, maxChars: 0, note: "预设：每月约 100 万字符" },
        baidufield: { name: "百度垂直领域", period: "month", charsLimit: 500000, requestsLimit: 0, qps: 10, maxChars: 0, note: "预设：每月约 50 万字符" },
        tencent: { name: "腾讯翻译", period: "month", charsLimit: 5000000, requestsLimit: 0, qps: 5, maxChars: 0, note: "预设：每月约 500 万字符" },
        huoshan: { name: "火山翻译", period: "month", charsLimit: 2000000, requestsLimit: 0, qps: 10, maxChars: 0, note: "预设：每月约 200 万字符" },
        aliyun: { name: "阿里翻译", period: "month", charsLimit: 1000000, requestsLimit: 0, qps: 50, maxChars: 0, note: "预设：每月约 100 万字符" },
      };
    },

    normalizeQuotaServiceID(serviceID) {
      const raw = asString(serviceID || "").trim();
      if (!raw) return "pdftranslate-default";
      const lower = raw.toLowerCase();
      const aliases = {
        "niu": "niutrans", "niu-trans": "niutrans", "niutrans": "niutrans",
        "baidu": "baidu", "baidufield": "baidufield", "baidu-field": "baidufield",
        "tencent": "tencent", "tencentcloud": "tencent",
        "huoshan": "huoshan", "volcengine": "huoshan", "volc": "huoshan",
        "aliyun": "aliyun", "ali": "aliyun", "alibaba": "aliyun",
      };
      return aliases[lower] || raw;
    },

    currentQuotaServiceID() {
      const provider = asString(this.pref("translationProvider", "pdftranslate"));
      if (provider === "gpt") return "gpt";
      const configured = asString(this.pref("pdftranslateService", "")).trim();
      const lastUsed = asString(this.pref("quota.lastPDFTranslateService", "")).trim();
      return this.normalizeQuotaServiceID(configured || lastUsed || "pdftranslate-default");
    },

    quotaPeriodKey(period = "month") {
      if (period === "account") return "account";
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    },

    readJSONPref(key) {
      try {
        const value = JSON.parse(asString(this.pref(key, "{}")) || "{}");
        return value && typeof value === "object" ? value : {};
      } catch (_) {
        return {};
      }
    },

    writeJSONPref(key, value) {
      this.setPref(key, JSON.stringify(value || {}));
    },

    getQuotaOverride(serviceID) {
      const key = this.normalizeQuotaServiceID(serviceID);
      return this.readJSONPref("quota.overrides")?.[key] || null;
    },

    setQuotaOverride(serviceID, values = {}) {
      const key = this.normalizeQuotaServiceID(serviceID);
      const overrides = this.readJSONPref("quota.overrides");
      overrides[key] = {
        name: asString(values.name || "").trim(),
        period: values.period === "account" ? "account" : "month",
        charsLimit: Math.max(0, Number(values.charsLimit) || 0),
        requestsLimit: Math.max(0, Number(values.requestsLimit) || 0),
        qps: Math.max(0, Number(values.qps) || 0),
        maxChars: Math.max(0, Number(values.maxChars) || 0),
        custom: true,
      };
      this.writeJSONPref("quota.overrides", overrides);
      return this.getQuotaSnapshot(key);
    },

    clearQuotaOverride(serviceID) {
      const key = this.normalizeQuotaServiceID(serviceID);
      const overrides = this.readJSONPref("quota.overrides");
      delete overrides[key];
      this.writeJSONPref("quota.overrides", overrides);
      return this.getQuotaSnapshot(key);
    },

    getQuotaConfig(serviceID) {
      const key = this.normalizeQuotaServiceID(serviceID);
      const preset = this.quotaPresets()[key] || null;
      const override = this.getQuotaOverride(key);
      const base = preset || { name: key === "gpt" ? "GPT / OpenAI-compatible" : key, period: "month", charsLimit: 0, requestsLimit: 0, qps: 0, maxChars: 0, note: "未内置额度；可手动设置" };
      const merged = { serviceID: key, ...base, ...(override || {}), preset: !!preset, overridden: !!override };
      if (!asString(merged.name).trim()) merged.name = base.name;
      return merged;
    },

    getQuotaUsage(serviceID, period = null) {
      const config = this.getQuotaConfig(serviceID);
      const periodKey = this.quotaPeriodKey(period || config.period);
      const store = this.readJSONPref("quota.usage");
      const record = store?.[config.serviceID]?.[periodKey] || {};
      return {
        periodKey,
        chars: Math.max(0, Number(record.chars) || 0),
        requests: Math.max(0, Number(record.requests) || 0),
        updatedAt: record.updatedAt || "",
      };
    },

    recordTranslationUsage(serviceID, chars, requests = 1) {
      if (!this.pref("quota.enabled", true)) return;
      const config = this.getQuotaConfig(serviceID);
      const periodKey = this.quotaPeriodKey(config.period);
      const store = this.readJSONPref("quota.usage");
      if (!store[config.serviceID]) store[config.serviceID] = {};
      const current = store[config.serviceID][periodKey] || { chars: 0, requests: 0 };
      store[config.serviceID][periodKey] = {
        chars: Math.max(0, Number(current.chars) || 0) + Math.max(0, Number(chars) || 0),
        requests: Math.max(0, Number(current.requests) || 0) + Math.max(0, Number(requests) || 0),
        updatedAt: new Date().toISOString(),
      };
      this.writeJSONPref("quota.usage", store);
    },

    resetQuotaUsage(serviceID) {
      const config = this.getQuotaConfig(serviceID);
      const periodKey = this.quotaPeriodKey(config.period);
      const store = this.readJSONPref("quota.usage");
      if (store?.[config.serviceID]) delete store[config.serviceID][periodKey];
      this.writeJSONPref("quota.usage", store);
      return this.getQuotaSnapshot(config.serviceID);
    },

    getQuotaSnapshot(serviceID = null) {
      const config = this.getQuotaConfig(serviceID || this.currentQuotaServiceID());
      const usage = this.getQuotaUsage(config.serviceID, config.period);
      const charsLimit = Math.max(0, Number(config.charsLimit) || 0);
      const requestsLimit = Math.max(0, Number(config.requestsLimit) || 0);
      const charsPercent = charsLimit ? (usage.chars / charsLimit) * 100 : 0;
      const requestsPercent = requestsLimit ? (usage.requests / requestsLimit) * 100 : 0;
      return {
        ...config,
        ...usage,
        charsRemaining: charsLimit ? Math.max(0, charsLimit - usage.chars) : null,
        requestsRemaining: requestsLimit ? Math.max(0, requestsLimit - usage.requests) : null,
        charsPercent,
        requestsPercent,
        percent: Math.max(charsPercent, requestsPercent),
      };
    },

    formatQuotaNumber(value) {
      const n = Math.max(0, Number(value) || 0);
      if (n >= 100000000) return `${(n / 100000000).toFixed(n % 100000000 ? 1 : 0)}亿`;
      if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 ? 1 : 0)}万`;
      return String(Math.round(n));
    },

    quotaSummaryText(serviceID = null) {
      const q = this.getQuotaSnapshot(serviceID || this.currentQuotaServiceID());
      const parts = [];
      if (q.charsLimit) parts.push(`${this.formatQuotaNumber(q.chars)}/${this.formatQuotaNumber(q.charsLimit)}字符`);
      if (q.requestsLimit) parts.push(`${this.formatQuotaNumber(q.requests)}/${this.formatQuotaNumber(q.requestsLimit)}次`);
      if (!parts.length) parts.push(`${this.formatQuotaNumber(q.chars)}字符 · ${this.formatQuotaNumber(q.requests)}次（本地统计）`);
      return `${q.name || q.serviceID}：${parts.join(" · ")}${q.charsLimit || q.requestsLimit ? ` · ${Math.round(q.percent)}%` : ""}`;
    },

    quotaPreflight(state, segments) {
      if (!this.pref("quota.enabled", true)) return true;
      const serviceID = this.currentQuotaServiceID();
      const q = this.getQuotaSnapshot(serviceID);
      const estimatedChars = segments.reduce((sum, seg) => sum + asString(seg.source).length, 0);
      const estimatedRequests = segments.length;
      const projectedChars = q.chars + estimatedChars;
      const projectedRequests = q.requests + estimatedRequests;
      const projectedCharsPct = q.charsLimit ? (projectedChars / q.charsLimit) * 100 : 0;
      const projectedReqPct = q.requestsLimit ? (projectedRequests / q.requestsLimit) * 100 : 0;
      const projectedPct = Math.max(projectedCharsPct, projectedReqPct);
      const warnAt = Math.max(1, Math.min(100, Number(this.pref("quota.warnPercent", 80)) || 80));
      const over = (q.charsLimit && projectedChars > q.charsLimit) || (q.requestsLimit && projectedRequests > q.requestsLimit);
      const near = projectedPct >= warnAt;
      const maxSegChars = segments.reduce((m, seg) => Math.max(m, asString(seg.source).length), 0);
      if (q.maxChars && maxSegChars > q.maxChars) {
        const e = new Error(`${q.name || q.serviceID} 单次字段上限约 ${q.maxChars} 字符；当前最大分段 ${maxSegChars} 字符。请减小“单段最大字符数”。`);
        e.zftStage = "翻译引擎额度检查";
        throw e;
      }
      state.quotaEstimate = { serviceID: q.serviceID, estimatedChars, estimatedRequests, projectedPct };
      if (!(over || near) || !this.pref("quota.confirmNearLimit", true)) return true;
      const lines = [
        `${q.name || q.serviceID} 额度提醒`,
        "",
        q.charsLimit ? `本期字符：${this.formatQuotaNumber(q.chars)} / ${this.formatQuotaNumber(q.charsLimit)}` : `本期已统计字符：${this.formatQuotaNumber(q.chars)}`,
        q.requestsLimit ? `本期请求：${this.formatQuotaNumber(q.requests)} / ${this.formatQuotaNumber(q.requestsLimit)}` : `本期已统计请求：${this.formatQuotaNumber(q.requests)}`,
        `本次预计：${this.formatQuotaNumber(estimatedChars)} 字符 · ${estimatedRequests} 次请求`,
        q.charsLimit || q.requestsLimit ? `执行后预计使用：${Math.round(projectedPct)}%` : "",
        over ? "⚠ 预计会超过当前设置的额度。" : `⚠ 预计将达到预警阈值 ${warnAt}%。`,
        "",
        "额度来自插件本地统计/预设，并不等同于服务商后台实时账单。是否继续？",
      ].filter(Boolean).join("\n");
      let ok = true;
      try {
        ok = Services.prompt.confirm(Zotero.getMainWindow?.() || null, "全文翻译 · 额度提醒", lines);
      } catch (_) {}
      if (!ok) {
        const e = new Error("用户在额度提醒处取消翻译");
        e.name = "ZFTCancelled";
        throw e;
      }
      return true;
    },

    getEffectiveRateSettings() {
      const enabled = !!this.pref("rate.enabled", true);
      let qps = Math.max(0.1, Number(this.pref("rate.maxQPS", 2)) || 2);
      let maxConcurrent = Math.max(1, Math.min(20, Number(this.pref("rate.maxConcurrent", 2)) || 2));
      const quota = this.getQuotaSnapshot(this.currentQuotaServiceID());
      // Provider quota/QPS is a hard ceiling when known; user rate settings can be more conservative.
      if (quota?.qps) qps = Math.min(qps, Math.max(0.1, Number(quota.qps) || qps));
      if (this.pref("quota.autoLimitConcurrency", true) && quota?.qps) {
        maxConcurrent = Math.min(maxConcurrent, Math.max(1, Math.ceil(Number(quota.qps) || 1)));
      }
      return {
        enabled,
        qps,
        maxConcurrent,
        maxRetries: Math.max(0, Math.min(10, Number(this.pref("rate.maxRetries", 5)) || 0)),
        backoffBaseMs: Math.max(250, Number(this.pref("rate.backoffBaseMs", 1500)) || 1500),
        maxBackoffMs: Math.max(1000, Number(this.pref("rate.maxBackoffMs", 20000)) || 20000),
        jitterMs: Math.max(0, Number(this.pref("rate.jitterMs", 350)) || 0),
      };
    },

    async cancellableDelay(state, ms) {
      const end = Date.now() + Math.max(0, Number(ms) || 0);
      while (Date.now() < end) {
        this.throwIfCancelled(state);
        const remaining = end - Date.now();
        await Zotero.Promise.delay(Math.min(200, Math.max(1, remaining)));
      }
      this.throwIfCancelled(state);
    },

    async waitForTranslationRateSlot(state) {
      const rate = this.getEffectiveRateSettings();
      if (!rate.enabled || !rate.qps) return;
      let release;
      const previous = this._translationRateGate || Promise.resolve();
      this._translationRateGate = new Promise((resolve) => { release = resolve; });
      await previous.catch(() => {});
      try {
        this.throwIfCancelled(state);
        const interval = Math.ceil(1000 / Math.max(0.1, rate.qps));
        const now = Date.now();
        const next = Number(this._translationNextRequestAt) || 0;
        if (next > now) await this.cancellableDelay(state, next - now);
        this._translationNextRequestAt = Math.max(Date.now(), next) + interval;
      } finally {
        release?.();
      }
    },

    isRateLimitError(error) {
      const text = `${this.safeErrorMessage(error)} ${asString(this.safeRead(error, "status", ""))}`.toLowerCase();
      return /(^|\D)429(\D|$)|too many requests|rate[ -]?limit|qps|requests? too frequent|frequency limit|请求过于频繁|请求频率|访问频率|频率限制|54003|54005/.test(text);
    },

    async translateTextWithRetry(text, itemID, state) {
      const rate = this.getEffectiveRateSettings();
      let attempt = 0;
      while (true) {
        this.throwIfCancelled(state);
        await this.waitForTranslationRateSlot(state);
        try {
          return await this.translateText(text, itemID, state);
        } catch (e) {
          if (state?.cancelRequested) this.throwIfCancelled(state);
          if (e?.name === "ZFTCancelled") throw e;
          if (!rate.enabled || !this.isRateLimitError(e) || attempt >= rate.maxRetries) throw e;
          const exp = Math.min(rate.maxBackoffMs, rate.backoffBaseMs * Math.pow(2, attempt));
          const jitter = rate.jitterMs ? Math.floor(Math.random() * (rate.jitterMs + 1)) : 0;
          const waitMs = exp + jitter;
          attempt++;
          this.setStatus(state, `翻译服务触发限流 · ${Math.round(waitMs / 100) / 10}s 后重试 ${attempt}/${rate.maxRetries}`, Math.max(8, Number(state.taskProgress) || 8));
          await this.cancellableDelay(state, waitMs);
        }
      }
    },

    async translateSegments(state, item) {
      const total = state.segments.length;
      const rate = this.getEffectiveRateSettings();
      let concurrency = Math.max(1, Math.min(12, Number(this.pref("concurrency", 3)) || 3));
      if (rate.enabled) concurrency = Math.min(concurrency, rate.maxConcurrent);
      let cursor = 0;
      let completed = 0;
      const pageTotals = new Map();
      const pageDone = new Map();
      for (const seg of state.segments) pageTotals.set(seg.pageIndex, (pageTotals.get(seg.pageIndex) || 0) + 1);
      const totalPages = pageTotals.size;
      if (rate.enabled) this.setStatus(state, `翻译限速：${rate.qps} QPS · 最大 ${concurrency} 并发`, 8);
      const workers = Array.from({ length: Math.min(concurrency, total) }, async () => {
        while (true) {
          this.throwIfCancelled(state);
          const idx = cursor++;
          if (idx >= total) return;
          const seg = state.segments[idx];
          try {
            const translated = await this.translateTextWithRetry(seg.source, item.id, state);
            this.throwIfCancelled(state);
            state.translationByIndex.set(idx, translated);
          } catch (e) {
            if (e?.name === "ZFTCancelled") throw e;
            state.translationByIndex.set(idx, `[翻译失败] ${this.safeErrorMessage(e)}`);
          }
          completed++;
          pageDone.set(seg.pageIndex, (pageDone.get(seg.pageIndex) || 0) + 1);
          const finishedPages = [...pageTotals.entries()].filter(([page, count]) => (pageDone.get(page) || 0) >= count).length;
          this.updateSegmentDOM(state, idx);
          if (state.displayMode === "translation" && state.layoutAvailable) this.scheduleOverlayRender(state);
          this.setStatus(state, `翻译 ${completed}/${total} 段 · ${finishedPages}/${totalPages} 页 · ${rate.enabled ? `${rate.qps} QPS` : "不限速"}`, 8 + Math.round((completed / total) * 90));
        }
      });
      await Promise.all(workers);
    },

    async translateText(text, itemID, state = null) {
      const provider = asString(this.pref("translationProvider", "pdftranslate"));
      if (provider === "gpt") return this.translateWithGPT(text, state);
      return this.translateWithPDFTranslate(text, itemID);
    },

    async translateWithPDFTranslate(text, itemID) {
      const api = Zotero.PDFTranslate?.api;
      if (!api?.translate) {
        throw new Error("未检测到 Translate for Zotero。请安装该插件，或在设置中改用 GPT/OpenAI-compatible 引擎。");
      }
      const main = asString(this.pref("pdftranslateService", "")).trim();
      const fallbacks = asString(this.pref("pdftranslateFallbackServices", ""))
        .split(/[,，\n]+/)
        .map((x) => x.trim())
        .filter(Boolean);
      const service = main ? [main, ...fallbacks] : undefined;
      const task = await api.translate(text, {
        pluginID: this.id,
        service,
        itemID,
        langfrom: asString(this.pref("sourceLanguage", "auto")),
        langto: asString(this.pref("targetLanguage", "zh-CN")),
      });
      if (!task?.result) {
        const detail = asString(task?.error || task?.message || task?.status || "").trim();
        throw new Error(`Translate for Zotero 翻译失败${detail ? `：${detail}` : ""}`);
      }
      const usedService = this.normalizeQuotaServiceID(asString(task?.service || main || "pdftranslate-default"));
      if (usedService && usedService !== "pdftranslate-default") this.setPref("quota.lastPDFTranslateService", usedService);
      this.recordTranslationUsage(usedService, asString(text).length, 1);
      return asString(task.result).trim();
    },

    async translateWithGPT(text, state = null) {
      const key = asString(this.pref("gpt.apiKey", "")).trim();
      if (!key) throw new Error("请先在设置中填写 GPT API Key。");
      const base = asString(this.pref("gpt.baseURL", "https://api.openai.com/v1")).replace(/\/$/, "");
      const model = asString(this.pref("gpt.model", "gpt-5.4-mini")).trim();
      const protocol = asString(this.pref("gpt.protocol", "responses"));
      const targetLanguage = asString(this.pref("targetLanguage", "zh-CN"));
      const template = asString(this.pref("gpt.prompt", "Translate to {{targetLanguage}}:\n{{text}}"));
      const prompt = template.replace(/\{\{targetLanguage\}\}/g, targetLanguage).replace(/\{\{tl\}\}/g, targetLanguage).replace(/\{\{text\}\}/g, text);
      const headers = { Authorization: `Bearer ${key}` };

      if (protocol === "chat") {
        const data = await this.httpJSON("POST", `${base}/chat/completions`, {
          model,
          temperature: Number(this.pref("gpt.temperature", 0.1)) || 0,
          messages: [{ role: "user", content: prompt }],
        }, headers, state);
        const value = data?.choices?.[0]?.message?.content;
        if (!value) throw new Error(data?.error?.message || "GPT Chat Completions 返回为空");
        this.recordTranslationUsage("gpt", asString(text).length, 1);
        return asString(value).trim();
      }

      const data = await this.httpJSON("POST", `${base}/responses`, {
        model,
        input: prompt,
      }, headers, state);
      const value = this.extractResponsesText(data);
      if (!value) throw new Error(data?.error?.message || "GPT Responses API 返回为空");
      this.recordTranslationUsage("gpt", asString(text).length, 1);
      return value.trim();
    },

    extractResponsesText(data) {
      if (typeof data?.output_text === "string") return data.output_text;
      const parts = [];
      for (const item of data?.output || []) {
        if (item?.type !== "message") continue;
        for (const c of item.content || []) {
          if (c?.type === "output_text" && c.text) parts.push(c.text);
        }
      }
      return parts.join("\n");
    },

    activatePreferredReaderMode(state) {
      const preferred = asString(this.pref("readerMode", "translation"));
      const mode = preferred === "translation" && !state.layoutAvailable ? "bilingual" : preferred;
      this.setReaderMode(state, ["source", "translation", "bilingual"].includes(mode) ? mode : "bilingual");
    },

    togglePrimaryTranslationView(state) {
      if (!state?.completed) return;
      if (state.displayMode === "translation") this.setReaderMode(state, "source");
      else this.setReaderMode(state, "translation");
    },

    setReaderMode(state, mode) {
      if (!state) return;
      if (mode === "translation" && !state.layoutAvailable) {
        mode = "bilingual";
        this.notify("全文翻译", "当前解析结果没有页面坐标，已切换为双屏对照；原位译文请使用 MinerU 解析器。");
      }
      state.displayMode = mode;
      if (mode === "source") {
        this.removeTranslationOverlays(state);
        this.stopOverlayLifecycle(state);
        if (state.panel) state.panel.hidden = true;
        this.applyPanelLayout(state, false);
      } else if (mode === "translation") {
        if (state.panel) state.panel.hidden = true;
        this.applyPanelLayout(state, false);
        this.startOverlayLifecycle(state);
        this.renderTranslationOverlay(state);
      } else {
        this.removeTranslationOverlays(state);
        this.stopOverlayLifecycle(state);
        this.ensurePanel(state);
        state.panel.hidden = false;
        this.applyPanelLayout(state, true);
      }
      this.updateModeButtons(state);
    },

    updateModeButtons(state) {
      state?.panel?.querySelectorAll?.(".zft-mode").forEach((button) => {
        button.dataset.active = button.dataset.mode === state.displayMode ? "true" : "false";
      });
    },

    getPDFView(state) {
      const reader = state?.reader;
      const candidates = [
        // Zotero 10 ReaderInstance is a Proxy: unknown properties are forwarded
        // to the internal Reader, whose _primaryView is already a PDFView.
        reader?._primaryView,
        reader?._internalReader?._primaryView,
        // Compatibility fallbacks for alternate/older wrappers.
        reader?._reader?._primaryView,
        reader?._reader?._primaryView?._view,
        reader?._iframeWindow?.wrappedJSObject?._reader?._primaryView,
        reader?._iframeWindow?.wrappedJSObject?._reader?._primaryView?._view,
        reader?._iframeWindow?._reader?._primaryView,
        reader?._iframeWindow?._reader?._primaryView?._view,
      ];
      return candidates.find((x) => x?._iframeWindow || x?._iframe?.contentWindow) || null;
    },

    getPDFInnerDocument(state) {
      const view = this.getPDFView(state);
      try { return view?._iframeWindow?.document || view?._iframe?.contentWindow?.document || null; }
      catch (_) { return null; }
    },

    getPDFPageView(state, pageIndex) {
      const view = this.getPDFView(state);
      try {
        const app = view?._iframeWindow?.PDFViewerApplication || view?._iframe?.contentWindow?.PDFViewerApplication;
        return app?.pdfViewer?.getPageView?.(pageIndex) || null;
      } catch (_) { return null; }
    },

    injectPDFOverlayStyle(doc) {
      if (!doc || doc.getElementById("zft-pdf-overlay-style")) return;
      const style = doc.createElement("style");
      style.id = "zft-pdf-overlay-style";
      style.textContent = `
        .zft-page-translation-overlay{position:absolute;inset:0;z-index:2;pointer-events:none;overflow:hidden;contain:layout paint style}
        .zft-page-translation-overlay .zft-overlay-block{position:absolute;box-sizing:border-box;overflow:hidden;display:block;padding:0;background:rgba(255,255,255,var(--zft-overlay-opacity,.98));color:#111;border-radius:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;font-weight:400;font-style:normal;line-height:1.18;letter-spacing:0;white-space:pre-wrap;word-break:normal;overflow-wrap:break-word;text-align:justify;text-justify:inter-ideograph;text-rendering:optimizeLegibility;-moz-osx-font-smoothing:grayscale}
        .zft-page-translation-overlay .zft-overlay-block[data-heading="true"]{font-weight:650;text-align:left}
        .zft-page-translation-overlay .zft-overlay-block[data-caption="true"]{padding:.1em .22em;background:rgba(255,255,255,calc(var(--zft-overlay-opacity,.98) * .94));text-align:left;line-height:1.22;border-radius:2px}
        .zft-page-translation-overlay .zft-overlay-block[data-caption-type="image"],
        .zft-page-translation-overlay .zft-overlay-block[data-caption-type="table"],
        .zft-page-translation-overlay .zft-overlay-block[data-caption-type="chart"]{box-shadow:0 0 0 1px rgba(0,0,0,.06) inset}
        .zft-page-translation-overlay .zft-overlay-block[data-role="paper-title"]{font-weight:700;text-align:center;line-height:1.08}
        .zft-page-translation-overlay .zft-overlay-block[data-role="author"],.zft-page-translation-overlay .zft-overlay-block[data-role="affiliation"]{text-align:center}
        .zft-page-translation-overlay .zft-overlay-block[data-role="heading-1"]{font-weight:700;line-height:1.1}
        .zft-page-translation-overlay .zft-overlay-block[data-role="heading-2"]{font-weight:650;line-height:1.12}
        .zft-page-translation-overlay .zft-overlay-block[data-role="heading-3"]{font-weight:600;line-height:1.14}
        .zft-page-translation-overlay .zft-overlay-block[data-role="abstract-heading"],.zft-page-translation-overlay .zft-overlay-block[data-role="keywords-heading"],.zft-page-translation-overlay .zft-overlay-block[data-role="reference-heading"]{font-weight:700;text-align:left}
        .zft-page-translation-overlay .zft-overlay-block[data-role="footnote"],.zft-page-translation-overlay .zft-overlay-block[data-role="reference-entry"]{text-align:left}
        .zft-page-translation-overlay .zft-overlay-block[data-overflow="true"]{outline:1px dashed rgba(180,35,24,.45)}
        .zft-page .zft-asset-placeholder{margin:10px 0;padding:10px 12px;border:1px dashed rgba(90,90,90,.35);border-radius:8px;background:rgba(127,127,127,.06)}
        .zft-page .zft-asset-label{font-weight:650;margin:0 0 3px}
        .zft-page .zft-asset-note{font-size:12px;opacity:.72}
        .zft-segment[data-caption="true"] .zft-translation{border-left:3px solid rgba(90,90,90,.24);padding-left:8px}
      `;
      doc.documentElement.append(style);
    },

    getLayoutTranslationGroups(state) {
      const groups = new Map();
      for (const seg of state.segments || []) {
        if (!Array.isArray(seg.bbox) || !seg.layoutBlockKey) continue;
        if (!groups.has(seg.layoutBlockKey)) {
          groups.set(seg.layoutBlockKey, {
            key: seg.layoutBlockKey,
            pageIndex: seg.pageIndex,
            bbox: seg.bbox.slice(),
            textLevel: seg.textLevel || 0,
            semanticRole: seg.semanticRole || "body",
            headingLevel: seg.headingLevel || 0,
            caption: !!seg.caption,
            captionTargetBBox: Array.isArray(seg.captionTargetBBox) ? seg.captionTargetBBox.slice() : null,
            captionTargetType: seg.captionTargetType || "",
            parts: new Array(seg.layoutChunkCount || 1).fill(""),
          });
        }
        const group = groups.get(seg.layoutBlockKey);
        group.parts[seg.layoutChunkIndex || 0] = state.translationByIndex.get(seg.index) || "";
      }
      for (const group of groups.values()) group.translation = group.parts.filter(Boolean).join(" ").trim();
      return groups;
    },

    adaptGroupBBoxForProtectedLayout(group) {
      if (!group?.caption || !Array.isArray(group?.captionTargetBBox) || !Array.isArray(group?.bbox)) return group?.bbox || null;
      const mode = !!this.pref("overlay.imageAwareLayout", true);
      if (!mode) return group.bbox;
      const [bx0, by0, bx1, by1] = group.bbox;
      const [ax0, ay0, ax1, ay1] = group.captionTargetBBox;
      const blockH = Math.max(10, by1 - by0);
      const margin = Math.max(4, Math.min(18, Math.round((ax1 - ax0) * 0.02)));
      const below = by0 >= ay1;
      const above = by1 <= ay0;
      const x0 = Math.max(0, ax0 - margin);
      const x1 = Math.min(1000, ax1 + margin);
      let y0 = by0, y1 = by1;
      if (below) {
        y0 = Math.max(0, ay1 + Math.max(2, Math.min(10, group.captionGap || 4)));
        y1 = Math.min(1000, y0 + blockH);
      } else if (above) {
        y1 = Math.min(1000, ay0 - Math.max(2, Math.min(10, group.captionGap || 4)));
        y0 = Math.max(0, y1 - blockH);
      }
      return [x0, y0, x1, y1];
    },

    transformNormalizedBBox(bbox, rotation) {
      const [x0, y0, x1, y1] = bbox;
      const r = ((Number(rotation) || 0) % 360 + 360) % 360;
      if (r === 90) return [1000 - y1, x0, 1000 - y0, x1];
      if (r === 180) return [1000 - x1, 1000 - y1, 1000 - x0, 1000 - y0];
      if (r === 270) return [y0, 1000 - x1, y1, 1000 - x0];
      return [x0, y0, x1, y1];
    },


    weightedMedianStyleValue(samples, key, fallback = 0) {
      const values = samples
        .map((sample) => ({ value: Number(sample[key]), weight: Math.max(0.0001, Number(sample.weight) || 0) }))
        .filter((entry) => Number.isFinite(entry.value) && entry.value > 0)
        .sort((a, b) => a.value - b.value);
      if (!values.length) return fallback;
      const total = values.reduce((sum, entry) => sum + entry.weight, 0);
      let acc = 0;
      for (const entry of values) {
        acc += entry.weight;
        if (acc >= total / 2) return entry.value;
      }
      return values[values.length - 1].value;
    },

    dominantStyleValue(samples, key, fallback = "") {
      const totals = new Map();
      for (const sample of samples) {
        const value = String(sample[key] || "").trim();
        if (!value) continue;
        totals.set(value, (totals.get(value) || 0) + Math.max(0.0001, Number(sample.weight) || 0));
      }
      let best = fallback, bestWeight = -1;
      for (const [value, weight] of totals) {
        if (weight > bestWeight) { best = value; bestWeight = weight; }
      }
      return best;
    },

    getOriginalTextStyle(pageDiv, overlayNode) {
      try {
        const doc = pageDiv?.ownerDocument;
        const win = doc?.defaultView;
        if (!doc || !win || !overlayNode) return null;
        const target = overlayNode.getBoundingClientRect();
        if (target.width < 1 || target.height < 1) return null;
        const targetArea = Math.max(1, target.width * target.height);
        const spans = pageDiv.querySelectorAll(":scope .textLayer span");
        const samples = [];
        let unionLeft = Infinity, unionTop = Infinity, unionRight = -Infinity, unionBottom = -Infinity;
        for (const span of spans) {
          const rect = span.getBoundingClientRect();
          const left = Math.max(target.left, rect.left);
          const top = Math.max(target.top, rect.top);
          const right = Math.min(target.right, rect.right);
          const bottom = Math.min(target.bottom, rect.bottom);
          const iw = right - left, ih = bottom - top;
          if (iw <= 0 || ih <= 0) continue;
          const intersection = iw * ih;
          const spanArea = Math.max(1, rect.width * rect.height);
          // Reject tiny accidental edge contacts with neighboring columns/lines.
          if (intersection / Math.min(targetArea, spanArea) < 0.08) continue;
          const cs = win.getComputedStyle(span);
          let fontSize = parseFloat(cs.fontSize) || 0;
          let lineHeight = parseFloat(cs.lineHeight) || 0;
          let letterSpacing = parseFloat(cs.letterSpacing) || 0;
          let fontWeight = parseInt(cs.fontWeight, 10);
          if (!Number.isFinite(fontWeight)) fontWeight = /bold/i.test(cs.fontWeight) ? 700 : 400;
          if (!lineHeight && fontSize) lineHeight = fontSize * 1.16;
          samples.push({
            weight: intersection,
            fontSize,
            lineHeight,
            letterSpacing: Number.isFinite(letterSpacing) ? letterSpacing : 0,
            fontWeight,
            fontFamily: cs.fontFamily || span.style.fontFamily || "",
            fontStyle: cs.fontStyle || "normal",
          });
          unionLeft = Math.min(unionLeft, rect.left);
          unionTop = Math.min(unionTop, rect.top);
          unionRight = Math.max(unionRight, rect.right);
          unionBottom = Math.max(unionBottom, rect.bottom);
        }
        if (!samples.length) return null;
        const fontSize = this.weightedMedianStyleValue(samples, "fontSize", 0);
        const lineHeightPx = this.weightedMedianStyleValue(samples, "lineHeight", fontSize * 1.16);
        const fontWeight = Math.round(this.weightedMedianStyleValue(samples, "fontWeight", 400) / 100) * 100;
        const letterSpacing = this.weightedMedianStyleValue(samples, "letterSpacing", 0);
        const fontFamily = this.dominantStyleValue(samples, "fontFamily", "");
        const fontStyle = this.dominantStyleValue(samples, "fontStyle", "normal");
        const leftGap = Number.isFinite(unionLeft) ? Math.max(0, unionLeft - target.left) : 0;
        const rightGap = Number.isFinite(unionRight) ? Math.max(0, target.right - unionRight) : 0;
        const centered = target.width > 0 && Math.abs(leftGap - rightGap) / target.width < 0.08 && leftGap / target.width > 0.05;
        return {
          fontSize,
          lineHeight: fontSize > 0 ? Math.max(0.95, Math.min(1.8, lineHeightPx / fontSize)) : 1.16,
          fontWeight: Math.max(100, Math.min(900, fontWeight || 400)),
          letterSpacing,
          fontFamily,
          fontStyle,
          textAlign: centered ? "center" : (overlayNode.dataset.heading === "true" ? "left" : "justify"),
          matchedSpans: samples.length,
        };
      } catch (_) { return null; }
    },

    makeCJKFontStack(originalFamily) {
      const original = String(originalFamily || "").trim();
      const looksSerif = /times|serif|roman|cambria|georgia|song|ming/i.test(original);
      const cjk = looksSerif
        ? '"Songti SC","STSong","Source Han Serif SC","Noto Serif CJK SC",serif'
        : '"PingFang SC","Microsoft YaHei","Source Han Sans SC","Noto Sans CJK SC",sans-serif';
      return original ? `${original},${cjk}` : cjk;
    },

    semanticStyleProfile(role) {
      const profiles = {
        "paper-title": { weight: 700, align: "center", lineHeight: 1.08, fallbackMax: 30 },
        author: { weight: 500, align: "center", lineHeight: 1.12, fallbackMax: 17 },
        affiliation: { weight: 400, align: "center", lineHeight: 1.14, fallbackMax: 15 },
        "abstract-heading": { weight: 700, align: "left", lineHeight: 1.1, fallbackMax: 17 },
        "abstract-body": { weight: 400, align: "justify", lineHeight: 1.16, fallbackMax: 17 },
        "keywords-heading": { weight: 650, align: "left", lineHeight: 1.12, fallbackMax: 16 },
        keywords: { weight: 400, align: "left", lineHeight: 1.14, fallbackMax: 16 },
        "heading-1": { weight: 700, align: "left", lineHeight: 1.1, fallbackMax: 23 },
        "heading-2": { weight: 650, align: "left", lineHeight: 1.12, fallbackMax: 20 },
        "heading-3": { weight: 600, align: "left", lineHeight: 1.14, fallbackMax: 18 },
        body: { weight: 400, align: "justify", lineHeight: 1.16, fallbackMax: 20 },
        "figure-caption": { weight: 400, align: "left", lineHeight: 1.2, fallbackMax: 15 },
        "table-caption": { weight: 400, align: "left", lineHeight: 1.2, fallbackMax: 15 },
        caption: { weight: 400, align: "left", lineHeight: 1.2, fallbackMax: 15 },
        footnote: { weight: 400, align: "left", lineHeight: 1.1, fallbackMax: 13 },
        header: { weight: 400, align: "center", lineHeight: 1.05, fallbackMax: 12 },
        footer: { weight: 400, align: "center", lineHeight: 1.05, fallbackMax: 12 },
        "reference-heading": { weight: 700, align: "left", lineHeight: 1.1, fallbackMax: 19 },
        "reference-entry": { weight: 400, align: "left", lineHeight: 1.12, fallbackMax: 15 },
      };
      return profiles[role] || profiles.body;
    },

    applyOriginalTextStyle(pageDiv, node) {
      if (!node) return;
      const mode = String(this.pref("overlay.styleMode", "match") || "match");
      if (mode !== "match") {
        delete node.dataset.zftBaseFontSize;
        return;
      }
      const style = this.getOriginalTextStyle(pageDiv, node);
      if (!style || !style.fontSize) {
        delete node.dataset.zftBaseFontSize;
        return;
      }
      const scale = Math.max(0.55, Math.min(1.6, Number(this.pref("overlay.fontScale", 1)) || 1));
      const baseFontSize = Math.max(4.5, style.fontSize * scale);
      const role = node.dataset.role || "body";
      const semantic = this.semanticStyleProfile(role);
      node.dataset.zftBaseFontSize = String(baseFontSize);
      node.dataset.zftMatchedSpans = String(style.matchedSpans || 0);
      node.style.fontSize = `${baseFontSize}px`;
      node.style.fontFamily = this.makeCJKFontStack(style.fontFamily);
      const preserveWeight = Number(style.fontWeight) || 400;
      node.style.fontWeight = String(Math.max(preserveWeight, semantic.weight || 400));
      node.style.fontStyle = style.fontStyle || "normal";
      node.style.lineHeight = String(Math.max(0.95, Math.min(1.8, role === "body" ? (style.lineHeight || semantic.lineHeight) : (semantic.lineHeight || style.lineHeight || 1.16))));
      node.style.letterSpacing = `${Number.isFinite(style.letterSpacing) ? style.letterSpacing : 0}px`;
      let align = style.textAlign || semantic.align || "justify";
      if (["paper-title", "author", "affiliation", "header", "footer"].includes(role)) align = style.textAlign === "center" ? "center" : semantic.align;
      if (/caption$/.test(role)) align = "left";
      node.style.textAlign = align;
    },

    styleAndFitOverlayPage(pageDiv, layer) {
      if (!layer) return;
      for (const node of layer.querySelectorAll(".zft-overlay-block")) this.applyOriginalTextStyle(pageDiv, node);
      this.fitOverlayPage(layer);
    },

    renderTranslationOverlay(state) {
      if (!state?.layoutAvailable || state.displayMode !== "translation") return;
      const doc = this.getPDFInnerDocument(state);
      if (!doc) return;
      state.overlayDoc = doc;
      this.injectPDFOverlayStyle(doc);
      const groups = this.getLayoutTranslationGroups(state);
      const byPage = new Map();
      for (const group of groups.values()) {
        if (!group.translation) continue;
        if (!byPage.has(group.pageIndex)) byPage.set(group.pageIndex, []);
        byPage.get(group.pageIndex).push(group);
      }
      for (const [pageIndex, pageGroups] of byPage) {
        const pageView = this.getPDFPageView(state, pageIndex);
        const pageDiv = pageView?.div || doc.querySelector(`.page[data-page-number="${pageIndex + 1}"]`) || doc.getElementById(`pageContainer${pageIndex + 1}`);
        if (!pageDiv) continue;
        let layer = pageDiv.querySelector(":scope > .zft-page-translation-overlay");
        if (!layer) {
          layer = doc.createElement("div");
          layer.className = "zft-page-translation-overlay";
          pageDiv.append(layer);
        }
        layer.style.setProperty("--zft-overlay-opacity", String(Math.max(0.72, Math.min(1, Number(this.pref("overlay.opacity", 0.98)) || 0.98))));
        const rotation = pageView?.viewport?.rotation || 0;
        const nodes = [];
        for (const group of pageGroups) {
          const rawBBox = this.adaptGroupBBoxForProtectedLayout(group) || group.bbox;
          const [x0, y0, x1, y1] = this.transformNormalizedBBox(rawBBox, rotation);
          const node = doc.createElement("div");
          node.className = "zft-overlay-block";
          node.dataset.key = group.key;
          const semanticRole = group.semanticRole || "body";
          node.dataset.role = semanticRole;
          node.dataset.heading = (group.textLevel > 0 || /^heading-/.test(semanticRole) || semanticRole === "paper-title" || /-heading$/.test(semanticRole)) ? "true" : "false";
          node.dataset.caption = (group.caption || /caption$/.test(semanticRole)) ? "true" : "false";
          if (group.captionTargetType) node.dataset.captionType = String(group.captionTargetType);
          node.textContent = group.translation;
          node.title = group.translation;
          node.style.left = `${x0 / 10}%`;
          node.style.top = `${y0 / 10}%`;
          node.style.width = `${Math.max(0.1, (x1 - x0) / 10)}%`;
          node.style.height = `${Math.max(0.1, (y1 - y0) / 10)}%`;
          nodes.push(node);
        }
        layer.replaceChildren(...nodes);
        this.styleAndFitOverlayPage(pageDiv, layer);
        this.observeOverlayPageSize(state, pageDiv, layer);
      }
    },

    fitOverlayPage(layer) {
      if (!layer) return;
      const scale = Math.max(0.55, Math.min(1.6, Number(this.pref("overlay.fontScale", 1)) || 1));
      const styleMode = String(this.pref("overlay.styleMode", "match") || "match");
      for (const node of layer.querySelectorAll(".zft-overlay-block")) {
        const w = node.clientWidth, h = node.clientHeight;
        if (w < 3 || h < 3) continue;
        const heading = node.dataset.heading === "true";
        const role = node.dataset.role || "body";
        const semantic = this.semanticStyleProfile(role);
        const matchedBase = Number(node.dataset.zftBaseFontSize) || 0;
        // In style-matching mode, the original text size is a ceiling. Translation may shrink
        // when Chinese needs more room, but is never enlarged simply to fill the MinerU bbox.
        const caption = node.dataset.caption === "true";
        const geometric = h * (role === "paper-title" ? 0.72 : (heading ? 0.62 : (caption ? 0.42 : (role === "footnote" ? 0.36 : 0.5))));
        let hi = matchedBase > 0 && styleMode === "match"
          ? matchedBase
          : Math.max(5, Math.min(semantic.fallbackMax || 20, geometric) * scale);
        const minSize = Math.min(hi, Math.max(4.2, hi * 0.58));
        let lo = minSize;
        let best = minSize;
        node.style.fontSize = `${hi}px`;
        if (node.scrollHeight <= h + 1 && node.scrollWidth <= w + 1) {
          best = hi;
        } else {
          for (let i = 0; i < 8; i++) {
            const mid = (lo + hi) / 2;
            node.style.fontSize = `${mid}px`;
            if (node.scrollHeight <= h + 1 && node.scrollWidth <= w + 1) { best = mid; lo = mid; }
            else hi = mid;
          }
        }
        node.style.fontSize = `${best}px`;
        node.dataset.overflow = (node.scrollHeight > h + 2 || node.scrollWidth > w + 2) ? "true" : "false";
      }
    },

    observeOverlayPageSize(state, pageDiv, layer) {
      if (!pageDiv || !layer || typeof pageDiv.ownerDocument?.defaultView?.ResizeObserver !== "function") return;
      if (state.overlayResizeObservers.has(pageDiv)) return;
      const RO = pageDiv.ownerDocument.defaultView.ResizeObserver;
      const observer = new RO(() => this.styleAndFitOverlayPage(pageDiv, layer));
      observer.observe(pageDiv);
      state.overlayResizeObservers.set(pageDiv, observer);
    },

    scheduleOverlayRender(state) {
      if (!state || state.overlayScheduled) return;
      state.overlayScheduled = setTimeout(() => {
        state.overlayScheduled = null;
        try { this.renderTranslationOverlay(state); } catch (e) { this.log("overlay render failed", String(e)); }
      }, 120);
    },

    startOverlayLifecycle(state) {
      if (!state?.layoutAvailable) return;
      if (!state.overlayTimer) {
        state.overlayTimer = setInterval(() => {
          if (state.displayMode === "translation") this.renderTranslationOverlay(state);
        }, 1200);
      }
    },

    stopOverlayLifecycle(state) {
      if (!state) return;
      if (state.overlayTimer) clearInterval(state.overlayTimer);
      state.overlayTimer = null;
      if (state.overlayScheduled) clearTimeout(state.overlayScheduled);
      state.overlayScheduled = null;
      for (const observer of state.overlayResizeObservers?.values?.() || []) {
        try { observer.disconnect(); } catch (_) {}
      }
      state.overlayResizeObservers?.clear?.();
    },

    removeTranslationOverlays(state) {
      const docs = [state?.overlayDoc, this.getPDFInnerDocument(state)].filter(Boolean);
      for (const doc of new Set(docs)) {
        try { doc.querySelectorAll(".zft-page-translation-overlay").forEach((node) => node.remove()); } catch (_) {}
      }
    },

    async exportNote(reader) {
      const itemID = this.getAttachmentID(reader);
      const state = itemID ? this.states.get(itemID) : null;
      if (!state?.completed) throw new Error("请先完成全文翻译，再导出 Zotero 笔记。");
      const attachment = Zotero.Items.get(itemID);
      const parent = attachment.parentID ? Zotero.Items.get(attachment.parentID) : null;
      const note = new Zotero.Item("note");
      if (parent) note.parentID = parent.id;
      else note.libraryID = attachment.libraryID;
      const showSource = !!this.pref("showSource", true);
      const blocks = [`<h1>全文翻译</h1>`];
      let page = -1;
      for (const seg of state.segments) {
        if (seg.pageIndex !== page) {
          page = seg.pageIndex;
          blocks.push(`<h2>第 ${page + 1} 页</h2>`);
        }
        if (showSource) blocks.push(`<p style="color:#666">${escapeHTML(seg.source)}</p>`);
        blocks.push(`<p>${escapeHTML(state.translationByIndex.get(seg.index) || "")}</p>`);
      }
      note.setNote(blocks.join("\n"));
      await note.saveTx();
      this.notify("全文翻译", "已保存为 Zotero 笔记");
      return note;
    },

    async exportSnapshot(reader) {
      const itemID = this.getAttachmentID(reader);
      const state = itemID ? this.states.get(itemID) : null;
      if (!state?.completed) throw new Error("请先完成全文翻译，再导出翻译快照。");
      const attachment = Zotero.Items.get(itemID);
      const parent = attachment.parentID ? Zotero.Items.get(attachment.parentID) : null;
      const title = parent?.getField("title") || attachment.getField("title") || "全文翻译";
      const temp = await this.makeTempDir(`zft-snapshot-${attachment.key || attachment.id}`);
      const outPath = PathUtils.join(temp, `${stem(await attachment.getFilePathAsync())}-translation.html`);
      const showSource = !!this.pref("showSource", true);
      const body = state.segments.map((seg) => {
        const source = showSource ? `<div class="src">${escapeHTML(seg.source)}</div>` : "";
        const trans = `<div class="trans">${escapeHTML(state.translationByIndex.get(seg.index) || "")}</div>`;
        const href = `zotero://open-pdf/library/items/${attachment.key}?page=${seg.pageIndex + 1}`;
        return `<section><a class="page" href="${href}">第 ${seg.pageIndex + 1} 页</a>${source}${trans}</section>`;
      }).join("\n");
      const html = `<!doctype html><meta charset="utf-8"><title>${escapeHTML(title)} - 全文翻译</title><style>body{font:16px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#222}section{margin:0 0 22px;padding:0 0 18px;border-bottom:1px solid #ddd}.page{font-size:12px;color:#777}.src{color:#777;font-size:14px;margin:6px 0}.trans{white-space:pre-wrap}</style><h1>${escapeHTML(title)}</h1>${body}`;
      await IOUtils.writeUTF8(outPath, html);
      const snap = await Zotero.Attachments.importFromFile({ file: outPath, parentItemID: parent?.id, libraryID: attachment.libraryID, title: `${title} - 翻译快照` });
      this.notify("全文翻译", "已保存翻译快照");
      return snap;
    },

    async exportLayoutPDF(reader, engine, options = {}) {
      const itemID = this.getAttachmentID(reader);
      if (!itemID) throw new Error("无法识别当前附件。");
      const state = this.getState(reader);
      if (state.running) throw new Error("当前已有翻译任务运行中，请先等待或取消当前任务。");
      this.startTask(state, "all");
      engine = this.pref("cloud.thinClient", true) ? "zft-cloud" : this.normalizedLayoutEngine(engine);
      const forceRetranslate = !!options.forceRetranslate;
      state.cloudHistoryHit = false;
      state.taskStatusText = forceRetranslate
        ? `准备重新翻译 · ${this.layoutEngineLabel(engine)}…`
        : `准备生成固定版式 PDF · ${this.layoutEngineLabel(engine)}…`;
      state.taskProgress = 2;
      this.updateTaskHUD(state);
      try {
        const item = Zotero.Items.get(itemID);
        const path = await item.getFilePathAsync();
        if (!path) throw new Error("附件文件不存在。");
        if (engine === "zft-cloud" && this.pref("cloud.reuseHistory", true) && !forceRetranslate) {
          const localTranslation = await this.findReusableLocalTranslation(item);
          if (localTranslation?.id) {
            state.cloudHistoryHit = true;
            this.finishTask(state, "done", "已使用本地译文");
            if (this.pref("compare.openAfterTranslation", true)) {
              await this.openNativeSplitComparison(reader, item, localTranslation);
            }
            return localTranslation;
          }
        }
        const temp = await this.makeTempDir(`zft-pdf-${item.key || item.id}`);
        let outPath;
        this.setStatus(state, `固定版式 PDF：${this.layoutEngineLabel(engine)} 正在处理页面、图片、表格与公式…`, 10);
        if (engine === "doc2x") outPath = await this.runDoc2XPDF(item, path, temp, state);
        else if (engine === "zft-cloud") outPath = await this.runCloudPDF2ZH(item, path, temp, state, { forceRetranslate });
        else outPath = await this.runPDF2ZH(item, path, temp, state);
        this.throwIfCancelled(state);
        if (!outPath) throw new Error("翻译程序执行结束，但没有检测到输出 PDF。");
        this.setStatus(state, "固定版式 PDF：正在导入 Zotero…", 95);
        const attachment = await this.attachGeneratedPDF(item, outPath, engine);
        if (engine === "zft-cloud" && state.lastCompletedCloudJobID) {
          this.markCloudJobImported(state.lastCompletedCloudJobID, this.cloudClientItemRef(item));
          state.lastCompletedCloudJobID = null;
        }
        if (engine === "zft-cloud" && attachment?.id) this.rememberComparePair(item, attachment);
        this.finishTask(state, "done", forceRetranslate ? "重新翻译完成 · 已生成新译文 PDF" : (state.cloudHistoryHit ? "已复用历史译文 PDF" : "固定版式译文 PDF 已生成"));
        if (engine === "zft-cloud" && attachment?.id && this.pref("compare.openAfterTranslation", true)) {
          await this.openNativeSplitComparison(reader, item, attachment);
        } else if (this.pref("output.openPDF", true) && attachment?.id) {
          Zotero.Reader.open(attachment.id);
        }
        return attachment;
      } catch (e) {
        if (e?.name === "ZFTCancelled") {
          this.finishTask(state, "cancelled", "已取消固定版式 PDF 翻译");
        } else {
          state.taskErrorReport = `阶段：固定版式 PDF\n错误类型：${e?.name || "Error"}\n错误信息：${this.safeErrorMessage(e)}`;
          this.finishTask(state, "error", "固定版式 PDF 生成失败 · 点击“详情”");
        }
        throw e;
      }
    },

    stripCLIOptions(tokens, optionNames) {
      const names = new Set(optionNames || []);
      const out = [];
      for (let i = 0; i < tokens.length; i++) {
        const token = String(tokens[i]);
        const exact = names.has(token);
        const inline = [...names].some((name) => token.startsWith(`${name}=`));
        if (inline) continue;
        if (exact) { i++; continue; }
        out.push(token);
      }
      return out;
    },

    async sha256File(path) {
      try {
        const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
        file.initWithPath(path);
        const stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
        stream.init(file, 0x01, 0, 0);
        try {
          const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
          hash.init(Ci.nsICryptoHash.SHA256);
          hash.updateFromStream(stream, -1);
          const raw = hash.finish(false);
          return Array.from(raw, (ch) => ch.charCodeAt(0).toString(16).padStart(2, "0")).join("");
        } finally {
          try { stream.close(); } catch (_) {}
        }
      } catch (e) {
        // Fallback for environments where the XPCOM hashing service is unavailable.
        const bytes = await IOUtils.read(path);
        if (globalThis.crypto?.subtle) {
          const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
          return [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
        }
        throw new Error(`无法计算 PDF SHA-256：${this.safeErrorMessage(e)}`);
      }
    },

    cloudComparePairs() {
      const value = this.cloudJSONPref("cloud.comparePairs", {});
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    },

    rememberComparePair(sourceItem, translatedItem) {
      if (!sourceItem || !translatedItem) return;
      const pairs = this.cloudComparePairs();
      const sourceRef = this.cloudClientItemRef(sourceItem);
      const translatedRef = this.cloudClientItemRef(translatedItem);
      pairs[sourceRef] = { sourceRef, translatedRef, langOut: asString(this.pref("targetLanguage", "zh-CN")), updatedAt: Date.now() };
      pairs[translatedRef] = { sourceRef, translatedRef, langOut: asString(this.pref("targetLanguage", "zh-CN")), updatedAt: Date.now() };
      const keys = Object.keys(pairs);
      if (keys.length > 500) {
        keys.sort((a,b)=>(pairs[a]?.updatedAt||0)-(pairs[b]?.updatedAt||0)).slice(0, keys.length-500).forEach((k)=>delete pairs[k]);
      }
      this.setCloudJSONPref("cloud.comparePairs", pairs);
    },

    resolveItemRef(itemRef) {
      const m = /^(\d+):(.+)$/.exec(asString(itemRef));
      if (!m) return null;
      try { return Zotero.Items.getByLibraryAndKey(Number(m[1]), m[2]) || Zotero.Items.get(Number(m[2])) || null; }
      catch (_) { return null; }
    },

    findComparePair(item) {
      const ref = this.cloudClientItemRef(item);
      const pair = this.cloudComparePairs()[ref];
      if (!pair) return null;
      const source = this.resolveItemRef(pair.sourceRef);
      const translated = this.resolveItemRef(pair.translatedRef);
      if (!source || !translated) return null;
      return { source, translated };
    },

    async openComparisonForReader(reader) {
      const itemID = this.getAttachmentID(reader);
      const item = itemID ? Zotero.Items.get(itemID) : null;
      if (!item) throw new Error("无法识别当前 PDF 附件。");
      const pair = this.findComparePair(item);
      if (!pair) throw new Error("没有找到与当前 PDF 关联的原文/译文。");
      let sourceReader = reader;
      if (item.id !== pair.source.id) {
        sourceReader = await Zotero.Reader.open(pair.source.id, null, { allowDuplicate: false });
        try { await sourceReader?._initPromise; } catch (_) {}
      }
      return this.openNativeSplitComparison(sourceReader, pair.source, pair.translated);
    },

    async findReusableLocalTranslation(sourceItem) {
      if (!sourceItem) return null;
      const ref = this.cloudClientItemRef(sourceItem);
      const targetLang = asString(this.pref("targetLanguage", "zh-CN"));
      const meta = this.cloudComparePairs()[ref];
      if (meta?.sourceRef === ref && (!meta.langOut || asString(meta.langOut) === targetLang)) {
        const translated = this.resolveItemRef(meta.translatedRef);
        if (translated?.isAttachment?.()) {
          try {
            const path = await translated.getFilePathAsync();
            if (path && await IOUtils.exists(path)) return translated;
          } catch (_) {}
        }
      }

      // Recover an existing translated sibling even if it predates the persisted pair map.
      // This keeps repeated translations local after plugin upgrades or preference resets.
      try {
        const parent = sourceItem.parentID ? Zotero.Items.get(sourceItem.parentID) : null;
        const ids = parent?.getAttachments?.() || [];
        const configuredTag = asString(this.pref("output.tag", "#全文翻译")).trim();
        const candidates = ids
          .map((id) => Zotero.Items.get(id))
          .filter((item) => item?.id && item.id !== sourceItem.id && item.isAttachment?.());
        for (const item of candidates.reverse()) {
          const title = asString(item.getField?.("title") || "");
          let tagged = false;
          if (configuredTag) {
            try { tagged = (item.getTags?.() || []).some((x) => asString(x?.tag) === configuredTag); } catch (_) {}
          }
          if (!tagged && !/全文翻译|translation/i.test(title)) continue;
          const contentType = asString(item.attachmentContentType || item.getField?.("contentType") || "").toLowerCase();
          if (contentType && !contentType.includes("pdf")) continue;
          const path = await item.getFilePathAsync();
          if (!path || !(await IOUtils.exists(path))) continue;
          this.rememberComparePair(sourceItem, item);
          return item;
        }
      } catch (_) {}
      return null;
    },

    readerInternal(reader) {
      try {
        return reader?._internalReader || reader?._iframeWindow?.wrappedJSObject?._reader || reader?._iframeWindow?._reader || null;
      } catch (_) { return null; }
    },

    stopNativeCompareSession(state) {
      const session = state?.nativeCompare;
      if (!session) return;
      try { session.primaryContainer?.removeEventListener("scroll", session.onPrimaryScroll); } catch (_) {}
      try { session.secondaryContainer?.removeEventListener("scroll", session.onSecondaryScroll); } catch (_) {}
      for (const binding of session.userInputBindings || []) {
        try { binding.target?.removeEventListener?.(binding.type, binding.handler, binding.options); } catch (_) {}
      }
      session.userInputBindings = [];
      for (const binding of session.eventBusBindings || []) {
        try {
          if (typeof binding.bus?.off === "function") binding.bus.off(binding.type, binding.handler);
          else if (typeof binding.bus?._off === "function") binding.bus._off(binding.type, binding.handler);
        } catch (_) {}
      }
      session.eventBusBindings = [];
      try {
        const win = session.primaryView?._iframeWindow || session.primaryView?._iframe?.contentWindow;
        if (session.syncRaf && win?.cancelAnimationFrame) win.cancelAnimationFrame(session.syncRaf);
      } catch (_) {}
      session.syncRaf = 0;
      try { session.badge?.remove(); } catch (_) {}
      if (session.restoreSplit !== false) {
        try { session.internal?.disableSplitView?.(); } catch (_) {}
      }
      state.nativeCompare = null;
    },

    getPDFViewScrollSnapshot(view, pageNumberOverride = null) {
      try {
        const win = view?._iframeWindow || view?._iframe?.contentWindow;
        const app = win?.PDFViewerApplication;
        const container = win?.document?.getElementById("viewerContainer");
        if (!app?.pdfViewer || !container) return null;
        const maxPages = Math.max(1, Number(app.pagesCount || app.pdfDocument?.numPages || app.pdfViewer.pagesCount || 1));
        const scrollTop = Number(container.scrollTop || 0);
        const viewportHeight = Math.max(1, Number(container.clientHeight || 1));
        // Use the viewport centre as a stable geometric anchor. PDF.js's
        // currentPageNumber flips when visibility crosses an internal threshold; at
        // half-page positions that creates a discontinuity and makes the two panes
        // oscillate between adjacent pages. A centre anchor remains continuous.
        const anchorViewportRatio = 0.5;
        const anchorY = scrollTop + viewportHeight * anchorViewportRatio;

        let pageNumber = Number(pageNumberOverride || 0);
        let pageView = null;
        if (Number.isFinite(pageNumber) && pageNumber > 0) {
          pageNumber = Math.max(1, Math.min(maxPages, pageNumber));
          pageView = app.pdfViewer.getPageView?.(pageNumber - 1) || null;
        } else {
          let bestDistance = Infinity;
          for (let i = 0; i < maxPages; i++) {
            const pv = app.pdfViewer.getPageView?.(i);
            const div = pv?.div;
            if (!div) continue;
            const top = Number(div.offsetTop || 0);
            const height = Math.max(1, Number(div.clientHeight || div.offsetHeight || 1));
            const bottom = top + height;
            if (anchorY >= top && anchorY <= bottom) {
              pageNumber = i + 1;
              pageView = pv;
              break;
            }
            const distance = anchorY < top ? top - anchorY : anchorY - bottom;
            if (distance < bestDistance) {
              bestDistance = distance;
              pageNumber = i + 1;
              pageView = pv;
            }
          }
        }
        if (!pageView || !pageNumber) {
          pageNumber = Math.max(1, Math.min(maxPages, Number(app.pdfViewer.currentPageNumber || 1)));
          pageView = app.pdfViewer.getPageView?.(pageNumber - 1) || null;
        }
        const pageDiv = pageView?.div;
        if (!pageDiv) return null;
        const height = Math.max(1, Number(pageDiv.clientHeight || pageDiv.offsetHeight || 1));
        const top = Number(pageDiv.offsetTop || 0);
        // Do not clamp to [0, 1]. Small negative/>1 values are useful when the centre
        // anchor is inside the inter-page gap; preserving them avoids a jump exactly
        // when half of the next page becomes visible.
        const pageAnchorOffset = (anchorY - top) / height;
        const maxX = Math.max(1, Number(container.scrollWidth || 0) - Number(container.clientWidth || 0));
        return {
          pageIndex: pageNumber - 1,
          pageNumber,
          pageAnchorOffset,
          anchorViewportRatio,
          xRatio: Math.max(0, Math.min(1, Number(container.scrollLeft || 0) / maxX)),
        };
      } catch (_) { return null; }
    },

    applyPDFViewScrollSnapshot(view, snapshot) {
      if (!view || !snapshot) return;
      try {
        const win = view?._iframeWindow || view?._iframe?.contentWindow;
        const app = win?.PDFViewerApplication;
        const container = win?.document?.getElementById("viewerContainer");
        if (!app?.pdfViewer || !container) return;
        const maxPages = Math.max(1, Number(app.pagesCount || app.pdfDocument?.numPages || app.pdfViewer.pagesCount || 1));
        const requested = Number(snapshot.pageNumber || (Number(snapshot.pageIndex || 0) + 1));
        const pageNumber = Math.max(1, Math.min(maxPages, Number.isFinite(requested) ? requested : 1));
        const applyAnchor = () => {
          try {
            const pageView = app.pdfViewer.getPageView?.(pageNumber - 1);
            const pageDiv = pageView?.div;
            if (!pageDiv) return;
            const height = Math.max(1, Number(pageDiv.clientHeight || pageDiv.offsetHeight || 1));
            const top = Number(pageDiv.offsetTop || 0);
            const viewportHeight = Math.max(1, Number(container.clientHeight || 1));
            const anchorViewportRatio = Math.max(0, Math.min(1, Number(snapshot.anchorViewportRatio ?? 0.5)));
            const rawOffset = Number(snapshot.pageAnchorOffset ?? snapshot.pageOffset ?? 0);
            const pageAnchorOffset = Number.isFinite(rawOffset) ? rawOffset : 0;
            const targetTop = top + pageAnchorOffset * height - viewportHeight * anchorViewportRatio;
            const maxY = Math.max(0, Number(container.scrollHeight || 0) - viewportHeight);
            container.scrollTop = Math.max(0, Math.min(maxY, targetTop));
            const maxX = Math.max(0, Number(container.scrollWidth || 0) - Number(container.clientWidth || 0));
            container.scrollLeft = Math.max(0, Math.min(1, Number(snapshot.xRatio || 0))) * maxX;
          } catch (_) {}
        };
        // scrollPageIntoView only ensures that the target page is materialized. The
        // final position is then computed from the geometric centre anchor, not from
        // PDF.js currentPageNumber, so partial-page views stay continuous.
        const current = Number(app.pdfViewer.currentPageNumber || 1);
        if (current !== pageNumber) {
          try { app.pdfViewer.scrollPageIntoView({ pageNumber }); } catch (_) {}
        }
        applyAnchor();
        if (win?.requestAnimationFrame) {
          win.requestAnimationFrame(() => {
            applyAnchor();
            win.requestAnimationFrame(applyAnchor);
          });
        } else {
          setTimeout(applyAnchor, 32);
          setTimeout(applyAnchor, 80);
        }
      } catch (_) {}
    },

    bindNativeCompareScroll(state, session) {
      const enabled = () => !!this.pref("compare.syncScroll", true);
      session.activeSide = session.activeSide || "primary";
      session.lastUserActionAt = 0;
      session.pendingSyncSide = null;
      session.pendingPageNumber = null;
      session.syncRaf = 0;
      session.userInputBindings = [];
      session.eventBusBindings = [];

      const viewFor = (side) => side === "primary" ? session.primaryView : session.secondaryView;
      const otherSide = (side) => side === "primary" ? "secondary" : "primary";
      const runtimeFor = (side) => {
        const view = viewFor(side);
        const win = view?._iframeWindow || view?._iframe?.contentWindow;
        return {
          view,
          win,
          app: win?.PDFViewerApplication || null,
          container: win?.document?.getElementById("viewerContainer") || null,
        };
      };
      const markActive = (side) => {
        session.activeSide = side;
        session.lastUserActionAt = Date.now();
        try { session.internal._lastViewPrimary = side === "primary"; } catch (_) {}
      };
      const scheduleSync = (side, pageNumber = null, force = false) => {
        if (!enabled()) return;
        // Programmatic scrolling of the follower generates its own scroll/page events.
        // Only the side the user is actually operating is allowed to drive the pair.
        if (!force && session.activeSide !== side) return;
        session.pendingSyncSide = side;
        if (Number.isFinite(Number(pageNumber)) && Number(pageNumber) > 0) {
          session.pendingPageNumber = Number(pageNumber);
        }
        if (session.syncRaf) return;
        const rt = runtimeFor(side);
        const request = rt.win?.requestAnimationFrame?.bind(rt.win) || ((fn) => setTimeout(fn, 16));
        session.syncRaf = request(() => {
          session.syncRaf = 0;
          const fromSide = session.pendingSyncSide || side;
          const explicitPage = session.pendingPageNumber;
          session.pendingSyncSide = null;
          session.pendingPageNumber = null;
          if (!enabled() || session.activeSide !== fromSide) return;
          const from = runtimeFor(fromSide);
          const toSide = otherSide(fromSide);
          const to = runtimeFor(toSide);
          const snapshot = this.getPDFViewScrollSnapshot(from.view, explicitPage);
          if (!snapshot || !to.view) return;
          this.applyPDFViewScrollSnapshot(to.view, snapshot);
        });
      };

      const bindInput = (side, target, type, options = { capture: true, passive: true }) => {
        if (!target?.addEventListener) return;
        const handler = () => markActive(side);
        try { target.addEventListener(type, handler, options); }
        catch (_) { try { target.addEventListener(type, handler, true); } catch (_) { return; } }
        session.userInputBindings.push({ target, type, handler, options });
      };

      for (const side of ["primary", "secondary"]) {
        const rt = runtimeFor(side);
        bindInput(side, rt.win, "wheel");
        bindInput(side, rt.win, "pointerdown");
        bindInput(side, rt.win, "touchstart");
        bindInput(side, rt.win, "keydown", { capture: true });
        bindInput(side, rt.win, "mousedown");
        const bus = rt.app?.eventBus;
        if (bus) {
          const pageHandler = () => {
            const lastViewSide = session.internal?._lastViewPrimary === false ? "secondary" : "primary";
            // pagechanging also fires during ordinary continuous scrolling. Forcing its
            // page number into the sync snapshot is exactly what makes half-page views
            // jump to the adjacent page. Use the event only as a wake-up signal and let
            // the geometric centre anchor determine the logical page.
            if (lastViewSide === side) markActive(side);
            scheduleSync(side);
          };
          try {
            if (typeof bus.on === "function") bus.on("pagechanging", pageHandler);
            else if (typeof bus._on === "function") bus._on("pagechanging", pageHandler);
            session.eventBusBindings.push({ bus, type: "pagechanging", handler: pageHandler });
          } catch (_) {}
        }
      }

      session.onPrimaryScroll = () => scheduleSync("primary");
      session.onSecondaryScroll = () => scheduleSync("secondary");
      session.primaryContainer?.addEventListener("scroll", session.onPrimaryScroll, { passive: true });
      session.secondaryContainer?.addEventListener("scroll", session.onSecondaryScroll, { passive: true });
    },

    ensureNativeCompareBadge(state, session) {
      const doc = this.refreshStateDocument(state);
      if (!doc) return null;
      try { doc.getElementById(COMPARE_BADGE_ID)?.remove(); } catch (_) {}
      const badge = doc.createElement("div");
      badge.id = COMPARE_BADGE_ID;
      const label = doc.createElement("label");
      const check = doc.createElement("input");
      check.type = "checkbox";
      check.checked = !!this.pref("compare.syncScroll", true);
      check.addEventListener("change", () => this.setPref("compare.syncScroll", !!check.checked));
      label.append(check, doc.createTextNode("联动"));
      const retranslate = doc.createElement("button");
      retranslate.type = "button";
      retranslate.textContent = "重译";
      retranslate.title = "忽略历史结果并重新翻译";
      retranslate.addEventListener("click", () => this.exportLayoutPDF(state.reader, "zft-cloud", { forceRetranslate: true }).catch((e) => this.reportError(e, state.reader)));
      const close = doc.createElement("button");
      close.type = "button";
      close.textContent = "×";
      close.title = "关闭译文对照";
      close.addEventListener("click", () => this.stopNativeCompareSession(state));
      badge.append(label, retranslate, close);
      doc.body.append(badge);
      session.badge = badge;
      return badge;
    },

    async openNativeSplitComparison(reader, sourceItem, translatedItem) {
      if (!reader || !sourceItem?.id || !translatedItem?.id) throw new Error("原文或译文附件不可用。");
      const state = this.getState(reader, this.getReaderDoc(reader));
      this.stopNativeCompareSession(state);
      try { this.closeCloudPDFPanel(state); } catch (_) {}
      try { this.closeTranslationPanel(state, { remove: true }); } catch (_) {}

      const translatedPath = await translatedItem.getFilePathAsync();
      if (!translatedPath || !(await IOUtils.exists(translatedPath))) throw new Error("本地译文 PDF 不存在。");

      const internal = this.readerInternal(reader);
      if (!internal?.toggleVerticalSplit || !internal?._createView) {
        return this.openCloudPDFPanel(reader, sourceItem, translatedItem);
      }

      // Zotero's own Reader loads PDFs through a zotero://attachment/... URL.
      // Reproduce that exact data shape for the translated attachment instead of
      // injecting an ArrayBuffer/file:// URL, which can yield a blank right pane.
      const targetWin = reader?._iframeWindow || null;
      const translatedURL = `zotero://attachment/${Zotero.API.getLibraryPrefix(translatedItem.libraryID)}/items/${translatedItem.key}/`;
      const plainTranslatedData = {
        url: translatedURL,
        importedFromURL: translatedItem.attachmentLinkMode === Zotero.Attachments.LINK_MODE_IMPORTED_URL
          ? translatedItem.getField("url")
          : undefined,
      };
      let translatedData;
      try {
        translatedData = Components.utils.cloneInto(plainTranslatedData, targetWin, { cloneFunctions: false });
      } catch (e) {
        this.log("failed to clone translated Reader data", this.safeErrorMessage(e));
        return this.openCloudPDFPanel(reader, sourceItem, translatedItem);
      }

      // Enable Zotero's native split layout first. Zotero creates a second view for
      // the source PDF; replace that view explicitly with one backed by the translated
      // attachment. _createView() reads internal._data synchronously.
      try { internal.disableSplitView?.(); } catch (_) {}
      await Zotero.Promise.delay(30);
      try {
        internal.toggleVerticalSplit(true);
      } catch (e) {
        this.log("failed to enable native vertical split", this.safeErrorMessage(e));
        return this.openCloudPDFPanel(reader, sourceItem, translatedItem);
      }
      await Zotero.Promise.delay(30);

      const primaryView = internal._primaryView;
      try { internal._secondaryView?.destroy?.(); } catch (_) {}
      try { internal._secondaryViewContainer?.replaceChildren?.(); } catch (_) {}
      internal._secondaryView = null;

      const originalData = internal._data;
      try {
        internal._data = translatedData;
        internal._secondaryView = internal._createView(false);
      } catch (e) {
        this.log("failed to create translated secondary PDFView", this.safeErrorMessage(e));
      } finally {
        internal._data = originalData;
      }

      const secondaryView = internal._secondaryView;
      if (!secondaryView) {
        try { internal.disableSplitView?.(); } catch (_) {}
        return this.openCloudPDFPanel(reader, sourceItem, translatedItem);
      }

      try { await secondaryView.initializedPromise; } catch (_) {}
      const loaded = await this.secondaryPDFReady(secondaryView, 6500);
      if (!loaded) {
        this.log(`translated secondary PDF did not load: ${translatedURL}`);
        try { secondaryView.destroy?.(); } catch (_) {}
        try { internal.disableSplitView?.(); } catch (_) {}
        return this.openCloudPDFPanel(reader, sourceItem, translatedItem);
      }

      const primaryContainer = primaryView?._iframeWindow?.document?.getElementById("viewerContainer") || null;
      const secondaryContainer = secondaryView?._iframeWindow?.document?.getElementById("viewerContainer") || null;
      const session = {
        internal,
        primaryView,
        secondaryView,
        primaryContainer,
        secondaryContainer,
        syncing: false,
        badge: null,
        restoreSplit: true,
        translatedItemID: translatedItem.id,
      };
      state.nativeCompare = session;
      this.bindNativeCompareScroll(state, session);
      this.ensureNativeCompareBadge(state, session);
      const initial = this.getPDFViewScrollSnapshot(primaryView);
      if (initial) this.applyPDFViewScrollSnapshot(secondaryView, initial);
      return session;
    },

    cloudJSONPref(key, fallback) {
      try {
        const value = JSON.parse(asString(this.pref(key, "")) || "null");
        return value === null || value === undefined ? fallback : value;
      } catch (_) {
        return fallback;
      }
    },

    setCloudJSONPref(key, value) {
      this.setPref(key, JSON.stringify(value));
    },

    cloudClientID() {
      let id = asString(this.pref("cloud.clientID", "")).trim();
      if (id) return id;
      try { id = Services.uuid.generateUUID().toString().replace(/[{}]/g, ""); }
      catch (_) { id = `zotero-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`; }
      this.setPref("cloud.clientID", id);
      return id;
    },

    cloudClientItemRef(item) {
      if (!item) return "";
      return `${Number(item.libraryID) || 1}:${asString(item.key || item.id)}`;
    },

    cloudProviderSelection() {
      const mode = asString(this.pref("cloud.providerMode", "server-default"));
      if (mode !== "custom") return { mode: "server-default", providers: [], strategy: "balanced" };
      const ids = ["baidu", "tencent", "volcengine", "aliyun", "openai_compatible"]
        .filter((id) => !!this.pref(`cloud.provider.${id}`, id !== "openai_compatible"));
      const strategyRaw = asString(this.pref("cloud.providerStrategy", "balanced")).toLowerCase();
      const strategy = strategyRaw === "failover" ? "failover" : "balanced";
      return { mode: "custom", providers: ids, strategy };
    },

    cloudProviderSummary() {
      const selection = this.cloudProviderSelection();
      if (selection.mode === "server-default") return "服务器默认引擎池";
      const names = { baidu: "百度", tencent: "腾讯 TMT", volcengine: "火山机器翻译", aliyun: "阿里", openai_compatible: "OpenAI" };
      const label = selection.providers.map((id) => names[id] || id).join("+") || "未选择引擎";
      return `${label} · ${selection.strategy === "failover" ? "主备" : "负载均衡"}`;
    },

    openCloudConsole() {
      const base = this.cloudPDF2ZHBaseURL();
      if (!base) throw new Error("请先填写 ZFT Cloud 地址。");
      try { Zotero.launchURL(base); }
      catch (_) { try { Services.externalProtocolService.loadURI(Services.io.newURI(base)); } catch (_) {} }
    },

    cloudRequestTokens() {
      const value = this.cloudJSONPref("cloud.requestTokens", {});
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    },

    getOrCreateCloudRequestID(itemRef) {
      const tokens = this.cloudRequestTokens();
      if (tokens[itemRef]) return asString(tokens[itemRef]);
      let requestID;
      try { requestID = Services.uuid.generateUUID().toString().replace(/[{}]/g, ""); }
      catch (_) { requestID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`; }
      tokens[itemRef] = requestID;
      this.setCloudJSONPref("cloud.requestTokens", tokens);
      return requestID;
    },

    clearCloudRequestID(itemRef) {
      if (!itemRef) return;
      const tokens = this.cloudRequestTokens();
      if (Object.prototype.hasOwnProperty.call(tokens, itemRef)) {
        delete tokens[itemRef];
        this.setCloudJSONPref("cloud.requestTokens", tokens);
      }
    },

    cloudPendingJobs() {
      const value = this.cloudJSONPref("cloud.pendingJobs", {});
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    },

    rememberCloudJob(job, item, requestID) {
      const jobID = asString(job?.id || "").trim();
      if (!jobID) return;
      const itemRef = this.cloudClientItemRef(item);
      const pending = this.cloudPendingJobs();
      pending[jobID] = {
        jobID,
        itemRef,
        requestID: asString(requestID || job?.client_request_id || ""),
        status: asString(job?.status || "QUEUED"),
        stage: asString(job?.stage || "queued"),
        progress: Number(job?.progress) || 0,
        createdAt: Date.now(),
      };
      this.setCloudJSONPref("cloud.pendingJobs", pending);
      this.cloudJobs.set(jobID, { ...job, client_item_key: job?.client_item_key || itemRef });
    },

    updateRememberedCloudJob(job) {
      const jobID = asString(job?.id || "").trim();
      if (!jobID) return;
      const pending = this.cloudPendingJobs();
      const prev = pending[jobID] || {};
      pending[jobID] = {
        ...prev,
        jobID,
        itemRef: asString(job?.client_item_key || prev.itemRef || ""),
        requestID: asString(job?.client_request_id || prev.requestID || ""),
        status: asString(job?.status || prev.status || ""),
        stage: asString(job?.stage || prev.stage || ""),
        progress: Number(job?.progress) || 0,
      };
      this.setCloudJSONPref("cloud.pendingJobs", pending);
      this.cloudJobs.set(jobID, { ...(this.cloudJobs.get(jobID) || {}), ...job });
    },

    forgetCloudJob(jobID, itemRef = "") {
      const pending = this.cloudPendingJobs();
      const ref = itemRef || pending[jobID]?.itemRef || "";
      if (pending[jobID]) {
        delete pending[jobID];
        this.setCloudJSONPref("cloud.pendingJobs", pending);
      }
      if (ref) this.clearCloudRequestID(ref);
      this.cloudJobs.delete(jobID);
    },

    cloudImportedJobs() {
      const value = this.cloudJSONPref("cloud.importedJobs", []);
      return Array.isArray(value) ? value : [];
    },

    isCloudJobImported(jobID) {
      return this.cloudImportedJobs().includes(asString(jobID));
    },

    markCloudJobImported(jobID, itemRef = "") {
      jobID = asString(jobID).trim();
      if (!jobID) return;
      const items = this.cloudImportedJobs().filter((x) => x !== jobID);
      items.push(jobID);
      while (items.length > 250) items.shift();
      this.setCloudJSONPref("cloud.importedJobs", items);
      this.forgetCloudJob(jobID, itemRef);
    },

    async resolveCloudSourceItem(itemRef) {
      const value = asString(itemRef).trim();
      const match = /^(\d+):(.+)$/.exec(value);
      if (!match) return null;
      const libraryID = Number(match[1]);
      const key = match[2];
      try {
        const item = await Zotero.Items.getByLibraryAndKey(libraryID, key);
        return item || null;
      } catch (_) {
        return null;
      }
    },

    cloudStageLabel(stage) {
      const value = asString(stage || "").toLowerCase();
      const labels = {
        queued: "排队中", parsing: "解析 PDF", translating: "多引擎翻译",
        typesetting: "译文排版", rendering: "生成 PDF", finalizing: "整理结果",
        cancelling: "正在取消", cancelled: "已取消", completed: "已完成", failed: "失败",
      };
      if (labels[value]) return labels[value];
      if (/layout/.test(value)) return "页面布局分析";
      if (/paragraph/.test(value)) return "段落分析";
      if (/style|formula/.test(value)) return "样式与公式分析";
      if (/font/.test(value)) return "字体映射";
      if (/translate/.test(value)) return "多引擎翻译";
      if (/save|pdf|render|create/.test(value)) return "生成固定版式 PDF";
      return asString(stage || "处理中");
    },

    async importCompletedCloudJob(job, { notify = true } = {}) {
      const jobID = asString(job?.id || "").trim();
      if (!jobID || this.isCloudJobImported(jobID) || this.cloudImporting.has(jobID)) return null;
      this.cloudImporting.add(jobID);
      try {
      const pending = this.cloudPendingJobs();
      const itemRef = asString(job?.client_item_key || pending[jobID]?.itemRef || "");
      const item = await this.resolveCloudSourceItem(itemRef);
      if (!item?.isAttachment?.()) {
        if (notify) this.notify("ZFT Cloud", `任务 ${jobID.slice(0, 8)} 已完成，但找不到原 Zotero 附件，结果保留在云端控制台。`);
        return null;
      }
      const temp = await this.makeTempDir(`zft-recover-${item.key || item.id}`);
      const sourcePath = await item.getFilePathAsync();
      const outPath = PathUtils.join(temp, `${stem(sourcePath || job.filename || "document.pdf")}-translated-mono.pdf`);
      await this.downloadCloudPDF2ZHResult(jobID, outPath, null);
      const attachment = await this.attachGeneratedPDF(item, outPath, "zft-cloud");
      if (attachment?.id) this.rememberComparePair(item, attachment);
      this.markCloudJobImported(jobID, itemRef);
      if (notify) this.notify("ZFT Cloud", `${job.filename || "PDF"} 云端翻译完成，已自动添加到 Zotero。`);
      return attachment;
      } finally {
        this.cloudImporting.delete(jobID);
      }
    },

    async monitorRecoveredCloudJob(initialJob) {
      const jobID = asString(initialJob?.id || "").trim();
      if (!jobID || this.cloudMonitors.has(jobID)) return;
      this.cloudMonitors.add(jobID);
      try {
        let job = initialJob;
        const pollMs = Math.max(800, Number(this.pref("cloud.pollMs", 1200)) || 1200);
        while (true) {
          const status = asString(job?.status || "").toUpperCase();
          this.updateRememberedCloudJob(job);
          if (status === "COMPLETED") {
            if (this.pref("cloud.autoImportCompleted", true)) await this.importCompletedCloudJob(job, { notify: true });
            return;
          }
          if (status === "FAILED" || status === "CANCELLED") {
            const pending = this.cloudPendingJobs();
            this.forgetCloudJob(jobID, job?.client_item_key || pending[jobID]?.itemRef || "");
            return;
          }
          await Zotero.Promise.delay(pollMs);
          job = await this.cloudPDF2ZHJSON("GET", `/api/v1/jobs/${encodeURIComponent(jobID)}`, null);
        }
      } catch (e) {
        this.log("recovered cloud monitor stopped", jobID, this.safeErrorMessage(e));
      } finally {
        this.cloudMonitors.delete(jobID);
      }
    },

    async restoreCloudJobs({ notify = false } = {}) {
      if (!this.pref("cloud.autoRestore", true) && !notify) return { total: 0, active: 0 };
      const base = this.cloudPDF2ZHBaseURL();
      const key = asString(this.pref("cloud.apiKey", "")).trim();
      if (!base || !key) return { total: 0, active: 0 };
      const clientID = this.cloudClientID();
      const limit = Math.max(10, Math.min(200, Number(this.pref("cloud.recoveryLookback", 100)) || 100));
      const list = await this.cloudPDF2ZHJSON("GET", `/api/v1/jobs?limit=${limit}&client_id=${encodeURIComponent(clientID)}`, null);
      const jobs = Array.isArray(list?.items) ? list.items : [];
      let active = 0, completed = 0;
      for (const job of jobs) {
        const status = asString(job?.status || "").toUpperCase();
        this.cloudJobs.set(job.id, job);
        if (!["COMPLETED", "FAILED", "CANCELLED"].includes(status)) {
          active++;
          this.updateRememberedCloudJob(job);
          this.monitorRecoveredCloudJob(job).catch((e) => this.log("monitor restore", this.safeErrorMessage(e)));
        } else if (status === "COMPLETED" && !this.isCloudJobImported(job.id)) {
          completed++;
          if (this.pref("cloud.autoImportCompleted", true)) {
            this.importCompletedCloudJob(job, { notify: true }).catch((e) => this.log("auto import recovered job", this.safeErrorMessage(e)));
          }
        }
      }
      if (notify) this.notify("ZFT Cloud", `已检查 ${jobs.length} 个云端任务：${active} 个进行中，${completed} 个待导入。`);
      return { total: jobs.length, active, completed };
    },

    cloudPDF2ZHBaseURL() {
      return asString(this.pref("cloud.baseURL", this.pref("pdf2zh.cloud.baseURL", ""))).trim().replace(/\/+$/, "");
    },

    cloudPDF2ZHHeaders() {
      const key = asString(this.pref("cloud.apiKey", this.pref("pdf2zh.cloud.apiKey", ""))).trim();
      return key ? { "X-API-Key": key } : {};
    },

    async testCloudPDF2ZH() {
      const base = this.cloudPDF2ZHBaseURL();
      if (!base) throw new Error("请先填写 ZFT Cloud 服务器地址。");
      const started = Date.now();
      let healthRes;
      try {
        healthRes = await Zotero.HTTP.request("GET", `${base}/health`, { responseType: "json", timeout: 15000, successCodes: false });
      } catch (e) {
        throw new Error(`无法连接 ZFT Cloud：${this.safeErrorMessage(e)}`);
      }
      const hs = Number(healthRes?.status) || 0;
      const health = healthRes?.response && typeof healthRes.response === "object" ? healthRes.response : (() => { try { return JSON.parse(healthRes?.responseText || "{}"); } catch (_) { return {}; } })();
      if (hs < 200 || hs >= 300 || !health?.ok) throw new Error(`ZFT Cloud 健康检查失败：HTTP ${hs || "?"}`);
      const key = asString(this.pref("cloud.apiKey", this.pref("pdf2zh.cloud.apiKey", ""))).trim();
      if (!key) throw new Error("服务器可访问，但尚未填写 ZFT Cloud API Key。");
      const statusRes = await Zotero.HTTP.request("GET", `${base}/api/v1/system/status`, { headers: { "X-API-Key": key }, responseType: "json", timeout: 15000, successCodes: false });
      const ss = Number(statusRes?.status) || 0;
      const system = statusRes?.response && typeof statusRes.response === "object" ? statusRes.response : (() => { try { return JSON.parse(statusRes?.responseText || "{}"); } catch (_) { return {}; } })();
      if (ss < 200 || ss >= 300) throw new Error(`ZFT Cloud API Key 验证失败：HTTP ${ss || "?"}`);
      let providers = [], runtime = null;
      try {
        const providerRes = await Zotero.HTTP.request("GET", `${base}/api/v1/providers`, { headers: { "X-API-Key": key }, responseType: "json", timeout: 15000, successCodes: false });
        if (Number(providerRes?.status) >= 200 && Number(providerRes?.status) < 300) providers = Array.isArray(providerRes.response) ? providerRes.response : JSON.parse(providerRes.responseText || "[]");
      } catch (_) {}
      try {
        const runtimeRes = await Zotero.HTTP.request("GET", `${base}/api/v1/system/runtime`, { headers: { "X-API-Key": key }, responseType: "json", timeout: 15000, successCodes: false });
        if (Number(runtimeRes?.status) >= 200 && Number(runtimeRes?.status) < 300) runtime = runtimeRes.response && typeof runtimeRes.response === "object" ? runtimeRes.response : JSON.parse(runtimeRes.responseText || "null");
      } catch (_) {}
      return { ...health, system, providers, runtime, elapsedMs: Date.now() - started };
    },

    async uploadCloudPDF2ZHJob(item, path, filename, state = null, options = {}) {
      const base = this.cloudPDF2ZHBaseURL();
      const key = asString(this.pref("cloud.apiKey", this.pref("pdf2zh.cloud.apiKey", ""))).trim();
      if (!base) throw new Error("请在设置 → 版式PDF 中填写 ZFT Cloud 服务器地址。");
      if (!key) throw new Error("请在设置 → 版式PDF 中填写 ZFT Cloud API Key。");
      const langIn = this.toPDF2ZHLang(this.pref("sourceLanguage", "auto"));
      const langOut = this.toPDF2ZHLang(this.pref("targetLanguage", "zh-CN"));
      const forceRetranslate = !!options.forceRetranslate;
      if (this.pref("cloud.reuseHistory", true) && !forceRetranslate) {
        if (state) this.setStatus(state, "ZFT Cloud：检查历史翻译…", 7);
        const sha256 = await this.sha256File(path);
        const lookup = await this.cloudPDF2ZHJSON("POST", "/api/v1/jobs/lookup", state, {
          source_sha256: sha256, lang_in: langIn, lang_out: langOut, pages: null, output_mode: "mono",
        });
        if (lookup?.found && lookup?.job?.id) {
          if (state) {
            state.cloudHistoryHit = true;
            this.setStatus(state, "ZFT Cloud：命中历史译文 · 跳过上传和翻译", 90);
          }
          return lookup.job;
        }
      }
      const bytes = await IOUtils.read(path);
      return new Promise((resolve, reject) => {
        let xhr;
        try {
          xhr = new XMLHttpRequest({ mozAnon: true });
          xhr.mozBackgroundRequest = true;
          xhr.open("POST", `${base}/api/v1/jobs`, true);
          xhr.timeout = Math.max(30000, Number(this.pref("cloud.uploadTimeoutMs", 300000)) || 300000);
          xhr.responseType = "text";
          xhr.setRequestHeader("X-API-Key", key);
          const win = Zotero.getMainWindow?.() || globalThis;
          const FormDataCtor = win.FormData || FormData;
          const BlobCtor = win.Blob || Blob;
          const form = new FormDataCtor();
          form.append("file", new BlobCtor([bytes], { type: "application/pdf" }), filename || "document.pdf");
          form.append("lang_in", langIn);
          form.append("lang_out", langOut);
          form.append("output_mode", "mono");
          const clientID = this.cloudClientID();
          const itemRef = this.cloudClientItemRef(item);
          if (forceRetranslate) this.clearCloudRequestID(itemRef);
          const requestID = this.getOrCreateCloudRequestID(itemRef);
          form.append("client_id", clientID);
          form.append("client_request_id", requestID);
          form.append("client_item_key", itemRef);
          const selection = this.cloudProviderSelection();
          if (selection.mode === "custom") {
            if (!selection.providers.length) throw new Error("自定义云端引擎模式下至少选择一个翻译引擎。");
            form.append("providers", selection.providers.join(","));
            form.append("provider_strategy", selection.strategy);
          }
          if (state) state.currentAbort = () => { try { xhr.abort(); } catch (_) {} };
          xhr.upload.onprogress = (event) => {
            if (!state || !event.lengthComputable) return;
            const pct = Math.round((event.loaded / event.total) * 100);
            this.setStatus(state, `ZFT Cloud：上传 PDF ${pct}%`, Math.min(18, 5 + Math.round(pct * 0.13)));
          };
          const fail = (message) => {
            if (state?.currentAbort) state.currentAbort = null;
            reject(new Error(message));
          };
          xhr.onload = () => {
            let status = 0, raw = "", data = null;
            try { status = Number(xhr.status) || 0; } catch (_) {}
            try { raw = asString(xhr.responseText || ""); } catch (_) {}
            try { data = JSON.parse(raw || "{}"); } catch (_) { data = null; }
            if (state?.currentAbort) state.currentAbort = null;
            if (status >= 200 && status < 300 && data?.id) {
              this.rememberCloudJob(data, item, requestID);
              resolve(data);
            } else reject(new Error(`ZFT Cloud 任务创建失败：HTTP ${status || "?"} ${asString(data?.detail || raw).slice(0, 500)}`));
          };
          xhr.onerror = () => fail("ZFT Cloud 上传网络错误");
          xhr.ontimeout = () => fail("ZFT Cloud 上传超时");
          xhr.onabort = () => fail("ZFT Cloud 上传已取消");
          xhr.send(form);
        } catch (e) {
          reject(new Error(`无法创建 ZFT Cloud 上传请求：${this.safeErrorMessage(e)}`));
        }
      });
    },

    async cloudPDF2ZHJSON(method, path, state = null, body = null) {
      const base = this.cloudPDF2ZHBaseURL();
      if (!base) throw new Error("ZFT Cloud 服务器地址为空。");
      let aborter = null;
      const options = { headers: this.cloudPDF2ZHHeaders(), responseType: "json", timeout: 30000, successCodes: false };
      if (body !== null && body !== undefined) {
        options.body = JSON.stringify(body);
        options.headers = { ...options.headers, "Content-Type": "application/json" };
      }
      if (state) options.cancellerReceiver = (cancel) => {
        aborter = () => { try { cancel(); } catch (_) {} };
        state.currentAbort = aborter;
      };
      try {
        const res = await Zotero.HTTP.request(method, `${base}${path}`, options);
        const status = Number(res?.status) || 0;
        const data = res?.response && typeof res.response === "object" ? res.response : (() => { try { return JSON.parse(res?.responseText || "{}"); } catch (_) { return {}; } })();
        if (status < 200 || status >= 300) throw new Error(`HTTP ${status || "?"} ${asString(data?.detail || data?.error_message || "")}`.trim());
        return data;
      } finally {
        if (state?.currentAbort === aborter) state.currentAbort = null;
      }
    },

    async cancelCloudPDF2ZHJob(state) {
      const jobID = asString(state?.remoteLayoutJobID || "").trim();
      if (!jobID) return;
      try {
        const job = await this.cloudPDF2ZHJSON("DELETE", `/api/v1/jobs/${encodeURIComponent(jobID)}`, null);
        this.updateRememberedCloudJob(job);
        const status = asString(job?.status || "").toUpperCase();
        if (status === "CANCELLED" || status === "FAILED") {
          const pending = this.cloudPendingJobs();
          this.forgetCloudJob(jobID, job?.client_item_key || pending[jobID]?.itemRef || "");
        }
      } finally {
        state.remoteLayoutJobID = null;
      }
    },

    async downloadCloudPDF2ZHResult(jobID, outPath, state = null) {
      const base = this.cloudPDF2ZHBaseURL();
      let aborter = null;
      const options = {
        headers: this.cloudPDF2ZHHeaders(),
        responseType: "arraybuffer",
        timeout: Math.max(60000, Number(this.pref("cloud.downloadTimeoutMs", 300000)) || 300000),
        successCodes: false,
      };
      if (state) options.cancellerReceiver = (cancel) => {
        aborter = () => { try { cancel(); } catch (_) {} };
        state.currentAbort = aborter;
      };
      try {
        const res = await Zotero.HTTP.request("GET", `${base}/api/v1/jobs/${encodeURIComponent(jobID)}/result/mono`, options);
        const status = Number(res?.status) || 0;
        if (status < 200 || status >= 300) throw new Error(`下载固定版式 PDF 失败：HTTP ${status || "?"}`);
        const buf = res?.response;
        if (!buf) throw new Error("ZFT Cloud 返回的 PDF 内容为空。");
        await IOUtils.write(outPath, new Uint8Array(buf));
        return outPath;
      } finally {
        if (state?.currentAbort === aborter) state.currentAbort = null;
      }
    },

    async runCloudPDF2ZH(item, path, outDir, state = null, options = {}) {
      const health = await this.testCloudPDF2ZH();
      const provider = asString(health?.system?.translator_provider || "");
      if (state) this.setStatus(state, `ZFT Cloud：连接正常 · BabelDOC · ${provider || "translator"}`, 5);
      this.throwIfCancelled(state);
      const created = await this.uploadCloudPDF2ZHJob(item, path, basename(path), state, options);
      const jobID = asString(created?.id || "").trim();
      if (!jobID) throw new Error("ZFT Cloud 没有返回任务 ID。");
      if (state) state.remoteLayoutJobID = jobID;
      const pollMs = Math.max(500, Number(this.pref("cloud.pollMs", 1200)) || 1200);
      try {
        while (true) {
          this.throwIfCancelled(state);
          const job = await this.cloudPDF2ZHJSON("GET", `/api/v1/jobs/${encodeURIComponent(jobID)}`, state);
          const status = asString(job?.status || "").toUpperCase();
          const remoteProgress = Math.max(0, Math.min(100, Number(job?.progress) || 0));
          const localProgress = 20 + Math.round(remoteProgress * 0.72);
          this.updateRememberedCloudJob(job);
          const engines = Array.isArray(job?.provider_ids) && job.provider_ids.length ? `${job.provider_ids.length} 引擎` : asString(job?.provider || "");
          if (state) this.setStatus(state, `ZFT Cloud：${this.cloudStageLabel(job?.stage || status)}${engines ? ` · ${engines}` : ""} · ${Math.round(remoteProgress)}%`, Math.min(92, localProgress));
          if (status === "COMPLETED") break;
          if (status === "CANCELLED") {
            const e = new Error("ZFT Cloud 固定版式任务已取消");
            e.name = "ZFTCancelled";
            throw e;
          }
          if (status === "FAILED") {
            const remoteError = asString(job?.error_message || job?.error_code || "未知错误");
            if (/asyncio\.exceptions\.CancelledError|BabelDOC internal CancelledError/i.test(remoteError)) {
              throw new Error("ZFT Cloud：BabelDOC 内部任务中断；服务器会自动重试一次。若仍失败，请重新提交任务。");
            }
            throw new Error(`ZFT Cloud 失败：${remoteError}`);
          }
          await Zotero.Promise.delay(pollMs);
        }
        this.throwIfCancelled(state);
        if (state) this.setStatus(state, "ZFT Cloud：下载 BabelDOC 固定版式 PDF…", 94);
        const outPath = PathUtils.join(outDir, `${stem(path)}-translated-mono.pdf`);
        await this.downloadCloudPDF2ZHResult(jobID, outPath, state);
        if (state) {
          state.remoteLayoutJobID = null;
          state.lastCompletedCloudJobID = jobID;
        }
        return outPath;
      } catch (e) {
        if (state?.cancelRequested) {
          try { await this.cancelCloudPDF2ZHJob(state); } catch (_) {}
          const cancelled = new Error("ZFT Cloud 固定版式任务已取消");
          cancelled.name = "ZFTCancelled";
          throw cancelled;
        }
        throw e;
      }
    },

    async runPDF2ZH(item, path, outDir, state = null) {
      const command = asString(this.pref("pdf2zh.command", "pdf2zh_next"));
      const args = [path, "--output", outDir, "--lang-in", this.toPDF2ZHLang(this.pref("sourceLanguage", "auto")), "--lang-out", this.toPDF2ZHLang(this.pref("targetLanguage", "zh-CN"))];
      const serviceArg = asString(this.pref("pdf2zh.serviceArg", "")).trim();
      if (serviceArg) args.push(...splitArgs(serviceArg));
      let extra = splitArgs(this.pref("pdf2zh.extraArgs", "--no-dual --enhance-compatibility --no-auto-extract-glossary"));
      extra = this.stripCLIOptions(extra, ["--qps", "--pool-max-workers"]);
      args.push(...extra);
      if (!args.includes("--no-dual")) args.push("--no-dual");
      const rate = this.getEffectiveRateSettings();
      if (rate.enabled) {
        // pdf2zh_next officially supports QPS + pool-max-workers. Keep both conservative.
        const pdfQPS = Math.max(1, Math.floor(rate.qps));
        const workers = Math.max(1, Math.min(rate.maxConcurrent, pdfQPS));
        args.push("--qps", String(pdfQPS), "--pool-max-workers", String(workers));
        if (state) this.setStatus(state, `pdf2zh_next：固定版式翻译 · ${pdfQPS} QPS · ${workers} workers`, 15);
      }
      this.notify("全文翻译", "正在调用 pdf2zh_next 生成固定版式译文 PDF…");
      await this.runProcess(command, args, state);
      const mono = await this.findFileRecursive(outDir, (p) => /-mono\.pdf$/i.test(p));
      const dual = await this.findFileRecursive(outDir, (p) => /-dual\.pdf$/i.test(p));
      return mono || dual || (await this.findFileRecursive(outDir, (p) => /\.pdf$/i.test(p)));
    },

    async runDoc2XPDF(item, path, outDir, state = null) {
      const command = asString(this.pref("doc2x.command", "doc2x"));
      const lang = this.toDoc2XLanguage(this.pref("targetLanguage", "zh-CN"));
      const args = ["translate", path, "--target-language", lang, "--translate-type", "pdf", "--out", outDir, "--overwrite"];
      const model = asString(this.pref("doc2x.targetModel", "")).trim();
      const termID = asString(this.pref("doc2x.termID", "")).trim();
      if (model) args.push("--target-model", model);
      if (termID) args.push("--term-id", termID);
      if (this.pref("doc2x.contextual", true)) args.push("--contextual-translation");
      if (this.pref("ignoreReferences", true)) args.push("--ignore-translate-types", "reference");
      this.notify("全文翻译", "正在调用 Doc2X 生成版式保真 PDF…");
      await this.runProcess(command, args, state);
      return this.findFileRecursive(outDir, (p) => /\.pdf$/i.test(p));
    },

    async attachGeneratedPDF(sourceItem, filePath, engine) {
      const parentID = sourceItem.parentID || undefined;
      const parent = parentID ? Zotero.Items.get(parentID) : null;
      const titleBase = parent?.getField("title") || sourceItem.getField("title") || stem(filePath);
      const title = `${titleBase} - 全文翻译 (${this.layoutEngineLabel(engine)})`;
      let attachment;
      if (asString(this.pref("output.importMode", "import")) === "link" && Zotero.Attachments.linkFromFile) {
        attachment = await Zotero.Attachments.linkFromFile({
          file: filePath,
          parentItemID: parentID,
          libraryID: sourceItem.libraryID,
          title,
        });
      } else {
        attachment = await Zotero.Attachments.importFromFile({
          file: filePath,
          parentItemID: parentID,
          libraryID: sourceItem.libraryID,
          title,
        });
      }
      if (attachment && this.pref("output.addTag", true)) {
        const tag = asString(this.pref("output.tag", "#全文翻译")).trim();
        if (tag) {
          attachment.addTag(tag);
          await attachment.saveTx();
        }
      }
      this.notify("全文翻译", "版式保真 PDF 已添加到 Zotero");
      return attachment;
    },

    toPDF2ZHLang(lang) {
      const s = asString(lang, "auto");
      if (s === "auto") return "en";
      if (/^zh/i.test(s)) return "zh-CN";
      return s.split("-")[0];
    },

    toDoc2XLanguage(lang) {
      const s = asString(lang, "zh-CN").toLowerCase();
      if (s.startsWith("zh")) return "zh";
      if (s.startsWith("pt-br")) return "pt-BR";
      return s.split("-")[0];
    },

    startPageSync(state) {
      if (!this.pref("syncReaderPage", true) || state.syncTimer) return;
      let last = -1;
      state.syncTimer = setInterval(() => {
        try {
          if (!state.panel || state.panel.hidden) return;
          const page = this.getCurrentPageIndex(state.reader);
          if (!Number.isInteger(page) || page < 0 || page === last) return;
          last = page;
          const target = state.body?.querySelector(`.zft-page[data-page-index="${page}"]`);
          target?.scrollIntoView({ block: "start", behavior: "smooth" });
        } catch (_) {}
      }, 700);
    },

    getCurrentPageIndex(reader) {
      const candidates = [
        reader?._internalReader?._primaryView?._view?._pageIndex,
        reader?._internalReader?._primaryView?.pageIndex,
        reader?._internalReader?._primaryView?._state?.pageIndex,
        reader?._reader?._primaryView?._view?._pageIndex,
        reader?._reader?._primaryView?.pageIndex,
        reader?._reader?._primaryView?._state?.pageIndex,
        reader?._iframeWindow?.wrappedJSObject?._reader?._primaryView?._view?._pageIndex,
        reader?._iframeWindow?.wrappedJSObject?._reader?._primaryView?.pageIndex,
      ];
      for (const v of candidates) {
        const n = Number(v);
        if (Number.isInteger(n) && n >= 0) return n;
      }
      return -1;
    },

    makeCacheKey(item, segments) {
      const config = [
        this.pref("parser", "zotero"),
        this.pref("translationProvider", "pdftranslate"),
        this.pref("pdftranslateService", ""),
        this.pref("targetLanguage", "zh-CN"),
        this.pref("gpt.model", ""),
        segments.map((s) => s.source).join("\u241E"),
      ].join("\u241F");
      return `${item.key || item.id}-${fnv1a(config)}`;
    },

    cacheDir() {
      return PathUtils.join(Zotero.Profile.dir, "zft-cache");
    },

    async loadTranslationCache(state) {
      if (!this.pref("cache.enabled", true) || !state.cacheKey) return null;
      try {
        const path = PathUtils.join(this.cacheDir(), `${state.cacheKey}.json`);
        if (!(await IOUtils.exists(path))) return null;
        return JSON.parse(await IOUtils.readUTF8(path));
      } catch (_) {
        return null;
      }
    },

    async saveTranslationCache(state) {
      if (!this.pref("cache.enabled", true) || !state.cacheKey) return;
      try {
        await IOUtils.makeDirectory(this.cacheDir(), { ignoreExisting: true });
        const translations = state.segments.map((_, i) => state.translationByIndex.get(i) || "");
        const path = PathUtils.join(this.cacheDir(), `${state.cacheKey}.json`);
        await IOUtils.writeUTF8(path, JSON.stringify({ version: 1, createdAt: Date.now(), translations }));
      } catch (e) {
        this.log("cache save failed", String(e));
      }
    },

    async putPresignedFileWithoutContentType(url, path, timeout = 180000, state = null) {
      const bytes = await IOUtils.read(path);
      // Uint8Array is deliberate: privileged XHR sends BufferSource as binary and does not
      // synthesize a form Content-Type. Do not call setRequestHeader("Content-Type", ...).
      const body = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      return new Promise((resolve, reject) => {
        let xhr;
        try {
          xhr = new XMLHttpRequest({ mozAnon: true });
          xhr.mozBackgroundRequest = true;
          xhr.open("PUT", url, true);
          xhr.timeout = timeout;
          xhr.responseType = "text";
          xhr.overrideMimeType("text/plain");
          if (state) state.currentAbort = () => { try { xhr.abort(); } catch (_) {} };
        } catch (e) {
          const err = new Error(`无法创建 OSS PUT 请求：${this.safeErrorMessage(e)}`);
          err.name = "ZFTUploadError";
          err.status = 0;
          reject(err);
          return;
        }

        const finishError = (message, status = 0, responseText = "") => {
          // Snapshot primitive values before the XHR/window can become a dead XPConnect object.
          const err = new Error(message);
          err.name = "ZFTUploadError";
          err.status = Number(status) || 0;
          err.responseText = asString(responseText).slice(0, 4000);
          err.requestContentType = "";
          if (state?.currentAbort) state.currentAbort = null;
          reject(err);
        };

        xhr.onloadend = () => {
          let status = 0;
          let responseText = "";
          try { status = Number(xhr.status) || 0; } catch (_) {}
          try { responseText = asString(xhr.responseText || xhr.response || ""); } catch (_) {}
          if ([200, 201, 204].includes(status)) {
            if (state?.currentAbort) state.currentAbort = null;
            resolve({ status });
            return;
          }
          finishError(`OSS PUT 返回 HTTP ${status || "未知"}`, status, responseText);
        };
        xhr.onerror = () => {
          let status = 0;
          let responseText = "";
          try { status = Number(xhr.status) || 0; } catch (_) {}
          try { responseText = asString(xhr.responseText || ""); } catch (_) {}
          finishError("OSS PUT 网络错误", status, responseText);
        };
        xhr.ontimeout = () => finishError(`OSS PUT 超时（${Math.round(timeout / 1000)} 秒）`);
        xhr.onabort = () => finishError("OSS PUT 已取消");

        try {
          // IMPORTANT: no setRequestHeader('Content-Type', ...).
          xhr.send(body);
        } catch (e) {
          finishError(`OSS PUT 发送失败：${this.safeErrorMessage(e)}`);
        }
      });
    },

    safeRead(obj, key, fallback = undefined) {
      try {
        const value = obj?.[key];
        return value === undefined || value === null ? fallback : value;
      } catch (_) {
        return fallback;
      }
    },

    safeErrorMessage(error) {
      const message = this.safeRead(error, "message", "");
      if (message) return asString(message);
      try { return asString(error, "未知错误"); } catch (_) { return "无法读取错误对象（dead object）"; }
    },

    snapshotNetworkError(error) {
      const snap = {
        name: asString(this.safeRead(error, "name", "Error")),
        message: this.safeErrorMessage(error),
        status: Number(this.safeRead(error, "status", 0)) || 0,
        responseStatus: Number(this.safeRead(error, "responseStatus", 0)) || 0,
        responseText: asString(this.safeRead(error, "responseText", "")),
      };
      const xhr = this.safeRead(error, "xmlhttp", null);
      if (xhr) {
        if (!snap.status) snap.status = Number(this.safeRead(xhr, "status", 0)) || 0;
        if (!snap.responseText) snap.responseText = asString(this.safeRead(xhr, "responseText", ""));
      }
      return snap;
    },

    async httpJSON(method, url, body, headers = {}, state = null) {
      let aborter = null;
      const options = {
        headers: { "Content-Type": "application/json", ...headers },
        responseType: "json",
      };
      if (state) {
        options.cancellerReceiver = (cancel) => {
          aborter = () => { try { cancel(); } catch (_) {} };
          state.currentAbort = aborter;
        };
      }
      if (body !== null && body !== undefined) options.body = JSON.stringify(body);
      try {
        const res = await Zotero.HTTP.request(method, url, options);
        if (res?.response && typeof res.response === "object") return res.response;
        const raw = res?.responseText || res?.response || "";
        return typeof raw === "string" ? JSON.parse(raw || "{}") : raw;
      } finally {
        if (state?.currentAbort === aborter) state.currentAbort = null;
      }
    },

    async runProcess(command, args, state = null) {
      let Subprocess;
      try {
        const mod = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
        Subprocess = mod.Subprocess || mod.default || mod;
      } catch (e) {
        throw new Error(`无法载入 Zotero 子进程模块：${e}`);
      }
      let proc;
      try {
        proc = await Subprocess.call({ command, arguments: args, stdout: "pipe", stderr: "pipe" });
        if (state) state.currentProcess = proc;
      } catch (e) {
        throw new Error(`无法启动命令 ${command}：${e?.message || e}`);
      }
      let stdout = "";
      let stderr = "";
      const readOut = (async () => {
        if (!proc.stdout?.readString) return;
        while (true) {
          const s = await proc.stdout.readString();
          if (!s) break;
          stdout += s;
        }
      })();
      const readErr = (async () => {
        if (!proc.stderr?.readString) return;
        while (true) {
          const s = await proc.stderr.readString();
          if (!s) break;
          stderr += s;
        }
      })();
      const result = await proc.wait();
      if (state?.currentProcess === proc) state.currentProcess = null;
      await Promise.allSettled([readOut, readErr]);
      this.throwIfCancelled(state);
      if (result.exitCode !== 0) {
        throw new Error(`${command} 退出码 ${result.exitCode}\n${stderr || stdout}`.slice(0, 4000));
      }
      return { stdout, stderr, exitCode: result.exitCode };
    },

    async makeTempDir(name) {
      const root = PathUtils.join(PathUtils.tempDir, name + "-" + Date.now());
      await IOUtils.makeDirectory(root, { ignoreExisting: true });
      return root;
    },

    async readUTF8(path) {
      if (IOUtils.readUTF8) return IOUtils.readUTF8(path);
      const bytes = await IOUtils.read(path);
      return new TextDecoder("utf-8").decode(bytes);
    },

    async findFileRecursive(root, predicate) {
      try {
        const children = await IOUtils.getChildren(root);
        for (const p of children) {
          const st = await IOUtils.stat(p);
          if (st.type === "directory") {
            const found = await this.findFileRecursive(p, predicate);
            if (found) return found;
          } else if (predicate(p)) {
            return p;
          }
        }
      } catch (_) {}
      return null;
    },

    async extractMinerUArtifactsFromZip(zipPath, outDir) {
      const { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
      const zip = Components.classes["@mozilla.org/libjar/zip-reader;1"].createInstance(Components.interfaces.nsIZipReader);
      zip.open(new FileUtils.File(zipPath));
      const found = { mdPath: null, contentPath: null, contentV2Path: null };
      try {
        const entries = zip.findEntries("*");
        while (entries.hasMore?.() || entries.hasMoreElements?.()) {
          const name = entries.getNext();
          if (!found.contentPath && /_content_list\.json$/i.test(name)) {
            found.contentPath = PathUtils.join(outDir, "content_list.json");
            zip.extract(name, new FileUtils.File(found.contentPath));
          } else if (!found.contentV2Path && /_content_list_v2\.json$/i.test(name)) {
            found.contentV2Path = PathUtils.join(outDir, "content_list_v2.json");
            zip.extract(name, new FileUtils.File(found.contentV2Path));
          } else if (!found.mdPath && /(?:^|\/)full\.md$/i.test(name)) {
            found.mdPath = PathUtils.join(outDir, "full.md");
            zip.extract(name, new FileUtils.File(found.mdPath));
          }
        }
        if (!found.mdPath) {
          const entries2 = zip.findEntries("*.md");
          if (entries2.hasMore?.() || entries2.hasMoreElements?.()) {
            const name = entries2.getNext();
            found.mdPath = PathUtils.join(outDir, "full.md");
            zip.extract(name, new FileUtils.File(found.mdPath));
          }
        }
        return found;
      } finally { zip.close(); }
    },

    async extractFullMarkdownFromZip(zipPath, outDir) {
      const { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
      const zip = Components.classes["@mozilla.org/libjar/zip-reader;1"].createInstance(Components.interfaces.nsIZipReader);
      const zipFile = new FileUtils.File(zipPath);
      zip.open(zipFile);
      try {
        let entry = null;
        for (const pattern of ["*full.md", "*.md", "*/**/*.md"]) {
          try {
            const entries = zip.findEntries(pattern);
            const has = entries?.hasMore?.() || entries?.hasMoreElements?.();
            if (has) {
              entry = entries.getNext();
              break;
            }
          } catch (_) {}
        }
        if (!entry) throw new Error("MinerU ZIP 中未找到 full.md。");
        const outputPath = PathUtils.join(outDir, "full.md");
        const outputFile = new FileUtils.File(outputPath);
        zip.extract(entry, outputFile);
        return outputPath;
      } finally {
        zip.close();
      }
    },

    notify(title, text) {
      try {
        const p = new Zotero.ProgressWindow();
        p.changeHeadline(title);
        p.addDescription(text);
        p.show();
        p.startCloseTimer(3500);
      } catch (_) {
        this.log(title, text);
      }
    },

    reportError(error, reader) {
      const message = this.safeErrorMessage(error) || "未知错误";
      const stage = asString(this.safeRead(error, "zftStage", ""));
      const name = asString(this.safeRead(error, "name", "Error"));
      const report = asString(this.safeRead(error, "zftReport", ""));
      try { Zotero.logError(new Error(`${name}: ${message}`)); } catch (_) {}
      const state = reader ? this.getState(reader) : null;
      if (state) {
        try {
          // An exception before translateReader's main try used to leave the HUD stuck in running state.
          if (/dead object/i.test(message) || /准备翻译/.test(state.taskStatusText || "")) {
            state.running = false;
            if (state.taskTimer) clearInterval(state.taskTimer);
            state.taskTimer = null;
          }
          const doc = this.refreshStateDocument(state);
          if (!state.panelDismissed) this.ensurePanel(state);
          state.taskErrorReport = asString(report || state.taskErrorReport || "").trim() || [
            `阶段：${stage || state.taskStatusText || "翻译"}`,
            `错误类型：${name}`,
            `错误信息：${message}`,
          ].join("\n");
          this.updateTaskHUD(state, "error");
          if (doc && this.safeDOMConnected(state.body)) {
            const box = doc.createElement("div");
            box.className = "zft-error";
            box.style.whiteSpace = "pre-wrap";
            box.textContent = state.taskErrorReport;
            state.body.prepend(box);
          }
        } catch (_) {}
      }
      this.notify("全文翻译失败", stage ? `${stage}失败；可点击任务卡“详情”查看诊断。` : message);
    },

    async getPDFTestContext() {
      const seen = new Set();
      const candidates = [];
      const active = this.getActiveReader();
      const activeID = active ? this.getAttachmentID(active) : null;
      if (activeID) candidates.push(Zotero.Items.get(activeID));
      try {
        const win = Zotero.getMainWindow?.();
        const selected = win?.ZoteroPane?.getSelectedItems?.() || [];
        for (const item of selected) {
          candidates.push(item);
          if (!item?.isAttachment?.() && item?.getAttachments) {
            for (const id of item.getAttachments()) candidates.push(Zotero.Items.get(id));
          }
        }
      } catch (_) {}
      for (const item of candidates) {
        if (!item?.isAttachment?.() || seen.has(item.id)) continue;
        seen.add(item.id);
        const path = await item.getFilePathAsync?.();
        if (!path || !/\.pdf$/i.test(path)) continue;
        return { item, path, reader: activeID === item.id ? active : null };
      }
      throw new Error("请先在 Zotero 中打开一篇 PDF，或在文献库中选中一个 PDF 附件，再执行解析测试。");
    },

    async testParserEnvironment(parser = null) {
      parser = asString(parser || this.pref("parser", "zotero"));
      const started = Date.now();
      if (parser === "zotero") {
        if (!Zotero.PDFWorker?.getFullText) throw new Error("当前 Zotero 未提供 PDFWorker.getFullText。 ");
        return { parser, ok: true, message: "Zotero PDFWorker 可用", elapsedMs: Date.now() - started };
      }
      if (parser === "mineru-local") {
        const command = asString(this.pref("mineru.command", "mineru"));
        const result = await this.runProcess(command, ["--version"]);
        const line = asString(result.stdout || result.stderr || "MinerU CLI 可执行").trim().split("\n")[0];
        return { parser, ok: true, message: line || "MinerU CLI 可执行", elapsedMs: Date.now() - started };
      }
      if (parser === "doc2x-md") {
        const command = asString(this.pref("doc2x.command", "doc2x"));
        const result = await this.runProcess(command, ["--version"]);
        const line = asString(result.stdout || result.stderr || "Doc2X CLI 可执行").trim().split("\n")[0];
        return { parser, ok: true, message: line || "Doc2X CLI 可执行", elapsedMs: Date.now() - started };
      }
      if (parser === "mineru-api") {
        const token = asString(this.pref("mineru.token", "")).trim();
        if (!token) throw new Error("MinerU Token 未填写。 ");
        const base = asString(this.pref("mineru.baseURL", "https://mineru.net")).replace(/\/$/, "");
        const testURL = `${base}/api/v4/extract-results/batch/__zft_connectivity_test__`;
        let response;
        try {
          response = await Zotero.HTTP.request("GET", testURL, {
            headers: { Authorization: `Bearer ${token}` },
            responseType: "text",
            successCodes: false,
            anon: true,
            timeout: 30000,
            errorDelayMax: 0,
          });
        } catch (e) {
          throw this.makeNetworkError("MinerU API 连通性测试", e, testURL, {
            hint: "检查 Base URL、网络和系统代理。",
          });
        }
        const status = Number(response?.status || 0);
        if (status === 401 || status === 403) {
          const e = new Error(`MinerU 鉴权失败：HTTP ${status}`);
          e.zftStage = "MinerU API 连通性测试";
          e.zftReport = this.buildNetworkDiagnostic(e.zftStage, e, testURL, {
            hint: "请求已到达 MinerU，但 Token 被拒绝。请重新生成或确认 Token 已启用。",
          });
          throw e;
        }
        if (status >= 500) throw new Error(`MinerU 服务异常：HTTP ${status}`);
        return { parser, ok: true, message: `MinerU API 可达 · HTTP ${status || "已连接"}`, elapsedMs: Date.now() - started };
      }
      throw new Error(`未知解析器：${parser}`);
    },

    async testParserWithCurrentPDF(parser = null, onProgress = null) {
      parser = asString(parser || this.pref("parser", "zotero"));
      const { item, path } = await this.getPDFTestContext();
      const started = Date.now();
      const state = {
        running: false,
        completed: false,
        taskHUD: null,
        taskStatusText: "准备解析测试…",
        taskProgress: 0,
        toolbarButtons: new Set(),
        cancelRequested: false,
        currentProcess: null,
        parserTestCallback: typeof onProgress === "function" ? onProgress : null,
      };
      const parsed = await this.parseDocumentWithParser(parser, item, path, state);
      const pages = Array.isArray(parsed?.pages) ? parsed.pages : [];
      const chars = pages.reduce((sum, p) => sum + asString(p?.text).length, 0);
      const blocks = Array.isArray(parsed?.layoutBlocks) ? parsed.layoutBlocks.length : 0;
      return {
        parser,
        ok: true,
        source: parsed?.source || parser,
        file: basename(path),
        pages: pages.length,
        layoutBlocks: blocks,
        chars,
        elapsedMs: Date.now() - started,
      };
    },

    getAvailableTranslateServices() {
      try {
        return Zotero.PDFTranslate?.api?.getServices?.() || [];
      } catch (_) {
        return [];
      }
    },
  };

  Zotero.ZoteroFulltextTranslator = Addon;
})();
