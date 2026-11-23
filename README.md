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

左の `Notes` タブからメモ本文・関数名・ファイルパスで検索できます。結果を選ぶとグラフとソースへ移動します。`Edit` で内容を書き直し、`Delete` で削除できます。再解析後に確認が必要になったメモや、対象が消えたメモも一覧に表示されます。

中央のグラフでは、ホイールで縦、Shift＋ホイールで横にスクロールできます。Ctrl＋ホイールはマウスポインターを中心に拡大・縮小します。空白部分の左ドラッグや、グラフにフォーカスがあるときの矢印キーでも上下左右に移動できます。

ノードを左ドラッグすると、接続を保ったまま表示位置を調整できます。手動の位置は `.codegraph/view-layout.json` にレイアウト別で保存し、次に Viewer を開いたときに復元します。ズームとスクロール位置は Viewer を開いている間のみ、タブとレイアウトを切り替えても復元されます。

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

ソース座標は行・列ともに 1 始まりで、範囲の終点はその文字を含みません。`analyze` の JSON にはファイル別診断が含まれ、構文エラーのあるファイルの前回のシンボルには `stale` が付きます。解決できなかった呼び出しは `inspect` の `unresolved_calls` に、同一クラスの `self.method()` は `INFERRED` の参照に現れます。

プロジェクト内の単純なクラスについては、コンストラクター、戻り値の型注釈、引数の型注釈からローカル変数や `self` の属性の型を追います。これらのレシーバー経由の呼び出しも `INFERRED` になります。

`.codegraph/annotations/annotations.jsonl` に注釈の正本を保存します。注釈対象の変更や削除は、再解析後に `NEEDS_REVIEW` または `ORPHANED` として確認できます。`graph.db` を再構築するときは `cg analyze` を実行します。

このリポジトリをセルフ解析してできる `.codegraph/` は、フォルダ全体を Git の除外対象にしています。

## 別のプロジェクトを解析する

CodeGraph のチェックアウトと、解析したい Python プロジェクトの絶対パスを指定します。次の PowerShell コマンドはどちらのディレクトリからでも実行できます。

```powershell
$codegraph = 'C:\tools\CodeGraph'
$project = 'C:\work\my-python-project'

uv sync --locked --project $codegraph
uv run --locked --project $codegraph cg --project $project init
uv run --locked --project $codegraph cg --project $project analyze
uv run --locked --project $codegraph cg --project $project search '関数名' --format json
uv run --locked --project $codegraph cg --project $project view
```

- `uv` の `--project $codegraph` は、CodeGraph を実行する環境を指定します。
- `cg` の `--project $project` は、解析対象と `.codegraph/` の保存先を指定します。
- 対象プロジェクトの Python コードが `src/` 以下にある場合は、初回の `init` に `--source-root src` を付けます。ソースを編集した後は `uv run --locked --project $codegraph cg --project $project update` を実行し、Viewer の **Refresh graph** を押します。

### 対象プロジェクトに Skill を配置する

対象プロジェクトの `.agents/skills/` に、CodeGraph の `codegraph/` ディレクトリを丸ごとコピーします。

```powershell
$skills = Join-Path $project '.agents/skills'
New-Item -ItemType Directory -Force -Path $skills | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $skills 'codegraph'))) {
    Copy-Item -LiteralPath (Join-Path $codegraph '.agents/skills/codegraph') -Destination $skills -Recurse
}
```

コピー先は `.agents/skills/codegraph/SKILL.md` と `.agents/skills/codegraph/references/io-format.md` です。同名の Skill がすでにある場合は、差分を確認して更新します。対象プロジェクトを Codex で開き、以下の呼び出し方を使います。

## Codex で CodeGraph Skill を使う

Codex の入力欄で `$codegraph` に続けて依頼を書きます。このリポジトリでは、たとえば `$codegraph Project.analyze の呼び出し元と根拠行を調べて` と入力します。 [CodeGraph Skill](.agents/skills/codegraph/SKILL.md)

### ファイル・DB の I/O を解析させる

Codex の入力欄に次を貼り付けます。

```text
$codegraph reveal file and database i/o
```

対象を絞るときは、たとえば `$codegraph src/repository.py のファイルと DB の入出力を解析し、結果を CodeGraph に取り込んで` と依頼します。Skill はグラフの状態を確認して、直接読み書きする関数・メソッドと根拠行を探し、ソースファイル単位の JSON を生成して `cg import-io` で登録します。

別のプロジェクトで `cg` が PATH にない場合は、Codex への依頼に CodeGraph のチェックアウトの絶対パスを添えます。たとえば「CodeGraph は `C:\tools\CodeGraph` にあります。CLI は `uv run --locked --project C:\tools\CodeGraph cg --project C:\work\my-python-project` で実行してください」と伝えます。

完了したら、開いている Viewer で **Refresh graph** を押し、**I/O** をオンにします。リソースを選ぶとアクセス元と根拠行が見えます。CLI で確認する場合は `uv run --locked --project $codegraph cg --project $project io --format json` を実行します。取り込み済みの JSON は対象プロジェクトの `.codegraph/facts/` にあります。

## I/O の取り込み形式

JSON の項目と例は、Skill に同梱した [I/O 出力形式 v1](.agents/skills/codegraph/references/io-format.md) にあります。ソースファイルごとに、読み書きするシンボルの ID、根拠行、操作対象、参照したファイルのハッシュを記録します。

JSON を手元で作成した場合は、次のコマンドで登録・確認できます。

```powershell
uv run --locked --project $codegraph cg --project $project import-io .\agent-output.json --format json
uv run --locked --project $codegraph cg --project $project io --format json
```

取り込みは同じソースファイルの既存文書をまとめて置き換えます。`accesses: []` でそのファイルの古い I/O 関係を外せます。取り込んだ JSON は対象プロジェクトの `.codegraph/facts/` に保存されます。入力が変更された文書は `cg io` に `STALE` と表示され、更新済みの文書を再取り込みすると辺が戻ります。

Viewer の `I/O` を有効にすると、右側のテーブル・ファイル・ハンドルが呼び出しグラフに加わります。読み取りは緑、書き込みはピンクの点線です。リソースを選ぶと、アクセス元と根拠行を確認できます。

### Viewer の起動確認

Tk 自体の動作を確認するには `uv run --locked python -m tkinter` を実行します。Windows で `init.tcl` のエラーが出る場合は、通常の PowerShell セッションでも同じコマンドを試してください。サンドボックス内では Tcl の ZIP ファイルのパス解決が壊れる場合があります。Tk を含む別の Python 3.14 を使う場合は `uv sync --locked --python <python.exe のパス>` で選択できます。
