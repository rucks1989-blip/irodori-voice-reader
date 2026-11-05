# 声質安定版の内部差分と追加方式（2026-11-05）

## 調査の基準

Reader の通常版開発ビルドは `scripts/build-backend.ps1 -StandardOnly`、安定版は同じ
audio.cpp `e12fc743ccb294753dcdced0be7778c76a178f95` に下記パッチを重ねる。
配布用の通常版完成品は `distribution.json` の上流 v0.9.3。固定ソースと配布バイナリを
同一ビルドとは扱わない。未パッチの通常版EXEへの設定追加だけでは安定版にならない。

旧 Studio は [21644c42a43885e8cae568d0fa8e4cb728061959](https://github.com/rucks1989-blip/irodori-voice-studio/tree/21644c42a43885e8cae568d0fa8e4cb728061959) を参照した。
[声質安定版を作る.bat](https://github.com/rucks1989-blip/irodori-voice-studio/blob/21644c42a43885e8cae568d0fa8e4cb728061959/声質安定版を作る.bat) は
`scripts/build_voice_stable.ps1` を実行し、同じ固定ソースに rescue と speaker direct を追加、
`audiocpp_server` を作り、`audiocpp_server_voice_stable_sm*_speaker_direct.exe` として保存する。
通常版とは別のモデルや別の音声生成方式ではない。旧 `stable-rescue.patch` と
`apply_speaker_embedding_patch.py` は、現 Reader と改行・末尾空行を除いて同じ内容だった。

## 内部処理の差分

| 箇所 | 通常経路との違い | 現在の安定版プロファイル |
| --- | --- | --- |
| `rf_dit.cpp` / `rf_dit.h` | 話者トークンに加算する attention mask と RF グラフの引数・キャッシュ条件を追加 | bias 0.75、全層、step 1～20 |
| `session.cpp` / `types.h` / `loader.cpp` | オプションの解析・検証と、予測した発話時間への補正 | `duration_offset_seconds=-0.64` |
| 同上 | RF の話者 branch の差分 RMS を probe step で検査し、弱い場合は seed をずらして生成をやり直す | step 10、閾値 0.15、最大2回、stride 1 |
| `session.cpp` / `audio_quality.cpp` | decode 後の波形品質検査に失敗した chunk を seed をずらして再生成 | max jump 0.4、最大2回、stride 1 |
| `apply_speaker_embedding_patch.py` / `speaker_embedding_loader.inc` | safetensors の話者埋め込みを直接読み、参照 WAV の encode を省く。サーバー・CLI の入力も追加 | 既存の speaker embedding 入力を保持 |
| `condition_encoder.cpp` / `session.cpp` | 話者条件・KV・参照 cache、診断・調査用コードの追加 | 既存機能を保持 |
| `external/ggml/src/ggml-cuda/fattn-common.cuh` | mask の実際の `ne31` と tile 数を分離、端の tile の境界を検査する修正 | 設定だけでは導入できない CUDA 側の差分 |

安定化の bias / rescue は既定では 0 / false。通常プロファイルはそれらを有効にせず、
安定プロファイルがリクエストの `options` として渡す。adaptive guidance、speaker simulation、
trace・調査用ベンチもパッチに含まれるが、安定版プロファイルは全機能を有効にするわけではない。
今回これらを削除・書換えしていない。`speaker_embedding_dump` は WAV から埋め込みを作る補助ターゲットであり、
読み上げのために毎回ビルドする対象ではない。

## 共用できるものと限界

* 同じ GGUF、Voice・参照 WAV / safetensors、Python 環境、API、ポート、chunk 処理を使う。
  通常版用・安定版用にモデルやVoiceを再取得しない。
* パッチ済みの1つの `audiocpp_server.exe` で両プロファイルを処理する。
  API は既存の別モデルIDを保ったまま options を変える。
* `audio-cpp-patch/apply_shared_session_patch.py` は canonical な通常ID・安定IDのモデル定義が一致する場合に
  同じ session を参照させる。path / family / task / mode / load・session options / override 等が一致し、
  voice preset がないことが条件。既存完成品にこのパッチが含まれる場合は重みの二重ロードも避けられる。
  古い完成品に session 共用があるとは断定しない。カスタムID・既存の異なる安定版定義を勝手に変更しない。
* CUDA DLL は選んだ完成品の組合せをそのまま再利用する。Toolkit は実行に不要。
  通常版 v0.9.3 の CUDA 13.3 DLL と、既存安定版の別 CUDA 版の DLL を無条件に混ぜない。
  ggml の kernel 修正もあるため、通常版の CUDA オブジェクトを別バージョンの完成品へ差し替える方法も採らない。
* 旧ビルドスクリプトも GPU 世代を検出して `*-real` を指定する。一方で sm61 固定の検査も残っている。
  「GTX 1070 で作った成果物が RTX 3060 で動いた」という利用実績だけから、全成果物の互換性を拡張しない。
  今ある同梱 GPU 完成品の宣言は sm86 のみ。sm61 等に自動で流用しない。
  明示指定した旧完成品は現在の GPU で native probe を通った場合に利用できる。

## 今回の追加経路

`声質安定版追加する.bat` → `scripts/add-stable.ps1` は `setup.ps1 -Repair` を呼ばない。
既存の専用 Python と通常モデルを使い、対象 API / backend のポートが空いていることを確認する。
利用中なら停止操作を案内して終了し、他プロセスを停止しない。

完成品の選択と検証は `scripts/stable-addon.ps1` に集約し、新規セットアップの安定版追加部分も呼び出す。

1. 明示指定の `-StableBackendDirectory`（旧名付きサーバーEXEも元ファイルを保持して利用）。
2. 現在使う実行ファイルのディレクトリー。build metadata または前回の検証記録で安定対応を確認する。
3. インストール先の親にある既存 `.ivr-build-cache` の完成済み runtime。完了記録のファイル hash も確認する。
4. 対応する同梱・既存配布完成品。ZIP の SHA-256 とサイズを検査する。
5. 対応品がなければ既存完成品の手動指定、または停止。CPU への勝手な変更、ソースビルド、開発ツールの導入をしない。

元の完成品を保持したままインストール用の専用ディレクトリーへ EXE と同じ組合せの DLL を配置する。
`Test-StableCandidate` / `verify-backend.py` で両経路の実行と native trace の bias / rescue ON・OFF を確認する。
同じ EXE・全DLL・検証スクリプト・backend・GPU世代・モデルパスで既に検証済みの場合は再生成を省く。
これは内部オプションと kernel 実行の確認であり、声の主観品質や実際の rescue 発動の証明ではない。

後付けの設定反映は `scripts/stable-addon.py` が行う。変更対象は runtime の実行ファイル参照、
共有サーバーの安定版モデルID（未登録時だけ通常モデルから alias を追加）、capabilities、導入マーカーのみ。
`config.json`、`config.voice_stable.json`、Voice、モデル、ブラウザー設定は書き換えない。
既存の安定版モデル定義・ユーザー追加キーを維持し、事前検証、同時実行ロック、元ファイルの保存、
失敗時 rollback、中断後の次回 recover を備える。独立した設定編集があれば上書き復元しない。

新規セットアップには完成品選択・検証の共通呼出しだけを組み込んだ。全体のセットアップ手順や
通常版の導入処理は今回の対象外。安定版のUIからビルド選択肢を除き、旧 `-StableMode build` もビルドせず案内する。
開発者が明示実行する既存 `build-backend.ps1` 自体と既存 Release は変更していない。

## 検証範囲と未確認事項

軽量テストは `test_stable_addon.py`（モック完成品による取得・再利用・DLL変更・未対応GPU、
設定保持・冪等性・rollback・中断復旧）、既存の Windows installer / backend candidate テストを使用する。
実利用環境への適用、GPUビルド、ツール追加、モデル・Voice・API設定の変更は行わない。
実機での追加操作と音声の聴き比べ、旧 sm61 完成品の RTX 3060 での互換性は未確認。
sm86 以外に対応する新しい完成品は作成していないため、対応する既存成果物がない環境への安定版追加は停止する。
