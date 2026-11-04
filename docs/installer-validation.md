# 独立導入の検証

現在の統合結果・実機確認・未確認事項は [integration-validation.md](integration-validation.md) を参照してください。
過去版の「Firefoxは固定ID・既定ポートで衝突時停止」という設計は廃止しました。
全ブラウザー選択で専用接続先とHostを作ります。署名済みの既存Firefox XPIは変更しません。
