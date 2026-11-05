"""Copy reviewed browser-neutral Chrome logic into the Firefox package.

Keep Firefox manifest, HTML, bootstrap and sidebar/Native Messaging APIs native
to Firefox. No installed environment, model or configuration is touched.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = (
    "adapters.js", "api-state.js", "background.js", "chatgpt-watcher.js", "content.js",
    "muse-watcher.js", "options.js", "popup.js", "sidebar.js", "text-processor.js",
    "voice-readiness.js", "character-mapping.css", "engine-picker.css", "voice-management.css",
    "options.css", "popup.css", "sidebar.css",
)


def firefox_source(name, source):
    if name == "background.js":
        source = source.replace('const NATIVE_HOST = "io.github.irodori.voice_reader";',
                                'const NATIVE_HOST = globalThis.IrodoriInstallConfig.nativeHost;')
        source = source.replace('browserApi.runtime.sendNativeMessage(NATIVE_HOST, { command, ...payload })',
                                'globalThis.IrodoriFirefoxNativeMessage(NATIVE_HOST, { command, ...payload })')
    return source


def main():
    for name in SHARED:
        source = (ROOT / "chrome-extension" / name).read_text(encoding="utf8")
        (ROOT / "firefox-extension" / name).write_text(firefox_source(name, source), encoding="utf8", newline="\n")
    print(f"Synchronized {len(SHARED)} shared files; Firefox platform files preserved")


if __name__ == "__main__":
    main()
