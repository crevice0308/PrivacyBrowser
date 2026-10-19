# PrivacyBrowser 0.1.0-dev

Firefox ESR を利用する、Windows 11 x64 / Ubuntu 系 Linux x86-64 向けブラウザの初期開発実装です。将来の SecureLite OS 搭載を想定しています。Brave Search を初期検索エンジンとし、検索欄と URL 欄を分けています。

**同梱されていた既存の開発用 EXE は、Firefox ESR にプロジェクトの UI とサービスを適用するオーバーレイです。PrivacyBrowser の名前・アイコンを持つ独自ソースビルド版は現在準備中です。既存 EXE を名前だけ変更して独立ブランドのビルドとして公開することはしません。** 完全な Zero History、Windows 実機動作、自動更新、軽量化目標の達成は、まだ検証を完了していません。

## 開発版の成果物

| 成果物 | 現在の状態 |
| --- | --- |
| プロジェクトのソース、テスト、GitHub Actions 設定 | 同梱 |
| Linux 開発用 bootstrap DEB | プロジェクトの UI・ランチャーを収録。Gecko 本体は含まず、対応 ESR の別途取得が必要 |
| Linux 実行確認 | ネイティブ ESR の親／chrome を起動。独自ホーム・設定・タスク画面、分類別削除、ブックマーク保持、診断 ZIP を Marionette で確認 |
| 独立ブランドの Gecko ソースビルド | 準備スクリプトと構成を実装。コンパイル未実施 |
| Windows x64 EXE・インストーラー | 既存の未署名オーバーレイは Firefox のネイティブ表示を保持。PrivacyBrowser ネイティブ版の再ビルド・検証は未完了 |
| 署名済み更新・APT リポジトリ | 未展開。自動更新・正式公開は無効 |

エンジンの固定値は `config/esr.json` の `153.5.0esr` です。2026-10-19 に確認した固定値であり、実行時に自動追従はしません。システムの Firefox を変更せず、専用コピーと専用プロファイルを使います。開発用コピー内の Firefox 名・ネイティブブランドは残っており、このコピーを独立製品の正式配布物として扱いません。

## 実装した機能

- 独自ホーム・設定画面・アイコン、OS のライト／ダーク設定に従う配色、上部検索ボタン。
- URL 欄のキーワード検索を無効化。中央検索欄、`Ctrl+K`、検索エンジンの選択・HTTPS のカスタム検索 URL。
- 8 種類の履歴保存フラグ、初期値 OFF、起動時／正常終了時の分類別削除、既存履歴削除。
- 最終ウィンドウからの終了要求、ダウンロード確認を尊重する終了処理、保守的な非アクティブタブ休止。
- `Shift+Esc`、メニュー、タブの右クリックから開くタスク画面。CPU／RAM はプロセス単位、タブの確認付き終了。
- 初期 OFF の JSON Lines 診断ログ、上限・ローテーション・削除・保存先・ユーザー操作による ZIP 出力。
- ESR バージョン監視、上流署名と SHA-512 の検証、アーカイブの安全な展開、ソース統合、公開を拒否するリリース検査。

タブ・ナビゲーション・ブックマーク・ダウンロード・権限管理・署名済み互換アドオン等は ESR の既存機能を利用します。uBlock Origin は同梱していません。Firefox のサンドボックス、サイト分離、証明書・ダウンロード検証を軽量化のために無効にする処理は追加していません。

## Zero History の現時点の範囲

8 フラグは閲覧・検索・Cookie・ダウンロード・フォーム・ディスクキャッシュ・セッション・サイトストレージを個別に制御します。ブックマークとダウンロードしたファイルを削除対象に含めません。

**現在の開発版では、Cookie とサイトストレージが実行中にディスクへ書き込まれる可能性があります。** 起動時と正常終了時の削除は実装していますが、クラッシュ・強制終了後を含む「一度も永続保存されない」という保証はありません。セッション保存を抑止する上流ソース用ガードも用意していますが、ソースビルド済みの実証が必要です。設定の変更後は再起動し、分類ごとの実データ再起動テストを行ってください。

検索履歴フラグは専用検索欄の検索語リストを制御します。閲覧履歴を ON にした場合、検索結果の URL が閲覧履歴に残ることは別の制御です。診断ログにはいずれの検索語も含めません。OS ログ、DNS キャッシュ、スワップ、ファイルシステムのスナップショット・バックアップ・削除痕跡、ネットワークサービス側の記録は保証範囲外です。

## 起動とテスト

Python 3.11 以上と、別途取得した対応 ESR の展開先が必要です。プロジェクトのルートで実行します。

```sh
python3 tools/dev_launcher.py --runtime /path/to/firefox-esr
```

Linux bootstrap DEB のインストール後は次のように起動できます。

```sh
privacy-browser-dev --runtime /path/to/firefox-esr
```

ポータブルなロジックとブランド生成の検査には、CI と同じ Node.js 22 と Python 3.12 を使います。`requirements-build.txt` は Python 3.12 の Linux / Windows x64 用 wheel をハッシュ固定しています。

```sh
node --test tests/unit/*.test.mjs
python3 -m pip install --only-binary=:all: --require-hashes -r requirements-build.txt
python3 -m unittest discover -s tests/unit -p '*_test.py' -v
python3 tools/check_source.py
```

提供アーカイブ由来の過去の検証記録は `tests/results/` に保存しています。これらは今回のソース変更後の再ビルド・再試験の証拠ではありません。提供元の Linux 統合レポートでは 20 項目成功、Brave 検索結果の 1 項目が環境制約で保留とされています。同じ過去の記録には、コンテナ内の bootstrap DEB の `dpkg -i`、インストール済みランチャーの `--help`、`dpkg -r` の実施も含まれます。デスクトップメニューをクリックする GUI 導入試験は未実施です。その過去の Linux 検証環境では user namespace 作成が `EPERM` になり、リモートコンテンツプロセスのクラッシュを検出しました。サンドボックスを弱めずに親／chrome の起動と設定適用を確認した範囲の結果です。Brave の実検索、日本語 IME 入力、一般ページの描画、1GB RAM・300MB 起動メモリ目標、長時間のリークは未検証です。スモークテストの成功数だけで通常閲覧の成功とは判断しません。

詳細は [ビルド手順](docs/BUILD.md)、[機能と未実装一覧](docs/FEATURE_STATUS.md)、[診断ログ仕様](docs/DIAGNOSTICS.md)、[変更履歴](CHANGELOG.md) を参照してください。新しいビルドの状態は [GitHub Actions](https://github.com/crevice0308/PrivacyBrowser/actions) を確認してください。ワークフローの存在だけでは、ビルド・Windows 動作・署名・公開の成功を意味しません。

## Windows ネイティブ表示 / Windows native identity

目標は、実際のアプリ本体 `privacybrowser.exe`、エクスプローラーの製品名・説明・アイコン、ウィンドウ・About 表示、インストーラー・ショートカットを `PrivacyBrowser` に揃えることです。Mozilla の署名済みバイナリを書き換えず、検証した ESR ソースに独自ブランドを適用して再ビルドします。元の著作権・ライセンス・第三者への帰属表記は保持します。

`windows-native.yml` は、main の同一コミットで Ubuntu / Windows の品質検査が成功した後に、標準 Windows runner で Gecko 全体をソースからビルドします。ネイティブ名・アイコン・バージョン資源、ローカルページ描画、ユーザー単位のインストール／アンインストール、通常のサイレント削除でのデータ保持、明示確認付きの完全削除を検査します。成功時も未署名・未公開候補としてのみ保存し、GitHub Release の公開は行いません。実際の成功は対応する Actions 実行結果で確認してください。

`windows-capacity.yml` は標準 GitHub-hosted Windows runner の空き容量とツール配置だけを確認します。ソフトウェアのインストール、セキュリティ設定の変更、ファイル削除、Gecko の取得、ビルド・署名・公開は行いません。ネイティブ EXE とインストーラーの生成・静的検査・Windows 実行試験が通るまで、修正済み Pre-release とは扱いません。

The goal is a genuinely rebuilt `privacybrowser.exe` with PrivacyBrowser product names, executable icons, native application UI, and installer/shortcut identity. The supplied developer overlay still contains upstream Firefox native branding. Renaming that file does not meet this goal, and it is not the rebuilt release artifact. Mozilla-signed binaries are not resource-patched or re-signed as project builds. Upstream copyright and license notices remain intact.

The exact engine source is pinned in `config/esr.json`; Gecko's internal version and compatible application ID are distinct from product display branding. Native compilation, Windows runtime/install/uninstall validation, and artifact checks are still required. The native Windows workflow compiles Gecko only after successful quality checks for the exact main commit. It validates native resources, local-page rendering, per-user installation, default-safe silent uninstall, and explicitly opted-in cleanup of owned profile/cache data, then stores an unsigned, unpublished candidate only if all checks pass. It does not publish a GitHub Release. The capacity workflow is only a preflight. Existing `tests/results/` files are historical supplied evidence, not results for newly modified code. No SignPath approval or project code-signing certificate is claimed. Automatic updates remain disabled.

This is experimental software. Cookie and site storage may reach disk during use; clean-exit cleanup does not guarantee crash-proof or never-written history. Normal browsing, Windows runtime behavior, memory targets, signed updates and production release readiness are not established by portable unit tests.

## 完全アンインストール / Complete uninstall

ネイティブ版では、確認付きで PrivacyBrowser 所有のプロファイル・キャッシュ・このインストールに対応するレジストリ値を削除する経路を実装しています。**完全削除を承認すると、保存済みパスワード、ブックマーク、Cookie、設定を含む対象プロファイルのデータは復元できなくなる可能性があります。** GUI では実行時に確認し、通常の `/S` はユーザーデータを保持します。自動化から完全削除する場合だけ、明示的に `/S /PURGEUSERDATA` を指定します。

所有マーカーが一致する既定の LocalAppData / RoamingAppData 内の PrivacyBrowser フォルダーだけを対象にします。他のブラウザー、共有レジストリ値、任意の `--profile` 指定先、対象外のダウンロードファイル、アプリ用フォルダー内の未知のファイルは削除しません。所有情報の不一致、ジャンクション／シンボリックリンク、実行中のアプリや削除失敗では処理を止めます。実際の成功は同一コミットの Windows CI の結果で確認してください。

削除は一括で元に戻せる処理ではありません。途中で失敗した場合、すでに削除済みの対象データは戻らず、残りの削除を再試行する形になります。削除中に別のプロセスから対象フォルダーへ書き込まないでください。

Complete uninstall requires explicit confirmation before deleting this installation's owned profile/cache data and registry values. This can permanently remove saved passwords, bookmarks, cookies and settings. Ordinary `/S` retains user data; unattended destructive cleanup requires the separate `/PURGEUSERDATA` opt-in. Only matching ownership-marked default app-data roots and exact per-install registry values are eligible. Other browsers, shared values, external custom profiles, downloads outside those roots and unknown installation-folder files are preserved. Invalid ownership, reparse points, an active browser or failed deletion stop the operation. Configured tests are not proof that Windows execution has passed.

Deletion is not an atomic transaction: an error can leave some owned data already removed, with the remaining cleanup available for retry. Do not write to those folders from another process during uninstall.

## ライセンス

本プロジェクトの追加ソースは MPL-2.0 です。[LICENSE](LICENSE) と [NOTICE](NOTICE) を参照してください。上流の Firefox／Gecko はそれぞれのファイル・同梱コンポーネントのライセンスに従います。独立した配布版は独自ブランドのソースビルドを行い、適用した上流ソースと改変ソースを対応する形で提供してください。
