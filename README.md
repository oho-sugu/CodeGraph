# CodeGraph

Python リポジトリを静的に解析し、関数・メソッドの呼び出し関係と根拠コードを CLI とローカルの Tk Viewer で探索できます。設計全体は [SPEC_ja.md](SPEC_ja.md) に記載しています。

## セットアップ

CodeGraph のチェックアウト内で実行します。Python 3.14 と uv を使用します。

```powershell
uv python install 3.14
uv sync --locked
uv run --locked python -m unittest discover -s tests
```

## 使い方

```powershell
$project = 'C:\path\to\your-project'
uv run --locked cg --project $project init
uv run --locked cg --project $project analyze
uv run --locked cg --project $project inspect --format json
uv run --locked cg --project $project view
```

ソースが `src/` 以下にあるプロジェクトでは、初期化時に `init --source-root src` を指定します。インストール済みの `cg` を対象プロジェクト内から実行する場合、プロジェクトは親ディレクトリから自動で検出されます。

Viewer は関数・メソッドと呼び出し関係の全体を、ファイルごとにまとめて表示します。ノードを選ぶと、ソースペインが対応する行へ移動し、直接の呼び出し元・先が強調されます。`Fit` で全体を見渡せます。`Clear focus` または Esc で強調を解除できます。

一覧で取得したシンボル ID を使って詳細や呼び出し元を調べ、注釈を付けられます。

```powershell
uv run --locked cg --project $project inspect $id --format json
uv run --locked cg --project $project refs $id --direction incoming --format json
uv run --locked cg --project $project annotate $id --type NOTE --text '互換性維持のための分岐'
uv run --locked cg --project $project inspect --annotations --format json
```

ソース座標は行・列ともに 1 始まりで、範囲の終点はその文字を含みません。`analyze` の JSON にはファイル別診断が含まれ、構文エラーのあるファイルの前回のシンボルには `stale` が付きます。解決できなかった呼び出しは `inspect` の `unresolved_calls` に、同一クラスの `self.method()` は `INFERRED` の参照に現れます。

`.codegraph/annotations/annotations.jsonl` に注釈の正本を保存します。注釈対象の変更や削除は、再解析後に `NEEDS_REVIEW` または `ORPHANED` として確認できます。`graph.db` を再構築するときは `cg analyze` を実行します。

### Viewer の起動確認

Tk 自体の動作を確認するには `uv run --locked python -m tkinter` を実行します。Windows で `init.tcl` のエラーが出る場合は、通常の PowerShell セッションでも同じコマンドを試してください。サンドボックス内では Tcl の ZIP ファイルのパス解決が壊れる場合があります。Tk を含む別の Python 3.14 を使う場合は `uv sync --locked --python <python.exe のパス>` で選択できます。
