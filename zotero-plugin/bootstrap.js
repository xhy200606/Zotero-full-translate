var chromeHandle;
var pluginContext;
function install(data, reason) { }
async function startup({ id, version, resourceURI, rootURI }, reason) {
    await Zotero.initializationPromise;
    if (!rootURI && resourceURI)
        rootURI = resourceURI.spec;
    const aomStartup = Components.classes["@mozilla.org/addons/addon-manager-startup;1"].getService(Components.interfaces.amIAddonManagerStartup);
    const manifestURI = Services.io.newURI(rootURI + "manifest.json");
    chromeHandle = aomStartup.registerChrome(manifestURI, [
        ["content", "zft", rootURI + "chrome/content/"]
    ]);
    pluginContext = {
        rootURI,
        Services,
        Components,
        ChromeUtils,
        Zotero,
        PathUtils: typeof PathUtils !== "undefined" ? PathUtils : undefined,
        IOUtils: typeof IOUtils !== "undefined" ? IOUtils : undefined,
        FileUtils: typeof FileUtils !== "undefined" ? FileUtils : undefined,
        fetch: typeof fetch !== "undefined" ? fetch : undefined
    };
    pluginContext._globalThis = pluginContext;
    Services.scriptloader.loadSubScript(rootURI + "chrome/content/main.js", pluginContext);
    Services.scriptloader.loadSubScript(rootURI + "chrome/content/translation-only.js", pluginContext);
    Zotero.PreferencePanes.register({
        pluginID: id,
        src: rootURI + "chrome/content/preferences.xhtml",
        scripts: [rootURI + "chrome/content/preferences.js", rootURI + "chrome/content/preferences-release.js"],
        label: "Zotero Full Translate",
        image: rootURI + "chrome/content/icons/icon-48.png"
    });
    await Zotero.ZoteroFulltextTranslator.init({ id, version, rootURI });
}
async function onMainWindowLoad({ window }, reason) {
    Zotero.ZoteroFulltextTranslator?.onMainWindowLoad(window);
}
async function onMainWindowUnload({ window }, reason) {
    Zotero.ZoteroFulltextTranslator?.onMainWindowUnload(window);
}
async function shutdown({ id, version, resourceURI, rootURI }, reason) {
    if (reason !== APP_SHUTDOWN) {
        try {
            await Zotero.ZoteroFulltextTranslator?.shutdown();
        }
        catch (e) {
            Zotero.logError(e);
        }
        try {
            delete Zotero.ZoteroFulltextTranslator;
        }
        catch (_) { }
        if (chromeHandle) {
            chromeHandle.destruct();
            chromeHandle = null;
        }
    }
}
function uninstall(data, reason) { }
