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

Viewer の `Hierarchy` は呼び出し元を左、呼び出し先を右へ配置します。共通の呼び出し先は一つにまとめ、循環するノードは同じ列に置きます。`By file` ではファイルごとの配置に切り替えられます。直接の呼び出し元はオレンジ、呼び出し先は青、両方に当てはまる周辺ノードは紫で強調します。推定された関係は破線です。`Fit` で全体を見渡せます。`Clear focus` または Esc で強調を解除できます。

左の `Symbols` タブでは関数名や完全修飾名の一部で絞り込めます。`Files` タブではフォルダ階層から探すか、ファイル名や相対パスで絞り込めます。ファイルを選ぶと、そのファイルの全ノードとファイル内の呼び出しを強調し、ソースを表示します。Ctrl+F で現在のタブの検索欄に移り、Enter で最初の結果を選べます。

Viewer はダーク表示で起動します。右上の `Dark` でライト表示に切り替えられます。ソースペインはキーワード、文字列、コメント、数値を色分けし、キーワードは太字、選択した関数の範囲は背景色で強調します。

関数・メソッドを選ぶと、右下の `Notes` で保存済みの注釈を読めます。`Add note` でメモを追加すると、グラフのノードに目印が付きます。

中央のグラフでは、ホイールで縦、Shift＋ホイールで横にスクロールできます。Ctrl＋ホイールはマウスポインターを中心に拡大・縮小します。左ドラッグや、グラフにフォーカスがあるときの矢印キーでも上下左右に移動できます。

一覧で取得したシンボル ID を使って詳細や呼び出し元を調べ、注釈を付けられます。

```powershell
uv run --locked cg --project $project status --format json
uv run --locked cg --project $project search '決済' --format json
uv run --locked cg --project $project inspect $id --format json
uv run --locked cg --project $project refs $id --direction incoming --format json
uv run --locked cg --project $project annotate $id --type NOTE --text '互換性維持のための分岐'
uv run --locked cg --project $project inspect --annotations --format json
```

`search` は名前、完全修飾名、パス、対象ノードが残っている注釈の本文を検索します。`status` が `unbuilt` または `stale` のときは、`cg update` でソースからグラフを更新できます。同じ内容の構文エラーが残っている場合、更新済みの状態と診断が両方返ります。

このリポジトリには Codex 用の [CodeGraph Skill](.agents/skills/codegraph/SKILL.md) が含まれます。別の対象リポジトリで使う場合は、そのリポジトリの `.agents/skills/` に `codegraph` ディレクトリを配置し、対象を `cg --project` で指定できます。

ソース座標は行・列ともに 1 始まりで、範囲の終点はその文字を含みません。`analyze` の JSON にはファイル別診断が含まれ、構文エラーのあるファイルの前回のシンボルには `stale` が付きます。解決できなかった呼び出しは `inspect` の `unresolved_calls` に、同一クラスの `self.method()` は `INFERRED` の参照に現れます。

プロジェクト内の単純なクラスについては、コンストラクター、戻り値の型注釈、引数の型注釈からローカル変数や `self` の属性の型を追います。これらのレシーバー経由の呼び出しも `INFERRED` になります。

`.codegraph/annotations/annotations.jsonl` に注釈の正本を保存します。注釈対象の変更や削除は、再解析後に `NEEDS_REVIEW` または `ORPHANED` として確認できます。`graph.db` を再構築するときは `cg analyze` を実行します。

## I/O の取り込み

Agent は SQL、ORM、ファイル操作のコードを読み、実際に操作する関数・メソッドの ID と根拠行を調べます。`cg search` や `cg inspect` で ID を取得できます。ソースファイルごとに次の JSON を一つ作ります。`inputs` には調査に使った各ファイルの内容から計算した小文字の SHA-256 を入れます。パスはすべて対象プロジェクトからの相対パスで、区切りは `/` です。

```json
{
  "version": 1,
  "source": "src/repository.py",
  "inputs": {
    "src/repository.py": "<SHA-256>",
    "migrations/001_orders.sql": "<SHA-256>"
  },
  "accesses": [
    {
      "node_id": "<cg が返したメソッド ID>",
      "operation": "READS",
      "line": 12,
      "resource": {
        "kind": "table",
        "database": "app",
        "schema": "main",
        "name": "orders",
        "definition": {"path": "migrations/001_orders.sql", "line": 1}
      }
    }
  ]
}
```

- 操作は `READS` または `WRITES` です。確定したファイルは `{"kind": "file", "path": "exports/orders.csv"}` で表します。
- 実行時にパスが決まるファイルは `{"kind": "handle", "anchor": {"kind": "parameter", "node_id": "<所有メソッド ID>", "name": "sink"}}` のように指定できます。開く箇所を使う場合は `anchor` の `kind` を `open` にし、`name` の代わりに 1 始まりの `line` と `column` を指定します。同じハンドルを渡していることが根拠から分かる場合は、別メソッドから同じアンカーを参照できます。
- テーブルの `definition` は任意です。対象を特定できないときは `resource: null` と `reason` を記録します。

```powershell
uv run --locked cg --project $project import-io .\agent-output.json --format json
uv run --locked cg --project $project io --format json
```

取り込みは同じソースファイルの既存文書をまとめて置き換えます。`accesses: []` でそのファイルの古い I/O 関係を外せます。取り込んだ JSON は対象プロジェクトの `.codegraph/facts/` に保存されます。入力が変更された文書は `cg io` に `STALE` と表示され、更新済みの文書を再取り込みすると辺が戻ります。

Viewer の `I/O` を有効にすると、右側のテーブル・ファイル・ハンドルが呼び出しグラフに加わります。読み取りは緑、書き込みはピンクの点線です。リソースを選ぶと、アクセス元と根拠行を確認できます。

### Viewer の起動確認

Tk 自体の動作を確認するには `uv run --locked python -m tkinter` を実行します。Windows で `init.tcl` のエラーが出る場合は、通常の PowerShell セッションでも同じコマンドを試してください。サンドボックス内では Tcl の ZIP ファイルのパス解決が壊れる場合があります。Tk を含む別の Python 3.14 を使う場合は `uv sync --locked --python <python.exe のパス>` で選択できます。
