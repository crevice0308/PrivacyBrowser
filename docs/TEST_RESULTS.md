# Privacy Browser v0.1.0-dev — 実装・検証結果

実施日：2026-10-19。Firefox ESR 153.5.0esr を利用した初期開発版。

## 成果物の範囲

独自 UI・サービス・テスト・CI・ソース統合ツールを実装し、検証済み ESR に適用して Linux 上で起動した。Linux DEB はプロジェクトのランチャーとコードを含む bootstrap パッケージで、Gecko 本体を含まない。Firefox 全体のソースコンパイル、Windows EXE・インストーラーの生成は未実施。

## 実行した検査

| 検査 | 結果 |
| --- | --- |
| Node 単体・Gecko API 模擬テスト | 61 / 61 成功 |
| Python 配布・署名・抽出・引数テスト | 11 / 11 成功 |
| Linux 実 ESR 統合 | 20 成功、1 保留 |
| Python / JavaScript 構文、XML、ローカル参照、特権境界 | 成功 |
| Linux DEB 作成・dpkg 導入・ランチャー --help・削除 | 成功、削除後にプログラムの残存なし |
| Mozilla 配布元の GPG 署名・SHA-512 | 固定済み主鍵・現行署名副鍵で検証成功 |

Linux 統合の内訳：

| 項目 | 状態 |
| --- | --- |
| window-controls | 成功 |
| URL-only-preference | 成功 |
| privacy-startup | 成功 |
| privileged-home-search-UI | 成功 |
| privileged-settings-UI | 成功 |
| native-task-manager | 成功 |
| unicodeSearchHistory | 成功 |
| searchCleared | 成功 |
| cookieCleared | 成功 |
| bookmarkRetained | 成功 |
| diagnosticsDefaultOff | 成功 |
| native-diagnostics-exported | 成功 |
| native-diagnostics-bounded | 成功 |
| native-diagnostics-deleted | 成功 |
| native-diagnostics-ZIP-no-secret | 成功 |
| Brave-search-dispatch | 成功 |
| Brave-search-results | 環境制約で保留 |
| last-window-close-exit | 成功 |
| native-process-exit | 成功 |
| no-session-files-after-exit | 成功 |
| bookmark-persisted-after-exit | 成功 |

独自ホーム・設定は JavaScript 初期化完了マーカーまで待機し、タスク画面は実際の行生成・選択・内部タブ保護を確認した。ネイティブ Cookie の削除、検索履歴の日本語保存と OFF 切替による削除、SQLite 上のブックマーク保持、実 Gecko ZIP 出力と機密文字列除外を確認した。最後のウィンドウを閉じ、親プロセスが終了コード 0 になることを確認した。

## 環境制約と未検証

実行環境は Ubuntu 24.04 コンテナ。Firefox のサンドボックスレベル 6 を維持したが、user namespace 作成が EPERM となり、一般コンテンツプロセスのクラッシュを検出した。Brave Search への検索要求送出は確認したが、検索結果ページは表示確認できていない。指定 6 語の URL 生成・Unicode 処理は単体テスト済み。実検索は先頭の 1 語だけを試行し、残り 5 語とローカル Web ページ描画は同じ制約で保留。日本語 IME の操作は未実施。

スモーク環境のメモリ API／OS 値は JSON に生の値として保存したが、通常閲覧を代表しないため有効な目標測定とは扱わない。起動 300MB 以下、Linux 1GB RAM、CPU・RAM の通常負荷、リーク、長時間安定性、全関連 PID の残留確認は未検証。Windows の全試験も未実施。

## 修正した主な不具合

- ESR 153 で利用可能な manifest 登録 API と組込み Services に合わせた。
- 初期化時期と終了時期を修正し、データベースが利用可能な間に削除を待機する。
- 閲覧削除 API がセッション全削除を誘発する競合を修正。最終試験の native_error_summary は空。
- 独自検索画面からブラウザ chrome へ遷移を委譲する。
- 終了確認の拒否を尊重し、共有プロセスを一括 kill しない。
- Windows の直接起動と引数配列を維持し、生成ランチャーで cmd.exe の引数再解釈を使わない。Windows 実機試験は残る。

## 残作業

- Cookie／サイトストレージの実行中のディスク書込みを抑えるエンジン側実装。クラッシュ時まで含む完全な非保存保証。
- 独立ブランドの Gecko ソースビルドとソース側セッションガードの実機検証。
- Windows EXE・インストーラー、単体で動く Linux バイナリ／DEB、両 OS の通常ブラウジング試験。
- 全診断イベント、拡張機能別の資源計測、フリーズ検出、クラッシュ復旧。
- 製品署名、署名付き Windows 更新、APT 配信、更新ロールバック。現在は自動更新・正式公開を無効化。
- GitHub PrivacyBrowser リポジトリへの作成／反映と Actions 実行。今回アクセスできる既存リポジトリは見つからず、接続のツールにはリポジトリ作成機能がないため、ローカルソースとして保存。

ブックマークとダウンロードしたファイルは削除対象から除外する。OS ログ、DNS キャッシュ、スワップ、バックアップや削除痕跡はブラウザの非保存保証に含めない。Cookie／ストレージは現段階では正常終了時などの削除処理であり、一度もディスクへ書かれないという保証はない。

詳細は README.md、docs/BUILD.md、docs/FEATURE_STATUS.md、docs/DIAGNOSTICS.md、CHANGELOG.md と tests/results/ を参照。
