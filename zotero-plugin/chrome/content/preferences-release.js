(function () {
    const VERSION = "0.4.3";
    const apply = () => {
        try {
            const chips = document.querySelectorAll("#zft-prefpane .versionchips .chip");
            if (chips[0])
                chips[0].textContent = `插件 ${Zotero.ZoteroFulltextTranslator?.version || VERSION}`;
        }
        catch (_) { }
        try {
            if (globalThis.ZFT_Preferences && !ZFT_Preferences.__zft043StatusPatched) {
                const originalStatus = ZFT_Preferences.status;
                if (typeof originalStatus === "function") {
                    ZFT_Preferences.status = function (id, text, ...args) {
                        text = String(text ?? "").replace(/插件\s+0\.4\.2/g, `插件 ${VERSION}`);
                        return originalStatus.call(this, id, text, ...args);
                    };
                    ZFT_Preferences.__zft043StatusPatched = true;
                }
            }
        }
        catch (_) { }
    };
    if (document.readyState === "loading")
        document.addEventListener("DOMContentLoaded", apply, { once: true });
    else
        apply();
    setTimeout(apply, 80);
})();
