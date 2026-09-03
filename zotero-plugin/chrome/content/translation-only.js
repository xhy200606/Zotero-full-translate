(function () {
    const addon = Zotero.ZoteroFulltextTranslator;
    if (!addon || addon.__zftTranslationOnly043Patched)
        return;
    addon.__zftTranslationOnly043Patched = true;

    addon.openTranslationOnlyForReader = async function (reader) {
        const itemID = this.getAttachmentID(reader);
        const item = itemID ? Zotero.Items.get(itemID) : null;
        if (!item)
            throw new Error("无法识别当前 PDF 附件。");
        const pair = this.findComparePair(item);
        if (!pair)
            throw new Error("没有找到与当前 PDF 关联的译文。");

        const state = this.getState(reader, this.getReaderDoc(reader));
        let snapshot = null;
        try {
            if (state.nativeCompare) {
                const session = state.nativeCompare;
                const activeView = session.activeSide === "secondary" ? session.secondaryView : session.primaryView;
                snapshot = this.getPDFViewScrollSnapshot(activeView);
                this.stopNativeCompareSession(state, { clearRemembered: true });
            }
            else {
                snapshot = this.getPDFViewScrollSnapshot(this.readerInternal(reader)?._primaryView);
                if (this.safeDOMConnected(state.cloudPanel))
                    this.closeCloudPDFPanel(state);
                this.rememberCompareOpen(pair.source, pair.translated, false);
            }
        }
        catch (e) {
            this.log("translation-only cleanup failed", this.safeErrorMessage(e));
        }

        let translatedReader = reader;
        if (item.id !== pair.translated.id) {
            translatedReader = await Zotero.Reader.open(pair.translated.id, null, { allowDuplicate: false });
            try {
                await translatedReader?._initPromise;
            }
            catch (_) { }
        }
        if (snapshot && translatedReader) {
            const restorePosition = () => {
                try {
                    const view = this.readerInternal(translatedReader)?._primaryView;
                    if (view)
                        this.applyPDFViewScrollSnapshot(view, snapshot);
                }
                catch (_) { }
            };
            restorePosition();
            setTimeout(restorePosition, 80);
            setTimeout(restorePosition, 240);
        }
        return translatedReader;
    };

    addon.injectTranslationOnlyQuickAction = function (reader, doc) {
        const menu = doc?.querySelector?.(".zft-quick-menu");
        if (!menu)
            return;
        const reading = [...menu.querySelectorAll(".zft-menu-group")].find((group) =>
            group.querySelector(".zft-menu-group-title")?.textContent?.trim() === "阅读"
        );
        if (!reading || reading.querySelector('[data-zft-translation-only="true"]'))
            return;

        const state = this.getState(reader, doc);
        const currentItem = state.itemID ? Zotero.Items.get(state.itemID) : null;
        const pair = currentItem ? this.findComparePair(currentItem) : null;
        const comparisonOpen = !!state.nativeCompare || this.safeDOMConnected(state.cloudPanel);
        const active = !!pair && !comparisonOpen && currentItem?.id === pair.translated.id;

        const button = doc.createElement("button");
        button.type = "button";
        button.className = "zft-menu-action";
        button.dataset.zftTranslationOnly = "true";
        button.disabled = !pair || active;

        const icon = doc.createElement("span");
        icon.className = "zft-menu-icon";
        icon.textContent = "译";
        const copy = doc.createElement("span");
        copy.className = "zft-menu-copy";
        const label = doc.createElement("span");
        label.className = "zft-menu-label";
        label.textContent = "仅译文模式";
        copy.append(label);
        const tail = doc.createElement("span");
        tail.className = "zft-menu-tail";
        tail.textContent = active ? "当前" : "›";
        button.append(icon, copy, tail);

        button.addEventListener("click", () => {
            if (button.disabled)
                return;
            menu.remove();
            Promise.resolve(this.openTranslationOnlyForReader(reader)).catch((e) => this.reportError(e, reader));
        });
        reading.insertBefore(button, reading.querySelector(".zft-menu-action") || null);
    };

    const originalShowQuickMenu = addon.showQuickMenu;
    addon.showQuickMenu = function (reader, doc, anchor) {
        const result = originalShowQuickMenu.call(this, reader, doc, anchor);
        try {
            this.injectTranslationOnlyQuickAction(reader, doc);
        }
        catch (e) {
            this.log("translation-only menu injection failed", this.safeErrorMessage(e));
        }
        return result;
    };

    const originalRegisterReaderHooks = addon.registerReaderHooks;
    addon.registerReaderHooks = function () {
        originalRegisterReaderHooks.call(this);
        this.translationOnlyContextHandler = ({ reader, append }) => {
            append({
                label: "Zotero Full Translate · 仅译文模式",
                onCommand: () => this.openTranslationOnlyForReader(reader).catch((e) => this.reportError(e, reader)),
            });
        };
        Zotero.Reader.registerEventListener("createViewContextMenu", this.translationOnlyContextHandler, this.id);
    };

    const originalShutdown = addon.shutdown;
    addon.shutdown = async function () {
        try {
            if (this.translationOnlyContextHandler)
                Zotero.Reader.unregisterEventListener("createViewContextMenu", this.translationOnlyContextHandler);
        }
        catch (_) { }
        this.translationOnlyContextHandler = null;
        return originalShutdown.call(this);
    };
})();
