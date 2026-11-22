"""Validate agent-produced I/O evidence and project it onto the current graph."""

import json
import re
from pathlib import Path, PurePosixPath

from .analysis import decode_source, digest, stable_id


class IOFactError(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise IOFactError(f"{label} must be a non-empty string.")
    return value


def relative_path(value):
    value = text(value, "path")
    normalized = PurePosixPath(value)
    if (normalized.is_absolute() or normalized.as_posix() != value or value == "."
            or ".." in normalized.parts or any(char in value for char in ("\\", ":", "\x00"))):
        raise IOFactError(f"Expected a project-relative POSIX path: {value!r}")
    return value


def record(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() <= set(required) | set(optional):
        raise IOFactError(f"Expected fields {', '.join(required)}.")
    return value


def node_in_inputs(node_id, nodes, inputs):
    node = nodes.get(text(node_id, "node_id"))
    if not node or node["type"] not in ("Function", "Method") or node["stale"]:
        raise IOFactError(f"Expected a current function or method: {node_id}")
    ref = node["source_ref"]
    if ref["path"] not in inputs:
        raise IOFactError(f"Missing input hash for {ref['path']}")
    return ref


def line_in_ref(line, ref):
    if type(line) is not int or not ref["start_line"] <= line <= ref["end_line"]:
        raise IOFactError(f"Evidence line {line!r} is outside the symbol in {ref['path']}.")


def resource_node(resource, nodes, inputs):
    if not isinstance(resource, dict):
        raise IOFactError("resource must be an object or null.")
    kind = resource.get("kind")
    definition = None
    if kind == "table":
        record(resource, ("kind", "database", "schema", "name"), ("definition",))
        database, schema, name = (text(resource[key], key) for key in ("database", "schema", "name"))
        key = (database, schema, name)
        label = ".".join(key)
        if "definition" in resource:
            definition = record(resource["definition"], ("path", "line"))
            path = relative_path(definition["path"])
            if path not in inputs or type(definition["line"]) is not int or definition["line"] < 1:
                raise IOFactError("A table definition needs a positive line and an input hash.")
    elif kind == "file":
        record(resource, ("kind", "path"))
        label = relative_path(resource["path"])
        key = (label,)
    elif kind == "handle":
        record(resource, ("kind", "anchor"))
        anchor = record(resource["anchor"], ("kind", "node_id"), ("name", "line", "column"))
        ref = node_in_inputs(anchor["node_id"], nodes, inputs)
        owner = nodes[anchor["node_id"]]["qualified_name"]
        if anchor["kind"] == "parameter":
            record(anchor, ("kind", "node_id", "name"))
            name = text(anchor["name"], "parameter name")
            if not name.isidentifier():
                raise IOFactError(f"Invalid parameter name: {name!r}")
            key = ("parameter", anchor["node_id"], name)
            label = f"{owner} · {name}"
        elif anchor["kind"] == "open":
            record(anchor, ("kind", "node_id", "line", "column"))
            line_in_ref(anchor["line"], ref)
            column = anchor["column"]
            if type(column) is not int or column < 1:
                raise IOFactError("Expected a positive open-site column.")
            key = ("open", anchor["node_id"], anchor["line"] - ref["start_line"], column)
            label = f"{owner} · open:{anchor['line']}:{column}"
        else:
            raise IOFactError("Expected a parameter or open handle anchor.")
    else:
        raise IOFactError(f"Unknown resource kind: {kind!r}")
    properties = {key: value for key, value in resource.items() if key != "definition"}
    return {"id": stable_id(kind, *key), "kind": kind, "name": label, "properties": properties,
            "definitions": [definition] if definition else []}


def validate(document, nodes):
    record(document, ("version", "source", "inputs", "accesses"))
    if type(document["version"]) is not int or document["version"] != 1:
        raise IOFactError("Expected I/O document version 1.")
    source = relative_path(document["source"])
    inputs = document["inputs"]
    if not isinstance(inputs, dict) or source not in inputs:
        raise IOFactError("inputs must include a hash for the source file.")
    for path, fingerprint in inputs.items():
        relative_path(path)
        if not isinstance(fingerprint, str) or not re.fullmatch("[a-f0-9]{64}", fingerprint):
            raise IOFactError(f"Expected a lowercase SHA-256 hash for {path}.")
    if not isinstance(document["accesses"], list):
        raise IOFactError("accesses must be a list.")
    compiled = []
    for claim in document["accesses"]:
        record(claim, ("node_id", "operation", "resource", "line"), ("reason",))
        ref = node_in_inputs(claim["node_id"], nodes, inputs)
        if ref["path"] != source:
            raise IOFactError(f"{claim['node_id']} does not belong to {source}.")
        line_in_ref(claim["line"], ref)
        if claim["operation"] not in ("READS", "WRITES"):
            raise IOFactError("Expected READS or WRITES.")
        evidence = {"path": source, "line": claim["line"]}
        if claim["resource"] is None:
            reason = text(claim.get("reason"), "Unresolved access reason")
            compiled.append((claim, evidence, None, reason))
        else:
            compiled.append((claim, evidence, resource_node(claim["resource"], nodes, inputs), None))
    return compiled


def read_document(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IOFactError(f"Cannot read I/O document {path}: {error}") from error


def read_input(root, path):
    candidate = (root / relative_path(path)).resolve()
    if not candidate.is_relative_to(root):
        raise IOFactError(f"Input escapes the project: {path}")
    try:
        return candidate.read_bytes()
    except OSError:
        return None


def changed_inputs(root, document, cache):
    changed = []
    for path, expected in sorted(document["inputs"].items()):
        if path not in cache:
            raw = read_input(root, path)
            cache[path] = digest(raw) if raw is not None else None
        if cache[path] != expected:
            changed.append(path)
    return changed


def outdated_snapshot_inputs(document, files):
    return sorted(
        path for path, expected in document["inputs"].items()
        if (path == document["source"] or path in files)
        and files.get(path, {}).get("source_hash") != expected
    )


def check_definitions(root, claims):
    lengths = {}
    for _, _, resource, _ in claims:
        for definition in resource["definitions"] if resource else ():
            path = definition["path"]
            if path not in lengths:
                raw = read_input(root, path)
                try:
                    lengths[path] = len(decode_source(raw).split("\n")) if raw is not None else 0
                except (SyntaxError, UnicodeError):
                    raise IOFactError(f"Cannot read the table definition as source: {path}") from None
            if definition["line"] > lengths[path]:
                raise IOFactError(f"Table definition line is outside {path}.")


def project_graph(root, paths, nodes, files):
    resources, edges, unresolved, documents = {}, {}, [], []
    cache = {}
    for path in paths:
        info = {"document": path.name}
        try:
            document = read_document(path)
            record(document, ("version", "source", "inputs", "accesses"))
            info["source"] = document["source"]
            source = relative_path(document["source"])
            inputs = document["inputs"]
            if not isinstance(inputs, dict) or source not in inputs:
                raise IOFactError("Missing input hashes.")
            changed = sorted(set(changed_inputs(root, document, cache)) | set(outdated_snapshot_inputs(document, files)))
            if changed:
                documents.append({**info, "state": "STALE", "changed_inputs": changed})
                continue
            claims = validate(document, nodes)
        except (IOFactError, OSError) as error:
            documents.append({**info, "state": "INVALID", "message": str(error)})
            continue
        documents.append({**info, "state": "CURRENT"})
        for claim, evidence, resource, reason in claims:
            origin, operation = claim["node_id"], claim["operation"]
            if resource is None:
                unresolved.append({"node_id": origin, "operation": operation, "evidence": evidence, "reason": reason})
                continue
            target = resource["id"]
            existing = resources.setdefault(target, resource)
            for definition in resource["definitions"]:
                if definition not in existing["definitions"]:
                    existing["definitions"].append(definition)
            edge_id = stable_id(operation, origin, target)
            edge = edges.setdefault(edge_id, {
                "id": edge_id, "from_id": origin, "to_id": target, "type": operation, "evidence": [],
                "provenance": {"method": "LLM", "status": "INFERRED"},
            })
            if evidence not in edge["evidence"]:
                edge["evidence"].append(evidence)
    return {
        "resources": sorted(resources.values(), key=lambda item: (item["kind"], item["name"], item["id"])),
        "edges": sorted(edges.values(), key=lambda item: item["id"]),
        "unresolved": unresolved,
        "documents": documents,
    }
