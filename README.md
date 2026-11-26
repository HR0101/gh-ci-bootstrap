# gh-ci-bootstrap

新規リポジトリ作成時（`gh repo create --source=...`）および Web 版 GitHub で空リポジトリを作って初回 `git push` した際に，汎用 GitHub Actions ワークフロー（`.github/workflows/ci.yml`）と標準設定ファイル群を自動生成・単独コミットするツール一式です．

## 主な機能

1. **Reusable Workflow による一元管理（`@v1`）**
   * 生成される `.github/workflows/ci.yml` は `HR0101/gh-ci-bootstrap/.github/workflows/common-ci.yml@v1` を呼ぶシンプルな構成で，共通チェックのルール改善を既存リポへも反映できます．
   * リポ単位で `with:` 入力による ON/OFF や上限調整，固有ジョブの追加も可能です．
2. **ローカル即時チェック（`ci-check`）＆ コミット前ブロック（`pre-commit`）**
   * `ci-check` コマンドで push 前に手元で 1 秒以内に CI と同じ共通チェックを実行できます．
   * `pre-commit` フックにより，絵文字・シークレット・コンフリクトマーカー・構文エラーの混入をコミット時点で防ぎます．
3. **変更ファイル判定による無料枠・時間節約**
   * PR / push の変更差分を解析し，ドキュメントのみの変更なら言語別ビルドをスキップ．また混在リポでも変更のあったスタック（特に `macos-26` を使う Apple ビルド等）だけを起動します．
4. **リポジトリ初期設定の自動生成**
   * まだ存在しない場合に限り，`.github/workflows/ci.yml`・`.github/dependabot.yml`（スタック自動判定）・`.editorconfig`・`.gitattributes` をまとめて初期化します．

## 構成

| パス | 配置先（シンボリックリンク） | 役割 |
| --- | --- | --- |
| `bin/gh` | `~/.local/bin/gh` | `gh repo create --source=...` 成功後に `ci-init` を呼び出し，`--push` 時は追加コミットも自動プッシュ |
| `bin/ci-check` | `~/.local/bin/ci-check` | ローカルリポジトリに対して `repo_check.py` を即実行する CLI |
| `scripts/repo_check.py` | `~/.local/share/gh-wrapper/repo_check.py` | CI・`ci-check`・`pre-commit` 共通の検査エンジン |
| `scripts/ci-init` | `~/.local/share/gh-wrapper/ci-init` | CI・Dependabot・EditorConfig・GitAttributes の生成・単独コミット処理 |
| `scripts/generate_dependabot.py` | `~/.local/share/gh-wrapper/generate_dependabot.py` | スタックに応じた `.github/dependabot.yml` 自動生成 |
| `scripts/pre-push-ci-init` | `~/.local/share/gh-wrapper/pre-push-ci-init` | 空の自分の GitHub リポジトリへの初回 `git push` 時のみ発火するフック本体 |
| `scripts/pre-commit-ci-check` | `~/.local/share/gh-wrapper/pre-commit-ci-check` | ステージ済み変更を事前検査する `pre-commit` フック本体 |
| `hooks/pre-push` | `~/.git-templates/hooks/pre-push` | `pre-push-ci-init` を `exec` する薄いスタブ |
| `hooks/pre-commit` | `~/.git-templates/hooks/pre-commit` | `pre-commit-ci-check` を `exec` する薄いスタブ |
| `templates/ci.yml` | `~/.local/share/gh-wrapper/ci.yml` | 生成される呼び出し用 `.github/workflows/ci.yml` テンプレート |
| `.github/workflows/common-ci.yml` | — | Reusable Workflow 本体（`workflow_call`） |

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

## 汎用 CI（`common-ci.yml`）のチェック内容

### 1. 共通チェック（全リポジトリで実行：`repo-checks`）
* **絵文字禁止（`lint-no-emoji`）**: コードや文書への絵文字混入を検出（`★`・`☆`・`♪`・`→`・`※` などの日本語記号やロックファイルは除外，`U+FE0F` 無しの記号系絵文字にも対応）
* **シークレット・機密ファイル検出**: `.env`（`.env.example` 等は許可），`AuthKey_*.p8` / `*.p8`，SSH 秘密鍵，キーストア，主要クラウド・AI・SaaS トークン，秘密鍵ヘッダー（Firebase 公開クライアント設定は既定で除外）
* **リポジトリ衛生**: マージコンフリクトマーカー残留（片割れも検出，Markdown 単独 `=======` は無視），巨大ファイル（既定 10MB 超）
* **構文チェック**: JSON（JSONC 自動除外），TOML，YAML（カスタムタグ対応），Apple Plist / Entitlements / `PrivacyInfo.xcprivacy` / `.xcstrings`，Shell（拡張子なし shebang 含む＋`shellcheck -S error`），Ruby（`Podfile` / `Gemfile` / `Fastfile` 等含む），`package.json` 無しの素の JS，Dockerfile（ヒアドキュメント・`# escape=` 対応）

### 2. スタック自動検出ジョブ（該当構成・変更がある場合のみ起動）
* **Node.js / TypeScript（`node`）**: `pnpm` / `yarn`（v1・Berry） / `bun` / `npm` を自動判別し，`lint` / `typecheck` / `format:check` / `test` / `build` と脆弱性監査（`audit`）を実行
* **Python（`python`）**: `uv` / `pip` を自動選択し，構文チェック（`py_compile`），`ruff`，`pytest`（テスト 0 件の終了コード 5 許容），`pip-audit` を実行
* **iOS / macOS（`apple` / `macos-26`）**: `.xcworkspace` / `.xcodeproj` / XcodeGen / SwiftPM / CocoaPods / SwiftLint に対応．署名なしで自動ビルド＆ユニットテスト（UI テスト除外）を実行
* **Flutter / Dart（`flutter-dart`）**: `pubspec.yaml` から Flutter / Dart を判別し `pub get`・`analyze`・`test` を実行
* **.NET / C#（`dotnet`）**: Unity（`ProjectSettings/`）やモバイル専用ワークロードを除外し，`restore`・`build`・`test` を実行

## 制御オプション・スキップ方法

* **ローカル手動チェック**: `ci-check`（全体），`ci-check --staged`（ステージ済みのみ），`ci-check --doctor`（依存ツール診断），`ci-check --strict-emoji`（`U+FE0F` 無しの記号も厳格検出）
* **`pre-commit` を 1 回だけスキップ**: `CI_CHECK_SKIP=1 git commit ...`
* **`pre-commit` をリポ単位で無効化**: `git config hooks.skipPreCommit true`
* **`gh` ラッパー / `pre-push` を 1 回だけスキップ**: `GH_SKIP_CI_INIT=1 gh repo create ...` または `GH_SKIP_CI_INIT=1 git push`
* **`ci.yml` のみ生成（Dependabot / EditorConfig 等を省く）**: `GH_CI_MINIMAL=1`
* **CI 内の特定行だけ例外化**: 該当行のコメントに `ci-ignore` を記載
* **既存リポジトリへフックを反映**: 対象リポジトリ内で `git init` を 1 回実行（既存フックは上書きされません）

## 既知の制約

* `init.templateDir` のフックは新規 `git init` / `git clone` 時に展開されます．Husky 等で `core.hooksPath` を上書きしているリポジトリや，Xcode 内蔵の Git UI 操作では動作しません．
* `gh repo create` の対話モード（`--source` なし）はホームディレクトリ等の誤検出を避けるためラッパーでは介入せず，初回 `git push` 時の `pre-push` フックで処理します．
* Private リポジトリでは `apple` ジョブの `macos-26` ランナーは無料枠の消費倍率が高くなります．不要なリポジトリでは生成後の `ci.yml` で `enable-apple: false` を渡してください．

## 開発・テスト

`scripts/repo_check.py` を編集した場合は，以下で `common-ci.yml` へ同期してからテストを実行してください：

```bash
python3 scripts/sync_workflows.py
pytest
```
