# バージョン比較

2026-11-04統合: main `3bde458`（両拡張ソース1.2.24）の機能に独立接続設定を追加し、両ソースを1.2.25へ統一しました。
既存インストール済みFirefox 1.2.25と署名済みXPIは別成果物で、変更していません。
セットアップはローカル0.1.0-private.2が基準です。ZIPを最新版として扱いません。

以下は前段の比較記録です。現在の状態は [integration-validation.md](integration-validation.md) を参照してください。

# バージョンとファイル比較の記録

2026-11-03、GitHubを読み取り専用で取得し、配布フォルダーとファイル単位のSHA256を比較しました。GitHubのPrivate設定は変更していません。

| 対象 | 作業開始時 | 修正版 |
|---|---|---|
| GitHub参照Chrome / Firefoxソース | 1.2.24 / 1.2.24 | GitHub未反映 |
| ダウンロード済みChrome / Firefox参照ソース | 1.2.23 / 1.2.23 | Chrome 1.2.25、Firefox参照は未変更 |
| 配布セットアップ | 0.1.0-private.1 | 0.1.0-private.2 |
| 同梱Firefox XPI | 署名済み1.2.23 | 署名済み1.2.25を変更せずコピー |
| 使用中Firefox | 署名済み1.2.25 | 操作・変更なし |
| 使用中Chromeの別環境ソース | 1.2.24 | 上書きなし |

Native Hostの1.3.0は制御プロトコルの版で、拡張や配布パッケージとは別に管理します。provenance.jsonは当初の取り込み記録として保持します。

## 開始時のGitHub参照との差分

比較したファイルは101件です。以下は開始時点で内容が異なった、または片側にのみあったファイルです。Firefox側は参照・比較のみで変更していません。

- `chrome-extension/background.js`
- `chrome-extension/manifest.json`
- `chrome-extension/options.html`
- `chrome-extension/options.js`
- `chrome-extension/sidebar.html`
- `chrome-extension/sidebar.js`
- `chrome-extension/text-processor.js`
- `chrome-extension/voice-readiness.js`
- `firefox-extension/background.js`
- `firefox-extension/manifest.json`
- `firefox-extension/options.html`
- `firefox-extension/options.js`
- `firefox-extension/sidebar.html`
- `firefox-extension/sidebar.js`
- `firefox-extension/text-processor.js`
- `firefox-extension/voice-readiness.js`
- `native-host/chrome_native_host.py`
- `native-host/irodori_native_host.py`
- `irodori-api/kurisu_voice_api.py`
- `irodori-api/tests/test_api_request_security.py`
- `README.md`
- `package-files.json`

## ビルド用ソースの欠落

元の配布一式には、provenance.jsonに記録されていたaudio_quality.h / audio_quality.cppが欠落していました。`models`というディレクトリー名を一律除外する配布検査が原因でした。元の記録とSHA256が一致するファイルを読み取りコピーして復元し、C++ソース用の限定許可と全追加ソースの欠落検査を追加しています。モデル重み・Voice・ユーザー設定の配布は禁止したままです。
