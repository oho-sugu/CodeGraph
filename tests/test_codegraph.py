import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from codegraph.core import CodeGraphError, init


FIXTURES = {
    "app.py": """\
import pkg.service as service
from pkg.service import done as finish
from pkg import service as service_module

def helper(value: int = 1) -> int:
    return value

class Base:
    def inherited(self):
        return 0

class Worker(Base):
    def execute(self):
        self.callback()
        self.inherited()
        helper(); helper()
        service.done()
        finish()
        service_module.done()
        getattr(self, "not_known")()

    async def callback(self):
        finish()

    def replaced(self):
        self = object()
        self.callback()

def shadow(helper):
    return helper()

def local_import():
    from pkg.service import done
    done()

def repeated():
    def nested():
        return 1

def repeated():
    def nested():
        return 2
""",
    "pkg/__init__.py": "",
    "pkg/service.py": """\
from .util import work

def done():
    café = 1; return work()
""",
    "pkg/util.py": "async def work():\n    return 1\n",
}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codegraph test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for path, source in FIXTURES.items():
            self.write(f"src/{path}", source)
        self.write("outside.py", "def outside(): pass\n")
        self.project = init(self.root, "src")

    def write(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    def symbols(self):
        return {node["qualified_name"]: node for node in self.project.symbols()}

    def test_graph_and_evidence(self):
        summary = self.project.analyze()
        self.assertEqual(summary["diagnostics"], [])
        self.assertEqual(summary["files"], 4)
        symbols = self.symbols()
        worker = symbols["app.Worker.execute"]["id"]
        outgoing = self.project.refs(worker, "outgoing")["outgoing"]
        self.assertEqual([item["node"]["qualified_name"] for item in outgoing], [
            "app.Worker.callback", "app.helper", "pkg.service.done",
        ])
        callback, helper, done = [item["edge"] for item in outgoing]
        self.assertEqual(callback["provenance"]["status"], "INFERRED")
        self.assertEqual(helper["provenance"]["status"], "VERIFIED")
        self.assertEqual(len(helper["evidence"]), 2)
        self.assertEqual(len(done["evidence"]), 3)
        self.assertEqual(done["provenance"]["analysis_run_id"], summary["analysis_run_id"])
        incoming = self.project.refs(symbols["pkg.service.done"]["id"], "incoming")["incoming"]
        self.assertEqual([item["node"]["qualified_name"] for item in incoming], [
            "app.Worker.callback", "app.Worker.execute", "app.local_import",
        ])
        unresolved = self.project.inspect(worker)["unresolved_calls"]
        self.assertEqual([item["expression"] for item in unresolved], ["self.inherited", "getattr", "getattr(self, 'not_known')"])
        for name in ("app.shadow", "app.Worker.replaced"):
            self.assertFalse(self.project.refs(symbols[name]["id"])["outgoing"])
        service = symbols["pkg.service.done"]
        edge = self.project.refs(service["id"])["outgoing"][0]["edge"]
        self.assertEqual(edge["evidence"][0]["start_column"], 22)
        self.assertEqual(service["source_ref"]["path"], "src/pkg/service.py")
        self.assertTrue(symbols["app.Worker.callback"]["properties"]["async"])
        self.assertEqual(symbols["app.helper"]["properties"]["signature"], "(value: int=1) -> int")
        nested = [node for node in self.project.symbols() if node["name"] == "nested"]
        self.assertEqual(len({node["id"] for node in nested}), 2)
        worker_module = symbols["app.helper"]["parent_id"]
        self.assertIn("pkg.service", [
            item["node"]["qualified_name"]
            for item in self.project.refs(worker_module, edge_type="IMPORTS")["outgoing"]
        ])

    def test_multiline_decorator_and_runtime_attribute_replacement(self):
        self.write("src/extra.py", """\
import pkg.service as service

class Worker:
    @(
        decorate
    )
    def decorated(self):
        pass

def replaced():
    service.done = replacement
    service.done()
""")
        self.project.analyze()
        symbols = self.symbols()
        decorated = symbols["extra.Worker.decorated"]
        self.assertEqual(decorated["source_ref"]["start_line"], 4)
        self.assertEqual(decorated["source_ref"]["start_column"], 5)
        replaced = symbols["extra.replaced"]["id"]
        self.assertFalse(self.project.refs(replaced)["outgoing"])
        self.assertEqual(self.project.inspect(replaced)["unresolved_calls"][0]["expression"], "service.done")

    def test_same_class_wrappers_and_nested_function_calls(self):
        self.write("src/wrapped.py", """\
from contextlib import contextmanager as managed

class Worker:
    def run(self):
        self.transform()
        self.session()
        self.dynamic()

    @staticmethod
    def transform():
        pass

    @managed
    def session(self):
        yield self.transform()

    @custom
    def dynamic(self):
        pass

class Shadowed:
    staticmethod = custom
    def run(self):
        self.transform()

    @staticmethod
    def transform(self):
        pass

def outer():
    def helper():
        pass
    helper()
""")
        self.project.analyze()
        symbols = self.symbols()

        def outgoing(name):
            edges = self.project.refs(symbols[name]["id"], "outgoing")["outgoing"]
            return [(edge["node"]["name"], edge["edge"]["provenance"]["status"]) for edge in edges]

        self.assertEqual(outgoing("wrapped.Worker.run"), [
            ("session", "INFERRED"), ("transform", "INFERRED"),
        ])
        self.assertEqual(outgoing("wrapped.Worker.session"), [("transform", "INFERRED")])
        self.assertEqual(outgoing("wrapped.Shadowed.run"), [])
        self.assertEqual(outgoing("wrapped.outer"), [("helper", "VERIFIED")])

    def test_method_receivers_from_local_constructors_and_type_annotations(self):
        self.write("src/model.py", """\
class Service:
    def run(self):
        pass

def discover() -> 'Service':
    return Service()

async def async_discover() -> Service:
    return Service()
""")
        self.write("src/consumer.py", """\
import model
from model import Service as Client, discover, async_discover

def execute(mode):
    if mode == 'init':
        project = init()
        return project
    project = discover()
    match mode:
        case 'run':
            project.run()

def from_constructor():
    client = Client()
    client.run()

def from_parameter(client: 'model.Service'):
    client.run()

def overwritten(flag):
    client = Client()
    if flag:
        client = other()
    client.run()

def modified():
    client = Client()
    client.run = other
    client.run()

def before_assignment():
    client.run()
    client = Client()

def async_result():
    client = async_discover()
    client.run()

class Viewer:
    def __init__(self, project: Client):
        self.project = project
        self.project.run()

    def refresh(self):
        self.project.run()

class Reassigns:
    def __init__(self, project: Client):
        self.project = project

    def refresh(self):
        self.project.run()

    def swap(self, project):
        self.project = project

class Conditional:
    def __init__(self, project: Client, enabled):
        if enabled:
            self.project = project

    def refresh(self):
        self.project.run()
""")
        self.project.analyze()
        symbols = self.symbols()
        resolved = ("execute", "from_constructor", "from_parameter", "Viewer.__init__", "Viewer.refresh")
        for name in resolved:
            with self.subTest(name=name):
                refs = self.project.refs(symbols[f"consumer.{name}"]["id"], "outgoing")["outgoing"]
                service = [ref for ref in refs if ref["node"]["qualified_name"] == "model.Service.run"]
                self.assertEqual(len(service), 1)
                self.assertEqual(service[0]["edge"]["provenance"]["status"], "INFERRED")
                self.assertEqual(service[0]["edge"]["evidence"][0]["path"], "src/consumer.py")
        unresolved = (
            ("overwritten", "client.run"), ("modified", "client.run"),
            ("before_assignment", "client.run"), ("async_result", "client.run"),
            ("Reassigns.refresh", "self.project.run"), ("Conditional.refresh", "self.project.run"),
        )
        for name, expression in unresolved:
            with self.subTest(name=name):
                node = symbols[f"consumer.{name}"]["id"]
                calls = self.project.inspect(node)["unresolved_calls"]
                self.assertIn(expression, {call["expression"] for call in calls})
                refs = self.project.refs(node, "outgoing")["outgoing"]
                self.assertNotIn("model.Service.run", {ref["node"]["qualified_name"] for ref in refs})

    def test_deterministic_changes_and_syntax_recovery(self):
        first = self.project.analyze()
        original = self.symbols()
        again = self.project.analyze()
        self.assertEqual(first["analysis_run_id"], again["analysis_run_id"])
        self.assertEqual(again["node_changes"], {"added": 0, "removed": 0, "updated": 0})
        self.assertEqual(again["edge_changes"], again["node_changes"])
        self.write("src/app.py", "# heading\n" + FIXTURES["app.py"])
        self.project.analyze()
        shifted = self.symbols()["app.helper"]
        self.assertEqual(original["app.helper"]["id"], shifted["id"])
        self.assertEqual(original["app.helper"]["source_hash"], shifted["source_hash"])
        self.assertEqual(original["app.helper"]["source_ref"]["start_line"] + 1, shifted["source_ref"]["start_line"])
        self.write("src/app.py", "def invalid(\n")
        self.write("src/pkg/util.py", FIXTURES["pkg/util.py"] + "def added(): pass\n")
        broken = self.project.analyze()
        self.assertEqual(broken["diagnostics"][0]["path"], "src/app.py")
        self.assertTrue(self.symbols()["app.helper"]["stale"])
        self.assertIn("pkg.util.added", self.symbols())
        self.assertEqual(self.project.refs(shifted["id"]), {"incoming": [], "outgoing": []})
        self.write("src/app.py", FIXTURES["app.py"])
        self.project.analyze()
        self.assertFalse(self.symbols()["app.helper"]["stale"])
        (self.root / "src/app.py").unlink()
        self.assertGreater(self.project.analyze()["node_changes"]["removed"], 0)
        self.assertNotIn("app.helper", self.symbols())

    def test_annotations_and_rebuild(self):
        self.project.analyze()
        node = self.symbols()["app.helper"]["id"]
        text = "決済\nCompatibility with older clients"
        item = self.project.annotate(node, "NOTE", text, "Reader")
        self.assertEqual(item["status"], "ACTIVE")
        self.write("src/app.py", "\n" + FIXTURES["app.py"])
        self.assertFalse(self.project.neighborhood(node)["source_current"])
        with self.assertRaisesRegex(CodeGraphError, "Source changed"):
            self.project.annotate(node, "NOTE", "Retry later")
        self.project.analyze()
        self.assertEqual(self.project.inspect(node)["annotations"][0]["status"], "ACTIVE")
        self.write("src/app.py", FIXTURES["app.py"].replace("return value", "return value + 1"))
        self.project.analyze()
        self.assertEqual(self.project.inspect(node)["annotations"][0]["status"], "NEEDS_REVIEW")
        self.write("src/app.py", "")
        self.project.analyze()
        self.assertEqual(self.project.annotations()[0]["status"], "ORPHANED")
        previous = self.project.annotation_path.read_bytes()
        self.project.db_path.unlink()
        self.project.analyze()
        self.assertEqual(self.project.annotation_path.read_bytes(), previous)
        self.assertEqual(self.project.annotations()[0]["text"], text)
        self.assertEqual(self.project.annotations()[0]["id"], item["id"])
        self.assertEqual(self.project.annotations()[0]["status"], "ORPHANED")

    def test_failed_snapshot_is_rolled_back_and_init_is_idempotent(self):
        analysis = self.project.analyze()
        node = self.symbols()["app.helper"]["id"]
        self.write("src/app.py", "")
        store = self.project.store_snapshot

        def fail_after_write(*args):
            store(*args)
            raise sqlite3.OperationalError("simulated interruption")

        with patch.object(self.project, "store_snapshot", side_effect=fail_after_write):
            with self.assertRaisesRegex(sqlite3.OperationalError, "simulated"):
                self.project.analyze()
        reopened = init(self.root)
        self.assertEqual(reopened.manifest, self.project.manifest)
        self.assertEqual(reopened.inspect(node)["node"]["provenance"]["analysis_run_id"], analysis["analysis_run_id"])
        with reopened.database() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_concurrent_annotations_preserve_both_records(self):
        self.project.analyze()
        node = self.symbols()["app.helper"]["id"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            for item in executor.map(lambda body: self.project.annotate(node, "NOTE", body), ("one", "two")):
                self.assertEqual(item["status"], "ACTIVE")
        self.assertEqual({item["text"] for item in self.project.annotations()}, {"one", "two"})

    def test_cli_contract(self):
        def cg(*args):
            return subprocess.run(
                [sys.executable, "-m", "codegraph", *args],
                cwd=self.root / "src/pkg", capture_output=True, encoding="utf-8",
            )

        analysis = cg("--project", str(self.root), "analyze", "--format", "json")
        self.assertEqual(analysis.returncode, 0, analysis.stderr)
        self.assertFalse(analysis.stderr)
        self.assertFalse(json.loads(analysis.stdout)["diagnostics"])
        result = cg("--format", "json", "inspect")
        self.assertEqual(result.returncode, 0, result.stderr)
        node = next(node for node in json.loads(result.stdout)["nodes"] if node["name"] == "helper")
        refs = json.loads(cg("refs", node["id"], "--direction", "incoming", "--format", "json").stdout)
        self.assertEqual(len(refs["incoming"]), 1)
        unknown = cg("inspect", "unknown", "--format", "json")
        self.assertEqual(unknown.returncode, 1)
        self.assertEqual(unknown.stdout, "")
        self.assertIn("Unknown node", unknown.stderr)
        self.assertEqual(cg("refs", node["id"], "--direction", "invalid").returncode, 2)

    def test_tk_source_selection(self):
        try:
            import tkinter as tk
        except ImportError as error:
            self.skipTest(str(error))
        try:
            window = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        window.withdraw()
        self.addCleanup(window.destroy)
        from codegraph.cli import main
        from codegraph.viewer import Viewer

        self.project.analyze()
        controllers = []

        def create_viewer(*args):
            controllers.append(Viewer(*args))

        with (
            patch("tkinter.Tk", return_value=window),
            patch.object(window, "mainloop") as mainloop,
            patch("codegraph.viewer.Viewer", side_effect=create_viewer),
        ):
            self.assertEqual(main(["--project", str(self.root), "view"]), 0)
            mainloop.assert_called_once()
        viewer, = controllers
        node = self.symbols()["app.Worker.execute"]
        target = self.symbols()["app.Worker.callback"]
        unrelated = self.symbols()["app.shadow"]
        graph = self.project.call_graph()
        self.assertEqual(len(viewer.node_items), len(graph["nodes"]))
        self.assertEqual(len(viewer.edge_items), len(graph["edges"]))
        before = {
            node_id: viewer.graph.coords(box)
            for node_id, (box, _label) in viewer.node_items.items()
        }
        viewer.symbols.selection_set(node["id"])
        window.update()
        self.assertEqual(before, {
            node_id: viewer.graph.coords(box)
            for node_id, (box, _label) in viewer.node_items.items()
        })

        def node_fill(symbol):
            return viewer.graph.itemcget(viewer.node_items[symbol["id"]][0], "fill")

        self.assertEqual(node_fill(node), viewer.COLORS["selected"])
        self.assertEqual(node_fill(target), viewer.COLORS["related"])
        self.assertEqual(node_fill(unrelated), viewer.COLORS["muted_fill"])
        for edge in graph["edges"]:
            fill = viewer.graph.itemcget(viewer.edge_items[edge["id"]], "fill")
            if edge["to_id"] == node["id"]:
                self.assertEqual(fill, viewer.COLORS["caller"])
            else:
                expected = "accent" if edge["from_id"] == node["id"] else "muted_line"
                self.assertEqual(fill, viewer.COLORS[expected])
        tags = list(map(str, viewer.source.tag_ranges("focus")))
        self.assertEqual(tags, [
            f'{node["source_ref"]["start_line"]}.0', f'{node["source_ref"]["end_line"] + 1}.0',
        ])
        self.assertIn("def execute(self):", viewer.source.get(*tags))
        self.write("src/app.py", "\n" + FIXTURES["app.py"])
        viewer.render_selection()
        self.assertFalse(viewer.source.tag_ranges("focus"))
        self.assertIn("Run cg analyze", viewer.status.get())
        viewer.zoom(1.25)
        self.assertEqual(node_fill(node), viewer.COLORS["selected"])
        self.assertEqual(len(viewer.node_items), len(graph["nodes"]))
        viewer.clear_focus()
        self.assertEqual(node_fill(unrelated), viewer.COLORS["white"])
        self.write("src/app.py", FIXTURES["app.py"])
        viewer.query.set("  WORKER.CALL ")
        self.assertEqual(viewer.symbols.get_children(), (target["id"],))
        self.assertEqual(viewer.search_summary.get(), "1 match")
        viewer.open_first_match(None)
        self.assertEqual(node_fill(target), viewer.COLORS["selected"])
        self.assertEqual(node_fill(node), viewer.COLORS["caller_fill"])
        self.assertEqual(viewer.callers_label.get(), "Callers · 1")
        self.assertEqual(viewer.callees_label.get(), "Callees · 1")
        incoming = next(edge for edge in graph["edges"] if edge["to_id"] == target["id"])
        outgoing = next(edge for edge in graph["edges"] if edge["from_id"] == target["id"])
        incoming_item = viewer.edge_items[incoming["id"]]
        outgoing_item = viewer.edge_items[outgoing["id"]]
        self.assertEqual(viewer.graph.itemcget(incoming_item, "fill"), viewer.COLORS["caller"])
        self.assertEqual(viewer.graph.itemcget(outgoing_item, "fill"), viewer.COLORS["accent"])
        self.assertTrue(viewer.graph.itemcget(incoming_item, "dash"))
        stacking = list(viewer.graph.find_all())
        self.assertLess(stacking.index(viewer.node_items[unrelated["id"]][0]), stacking.index(incoming_item))
        self.assertLess(stacking.index(incoming_item), stacking.index(viewer.node_items[target["id"]][0]))
        self.assertIn("def callback(self):", viewer.source.get(*viewer.source.tag_ranges("focus")))
        viewer.query.set("missing symbol")
        self.assertFalse(viewer.symbols.get_children())
        self.assertEqual(viewer.search_summary.get(), "No matching symbols")
        self.assertEqual(node_fill(target), viewer.COLORS["selected"])
        box = viewer.node_items[unrelated["id"]][0]
        with patch.object(viewer.graph, "find_withtag", return_value=(box,)):
            viewer.graph_clicked(None)
        self.assertEqual(node_fill(unrelated), viewer.COLORS["selected"])
        self.assertFalse(viewer.symbols.get_children())
        self.assertEqual(len(viewer.node_items), len(graph["nodes"]))
        self.assertEqual(len(viewer.edge_items), len(graph["edges"]))
        viewer.clear_search(None)
        self.assertEqual(len(viewer.symbols.get_children()), len(graph["nodes"]))
        self.assertEqual(viewer.symbols.selection(), (unrelated["id"],))
        viewer.show_source(
            'def example():\n    message = "🧪"; return 42  # hi\n    note = """first\nsecond"""\n',
            {"start_line": 2, "end_line": 2},
        )

        def styled_text(tag):
            indices = viewer.source.tag_ranges(tag)
            return [viewer.source.get(first, last) for first, last in zip(indices[::2], indices[1::2])]

        self.assertEqual(styled_text("keyword"), ["def", "return"])
        self.assertEqual(styled_text("string"), ['"🧪"', '"""first', 'second"""'])
        self.assertEqual(styled_text("number"), ["42"])
        self.assertEqual(styled_text("comment"), ["# hi"])
        self.assertEqual(styled_text("line_number")[:2], ["1  ", "2  "])
        self.assertIn("return 42", styled_text("focus")[0])
        viewer.show_source('if ready:\n    value = """unfinished', None)
        self.assertEqual(styled_text("keyword"), ["if"])
        self.assertIn("unfinished", viewer.source.get("1.0", "end"))
        self.assertEqual(viewer.source.cget("state"), "disabled")
