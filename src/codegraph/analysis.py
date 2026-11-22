"""Deterministic extraction and conservative, project-local name resolution."""

import ast
import hashlib
import io
import json
import os
import tokenize
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DECLARATIONS = (*FUNCTIONS, ast.ClassDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(kind, *parts):
    return f"{kind.lower()}_{digest(json_text(parts).encode())[:32]}"


def decode_source(raw):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def decorator_positions(source):
    positions = defaultdict(list)
    at_start = True
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NEWLINE:
            at_start = True
        elif token.type not in (tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.COMMENT):
            if at_start and token.type == tokenize.OP and token.string == "@":
                line, column = token.start
                positions[column].append(line)
            at_start = False
    return positions


def python_files(root):
    def fail(error):
        raise error

    for directory, subdirs, files in os.walk(root, onerror=fail):
        base = Path(directory)
        subdirs[:] = sorted(
            name for name in subdirs
            if not name.startswith(".")
            and name not in {"__pycache__", "venv", "node_modules"}
            and not (base / name).is_symlink()
            and not (base / name).is_junction()
        )
        for name in sorted(files):
            path = base / name
            if path.suffix == ".py" and not path.is_symlink():
                yield path


class Bindings(ast.NodeVisitor):
    """Collect possible bindings in one scope, including conditional writes."""

    def __init__(self, body, args=None, type_params=()):
        self.counts = Counter()
        self.direct = {}
        self.external = set()
        self.attributes = set()
        self.star = False
        if args is not None:
            regular = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            self.counts.update(arg.arg for arg in [*regular, args.vararg, args.kwarg] if arg)
        for param in type_params:
            self.counts[param.name] += 1
        for statement in body:
            self.visit(statement)
            if isinstance(statement, DECLARATIONS):
                self.direct[statement.name] = statement
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                for item in statement.names:
                    self.direct[self.import_name(statement, item)] = (statement, item)

    @staticmethod
    def import_name(statement, item):
        if isinstance(statement, ast.Import):
            return item.asname or item.name.split(".")[0]
        return item.asname or item.name

    def unique(self, name):
        if self.counts[name] == 1 and name not in self.external and not self.star:
            return self.direct.get(name)
        return None

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.counts[node.id] += 1

    def visit_Attribute(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name):
            self.attributes.add((node.value.id, node.attr))
        self.generic_visit(node)

    def visit_Import(self, node):
        for item in node.names:
            self.counts[self.import_name(node, item)] += 1
            self.star |= item.name == "*"

    visit_ImportFrom = visit_Import

    def visit_FunctionDef(self, node):
        self.counts[node.name] += 1
        defaults = [*node.args.defaults, *(arg for arg in node.args.kw_defaults if arg)]
        for expression in [*node.decorator_list, *defaults]:
            self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.counts[node.name] += 1
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)

    def visit_Lambda(self, node):
        for expression in [*node.args.defaults, *(arg for arg in node.args.kw_defaults if arg)]:
            self.visit(expression)

    def visit_ListComp(self, node):
        for expression in ast.walk(node):
            if isinstance(expression, ast.NamedExpr):
                self.visit(expression.target)

    visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_ListComp

    def visit_Global(self, node):
        self.external.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node):
        if node.name:
            self.counts[node.name] += 1
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.counts[node.name] += 1
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.counts[node.rest] += 1
        self.generic_visit(node)


@dataclass
class Declaration:
    syntax: ast.AST
    node: dict
    bindings: Bindings
    parent: "Declaration | None" = None


@dataclass
class ParsedFile:
    path: str
    module: str
    package: bool
    source: str
    tree: ast.Module
    source_hash: str
    lines: list = field(init=False)
    decorators: dict = field(init=False)
    scopes: dict = field(init=False, default_factory=dict)
    root: Declaration = field(init=False)

    def __post_init__(self):
        self.lines = self.source.split("\n")
        self.decorators = decorator_positions(self.source)
        module = self.make_node("Module", self.module, self.module, self.tree, None, 0)
        self.root = Declaration(self.tree, module, Bindings(self.tree.body))
        self.scopes[self.tree] = self.root
        self.collect(self.tree.body, self.root)

    def column(self, line, byte_offset):
        return len(self.lines[line - 1].encode("utf-8")[:byte_offset].decode("utf-8")) + 1

    def ref(self, syntax, include_decorators=False):
        if isinstance(syntax, ast.Module):
            start_line, start_column = 1, 1
            end_line, end_column = len(self.lines), len(self.lines[-1]) + 1
        else:
            start_line = syntax.lineno
            if include_decorators and getattr(syntax, "decorator_list", ()):
                lines = self.decorators.get(syntax.col_offset, ())
                index = bisect_right(lines, syntax.decorator_list[0].lineno)
                if index:
                    start_line = lines[index - 1]
            start_column = self.column(start_line, syntax.col_offset)
            end_line = syntax.end_lineno
            end_column = self.column(end_line, syntax.end_col_offset)
        return {
            "file_id": stable_id("file", self.path), "path": self.path,
            "start_line": start_line, "start_column": start_column,
            "end_line": end_line, "end_column": end_column,
        }

    def make_node(self, kind, name, qualified_name, syntax, parent, ordinal):
        ref = self.ref(syntax, include_decorators=True)
        lines = self.lines[ref["start_line"] - 1:ref["end_line"]]
        lines[-1] = lines[-1][:ref["end_column"] - 1]
        lines[0] = lines[0][ref["start_column"] - 1:]
        properties = {}
        if isinstance(syntax, FUNCTIONS):
            signature = f"({ast.unparse(syntax.args)})"
            if syntax.returns is not None:
                signature += f" -> {ast.unparse(syntax.returns)}"
            properties = {"signature": signature, "async": isinstance(syntax, ast.AsyncFunctionDef)}
        return {
            "id": stable_id(kind, self.path, qualified_name, ordinal),
            "type": kind, "name": name, "qualified_name": qualified_name,
            "parent_id": parent.node["id"] if parent else None,
            "source_ref": ref, "source_hash": digest("\n".join(lines).encode()),
            "properties": properties,
        }

    def collect(self, statements, parent):
        def declarations(children):
            for child in children:
                if isinstance(child, DECLARATIONS):
                    yield child
                elif isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                    yield from declarations(ast.iter_child_nodes(child))

        ordinals = Counter()
        prefix = parent.node["qualified_name"]
        if isinstance(parent.syntax, FUNCTIONS):
            prefix += ".<locals>"
        for syntax in declarations(statements):
            if isinstance(syntax, ast.ClassDef):
                kind = "Class"
            else:
                kind = "Method" if isinstance(parent.syntax, ast.ClassDef) else "Function"
            qualified_name = f"{prefix}.{syntax.name}"
            ordinal = ordinals[kind, qualified_name]
            ordinals[kind, qualified_name] += 1
            # Children of duplicate declarations require separate IDs as well.
            node = self.make_node(kind, syntax.name, qualified_name, syntax, parent, ordinal)
            node["id"] = stable_id(kind, self.path, parent.node["id"], syntax.name, ordinal)
            bindings = Bindings(syntax.body, getattr(syntax, "args", None), syntax.type_params)
            scope = Declaration(syntax, node, bindings, parent)
            self.scopes[syntax] = scope
            self.collect(syntax.body, scope)


def parse_file(project, root, path):
    relative = path.relative_to(project).as_posix()
    parts = list(path.relative_to(root).with_suffix("").parts)
    package = parts[-1] == "__init__"
    if package and len(parts) > 1:
        parts.pop()
    module = ".".join(parts)
    raw = path.read_bytes()
    source = decode_source(raw)
    tree = ast.parse(source, filename=relative, type_comments=True)
    # Compile checks invalid scope constructs that AST parsing alone accepts.
    compile(tree, relative, "exec")
    return ParsedFile(relative, module, package, source, tree, digest(raw))


class BodyCalls(ast.NodeVisitor):
    def __init__(self, body):
        self.calls = []
        self.implicit_scope = 0
        for statement in body:
            self.visit(statement)

    def visit_Call(self, node):
        self.calls.append((node, self.implicit_scope > 0))
        self.generic_visit(node)

    def visit_Lambda(self, node):
        self.implicit_scope += 1
        self.generic_visit(node)
        self.implicit_scope -= 1

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_Lambda

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_ClassDef = visit_FunctionDef


def dotted_name(expression):
    if isinstance(expression, ast.Name):
        return (expression.id,)
    if isinstance(expression, ast.Attribute):
        parent = dotted_name(expression.value)
        if parent:
            return (*parent, expression.attr)
    return ()


class Resolver:
    def __init__(self, files):
        self.modules = defaultdict(list)
        for file in files:
            self.modules[file.module].append(file)

    def module(self, name):
        candidates = self.modules.get(name, ())
        return candidates[0] if len(candidates) == 1 else None

    def from_module(self, file, statement):
        if not statement.level:
            return statement.module
        package = file.module.split(".")
        if not file.package:
            package.pop()
        if statement.level > len(package):
            return None
        base = package[:len(package) + 1 - statement.level]
        return ".".join([*base, *([statement.module] if statement.module else [])])

    def imported_function(self, file, binding, tail):
        statement, item = binding
        if isinstance(statement, ast.Import):
            prefix = () if item.asname else tuple(item.name.split(".")[1:])
            if len(tail) != len(prefix) + 1 or tail[:-1] != prefix:
                return None
            module_name, name = item.name, tail[-1]
        else:
            module_name, name = self.from_module(file, statement), item.name
            if len(tail) == 1 and module_name:
                package = self.module(module_name)
                if package and (package.root.bindings.counts[name] or package.root.bindings.star):
                    return None
                module_name, name = f"{module_name}.{name}", tail[0]
            elif tail:
                return None
        module = self.module(module_name)
        if module:
            return self.function(module, module.root.bindings.unique(name))
        return None

    @staticmethod
    def function(file, syntax):
        if isinstance(syntax, FUNCTIONS) and not syntax.decorator_list:
            return file.scopes[syntax].node
        return None

    def method_wrapper(self, file, owner, syntax):
        if not syntax.decorator_list:
            return "plain"
        if len(syntax.decorator_list) != 1 or not isinstance(syntax.decorator_list[0], ast.Name):
            return None
        name = syntax.decorator_list[0].id
        enclosing = owner
        while enclosing is not file.root:
            if enclosing.bindings.counts[name] or name in enclosing.bindings.external:
                return None
            enclosing = enclosing.parent
        module = file.root.bindings
        if name == "staticmethod" and not module.counts[name] and not module.star:
            return "static"
        binding = module.unique(name)
        if isinstance(binding, tuple):
            statement, item = binding
            if (
                isinstance(statement, ast.ImportFrom) and not statement.level
                and statement.module == "contextlib" and item.name == "contextmanager"
                and "contextlib" not in self.modules
                and statement.end_lineno < owner.syntax.lineno
            ):
                return "contextmanager"
        return None

    def target(self, file, scope, call):
        parts = dotted_name(call.func)
        if not parts:
            return None, "dynamic_expression"
        name, *tail_list = parts
        tail = tuple(tail_list)
        local = scope.bindings
        if name in local.external:
            return None, "explicit_outer_binding"
        if name == "self" and len(tail) == 1 and scope.node["type"] == "Method":
            owner = scope.parent
            args = [*scope.syntax.args.posonlyargs, *scope.syntax.args.args]
            attribute = tail[0]
            if (
                args and args[0].arg == "self" and local.counts["self"] == 1
                and not owner.syntax.decorator_list
                and self.method_wrapper(file, owner, scope.syntax) in ("plain", "contextmanager")
                and not any(
                    ("self", attribute) in sibling.bindings.attributes
                    for sibling in file.scopes.values() if sibling.parent is owner
                )
            ):
                target = owner.bindings.unique(attribute)
                if isinstance(target, FUNCTIONS) and self.method_wrapper(file, owner, target):
                    return file.scopes[target].node, "INFERRED"
            return None, "ambiguous_method"
        if local.counts[name] or local.star:
            binding = local.unique(name)
            if not tail and isinstance(binding, FUNCTIONS) and binding.end_lineno < call.lineno:
                target = self.function(file, binding)
                if target:
                    return target, "VERIFIED"
            if isinstance(binding, tuple) and binding[0].end_lineno < call.lineno:
                if tail and (name, tail[0]) in local.attributes:
                    return None, "shadowed_attribute"
                target = self.imported_function(file, binding, tail)
                if target:
                    return target, "VERIFIED"
            return None, "local_binding"
        outer = scope.parent
        while outer is not file.root:
            if isinstance(outer.syntax, FUNCTIONS) and (
                outer.bindings.counts[name] or name in outer.bindings.external
            ):
                return None, "closure_binding"
            outer = outer.parent
        module = file.root.bindings
        binding = module.unique(name)
        if isinstance(binding, tuple) and not (
            tail and ((name, tail[0]) in module.attributes or (name, tail[0]) in local.attributes)
        ):
            target = self.imported_function(file, binding, tail)
        else:
            target = self.function(file, binding) if not tail else None
        return (target, "VERIFIED") if target else (None, "unresolved_name")

    def imports(self, file, statement):
        if isinstance(statement, ast.Import):
            return [self.module(item.name) for item in statement.names]
        base = self.from_module(file, statement)
        if not base:
            return []
        package = self.module(base)
        targets = [package] if package else []
        for item in statement.names:
            if not package or not (package.root.bindings.counts[item.name] or package.root.bindings.star):
                targets.append(self.module(f"{base}.{item.name}"))
        return targets


def relations(files):
    resolver = Resolver(files)
    edges = {}
    unresolved = []

    def edge(kind, origin, target, status, evidence):
        edge_id = stable_id(kind, origin["id"], target["id"], status)
        if edge_id not in edges:
            edges[edge_id] = {
                "id": edge_id, "type": kind, "from_id": origin["id"], "to_id": target["id"],
                "properties": {}, "evidence": [],
                "provenance": {"method": "STATIC", "status": status},
            }
        if evidence not in edges[edge_id]["evidence"]:
            edges[edge_id]["evidence"].append(evidence)

    for file in files:
        for syntax, scope in file.scopes.items():
            for statement in syntax.body:
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    for target_file in resolver.imports(file, statement):
                        if target_file is not None:
                            edge("IMPORTS", scope.node, target_file.root.node, "VERIFIED", file.ref(statement))
            if not isinstance(syntax, FUNCTIONS):
                continue
            for call, implicit_scope in BodyCalls(syntax.body).calls:
                target, status = (
                    (None, "implicit_scope") if implicit_scope else resolver.target(file, scope, call)
                )
                evidence = file.ref(call)
                if target:
                    edge("CALLS", scope.node, target, status, evidence)
                else:
                    unresolved.append({
                        "node_id": scope.node["id"], "expression": ast.unparse(call.func),
                        "reason": status, "source_ref": evidence,
                    })
    return list(edges.values()), unresolved
