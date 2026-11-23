# I/O output format (version 1)

Write one UTF-8 JSON object for each analyzed Python source file. Replace the example paths, hashes, symbol ID, and lines with values from the target project.

```json
{
  "version": 1,
  "source": "src/repository.py",
  "inputs": {
    "src/repository.py": "<lowercase SHA-256>",
    "migrations/001_orders.sql": "<lowercase SHA-256>"
  },
  "accesses": [
    {
      "node_id": "<CodeGraph symbol ID>",
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

- `source` is the file that owns the symbols in `accesses`. Paths are project-relative and use `/`. Compute a lowercase SHA-256 from the raw bytes of the source and every file used as evidence, then put those paths and hashes in `inputs`.
- Each access has a `node_id` from `cg search` or `cg inspect`, the `operation` `READS` or `WRITES`, a one-based `line` within the symbol, and a `resource`.
- A table uses `kind: "table"`, a logical `database` name, `schema`, and `name`. If known, add `definition` with a project-relative `path` and one-based `line`, and include the definition file in `inputs`.
- A known file uses `{"kind": "file", "path": "exports/orders.csv"}`.
- A handle passed as an argument uses `{"kind": "handle", "anchor": {"kind": "parameter", "node_id": "<owner symbol ID>", "name": "sink"}}`.
- A handle opened in code uses `{"kind": "handle", "anchor": {"kind": "open", "node_id": "<owner symbol ID>", "line": 9, "column": 14}}`. Lines and columns are one-based. Include the anchor owner's source in `inputs`. Reuse the same anchor only when the evidence shows that the same handle is passed between symbols.
- For an unresolved target, use `"resource": null` and add a non-empty `reason` to the access.

Run `cg import-io <artifact> --format json`, then check `cg io --format json`. An import replaces the entire document for that `source`, so keep the existing claims that are still valid when refreshing part of a file. Use `"accesses": []` to intentionally clear the file's I/O claims.
