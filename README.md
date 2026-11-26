# gh-ci-bootstrap

新規リポジトリ作成時（`gh repo create --source=...`）および Web 版 GitHub で空リポジトリを作って初回 `git push` した際に，汎用 GitHub Actions ワークフロー（`.github/workflows/ci.yml`）を自動生成・単独コミットするローカルツール一式です．

## 構成

| パス | 配置先（シンボリックリンク） | 役割 |
| --- | --- | --- |
| `bin/gh` | `~/.local/bin/gh` | `gh repo create --source=...` 成功後に `ci-init` を呼び出し，`--push` 時は追加コミットも自動プッシュ |
| `scripts/ci-init` | `~/.local/share/gh-wrapper/ci-init` | `gh` ラッパーと `pre-push` フック共通の CI 生成・単独コミット処理 |
| `scripts/pre-push-ci-init` | `~/.local/share/gh-wrapper/pre-push-ci-init` | 空の自分の GitHub リポジトリへの初回 `git push` 時のみ発火するフック本体 |
| `hooks/pre-push` | `~/.git-templates/hooks/pre-push` | `pre-push-ci-init` を `exec` する薄いスタブ（本体更新が全リポへ即時反映） |
| `templates/ci.yml` | `~/.local/share/gh-wrapper/ci.yml` | 自動配置される汎用 CI ワークフローテンプレート |

## セットアップ

```bash
git clone https://github.com/HR0101/gh-ci-bootstrap.git
cd gh-ci-bootstrap
./install.sh --owner <your-github-username>
```

`~/.local/bin` が本物の `gh` より先に解決されるよう，`~/.zshenv` などに以下を設定してください：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

アンインストール（このリポジトリを指すシンボリックリンクのみ安全に解除）：

```bash
./uninstall.sh
```

## 汎用 CI（`templates/ci.yml`）で自動実行される内容

### 1. 共通チェック（全リポジトリで実行：`repo-checks`）
* **絵文字禁止（`lint-no-emoji`）**: コードや文書への絵文字混入を検出（`★`・`♪` などの日本語記号やロックファイルは除外）
* **シークレット・機密ファイル検出**: `.env`（`.env.example` 等は許可），`AuthKey_*.p8` / `*.p8`，SSH 秘密鍵，キーストア，主要クラウド・AI・SaaS トークン，秘密鍵ヘッダー（Firebase 公開クライアント設定は既定で除外）
* **リポジトリ衛生**: マージコンフリクトマーカー残留（片割れも検出，Markdown 単独 `=======` は無視），巨大ファイル（既定 10MB 超）
* **構文チェック**: JSON（JSONC 自動除外），TOML，YAML（カスタムタグ対応），Apple Plist / Entitlements / `PrivacyInfo.xcprivacy` / `.xcstrings`，Shell（拡張子なし shebang 含む＋`shellcheck -S error`），Ruby（`Podfile` / `Gemfile` / `Fastfile` 等含む），`package.json` 無しの素の JS，Dockerfile（ヒアドキュメント・`# escape=` 対応）

### 2. スタック自動検出ジョブ（該当構成がある場合のみ起動）
* **Node.js / TypeScript（`node`）**: `pnpm` / `yarn`（v1・Berry） / `bun` / `npm` を自動判別し，`lint` / `typecheck` / `format:check` / `test` / `build` と脆弱性監査（`audit`）を実行
* **Python（`python`）**: `uv` / `pip` を自動選択し，構文チェック（`py_compile`），`ruff`，`pytest`（テスト 0 件の終了コード 5 許容），`pip-audit` を実行
* **iOS / macOS（`apple` / `macos-26`）**: `.xcworkspace` / `.xcodeproj` / XcodeGen / SwiftPM / CocoaPods / SwiftLint に対応．署名なしで自動ビルド＆ユニットテスト（UI テスト除外）を実行
* **Flutter / Dart（`flutter-dart`）**: `pubspec.yaml` から Flutter / Dart を判別し `pub get`・`analyze`・`test` を実行
* **.NET / C#（`dotnet`）**: Unity（`ProjectSettings/`）やモバイル専用ワークロードを除外し，`restore`・`build`・`test` を実行

## 制御オプション・スキップ方法

* **1 回だけスキップ**: `GH_SKIP_CI_INIT=1 gh repo create ...` または `GH_SKIP_CI_INIT=1 git push`
* **リポジトリ単位で無効化**: `git config hooks.skipCiInit true`
* **テンプレートを一時差し替え**: `GH_CI_TEMPLATE=/path/to/custom.yml`
* **CI 内の特定行だけ例外化**: 該当行のコメントに `ci-ignore` を記載
* **既存のローカルリポジトリへ `pre-push` スタブを反映**: 対象リポジトリ内で `git init` を 1 回実行（既存のフックは上書きされません）

## 既知の制約

* `init.templateDir` のフックは新規 `git init` / `git clone` 時に展開されます．Husky 等で `core.hooksPath` を上書きしているリポジトリや，Xcode 内蔵の Git UI 操作では動作しません．
* `gh repo create` の対話モード（`--source` なし）はホームディレクトリ等の誤検出を避けるためラッパーでは介入せず，初回 `git push` 時の `pre-push` フックで処理します．
* Private リポジトリでは `apple` ジョブの `macos-26` ランナーは無料枠の消費倍率が高くなります．不要なリポジトリでは生成後の `ci.yml` の `apple:` ジョブの `if:` を `false` に変更してください．

## テスト

```bash
pytest
```
