# ビルド・起動・検証手順

## 今回作成した成果物の区別

`tools/build_dev.py` は既存 ESR のバイナリにプロジェクト UI を適用するローカル開発用の組み立てです。C++／Gecko のコンパイルではありません。`tools/package_deb.py --bootstrap-only` はプロジェクトのコードだけを DEB にします。Windows の実行ファイル、独立ソースビルドの Linux バイナリ、正式インストーラーは今回の成果物に含みません。

## 必要な環境

- 開発用オーバーレイ：Python 3.11 以上、Linux の場合は Gecko の GTK 等の実行依存、`config/esr.json` と一致する Firefox ESR の完全なアプリディレクトリ。
- ポータブルテスト：Node.js 22 相当、Python 3.11 以上。追加の npm パッケージは不要。
- DEB の作成：`dpkg-deb`。インストール操作には通常管理権限が必要。
- 独立ソースビルド：上流 Firefox の対象 ESR が必要とするコンパイラ・Rust・Python・Mozilla ツールチェーン、GPG、十分な RAM とディスク、ブランド画像変換用 Pillow。Windows は Windows 11 x64、MozillaBuild と Visual Studio Build Tools が必要。

推奨 RAM 4GB・Linux 最低目標 1GB は**実行製品の目標**です。Gecko をビルドするホストの必要メモリを示していません。上流の最新ビルド要件は [Firefox Source Docs](https://firefox-source-docs.mozilla.org/setup/index.html) を確認してください。

## 対応 ESR の取得

固定値と署名キーのピンは `config/esr.json` と `config/update-trust.json` にあります。バージョンの HTTPS メタデータは更新検知用であり、真正性の証明には使用しません。

```sh
python3 tools/update_esr.py check --output reports/esr-observation.json
python3 tools/provision_upstream_key.py --output upstream/mozilla-release.gpg
python3 tools/update_esr.py fetch \
  --version 153.5.0esr --kind linux --destination upstream \
  --keyring upstream/mozilla-release.gpg \
  --fingerprint 14F26682D0916CDD81E37B6D61B7B526D98F0353 \
  --signing-fingerprint 827E658608679618CD349F93678E455D76767AA3
```

キーの公開元・フルフィンガープリントを独立に照合してから使用します。上流のキー変更は手動レビューの対象です。署名不一致・SHA-512 不一致・許可外ホストへの取得・構造変更時に処理を停止します。ダウンロード完了後は、そのアーカイブの上流手順に従って別ディレクトリへ展開してください。署名検証を省略してそのまま製品を公開する経路は用意していません。

## ローカル開発用に起動

```sh
python3 tools/dev_launcher.py --runtime /absolute/path/to/firefox-esr
```

ランチャーは `application.ini` のバージョンと ESR ソース系統を確認し、入力先を変更せず、専用キャッシュへコピーします。コピーには独自 chrome と更新無効化ポリシーを適用します。

| OS | 開発用の保存先 |
| --- | --- |
| Linux ランタイムコピー | `$XDG_CACHE_HOME/privacybrowser-dev` または `~/.cache/privacybrowser-dev` |
| Linux プロファイル | `$XDG_CONFIG_HOME/privacybrowser-dev/profile` または `~/.config/privacybrowser-dev/profile` |
| Windows ランタイムコピー | `%LOCALAPPDATA%\PrivacyBrowser-Dev\Cache` |
| Windows プロファイル | `%LOCALAPPDATA%\PrivacyBrowser-Dev\Profile` |

明示的に組み立てる場合は、存在しない出力先を指定します。

```sh
python3 tools/build_dev.py --runtime /path/to/firefox-esr --output /path/to/privacybrowser-dev
/path/to/privacybrowser-dev/privacy-browser-dev
```

`PRIVACY_BROWSER_BUILD.json` には `developer-runtime-overlay`、`source_compiled:false`、`native_branding_replaced:false` を記録します。元の Firefox のブランドが残った開発用コピーは、正式な Privacy Browser 配布版とは区別してください。

## Linux bootstrap DEB

```sh
python3 tools/package_deb.py --bootstrap-only \
  --output dist/privacy-browser-dev-bootstrap_0.1.0~dev1_all.deb
sudo dpkg -i dist/privacy-browser-dev-bootstrap_0.1.0~dev1_all.deb
privacy-browser-dev --runtime /path/to/firefox-esr
sudo dpkg -r privacy-browser-dev-bootstrap
```

今回の Linux コンテナ内で `dpkg -i`、`/usr/bin/privacy-browser-dev --help`、`dpkg -r` を実行して成功しました。通常のデスクトップ環境でのメニュー起動確認とは区別しています。

この DEB は Gecko 本体を含まず、独立したブラウザの完全インストーラーではありません。ESR の別途取得・配置が必要です。デスクトップエントリと独自アイコンを含みます。アプリパッケージの削除時に専用プロファイル・キャッシュは自動削除しません。

## 独立ブランドのソースビルド経路（未実行）

次は実行手順であり、今回実行・成功済みという報告ではありません。

```sh
python3 tools/update_esr.py fetch \
  --version 153.5.0esr --kind source --destination upstream \
  --keyring upstream/mozilla-release.gpg \
  --fingerprint 14F26682D0916CDD81E37B6D61B7B526D98F0353 \
  --signing-fingerprint 827E658608679618CD349F93678E455D76767AA3
python3 tools/extract_source.py \
  --archive upstream/firefox-153.5.0esr.source.tar.xz \
  --verification upstream/firefox-153.5.0esr.source.tar.xz.verification.json \
  --destination upstream/source
python3 tools/prepare_source.py --source upstream/source --target linux
cd upstream/source
./mach build
./mach package
```

Windows では MozillaBuild のシェルで `--target windows` を使用します。準備処理は独自のアプリ名・画像、プロファイル名、chrome 同梱、`SessionFile` ガード、対象の mozconfig を反映します。上流の変更で一致しないアンカーを検出した場合、手動レビューを要求して停止します。Firefox 互換の署名済みアドオンのため Gecko アプリ ID を維持します。自動更新はプロジェクトの署名基盤ができるまで無効です。

`.github/workflows/source-build.yml` は、ツールチェーンを事前配置した Linux／Windows の self-hosted runner を想定した手動起動用です。未署名・未公開候補としてのみ保存します。今回 GitHub Actions を実行した実績はありません。

`packaging/windows/privacy-browser-dev.nsi` は開発用 NSIS インストーラー原稿です。`makensis /DRUNTIME_DIR=...` 用の構成ですが、未コンパイル・未署名・インストール／アンインストール未検証です。

## 検査

```sh
node --test tests/unit/*.test.mjs
python3 -m unittest discover -s tests/unit -p '*_test.py' -v
python3 -m compileall -q tools
python3 tools/integration_test.py --binary /path/to/privacybrowser-dev/firefox \
  --output tests/results/linux-integration.json
```

Marionette のシステム権限はローカルのテスト用起動に限って使用します。普段のランチャーにテスト権限を付けません。ネイティブ stderr はプロファイルと共に一時領域へ出力し、診断 ZIP に含めません。公開前には通常のデスクトップ環境で日本語 IME、一般サイト、6 キーワードの検索、全分類の実データ再起動検証、完全終了、異常終了、インストール削除、署名、更新失敗復旧、1GB RAM、長時間メモリ測定を追加する必要があります。
