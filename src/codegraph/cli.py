"""Command-line entry point for CodeGraph."""

import argparse
import io
import json
import sqlite3
import sys
from pathlib import Path

from .core import ANNOTATION_TYPES, ONTOLOGY, CodeGraphError, discover, init


def add_options(parser):
    parser.add_argument("--project", type=Path, default=argparse.SUPPRESS, help="Project directory")
    parser.add_argument("--format", choices=("text", "json"), default=argparse.SUPPRESS,
                        help="Output format (default: text)")


def parser():
    commands = argparse.ArgumentParser(prog="cg", description="Explore a local Python code graph.")
    add_options(commands)
    subcommands = commands.add_subparsers(dest="command", required=True)

    def command(name, help_text):
        sub = subcommands.add_parser(name, help=help_text)
        add_options(sub)
        return sub

    initialize = command("init", "Create or open a CodeGraph project")
    initialize.add_argument("--source-root", help="Source directory relative to the project")
    command("analyze", "Rebuild the static graph from Python source")
    inspect = command("inspect", "Inspect a node or list analyzed functions and methods")
    selection = inspect.add_mutually_exclusive_group()
    selection.add_argument("node_id", nargs="?")
    selection.add_argument("--annotations", action="store_true", help="List all annotations")
    refs = command("refs", "List direct references to or from a node")
    refs.add_argument("node_id")
    refs.add_argument("--direction", choices=("incoming", "outgoing", "both"), default="both")
    refs.add_argument("--edge", choices=tuple(ONTOLOGY["edges"]), default="CALLS")
    annotate = command("annotate", "Add an annotation to a function or method")
    annotate.add_argument("node_id")
    annotate.add_argument("--type", choices=ANNOTATION_TYPES, required=True)
    annotate.add_argument("--text", required=True)
    annotate.add_argument("--author", help="Author name (default: local username)")
    command("view", "Open the Tk graph viewer")
    return commands


def location(ref):
    return f'{ref["path"]}:{ref["start_line"]}:{ref["start_column"]}'


def symbol_line(node):
    stale = " [stale]" if node["stale"] else ""
    return f'{node["qualified_name"]}  {node["id"]}  {location(node["source_ref"])}{stale}'


def annotation_line(item):
    return f'{item["type"]} [{item["status"]}] {item["target_node_id"]} ({item["author"]}): {item["text"]}'


def render(command, result):
    if command == "init":
        return f'CodeGraph project at {result["project"]}'
    if command == "analyze":
        summary = (
            f'{result["files"]} files, {result["nodes"]} nodes, {result["edges"]} edges, '
            f'{result["unresolved_calls"]} unresolved calls.'
        )
        diagnostics = [
            f'{item["path"]}:{item.get("line") or "?"}: {item["message"]}'
            for item in result["diagnostics"]
        ]
        return "\n".join([summary, *diagnostics])
    if command == "inspect":
        if "nodes" in result:
            return "\n".join(map(symbol_line, result["nodes"])) or "No functions or methods found."
        if "node" not in result:
            return "\n".join(map(annotation_line, result["annotations"])) or "No annotations found."
        node = result["node"]
        lines = [symbol_line(node)]
        if "signature" in node["properties"]:
            lines.append(f'{node["name"]}{node["properties"]["signature"]}')
        lines.extend(
            f'Unresolved {item["expression"]} at {location(item["source_ref"])} ({item["reason"]})'
            for item in result["unresolved_calls"]
        )
        return "\n".join([*lines, *map(annotation_line, result["annotations"])])
    if command == "refs":
        lines = []
        for direction, items in result.items():
            lines.append(direction.capitalize())
            for item in items:
                edge = item["edge"]
                evidence = ", ".join(map(location, edge["evidence"]))
                lines.append(f'  {symbol_line(item["node"])} [{edge["provenance"]["status"]}]')
                lines.append(f'    {evidence}')
            if not items:
                lines.append("  No references.")
        return "\n".join(lines)
    return annotation_line(result)


def execute(args):
    project_path = getattr(args, "project", None)
    if args.command == "init":
        project = init(project_path or Path.cwd(), args.source_root)
        return {"project": str(project.root), "manifest": project.manifest}
    project = discover(project_path)
    match args.command:
        case "analyze":
            return project.analyze()
        case "inspect":
            if args.annotations:
                return {"annotations": project.annotations()}
            return project.inspect(args.node_id) if args.node_id else {"nodes": project.symbols()}
        case "refs":
            return project.refs(args.node_id, args.direction, args.edge)
        case "annotate":
            return project.annotate(args.node_id, args.type, args.text, args.author)
        case "view":
            from .viewer import open_viewer

            open_viewer(project)
            return None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        if result is not None:
            if getattr(args, "format", "text") == "json":
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                print(render(args.command, result))
    except (CodeGraphError, OSError, sqlite3.Error) as error:
        print(f"cg: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
