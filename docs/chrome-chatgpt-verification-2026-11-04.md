# Chrome ChatGPT 自動読み上げの実機確認（2026-11-04）

## 原因と修正範囲

実際の Chrome 156.0.8078.80 を Windows UI Automation と DevTools で操作し、以下を切り分けた。

1. 現行の入力欄は `[contenteditable="true"][data-composer-markdown]`。送信監視の対象に追加。
2. 新規チャットは `/` → `/c/local-chatgpt%3A<uuid>` → `/c/<server-id>` と二段階で遷移する。既存実装は2回目を別会話への移動として pending を破棄していた。仮IDから正式IDへの移行も同じ送信として引き継ぐ。
3. `data-talvt-turn-state` はユーザーとアシスタントの両方を包む。ユーザー見出しからこれを選ぶと、回答がユーザー要素の内側になり、対応回答の探索から除外される。またアシスタントのIDをユーザーIDとして拾っていた。ユーザー見出しの次の search unit を優先し、反対のロールを含む共通祖先を選ばず、`data-chatgpt-search-message-ids` から各投稿自身のIDを取得する。
4. ユーザー見出しの親は `display:contents` で `getClientRects().length === 0`。単に共通祖先を親要素へ変更するだけでは可視判定でユーザー投稿が消えるため、可視の search unit 自体を使用する。

ChatGPTのみ、送信内容と異なる新規ユーザー投稿、および後続の別質問の回答も受け付けないようにした。監視方式、安定待ち、既存キュー、音声生成・再生機構は流用。Firefox、Geminiの処理、インストーラー、Native Host、API、モデル、Voice設定のコードは変更していない。

## 実際に有効な拡張への反映

- 作業フォルダーの拡張は Chrome の設定上では無効だった。
- 有効だった拡張: `amfidblggmgkjacbfoemmfdkdpebepnc`
- 実体: `D:\chatadapt\IrodoriVoiceReader\both-2c3c01d917b5\chrome-extension`
- この生成済み拡張の ChatGPT 関連ファイルは作業ブランチより古かった。
- `adapters.js`、`chatgpt-watcher.js`、`content.js` を作業フォルダー版に反映し、Chrome の拡張詳細画面で再読み込み。manifest、install-config、登録ID、接続先、保存済みVoice設定は維持した。
- 反映前の3ファイルは `D:\AI_Cache\temp\opencode\ivr-chrome-before-20261104` に保存。
- Git上の本体コード変更は `chrome-extension/adapters.js` と `chrome-extension/chatgpt-watcher.js` のみ。

## 実機の一連の動作

既存のログ機能が有効だったため、そのまま使用した。テキストを直接 enqueue する代替テストではなく、Chrome上の新規チャット／追質問を実際に送信した。

| 操作 | ChatGPTの回答 | 結果 |
| --- | --- | --- |
| 新規チャット、Enter送信 | 音声再生の確認です。 | 二段階遷移を保持。1回 enqueue、WAV 176,684 bytes、playing → ended |
| 同じ会話への追質問、Enter送信 | 二回目も音声で再生します。 | 1回 enqueue、WAV 215,084 bytes、playing → ended、Windows出力の非ゼロ信号も確認 |
| ページ再読み込み | 上の2件の履歴 | baseline のみ。20秒の観測中に追加 enqueue なし、Windows出力なし |
| 再読み込み後、送信ボタンのクリック | クリック送信も確認します。 | trigger=click、1回 enqueue、WAV 199,724 bytes、playing → ended |

同一 message_id に対して `chatgpt_answer_detected` → `enqueue` → `job_queued` → `tts_start` → HTTP 200 `audio/wav` → `generated` → `play_call` → `playing` → `ended` を確認。最初の2回答については、ページパスと回答文字列から再計算した SHA-256 がキューの text_hash と一致した。

追質問の実測（UTC）:

- message_id: `879cba70-6738-4141-9900-23c4baa4be70`
- enqueue: `06:00:49.430`
- `/tts` HTTP 200: `06:00:50.333`
- playing: `06:00:50.385`
- ended: `06:00:52.709`
- WASAPIスピーカーループバックの非ゼロ区間: `06:00:50.519`～`06:00:52.720`
- 最大ピーク: `0.810480`、最大RMS: `0.249146`（float32）
- 音声信号そのものは保存せず、時刻・ピーク・RMSだけを一時JSONに記録。

Native Host の status と timing_log_append 通信も成功している。既存構成ではNative HostはAPIの起動確認・Voice管理等を担当し、読み上げテキストはChromeの既存キューから `http://127.0.0.1:18765/tts` へHTTPで渡る。Native Host経由でテキストを送る別経路は新設していない。APIの `/health` は backend_ready=true、既存 audio.cpp / RTX 3060 / irodori-500m-v3-q8 を使用。既存のVoice解決により `tera` が使われた。保存済みのVoice選択は変更していない。

確認ログ（このPC上）:

- `D:\chatadapt\IrodoriVoiceReader\both-2c3c01d917b5\chrome-host\timing_logs\timing-20261104055658-58f320e0fdf441778ff6004865397aff.jsonl`
- 同じ `chrome-host` 内の `native_host.log`
- `D:\AI_Cache\temp\opencode\ivr-loopback-1793772068367960600.json`（追質問の出力計測）
- `D:\AI_Cache\temp\opencode\ivr-loopback-1793772166923340000.json`（再読み込み後、出力ゼロ）

## 回帰テスト

- `node tests/test_chrome_chatgpt_watcher.js`: 新入力欄、二段階遷移、生成中の待機、完成後1回だけ投入、履歴無視、別質問の拒否、別会話への移動。
- `tests/test_chrome_chatgpt_adapter.html`: HTTPでリポジトリを配信してブラウザーで開く。実DOMを縮小したfixtureと旧DOMの21 assertionsがPASS。`display:contents` も再現。
- 既存の `test_text_processor.js`、`test_dialogue_scope.js`、`test_speaker_delimiters.js` と構文・diffチェックもPASS。

## 未確認の範囲

物理スピーカーからの空気振動をマイクで測定したわけではなく、Windowsのスピーカー出力ストリームを計測した。人による聴感・発音品質、全モデル／全Voice、添付ファイル・再生成・長文の全パターンは今回の実機テスト対象外。FirefoxとGeminiの実機動作は変更対象外のため操作していない。
