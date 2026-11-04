# ダウンロードに失敗しても、その場で続けられます

`setup.bat` は curl → Python標準TLSの順に取得を試し、登録済みの代替URLも試します。両方ともHTTPSの証明書・ホスト名検証を有効にしたままです。Windowsの証明書失効確認設定を変更せず、`--insecure` 等は使いません。方式別の終了コード・URL・実際のエラーを取得先の `.attempts.jsonl` に記録します。

## 復旧画面の操作

取得に失敗しても、この画面から操作を選べます。

1. **今すぐ再ダウンロード／再確認**：何度でも実行できます。正常な完成済みファイルは再利用し、途中データを取り直す場合も旧データを別名で保持します。
2. **別の安全な取得方法**：Python標準TLSを先に試します。Schannelと別のTLS実装を使いますが、証明書やネットワークの問題を必ず解決できるわけではありません。
3. **配布ページを開く**：下記の正確な版のページを開きます。
4. **単体ファイル・ZIPを参照**：GGUF、wheel、ZIPを保存した場所から選びます。展開する必要はありません。
5. **保存済み／ZIP展開済みフォルダーを参照**：好きな場所へ展開したフォルダーを選びます。必要なファイルを検出し、配布物由来のファイル別SHA-256と照合します。単に実行ファイル名や `build.json` があるだけでは信用しません。未登録の追加プログラムはコピーしません。
6. **AIに相談**：対象名・版・URL・検索語・エラーを埋めた相談文を表示します。自動送信・自動クリップボードコピーはしません。
7. **中断して後で再開**：選択したインストール先、検証済みファイル、途中データを残します。

再試行回数・クールダウン・強制待機はありません。同じ保存先への同時書き込みだけを排他制御します。再試行に失敗しても同じ画面に戻り、取得・検証に成功すれば続きへ進みます。

**ファイルを `distribution.json` の横へ移動する必要はありません。** 例えば Eドライブのモデル、Cドライブの展開済みaudio.cppを参照し、Dドライブへ本体を導入できます。検証・コピー・展開はセットアップが行い、選択元は変更しません。大きいファイルのコピー先の容量は必要です。フォルダー検索は深さ5・1,500フォルダーまでなので、見つからない場合は必要ファイルが入っているフォルダーそのものを選んでください。移動・階層作成は不要です。リンク／ジャンクション経由の取り込みは拒否します。

## 必要ファイルの一覧

サイズはbytesです。版・直接URL・SHA-256の機械可読な定義は [distribution.json](../distribution.json)、展開済みフォルダーの照合表は [download-catalog.json](../scripts/download-catalog.json) にあります。大きいモデルやCUDA DLL自体は配布ソースZIPへ同梱しません。

| 正式名称／正確なファイル名 | サイズ | 用途・必須条件 | 配布ページ／選び方 |
| --- | ---: | --- | --- |
| Irodori-TTS 500M v3 Q8_0 `irodori-tts-500m-v3-q8_0.gguf` | 1,093,732,096 | 必須。通常版・声質安定版で共有する単体GGUF | [Hugging Faceの固定ファイル](https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/a70a4ecb70ee38ef43a5aeae9fbefb512778e262/Irodori-TTS-500M-v3-GGUF/irodori-tts-500m-v3-q8_0.gguf) → Download。f16版・HTMLは不可。展開不要 |
| audio.cpp v0.9.3 CUDA 13.3 通常版 `audio-v0.9.3-bin-windows-x64-cuda13.3.zip` | 277,336,614 | CUDA方式で必要な完成済み実行ファイル | [v0.9.3 Release](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.3) → Assets / Show all assets → 同名ファイル。lib、Linux、Source codeを選ばない |
| 同版CUDA実行時DLL `audio-v0.9.3-cudart-windows-x64-cuda13.3.zip` | 575,457,454 | 上のCUDA完成品と一緒に必要 | 同じReleaseのAssets → 同名cudart ZIP。CUDA Toolkit全体ではない |
| audio.cpp v0.9.3 CPU portable 通常版 `audio-v0.9.3-bin-windows-x64-cpu-portable.zip` | 30,409,504 | CPU方式で必要な完成品。CUDA ZIPは不要 | 同じReleaseのAssets → 同名cpu-portable ZIP |
| Irodori同梱の声質安定対応CPU完成品 `audio-cpp-stable-cpu-x64.zip` | 4,297,809 | 任意。配布物に同梱されている場合に利用 | 現在、検証済みの単独公開URLはない。同名ZIPの参照、または任意ソースビルドを使用。架空のReleaseリンクは案内しない |
| audio.cpp 固定コミットのソース `audio.cpp-e12fc743ccb294753dcdced0be7778c76a178f95.zip` | 95,594,222 | 任意ビルド時のみ必要。完成品ではない | [固定コミット](https://github.com/0xShug0/audio.cpp/tree/e12fc743ccb294753dcdced0be7778c76a178f95) → Code → Download ZIP。mainのZIP・別版のSource codeは不可。セットアップがパッチ・ビルドを行う |

### 直接URL・代替URL

- モデル：`https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/a70a4ecb70ee38ef43a5aeae9fbefb512778e262/Irodori-TTS-500M-v3-GGUF/irodori-tts-500m-v3-q8_0.gguf`
- 通常版／DLL：`https://github.com/0xShug0/audio.cpp/releases/download/v0.9.3/` の後ろに上表の正確なファイル名を付けたURL。
- ソース：`https://codeload.github.com/0xShug0/audio.cpp/zip/e12fc743ccb294753dcdced0be7778c76a178f95`
- ソースの代替：`https://github.com/0xShug0/audio.cpp/archive/e12fc743ccb294753dcdced0be7778c76a178f95.zip`。同じGitHub資産への別の入口であり、独立ミラーではありません。
- それ以外の検証済み独立ミラーは現在ありません。見つからないリンクを捏造したり、互換性不明な最新版に切り替えたりしません。

検索語はモデルなら `audio-cpp Irodori-TTS-500M-v3-GGUF irodori-tts-500m-v3-q8_0.gguf`、エンジンなら `0xShug0 audio.cpp v0.9.3 Windows x64` と正確なファイル名、ソースなら固定コミットIDです。復旧画面・AI相談文には対象ごとの語を自動表示します。配布元が全て失われた場合、復旧を保証することはできませんが、手元の同一ファイル・展開済みフォルダーも検証して使用できます。

## Python依存ファイル（Windows x64 / Python 3.12）

次の9個をサイズ・SHA-256で検証後、専用venvへ `pip --no-index --require-hashes` で導入します。システムのPythonパッケージは変更しません。既に一致する版が導入済みなら取得・再導入を省きます。各直接URLは `distribution.json` の `pythonWheels` に完全な形で記録されています。

| 正式パッケージ／正確なファイル名 | bytes | 用途・配布ページ |
| --- | ---: | --- |
| NumPy `numpy-2.4.4-cp312-cp312-win_amd64.whl` | 12,320,584 | 音声配列処理。[2.4.4 files](https://pypi.org/project/numpy/2.4.4/#files) |
| Requests `requests-2.34.0-py3-none-any.whl` | 73,021 | API通信。[2.34.0 files](https://pypi.org/project/requests/2.34.0/#files) |
| SoundFile `soundfile-0.13.1-py2.py3-none-win_amd64.whl` | 1,019,162 | WAV入出力。[0.13.1 files](https://pypi.org/project/soundfile/0.13.1/#files) |
| certifi `certifi-2026.10.23-py3-none-any.whl` | 135,082 | Requestsの依存。[files](https://pypi.org/project/certifi/2026.10.23/#files) |
| CFFI `cffi-2.1.1-cp312-cp312-win_amd64.whl` | 185,919 | SoundFileの依存。[files](https://pypi.org/project/cffi/2.1.1/#files) |
| charset-normalizer `charset_normalizer-3.5.2-cp312-cp312-win_amd64.whl` | 207,486 | Requestsの依存。[files](https://pypi.org/project/charset-normalizer/3.5.2/#files) |
| idna `idna-3.20-py3-none-any.whl` | 69,583 | Requestsの依存。[files](https://pypi.org/project/idna/3.20/#files) |
| pycparser `pycparser-3.12-py3-none-any.whl` | 62,600 | CFFIの依存。[files](https://pypi.org/project/pycparser/3.12/#files) |
| urllib3 `urllib3-2.8.0-py3-none-any.whl` | 135,717 | Requestsの依存。[files](https://pypi.org/project/urllib3/2.8.0/#files) |

PyPIページの **Download files** で表と同じ `.whl` を選びます。tar.gz、Linux、別Python版のwheelは選びません。ZIPのように展開せず、ファイルまたは保存したフォルダーを参照してください。検索語は `PyPI パッケージ名 バージョン 正確なファイル名` です。

## システムに不足する場合だけ必要なツール

Python 3.12 x64、Microsoft Visual C++ x64ランタイムは、未導入の場合のみ既存のwinget導入手順を使用します。ソースビルドを選んだ場合だけ、Git for Windows、Visual Studio 2022 Build ToolsのC++/Windows SDK/CMake/Ninja、対象GPUをサポートするCUDA Toolkitが必要です。通常版完成品を利用するためにCUDA Toolkit全体やC++ビルド環境を導入する必要はありません。

これらはOS状態やwingetが選ぶ修正バージョンで容量・ファイル名が変わるシステムパッケージです。固定サイズ・未確認の直リンクは案内しません。インストール前には従来どおり利用者の確認が必要です。完全新規PCでのシステムツール導入は、今回の復旧テストと別の検証項目です。

## 再実行と配布検査

中断した環境は「前回の続きから再開」を選びます。完了済み環境はモデルの検証後、設定・Voice・Native Host・稼働中APIに触れずに再利用します。修復・アプリ更新・声質安定版の追加を明示的に行いたい場合は `setup.bat -Repair` を使用できます。

開発中に `distribution.json` 等を変更した場合、配布一覧が古いと `配布ソースの検査失敗` で停止するのが正常です。配布者はソース変更後に `python scripts/make-inventory.py`、`python scripts/privacy_scan.py` を実行して一覧を更新・検証してください。利用者にハッシュ検査の無効化を求めません。
