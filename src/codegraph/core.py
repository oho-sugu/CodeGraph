"""Project storage and shared operations used by the CLI and the Viewer."""

import getpass
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .analysis import decode_source, digest, json_text, parse_file, python_files, relations, stable_id


SCHEMA_VERSION = 1
ANNOTATION_TYPES = ("NOTE", "EXPLANATION", "WARNING", "QUESTION", "DECISION", "TODO", "TAG")
ONTOLOGY = {
    "version": 1,
    "nodes": ["Module", "Class", "Function", "Method"],
    "edges": {
        "CALLS": {"from": ["Function", "Method"], "to": ["Function", "Method"]},
        "CONTAINS": {"from": ["Module", "Class", "Function", "Method"], "to": ["Class", "Function", "Method"]},
        "IMPORTS": {"from": ["Module", "Class", "Function", "Method"], "to": ["Module"]},
    },
}
MIGRATIONS = {
    1: (
        "CREATE TABLE files (path TEXT PRIMARY KEY, payload TEXT NOT NULL)",
        """CREATE TABLE nodes (
            id TEXT PRIMARY KEY, path TEXT NOT NULL REFERENCES files(path),
            type TEXT NOT NULL, payload TEXT NOT NULL
        )""",
        "CREATE INDEX nodes_by_type ON nodes(type)",
        """CREATE TABLE edges (
            id TEXT PRIMARY KEY, type TEXT NOT NULL,
            from_id TEXT NOT NULL REFERENCES nodes(id),
            to_id TEXT NOT NULL REFERENCES nodes(id), payload TEXT NOT NULL
        )""",
        "CREATE INDEX edges_outgoing ON edges(from_id, type)",
        "CREATE INDEX edges_incoming ON edges(to_id, type)",
        """CREATE TABLE unresolved (
            node_id TEXT NOT NULL REFERENCES nodes(id), payload TEXT NOT NULL,
            PRIMARY KEY (node_id, payload)
        )""",
        "CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, analyzed_at TEXT NOT NULL)",
        """CREATE TABLE graph_state (
            slot INTEGER PRIMARY KEY CHECK (slot = 1),
            run_id TEXT NOT NULL REFERENCES analysis_runs(id)
        )""",
    ),
}


class CodeGraphError(Exception):
    pass


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, encoding="utf-8", newline="\n", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_directory(root, setting):
    relative = Path(setting)
    if relative.anchor:
        raise CodeGraphError("The source root must be relative to the project.")
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise CodeGraphError("The source root must be an existing directory inside the project.")
    return target


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CodeGraphError(f"Cannot read {path.name}: {error}") from error


def connect(path, *, create=False):
    if create:
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
    else:
        db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=10, isolation_level=None)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise CodeGraphError(f"Database schema {version} requires a newer CodeGraph.")
    for target in range(version + 1, SCHEMA_VERSION + 1):
        for statement in MIGRATIONS[target]:
            db.execute(statement)
        db.execute(f"PRAGMA user_version = {target}")


def init(root, source_root=None):
    root = Path(root).resolve()
    home = root / ".codegraph"
    requested = source_directory(root, source_root) if source_root is not None else None
    if not root.is_dir():
        raise CodeGraphError("The project directory does not exist.")
    home.mkdir(exist_ok=True)
    with closing(connect(home / "graph.db", create=True)) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("BEGIN IMMEDIATE")
        with db:
            migrate(db)
            manifest = home / "manifest.json"
            if manifest.exists():
                project = Project(root)
                if requested is not None and requested != project.source_root:
                    raise CodeGraphError("The project already has a different source root.")
            else:
                target = requested or root
                data = {
                    "schema_version": SCHEMA_VERSION, "project_id": str(uuid.uuid4()),
                    "source_root": target.relative_to(root).as_posix(),
                }
            for directory in ("ontology", "annotations"):
                (home / directory).mkdir(exist_ok=True)
            initial_files = {
                home / "ontology" / "ontology.json": json_text(ONTOLOGY) + "\n",
                home / "annotations" / "annotations.jsonl": "",
                home / ".gitignore": "/graph.db\n/graph.db-*\n/cache/\n",
            }
            for path, content in initial_files.items():
                if not path.exists():
                    atomic_write(path, content)
            if not manifest.exists():
                atomic_write(manifest, json_text(data) + "\n")
    return Project(root)


def discover(start=None):
    if start is not None:
        return Project(Path(start).resolve())
    cwd = Path.cwd()
    for path in (cwd, *cwd.parents):
        if (path / ".codegraph" / "manifest.json").is_file():
            return Project(path)
    raise CodeGraphError("No CodeGraph project found. Run cg init first.")


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = self.root / ".codegraph"
        self.db_path = self.home / "graph.db"
        self.annotation_path = self.home / "annotations" / "annotations.jsonl"
        manifest_path = self.home / "manifest.json"
        if not manifest_path.is_file():
            raise CodeGraphError(f"No CodeGraph project at {self.root}. Run cg init first.")
        self.manifest = read_json(manifest_path)
        if (
            not isinstance(self.manifest, dict)
            or self.manifest.get("schema_version") != SCHEMA_VERSION
            or not isinstance(self.manifest.get("project_id"), str)
            or not isinstance(self.manifest.get("source_root"), str)
        ):
            raise CodeGraphError("Invalid or unsupported CodeGraph manifest.")
        self.source_root = source_directory(self.root, self.manifest["source_root"])

    @contextmanager
    def database(self, *, write=False):
        if write and not self.db_path.exists():
            init(self.root)
        if not self.db_path.is_file():
            raise CodeGraphError("The graph database is missing. Run cg analyze to rebuild it.")
        with closing(connect(self.db_path)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                db.execute("BEGIN IMMEDIATE")
                with db:
                    migrate(db)
            elif version > SCHEMA_VERSION:
                raise CodeGraphError(f"Database schema {version} requires a newer CodeGraph.")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db

    def analyze(self):
        with self.database(write=True) as db:
            previous_files = self.payloads(db, "SELECT path, payload FROM files")
            previous_nodes = self.payloads(db, "SELECT id, payload FROM nodes")
            previous_edges = self.payloads(db, "SELECT id, payload FROM edges")
            files, nodes, parsed = {}, {}, []
            old_nodes_by_path = {}
            for node in previous_nodes.values():
                old_nodes_by_path.setdefault(node["source_ref"]["path"], []).append(node)
            for path in python_files(self.source_root):
                relative = path.relative_to(self.root).as_posix()
                try:
                    file = parse_file(self.root, self.source_root, path)
                except (SyntaxError, UnicodeError, OSError, ValueError) as error:
                    diagnostic = {"path": relative, "message": error.msg if isinstance(error, SyntaxError) else str(error)}
                    if isinstance(error, SyntaxError):
                        diagnostic.update(line=error.lineno, column=error.offset)
                    previous = previous_files.get(relative, {})
                    files[relative] = {**previous, "diagnostic": diagnostic}
                    for node in old_nodes_by_path.get(relative, ()):
                        nodes[node["id"]] = {**node, "stale": True}
                else:
                    parsed.append(file)
                    files[relative] = {"source_hash": file.source_hash, "diagnostic": None}
                    for scope in file.scopes.values():
                        node = {**scope.node, "stale": False}
                        nodes[node["id"]] = node
            edges, unresolved = relations(parsed)
            for node in nodes.values():
                if node["parent_id"]:
                    edges.append({
                        "id": stable_id("contains", node["parent_id"], node["id"]),
                        "type": "CONTAINS", "from_id": node["parent_id"], "to_id": node["id"],
                        "properties": {}, "evidence": [node["source_ref"]],
                        "provenance": {"method": "STATIC", "status": "UNKNOWN" if node["stale"] else "VERIFIED"},
                    })
            edges = {edge["id"]: edge for edge in edges}
            run_id = stable_id("run", files, nodes, edges, unresolved)
            db.execute("INSERT OR IGNORE INTO analysis_runs VALUES (?, ?)", (run_id, now()))
            db.execute("INSERT OR REPLACE INTO graph_state VALUES (1, ?)", (run_id,))
            self.store_snapshot(db, files, nodes, edges, unresolved)
            return {
                "analysis_run_id": run_id,
                "files": len(files), "nodes": len(nodes), "edges": len(edges),
                "unresolved_calls": len(unresolved),
                "node_changes": self.changes(previous_nodes, nodes),
                "edge_changes": self.changes(previous_edges, edges),
                "diagnostics": [file["diagnostic"] for file in files.values() if file["diagnostic"]],
            }

    @staticmethod
    def store_snapshot(db, files, nodes, edges, unresolved):
        for table in ("unresolved", "edges", "nodes", "files"):
            db.execute(f"DELETE FROM {table}")
        db.executemany("INSERT INTO files VALUES (?, ?)", (
            (path, json_text(file)) for path, file in files.items()
        ))
        db.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?)", (
            (node["id"], node["source_ref"]["path"], node["type"], json_text(node))
            for node in nodes.values()
        ))
        db.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?)", (
            (edge["id"], edge["type"], edge["from_id"], edge["to_id"], json_text(edge))
            for edge in edges.values()
        ))
        db.executemany("INSERT INTO unresolved VALUES (?, ?)", (
            (call["node_id"], json_text(call)) for call in unresolved
        ))

    @staticmethod
    def changes(before, after):
        return {
            "added": len(after.keys() - before.keys()),
            "removed": len(before.keys() - after.keys()),
            "updated": sum(before[key] != after[key] for key in before.keys() & after.keys()),
        }

    @staticmethod
    def payloads(db, query):
        return {key: json.loads(payload) for key, payload in db.execute(query)}

    @staticmethod
    def run_id(db):
        row = db.execute("SELECT run_id FROM graph_state WHERE slot = 1").fetchone()
        return row[0] if row else None

    @staticmethod
    def with_provenance(node, run_id):
        status = "UNKNOWN" if node["stale"] else "VERIFIED"
        return {**node, "provenance": {"method": "STATIC", "status": status, "analysis_run_id": run_id}}

    def symbols(self):
        with self.database() as db:
            return self.read_symbols(db, self.run_id(db))

    def read_symbols(self, db, run_id):
        result = self.payloads(db, "SELECT id, payload FROM nodes WHERE type IN ('Function', 'Method')")
        return [
            self.with_provenance(node, run_id)
            for node in sorted(result.values(), key=lambda node: (node["qualified_name"], node["id"]))
        ]

    def call_graph(self):
        with self.database() as db:
            run_id = self.run_id(db)
            nodes = self.read_symbols(db, run_id)
            edges = [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM edges WHERE type = 'CALLS' ORDER BY id")
            ]
            for edge in edges:
                edge["provenance"]["analysis_run_id"] = run_id
            return {"nodes": nodes, "edges": edges}

    @staticmethod
    def get_node(db, node_id):
        row = db.execute("SELECT payload FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise CodeGraphError(f"Unknown node: {node_id}")
        return json.loads(row[0])

    def inspect(self, node_id):
        with self.database() as db:
            node = self.get_node(db, node_id)
            unresolved = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM unresolved WHERE node_id = ? ORDER BY payload", (node_id,)
            )]
            unresolved.sort(key=lambda call: (
                call["source_ref"]["start_line"], call["source_ref"]["start_column"]
            ))
            annotations = [
                self.annotation_state(item, node)
                for item in self.read_annotations() if item["target_node_id"] == node_id
            ]
            return {
                "node": self.with_provenance(node, self.run_id(db)),
                "unresolved_calls": unresolved, "annotations": annotations,
            }

    def refs(self, node_id, direction="both", edge_type="CALLS"):
        with self.database() as db:
            self.get_node(db, node_id)
            return self.direct_refs(db, node_id, direction, edge_type, self.run_id(db))

    def direct_refs(self, db, node_id, direction, edge_type, run_id):
        if direction not in ("incoming", "outgoing", "both"):
            raise CodeGraphError(f"Unknown direction: {direction}")
        if edge_type not in ONTOLOGY["edges"]:
            raise CodeGraphError(f"Unknown edge type: {edge_type}")
        result = {}
        for label, field, peer in (("incoming", "to_id", "from_id"), ("outgoing", "from_id", "to_id")):
            if direction in (label, "both"):
                rows = db.execute(
                    f"SELECT payload FROM edges WHERE {field} = ? AND type = ? ORDER BY id",
                    (node_id, edge_type),
                )
                items = []
                for row in rows:
                    edge = json.loads(row[0])
                    edge["provenance"]["analysis_run_id"] = run_id
                    node = self.with_provenance(self.get_node(db, edge[peer]), run_id)
                    items.append({"edge": edge, "node": node})
                result[label] = sorted(items, key=lambda item: (item["node"]["qualified_name"], item["edge"]["id"]))
        return result

    def neighborhood(self, node_id):
        with self.database() as db:
            node = self.get_node(db, node_id)
            run_id = self.run_id(db)
            return {
                "node": self.with_provenance(node, run_id),
                "refs": self.direct_refs(db, node_id, "both", "CALLS", run_id),
                **self.source_for(db, node),
            }

    def source(self, node_id):
        with self.database() as db:
            node = self.get_node(db, node_id)
            return {"node": self.with_provenance(node, self.run_id(db)), **self.source_for(db, node)}

    def source_for(self, db, node):
        row = db.execute("SELECT payload FROM files WHERE path = ?", (node["source_ref"]["path"],)).fetchone()
        return self.live_source(node, json.loads(row[0]))

    def live_source(self, node, file):
        path = (self.root / node["source_ref"]["path"]).resolve()
        if not path.is_relative_to(self.source_root):
            return {"source": None, "source_current": False}
        try:
            raw = path.read_bytes()
            source = decode_source(raw)
        except (OSError, SyntaxError, UnicodeError):
            return {"source": None, "source_current": False}
        return {
            "source": source,
            "source_current": not node["stale"] and digest(raw) == file.get("source_hash"),
        }

    def read_annotations(self):
        annotations = []
        ids = set()
        try:
            for number, line in enumerate(self.annotation_path.read_text(encoding="utf-8").split("\n"), 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if (
                    not isinstance(item, dict)
                    or any(not isinstance(item.get(key), str) for key in (
                        "id", "target_node_id", "type", "text", "author", "created_at", "source_hash"
                    ))
                    or item["type"] not in ANNOTATION_TYPES
                    or item["id"] in ids
                ):
                    raise ValueError(f"invalid or duplicate annotation on line {number}")
                ids.add(item["id"])
                annotations.append(item)
        except (OSError, ValueError) as error:
            raise CodeGraphError(f"Cannot read annotations.jsonl: {error}") from error
        return sorted(annotations, key=lambda item: item["id"])

    @staticmethod
    def annotation_state(item, node):
        if node is None:
            status = "ORPHANED"
        elif node["stale"] or item["source_hash"] != node["source_hash"]:
            status = "NEEDS_REVIEW"
        else:
            status = "ACTIVE"
        return {**item, "status": status}

    def annotations(self):
        with self.database() as db:
            nodes = self.payloads(db, "SELECT id, payload FROM nodes")
            return [self.annotation_state(item, nodes.get(item["target_node_id"])) for item in self.read_annotations()]

    def annotate(self, node_id, kind, body, author=None):
        if kind not in ANNOTATION_TYPES or not body.strip():
            raise CodeGraphError("An annotation requires a supported type and non-empty text.")
        with self.database(write=True) as db:
            node = self.get_node(db, node_id)
            if node["type"] not in ("Function", "Method") or node["stale"]:
                raise CodeGraphError("Select a current function or method to annotate.")
            file = db.execute("SELECT payload FROM files WHERE path = ?", (node["source_ref"]["path"],)).fetchone()
            if not self.live_source(node, json.loads(file[0]))["source_current"]:
                raise CodeGraphError("Source changed since analysis. Run cg analyze and retry.")
            item = {
                "id": str(uuid.uuid4()), "target_node_id": node_id, "type": kind, "text": body,
                "author": author or getpass.getuser(), "created_at": now(), "source_hash": node["source_hash"],
            }
            annotations = sorted([*self.read_annotations(), item], key=lambda record: record["id"])
            atomic_write(self.annotation_path, "".join(json_text(record) + "\n" for record in annotations))
            return self.annotation_state(item, node)
