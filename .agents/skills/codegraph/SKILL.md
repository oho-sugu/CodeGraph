---
name: codegraph
description: Explore a Python repository with the CodeGraph CLI. Use to find functions or notes, trace callers and callees, explain code with source evidence, or refresh an existing CodeGraph snapshot.
---

# CodeGraph

Use `cg` in the target project, or run `uv run --locked cg --project <project-root>` from a CodeGraph checkout.

1. Run `cg status --format json`. For an `unbuilt` graph, run `cg analyze` when a local analysis is within the task. For a `stale` graph, run `cg update` when refreshing is within the task. For read-only work, use current source directly and report that graph evidence may be stale. If the project is missing its manifest, use source search or initialize it when the user requests setup.
2. Run `cg search <query> --format json` and select a node by its ID, kind, qualified name, and source path. Use `--type Function` or `--type Method` to narrow ambiguous matches.
3. Run `cg inspect <node-id> --format json` and `cg refs <node-id> --direction incoming --edge CALLS --format json` for impact analysis. Use `outgoing` for implementation flow and follow additional hops where they matter. Use a visited set when following multiple hops.
4. Open the cited source before presenting important claims. Include paths and lines for key edges. Report `INFERRED`, stale nodes, and unresolved calls where they affect the answer.

Use `cg annotate <node-id> --type NOTE --text <body>` only when the user authorizes saving a note. If CodeGraph commands are unavailable, continue with repository source search and report the limitation.


## File and database I/O

When the user requests I/O mapping, read the CodeGraph checkout's README section on I/O import for the versioned JSON contract. Check the target graph with `cg status`, then use `cg search` and `inspect` to get symbol IDs. Follow SQL, ORM mappings, and file handles far enough to identify the symbol that actually performs the I/O. Record direct reads and writes, their source lines, and the hashes of the source and any definitions or mappings used. Use a path when it is known, otherwise use a supported handle anchor when evidence identifies it. Leave ambiguous targets unresolved with a reason. Import the completed document with `cg import-io <artifact> --format json`, then inspect it with `cg io --format json`.
