"""Standalone Tk window for reading the local call graph and its source."""

import io
import keyword
import sqlite3
import tokenize
from collections import defaultdict
from math import ceil

from .core import CodeGraphError, Project
from .hierarchy import hierarchy_layout
from .viewer_theme import DARK, LIGHT, SOURCE_TAGS, style_widgets


NODE_WIDTH = 154
NODE_HEIGHT = 30
COLUMN_STEP = 180
ROW_STEP = 43
TOKEN_STYLES = {
    tokenize.STRING: "string",
    tokenize.FSTRING_START: "string",
    tokenize.FSTRING_MIDDLE: "string",
    tokenize.FSTRING_END: "string",
    tokenize.TSTRING_START: "string",
    tokenize.TSTRING_MIDDLE: "string",
    tokenize.TSTRING_END: "string",
    tokenize.COMMENT: "comment",
    tokenize.NUMBER: "number",
}


def source_styles(source):
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            style = TOKEN_STYLES.get(token.type)
            if token.type == tokenize.NAME and keyword.iskeyword(token.string):
                style = "keyword"
            if style:
                yield style, token.start, token.end
    except (tokenize.TokenError, SyntaxError):
        # Keep the readable text and any tokens emitted before a syntax error.
        return


def layout_graph(nodes):
    by_file = defaultdict(list)
    for node in nodes:
        by_file[node["source_ref"]["path"]].append(node)
    files = sorted(by_file.items())
    for _, members in files:
        members.sort(key=lambda node: (node["source_ref"]["start_line"], node["id"]))
    return min(
        (place_files(files, limit) for limit in range(750, 2191, COLUMN_STEP)),
        key=lambda layout: max(layout[2][0] / 1.15, layout[2][1]),
    )


def place_files(files, limit):
    groups, positions = [], {}
    x = y = 18
    shelf_height = 0
    width = 0
    for path, members in files:
        columns = min(5, ceil(len(members) / 11))
        rows = ceil(len(members) / columns)
        group_width = (columns - 1) * COLUMN_STEP + NODE_WIDTH + 30
        group_height = (rows - 1) * ROW_STEP + NODE_HEIGHT + 47
        if x > 18 and x + group_width > limit:
            x, y = 18, y + shelf_height + 20
            shelf_height = 0
        groups.append((path, x, y, x + group_width, y + group_height))
        for index, node in enumerate(members):
            column, row = divmod(index, rows)
            positions[node["id"]] = (x + 15 + NODE_WIDTH / 2 + column * COLUMN_STEP,
                                     y + 34 + NODE_HEIGHT / 2 + row * ROW_STEP)
        x += group_width + 18
        width = max(width, x)
        shelf_height = max(shelf_height, group_height)
    return groups, positions, (width, y + shelf_height + 18)


def edge_sides(origin, destination):
    distance = destination[0] - origin[0]
    if abs(distance) < NODE_WIDTH + 12:
        return 1, 1
    direction = 1 if distance > 0 else -1
    return direction, -direction


def edge_ports(edges, positions):
    ports = {edge["id"]: [0, 0] for edge in edges}
    channels = defaultdict(list)
    for edge in edges:
        start, end = edge["from_id"], edge["to_id"]
        if start == end:
            continue
        origin, destination = positions[start], positions[end]
        start_side, end_side = edge_sides(origin, destination)
        channels[start, start_side].append((destination[1], destination[0], edge["id"], 0))
        channels[end, end_side].append((origin[1], origin[0], edge["id"], 1))
    for connected in channels.values():
        if len(connected) < 2:
            continue
        for index, (_, _, edge_id, endpoint) in enumerate(sorted(connected)):
            ports[edge_id][endpoint] = -10 + 20 * index / (len(connected) - 1)
    return ports


def edge_path(origin, destination, offsets):
    x1, y1 = origin
    x2, y2 = destination
    half = NODE_WIDTH / 2
    if origin == destination:
        right = x1 + half
        return right, y1 - 7, right + 21, y1 - 21, right + 21, y1 + 21, right, y1 + 7
    y1 += offsets[0]
    y2 += offsets[1]
    start_side, end_side = edge_sides(origin, destination)
    if start_side == end_side:
        gutter = max(x1, x2) + half + 11
        return x1 + half, y1, gutter, y1, gutter, y2, x2 + half, y2
    start, end = x1 + start_side * half, x2 + end_side * half
    bend = (start + end) / 2 + start_side * min(8, abs(end - start) / 4)
    return start, y1, bend, y1, bend, y2, end, y2


def open_viewer(project: Project):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as error:
        raise CodeGraphError("This Python installation does not include Tk.") from error
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise CodeGraphError(f"Cannot open the Tk Viewer: {error}") from error
    Viewer(root, project, tk, ttk)
    root.mainloop()


class Viewer:
    def __init__(self, root, project: Project, tk, ttk):
        from tkinter.font import nametofont
        from .note_editor import NotesBrowser

        self.project = project
        self.tk = tk
        self.root = root
        self.COLORS = DARK
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        root.title(f"CodeGraph · {project.root.name}")
        root.geometry("1280x740")
        root.minsize(740, 440)

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=str(project.root)).pack(side="left")
        ttk.Button(header, text="Refresh graph", command=self.refresh).pack(side="right")
        self.dark_mode = tk.BooleanVar(value=True)
        ttk.Checkbutton(header, text="Dark", variable=self.dark_mode, command=self.apply_theme).pack(
            side="right", padx=8
        )
        self.graph_summary = tk.StringVar()
        ttk.Label(header, textvariable=self.graph_summary).pack(side="right", padx=10)

        panes = ttk.Panedwindow(main, orient="horizontal")
        panes.pack(fill="both", expand=True)
        sidebar = ttk.Notebook(panes)
        symbols = ttk.Frame(sidebar, padding=6)
        files = ttk.Frame(sidebar, padding=6)
        sidebar.add(symbols, text="Symbols")
        sidebar.add(files, text="Files")
        self.notes_browser = NotesBrowser(sidebar, self.select_note, self.edit_note, self.delete_note)
        sidebar.add(self.notes_browser, text="Notes")
        self.sidebar = sidebar
        self.files_tab = files
        graph = ttk.Labelframe(panes, text="Call graph")
        details = ttk.Panedwindow(panes, orient="vertical")
        source = ttk.Labelframe(details, text="Source", padding=6)
        notes = ttk.Labelframe(details, text="Notes", padding=6)
        details.add(source, weight=4)
        details.add(notes, weight=1)
        panes.add(sidebar, weight=1)
        panes.add(graph, weight=5)
        panes.add(details, weight=2)

        symbols.columnconfigure(0, weight=1)
        symbols.rowconfigure(2, weight=1)
        self.query = tk.StringVar()
        ttk.Label(symbols, text="Search symbols").grid(row=0, column=0, sticky="w")
        self.search = ttk.Entry(symbols, textvariable=self.query)
        self.search.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        self.search.bind("<Return>", self.open_first_match)
        self.search.bind("<Escape>", self.clear_search)
        root.bind("<Control-f>", self.focus_search)
        results = ttk.Frame(symbols)
        results.grid(row=2, column=0, sticky="nsew")
        self.search_summary = tk.StringVar()
        ttk.Label(symbols, textvariable=self.search_summary).grid(row=3, column=0, sticky="w")
        self.symbols = ttk.Treeview(results, show="tree", selectmode="browse", takefocus=True)
        self.symbols.column("#0", width=225, minwidth=135)
        self.scrollable(results, self.symbols, ttk, horizontal=True)
        self.symbols.bind("<<TreeviewSelect>>", self.selection_changed)

        files.columnconfigure(0, weight=1)
        files.rowconfigure(2, weight=1)
        self.file_query = tk.StringVar()
        ttk.Label(files, text="Search files").grid(row=0, column=0, sticky="w")
        self.file_search = ttk.Entry(files, textvariable=self.file_query)
        self.file_search.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        self.file_search.bind("<Return>", self.open_first_file)
        self.file_search.bind("<Escape>", lambda event: self.clear_search(event, self.file_query))
        file_results = ttk.Frame(files)
        file_results.grid(row=2, column=0, sticky="nsew")
        self.file_tree = ttk.Treeview(file_results, show="tree", selectmode="browse")
        self.file_tree.column("#0", width=225, minwidth=135)
        self.scrollable(file_results, self.file_tree, ttk, horizontal=True)
        self.file_tree.bind("<<TreeviewSelect>>", self.file_selection_changed)
        self.file_summary = tk.StringVar()
        ttk.Label(files, textvariable=self.file_summary).grid(row=3, column=0, sticky="w")

        graph.columnconfigure(0, weight=1)
        graph.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(graph, padding=(6, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        for label, action in (
            ("Fit", self.fit_graph), ("−", lambda: self.zoom(0.8)),
            ("+", lambda: self.zoom(1.25)), ("Clear focus", self.clear_focus),
        ):
            width = 10 if label == "Clear focus" else 4
            ttk.Button(toolbar, text=label, command=action, width=width).pack(side="left", padx=(0, 4))
        self.layout_mode = tk.StringVar(value="Hierarchy")
        for mode in ("Hierarchy", "By file"):
            ttk.Radiobutton(toolbar, text=mode, value=mode, variable=self.layout_mode,
                            command=self.change_layout).pack(side="left", padx=(4, 0))
        self.io_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="I/O", variable=self.io_enabled, command=self.change_layout).pack(
            side="left", padx=(6, 0)
        )
        legend = ttk.Frame(graph, padding=(7, 0, 7, 5))
        legend.grid(row=1, column=0, sticky="ew")
        self.callers_label = tk.StringVar()
        self.callees_label = tk.StringVar()
        self.callers_legend = ttk.Label(legend, textvariable=self.callers_label)
        self.callers_legend.pack(side="left", padx=(0, 12))
        self.callees_legend = ttk.Label(legend, textvariable=self.callees_label)
        self.callees_legend.pack(side="left")
        self.io_summary = tk.StringVar()
        ttk.Label(legend, textvariable=self.io_summary).pack(side="left", padx=(12, 0))
        viewport = ttk.Frame(graph, padding=(6, 0, 6, 6))
        viewport.grid(row=2, column=0, sticky="nsew")
        self.graph = tk.Canvas(viewport, width=640, background=self.COLORS["paper"], highlightthickness=0,
                               takefocus=True)
        self.scrollable(viewport, self.graph, ttk, horizontal=True)
        self.graph.bind("<ButtonPress-1>", self.start_graph_drag)
        self.graph.bind("<B1-Motion>", self.drag_graph)
        self.graph.bind("<ButtonRelease-1>", self.end_graph_drag)
        self.graph.bind("<Button-2>", lambda event: self.graph.scan_mark(event.x, event.y))
        self.graph.bind("<B2-Motion>", lambda event: self.graph.scan_dragto(event.x, event.y, gain=1))
        self.bind_graph_navigation()
        self.graph.bind("<Configure>", self.resize_graph)
        root.bind("<Escape>", lambda _event: self.clear_focus())
        self.nodes = []
        self.edges = []
        self.files = []
        self.visible_files = []
        self.layout = ([], {}, (0, 0))
        self.scale = 1.0
        self.fitting = True
        self.focus_id = None
        self.focus_path = None
        self.focus_resource = None
        self.graph_nodes = {}
        self.node_items = {}
        self.edge_items = {}
        self.io_items = {}
        self.resource_items = {}
        self.resources = {}
        self.io = {"resources": [], "edges": [], "unresolved": [], "documents": []}
        self.note_markers = {}
        self.notes_by_node = {}
        self.notes_error = None
        self.note_target = None
        self.orphan_note = None
        self.drag_origin = None
        self.drag_node = None
        self.drag_position = None
        self.dragging = False
        self.manual_positions = {}
        self.layout_extent = (0, 0)
        self.viewport_states = {}
        self.view_key = self.current_view_key()

        self.source = tk.Text(
            source, font="TkFixedFont", width=42, wrap="none", state="disabled",
            relief="flat", padx=8, pady=8, background=self.COLORS["paper"],
            foreground=self.COLORS["ink"],
        )
        self.keyword_font = nametofont("TkFixedFont", root=root).copy()
        self.keyword_font.configure(weight="bold")
        self.source.tag_configure("keyword", font=self.keyword_font)
        self.scrollable(source, self.source, ttk, horizontal=True)
        notes.columnconfigure(0, weight=1)
        notes.rowconfigure(1, weight=1)
        self.add_note_button = ttk.Button(notes, text="Add note", command=self.add_note, state="disabled")
        self.add_note_button.grid(row=0, column=0, sticky="e", pady=(0, 4))
        note_list = ttk.Frame(notes)
        note_list.grid(row=1, column=0, sticky="nsew")
        self.note_text = tk.Text(note_list, height=4, width=25, wrap="word", state="disabled",
                                 relief="flat", padx=8, pady=6)
        self.scrollable(note_list, self.note_text, ttk)
        self.status = tk.StringVar()
        ttk.Label(main, textvariable=self.status).pack(fill="x", pady=(8, 0))
        self.query.trace_add("write", self.update_symbol_list)
        self.file_query.trace_add("write", self.update_file_list)
        self.sidebar.bind("<<NotebookTabChanged>>", self.change_sidebar)
        self.apply_theme()
        self.refresh()

    def apply_theme(self):
        self.COLORS = colors = DARK if self.dark_mode.get() else LIGHT
        style_widgets(self.style, colors)
        self.root.configure(background=colors["chrome"])
        self.graph.configure(background=colors["paper"])
        self.source.configure(background=colors["paper"], foreground=colors["ink"],
                              selectbackground=colors["selected"], selectforeground=colors["selected_ink"])
        self.note_text.configure(background=colors["paper"], foreground=colors["ink"],
                                 selectbackground=colors["selected"], selectforeground=colors["selected_ink"])
        self.source.tag_configure("focus", background=colors["source_focus"])
        for tag in SOURCE_TAGS:
            self.source.tag_configure(tag, foreground=colors[tag])
        self.callers_legend.configure(foreground=colors["caller"])
        self.callees_legend.configure(foreground=colors["accent"])
        self.draw_graph()

    @staticmethod
    def scrollable(parent, widget, ttk, *, horizontal=False):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        widget.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        widget.configure(yscrollcommand=vertical.set)
        if horizontal:
            bottom = ttk.Scrollbar(parent, orient="horizontal", command=widget.xview)
            bottom.grid(row=1, column=0, sticky="ew")
            widget.configure(xscrollcommand=bottom.set)

    def refresh(self):
        viewport = self.viewport()
        try:
            data = self.project.call_graph()
        except (CodeGraphError, OSError, sqlite3.Error) as error:
            self.status.set(str(error))
            return
        self.nodes, self.edges, self.files = data["nodes"], data["edges"], data["files"]
        self.io = data["io"]
        self.resources = {resource["id"]: resource for resource in self.io["resources"]}
        if self.focus_resource not in self.resources:
            self.focus_resource = None
        layout_error = None
        try:
            self.manual_positions = self.project.view_positions()
        except (CodeGraphError, OSError) as error:
            layout_error = str(error)
        self.load_notes()
        self.update_layout()
        self.graph_summary.set(f"{len(self.nodes)} symbols · {len(self.edges)} calls")
        if self.focus_id not in self.layout[1]:
            self.focus_id = None
        if self.focus_path not in self.files:
            self.focus_path = None
        self.update_symbol_list()
        self.update_file_list()
        self.restore_viewport(viewport)
        self.render_selection()
        if layout_error:
            self.status.set(layout_error)

    def update_layout(self):
        if self.layout_mode.get() == "Hierarchy":
            self.layout = hierarchy_layout(self.nodes, self.edges, NODE_WIDTH, NODE_HEIGHT)
        else:
            self.layout = layout_graph(self.nodes)
        if self.io_enabled.get() and self.resources:
            groups, positions, (width, height) = self.layout
            lanes = (
                ("Tables", [item for item in self.resources.values() if item["kind"] == "table"]),
                ("Files and handles", [item for item in self.resources.values() if item["kind"] != "table"]),
            )
            for title, members in lanes:
                if not members:
                    continue
                left = width + 45
                bottom = 72 + len(members) * ROW_STEP
                groups.append((title, left, 18, left + NODE_WIDTH + 30, bottom))
                for row, item in enumerate(members):
                    positions[item["id"]] = (left + NODE_WIDTH / 2 + 15, 66 + row * ROW_STEP)
                width, height = left + NODE_WIDTH + 54, max(height, bottom + 18)
            self.layout = groups, positions, (width, height)
        groups, positions, self.layout_extent = self.layout
        overrides = self.manual_positions.get(self.layout_mode.get(), {})
        for node_id in positions.keys() & overrides.keys():
            positions[node_id] = tuple(overrides[node_id])
        self.expand_layout()

    def expand_layout(self):
        groups, positions, _ = self.layout
        width, height = self.layout_extent
        for x, y in positions.values():
            width = max(width, x + NODE_WIDTH / 2 + 26)
            height = max(height, y + NODE_HEIGHT / 2 + 26)
        self.layout = groups, positions, (width, height)

    def current_view_key(self):
        return self.layout_mode.get(), self.io_enabled.get(), self.sidebar.select()

    def viewport(self):
        return (self.scale, self.fitting, self.graph.canvasx(0) / self.scale,
                self.graph.canvasy(0) / self.scale)

    def restore_viewport(self, state):
        if state is None or state[1]:
            self.fit_graph()
            return
        self.scale, self.fitting, x, y = state
        self.draw_graph()
        width, height = self.layout[2]
        if width and height:
            self.graph.xview_moveto(x / width)
            self.graph.yview_moveto(y / height)

    def change_sidebar(self, _event):
        key = self.current_view_key()
        if key != self.view_key:
            self.viewport_states[self.view_key] = self.viewport()
            self.view_key = key
            if key in self.viewport_states:
                self.restore_viewport(self.viewport_states[key])

    def change_layout(self):
        key = self.current_view_key()
        if key == self.view_key:
            return
        self.viewport_states[self.view_key] = self.viewport()
        self.view_key = key
        if not self.io_enabled.get() and self.focus_resource:
            self.focus_resource = None
            self.render_selection()
        self.update_layout()
        self.restore_viewport(self.viewport_states.get(key))

    def update_symbol_list(self, *_trace):
        query = self.query.get().strip().casefold()
        matches = self.nodes
        if query:
            ranked = []
            for node in self.nodes:
                qualified = node["qualified_name"].casefold()
                name = node["name"].casefold()
                if query in qualified:
                    rank = (query not in (name, qualified), not name.startswith(query))
                    ranked.append((rank, node))
            matches = [node for _, node in sorted(ranked, key=lambda hit: hit[0])]
        if not query:
            self.search_summary.set("")
        elif matches:
            self.search_summary.set(f"{len(matches)} " + ("match" if len(matches) == 1 else "matches"))
        else:
            self.search_summary.set("No matching symbols")
        self.symbols.delete(*self.symbols.get_children())
        for node in matches:
            suffix = " [stale]" if node["stale"] else ""
            self.symbols.insert("", "end", iid=node["id"], text=node["qualified_name"] + suffix)
        if self.focus_id and self.symbols.exists(self.focus_id):
            self.symbols.selection_set(self.focus_id)
            self.symbols.see(self.focus_id)

    def focus_search(self, _event):
        tab = self.sidebar.select()
        if tab == str(self.notes_browser):
            field = self.notes_browser.search
        elif tab == str(self.files_tab):
            field = self.file_search
        else:
            field = self.search
        field.focus_set()
        field.selection_range(0, "end")
        return "break"

    def clear_search(self, _event, query=None):
        if query is None:
            query = self.query
        if query.get():
            query.set("")
            return "break"
        return None

    def open_first_match(self, _event):
        matches = self.symbols.get_children()
        if matches:
            self.select_symbol(matches[0])
        return "break"

    def clear_focus(self, *, orphan_note=None):
        self.focus_id = None
        self.focus_path = None
        self.focus_resource = None
        self.orphan_note = orphan_note
        self.symbols.selection_remove(*self.symbols.selection())
        self.file_tree.selection_remove(*self.file_tree.selection())
        self.render_selection()

    def selection_changed(self, _event):
        selection = self.symbols.selection()
        if selection and selection[0] != self.focus_id:
            self.select_symbol(selection[0])

    def select_symbol(self, node_id):
        self.focus_id = node_id
        self.focus_path = None
        self.focus_resource = None
        self.orphan_note = None
        self.file_tree.selection_remove(*self.file_tree.selection())
        if self.symbols.exists(node_id):
            if self.symbols.selection() != (node_id,):
                self.symbols.selection_set(node_id)
            self.symbols.see(node_id)
        else:
            self.symbols.selection_remove(*self.symbols.selection())
        self.reveal_node(node_id)
        self.render_selection()

    def update_file_list(self, *_trace):
        query = self.file_query.get().strip().replace("\\", "/").casefold()
        matches = [path for path in self.files if query in path.casefold()]
        self.visible_files = sorted(matches, key=lambda path: (
            query not in (path.casefold(), path.rsplit("/", 1)[-1].casefold()), path
        ))
        summary = ""
        if query:
            if matches:
                noun = "file" if len(matches) == 1 else "files"
                summary = f"{len(matches)} matching {noun}"
            else:
                summary = "No matching files"
        self.file_summary.set(summary)
        tree = self.file_tree
        tree.delete(*tree.get_children())
        folders = {}
        for path in matches:
            parts = path.split("/")
            parent = ""
            for index, part in enumerate(parts[:-1], 1):
                folder = "/".join(parts[:index])
                if folder not in folders:
                    folders[folder] = tree.insert(parent, "end", text=part, open=True)
                parent = folders[folder]
        for path in matches:
            folder, _, name = path.rpartition("/")
            tree.insert(folders.get(folder, ""), "end", iid=path, text=name)
        if self.focus_path and tree.exists(self.focus_path):
            tree.selection_set(self.focus_path)
            tree.see(self.focus_path)

    def open_first_file(self, _event):
        if self.visible_files:
            self.select_file(self.visible_files[0])
        return "break"

    def file_selection_changed(self, _event):
        selection = self.file_tree.selection()
        if selection and selection[0] in self.visible_files and selection[0] != self.focus_path:
            self.select_file(selection[0])

    def select_file(self, path):
        self.focus_path, self.focus_id = path, None
        self.focus_resource = None
        self.orphan_note = None
        self.symbols.selection_remove(*self.symbols.selection())
        if self.file_tree.selection() != (path,):
            self.file_tree.selection_set(path)
        self.file_tree.see(path)
        self.render_selection()
        members = [node["id"] for node in self.nodes if node["source_ref"]["path"] == path]
        if members:
            self.reveal_node(members[0])

    def render_selection(self):
        self.highlight_graph()
        self.render_notes()
        if self.focus_resource:
            self.render_resource()
            return
        if not (self.focus_id or self.focus_path):
            self.show_source("", None)
            if self.orphan_note:
                self.status.set("The symbol for this note is missing.")
            else:
                self.status.set("Select a function or method to see its calls and source." if self.nodes
                                else "Run cg analyze to add Python functions and methods.")
            return
        try:
            if self.focus_path:
                data = self.project.file_source(self.focus_path)
            else:
                data = self.project.source(self.focus_id)
        except (CodeGraphError, OSError, sqlite3.Error) as error:
            self.show_source("", None)
            self.status.set(str(error))
            return
        ref = None
        if self.focus_path:
            location = self.focus_path
        else:
            node = data["node"]
            ref = node["source_ref"]
            location = f'{node["qualified_name"]} · {ref["path"]}:{ref["start_line"]}'
        current = data["source_current"]
        if self.focus_id and current and not self.notes_error:
            self.note_target = data["node"]
            self.add_note_button.state(["!disabled"])
        self.show_source(data["source"] or "", ref if current else None)
        self.status.set(location if current else f"{location} · Source changed. Run cg analyze and refresh.")

    def select_resource(self, resource_id):
        self.focus_resource, self.focus_id, self.focus_path = resource_id, None, None
        self.orphan_note = None
        self.symbols.selection_remove(*self.symbols.selection())
        self.file_tree.selection_remove(*self.file_tree.selection())
        self.reveal_node(resource_id)
        self.render_selection()

    def render_resource(self):
        resource = self.resources[self.focus_resource]
        names = {node["id"]: node["qualified_name"] for node in self.nodes}
        lines = [resource["name"], ""]
        for definition in resource["definitions"]:
            lines.append(f'Definition  {definition["path"]}:{definition["line"]}')
        for edge in self.io["edges"]:
            if edge["to_id"] != self.focus_resource:
                continue
            lines.append(f'{edge["type"]}  {names[edge["from_id"]]}')
            lines.extend(f'  {ref["path"]}:{ref["line"]}' for ref in edge["evidence"])
        self.source.configure(state="normal")
        self.source.delete("1.0", "end")
        self.source.insert("1.0", "\n".join(lines))
        self.source.configure(state="disabled")
        self.status.set(resource["name"])

    def load_notes(self):
        notes = defaultdict(list)
        self.notes_error = None
        entries = []
        try:
            entries = self.project.annotations()
            for note in entries:
                notes[note["target_node_id"]].append(note)
        except (CodeGraphError, OSError, sqlite3.Error) as error:
            self.notes_error = str(error)
        self.notes_by_node = notes
        self.notes_browser.set_notes(entries, self.nodes)
        if self.orphan_note:
            self.orphan_note = self.notes_browser.entries.get(self.orphan_note["id"])

    def render_notes(self):
        self.note_target = None
        self.add_note_button.state(["disabled"])
        if self.notes_error:
            text = self.notes_error
        elif self.focus_id or self.orphan_note:
            notes = (self.orphan_note,) if self.orphan_note else self.notes_by_node.get(self.focus_id, ())
            text = "\n\n".join(
                f'{note["type"]} · {note["status"]}\n{note["text"]}'
                for note in sorted(notes, key=lambda item: (item["created_at"], item["id"]))
            ) or "No notes yet."
        else:
            text = "Select a function or method to view its notes."
        self.note_text.configure(state="normal")
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", text)
        self.note_text.configure(state="disabled")

    def add_note(self):
        from .note_editor import open_note_editor

        if self.note_target:
            node = self.note_target
            open_note_editor(self.root, self.COLORS, node["qualified_name"],
                             lambda body: self.save_note(node["id"], body))

    def save_note(self, node_id, body):
        try:
            self.project.annotate(node_id, "NOTE", body)
        except (OSError, sqlite3.Error) as error:
            raise CodeGraphError(f"Cannot save note: {error}") from error
        self.refresh_notes()

    def refresh_notes(self):
        self.load_notes()
        self.restore_viewport(self.viewport())
        self.render_selection()

    def select_note(self, note):
        target = note["target_node_id"]
        if target in self.node_items:
            self.select_symbol(target)
        else:
            self.clear_focus(orphan_note=note)

    def edit_note(self, note):
        from .note_editor import open_note_editor

        if note:
            open_note_editor(self.root, self.COLORS, self.notes_browser.target_name(note),
                             lambda body: self.change_note(note, body), body=note["text"])

    def delete_note(self, note):
        from tkinter import messagebox

        if note and messagebox.askyesno("Delete note", "Delete the selected note?",
                                        parent=self.root, default=messagebox.NO):
            try:
                self.change_note(note, None)
            except CodeGraphError as error:
                self.status.set(str(error))

    def change_note(self, note, body):
        try:
            self.project.change_annotation(note["id"], body, expected_text=note["text"])
        except (OSError, sqlite3.Error) as error:
            raise CodeGraphError(f"Cannot save note: {error}") from error
        self.refresh_notes()

    def show_source(self, source, ref):
        self.source.configure(state="normal")
        self.source.delete("1.0", "end")
        lines = source.split("\n") if source else []
        digits = len(str(len(lines)))
        numbered = "\n".join(f"{number:>{digits}}  {line}" for number, line in enumerate(lines, 1))
        self.source.insert("1.0", numbered)
        if lines:
            self.color_source(source, lines, digits + 2)
        if ref:
            first = f'{ref["start_line"]}.0'
            self.source.tag_add("focus", first, f'{ref["end_line"] + 1}.0')
            self.source.yview(first)
        self.source.configure(state="disabled")

    def color_source(self, source, lines, gutter):
        ranges = defaultdict(list)
        for line in range(1, len(lines) + 1):
            ranges["line_number"].extend((f"{line}.0", f"{line}.{gutter}"))
        for tag, (first_line, first_col), (last_line, last_col) in source_styles(source):
            for line in range(first_line, min(last_line, len(lines)) + 1):
                start = first_col if line == first_line else 0
                end = last_col if line == last_line else len(lines[line - 1])
                if start < end:
                    ranges[tag].extend((f"{line}.{gutter + start}", f"{line}.{gutter + end}"))
        for tag, indices in ranges.items():
            self.source.tag_add(tag, *indices)

    def resize_graph(self, _event):
        if self.fitting and self.nodes and abs(self.fit_scale() - self.scale) > 0.001:
            self.fit_graph()

    def fit_scale(self):
        width, height = self.layout[2]
        if not width or self.graph.winfo_width() <= 1 or self.graph.winfo_height() <= 1:
            return 1.0
        return min(1.0, self.graph.winfo_width() / width, self.graph.winfo_height() / height)

    def fit_graph(self):
        self.fitting = True
        self.scale = self.fit_scale()
        self.draw_graph()
        self.graph.xview_moveto(0)
        self.graph.yview_moveto(0)

    def bind_graph_navigation(self):
        graph = self.graph
        for event, axis in (("<MouseWheel>", "y"), ("<Shift-MouseWheel>", "x"),
                            ("<Control-MouseWheel>", "zoom")):
            graph.bind(event, lambda event, axis=axis: self.graph_wheel(event, axis))
        for key, dx, dy in (("Left", -48, 0), ("Right", 48, 0), ("Up", 0, -48), ("Down", 0, 48)):
            graph.bind(f"<{key}>", lambda _event, dx=dx, dy=dy: self.pan_by(dx, dy))
        for key, factor in (("plus", 1.25), ("equal", 1.25), ("minus", 0.8)):
            graph.bind(f"<{key}>", lambda _event, factor=factor: self.zoom(factor))

    def graph_wheel(self, event, axis):
        steps = max(-5, min(5, event.delta / 120))
        if axis == "zoom":
            self.zoom(1.25 ** steps, (event.x, event.y))
        elif axis == "x":
            self.pan_by(-48 * steps, 0)
        else:
            self.pan_by(0, -48 * steps)
        return "break"

    def pan_by(self, dx, dy):
        if self.nodes:
            canvas = self.graph
            width, height = self.layout[2]
            if dx:
                canvas.xview_moveto((canvas.canvasx(0) + dx) / (width * self.scale))
            if dy:
                canvas.yview_moveto((canvas.canvasy(0) + dy) / (height * self.scale))
        return "break"

    def start_graph_drag(self, event):
        self.graph.focus_set()
        self.drag_origin = event.x, event.y
        current = self.graph.find_withtag("current")
        self.drag_node = self.graph_nodes.get(current[0]) if current else None
        self.drag_position = self.layout[1].get(self.drag_node)
        self.dragging = False
        self.graph.scan_mark(event.x, event.y)

    def drag_graph(self, event):
        if self.drag_origin:
            x, y = self.drag_origin
            if self.dragging or max(abs(event.x - x), abs(event.y - y)) >= 4:
                self.dragging = True
                if self.drag_node is None:
                    self.graph.scan_dragto(event.x, event.y, gain=1)
                else:
                    start_x, start_y = self.drag_position
                    point = (max(NODE_WIDTH / 2 + 18, start_x + (event.x - x) / self.scale),
                             max(NODE_HEIGHT / 2 + 18, start_y + (event.y - y) / self.scale))
                    self.move_node(self.drag_node, point)

    def end_graph_drag(self, event):
        if self.drag_origin and not self.dragging:
            self.graph_clicked(event)
        elif self.drag_node and self.layout[1][self.drag_node] != self.drag_position:
            point = self.layout[1][self.drag_node]
            mode = self.layout_mode.get()
            try:
                self.project.save_view_position(mode, self.drag_node, point)
                self.manual_positions.setdefault(mode, {})[self.drag_node] = list(point)
            except (CodeGraphError, OSError, sqlite3.Error) as error:
                self.status.set(f"Could not save node position: {error}")
            self.restore_viewport(self.viewport())
        self.drag_origin = None
        self.drag_node = None
        self.drag_position = None

    def move_node(self, node_id, point):
        canvas, scale = self.graph, self.scale
        positions = self.layout[1]
        old_x, old_y = positions[node_id]
        positions[node_id] = point
        self.fitting = False
        self.expand_layout()
        width, height = self.layout[2]
        canvas.configure(scrollregion=(0, 0, width * scale, height * scale))
        items = self.node_items if node_id in self.node_items else self.resource_items
        marker = (self.note_markers[node_id],) if node_id in self.note_markers else ()
        for item in (*items[node_id], *marker):
            canvas.move(item, (point[0] - old_x) * scale, (point[1] - old_y) * scale)
        self.move_edges(node_id, self.edges, self.edge_items)
        if self.io_enabled.get():
            self.move_edges(node_id, self.io["edges"], self.io_items, with_labels=True)

    def move_edges(self, node_id, edges, items, *, with_labels=False):
        positions = self.layout[1]
        ports = edge_ports(edges, positions)
        for edge in edges:
            start, end = edge["from_id"], edge["to_id"]
            if node_id not in (start, end):
                continue
            points = edge_path(positions[start], positions[end], ports[edge["id"]])
            item = items[edge["id"]]
            if with_labels:
                item, label = item
                self.graph.coords(label, (points[2] + points[4]) * self.scale / 2,
                                  (points[3] + points[5]) * self.scale / 2)
            self.graph.coords(item, *(value * self.scale for value in points))

    def zoom(self, factor, anchor=None):
        if not self.nodes:
            return
        canvas = self.graph
        screen_x, screen_y = anchor or (canvas.winfo_width() / 2, canvas.winfo_height() / 2)
        center_x = canvas.canvasx(screen_x) / self.scale
        center_y = canvas.canvasy(screen_y) / self.scale
        lower = self.fit_scale()
        self.fitting = False
        self.scale = min(3.0, max(lower, self.scale * factor))
        self.draw_graph()
        width, height = self.layout[2]
        canvas.xview_moveto((center_x * self.scale - screen_x) / (width * self.scale))
        canvas.yview_moveto((center_y * self.scale - screen_y) / (height * self.scale))

    def reveal_node(self, node_id):
        canvas = self.graph
        if canvas.winfo_width() <= 1 or canvas.winfo_height() <= 1:
            return
        items = self.node_items if node_id in self.node_items else self.resource_items
        box, _ = items[node_id]
        left, top, right, bottom = canvas.bbox(box)
        width, height = self.layout[2]
        for start, end, origin, visible, extent, move in (
            (left, right, canvas.canvasx(0), canvas.winfo_width(), width, canvas.xview_moveto),
            (top, bottom, canvas.canvasy(0), canvas.winfo_height(), height, canvas.yview_moveto),
        ):
            if start < origin or end > origin + visible:
                move(((start + end - visible) / 2) / (extent * self.scale))

    def draw_graph(self):
        canvas, colors = self.graph, self.COLORS
        groups, positions, (width, height) = self.layout
        scale = self.scale
        canvas.delete("all")
        self.graph_nodes.clear()
        self.node_items.clear()
        self.edge_items.clear()
        self.note_markers.clear()
        self.resource_items.clear()
        self.io_items.clear()
        canvas.configure(scrollregion=(0, 0, width * scale, height * scale))
        label_font = ("Segoe UI", -max(6, round(12 * scale)))
        title_font = ("Segoe UI", -max(7, round(12 * scale)), "bold")
        for path, x1, y1, x2, y2 in groups:
            canvas.create_rectangle(x1 * scale, y1 * scale, x2 * scale, y2 * scale,
                                    fill=colors["group"], outline=colors["muted_line"])
            if scale >= 0.28:
                title = path if len(path) <= 25 else "…" + path[-24:]
                canvas.create_text((x1 + 10) * scale, (y1 + 15) * scale, anchor="w",
                                   text=title, font=title_font, fill=colors["ink"])
        ports = edge_ports(self.edges, positions)
        for edge in self.edges:
            points = edge_path(positions[edge["from_id"]], positions[edge["to_id"]], ports[edge["id"]])
            inferred = edge["provenance"]["status"] == "INFERRED"
            item = canvas.create_line(
                *(value * scale for value in points), arrow="last", smooth=True,
                dash=(4, 3) if inferred else (), tags="edges",
            )
            self.edge_items[edge["id"]] = item
        for node in self.nodes:
            node_id = node["id"]
            x, y = positions[node_id]
            box = canvas.create_rectangle(
                (x - NODE_WIDTH / 2) * scale, (y - NODE_HEIGHT / 2) * scale,
                (x + NODE_WIDTH / 2) * scale, (y + NODE_HEIGHT / 2) * scale, tags="nodes",
            )
            name = node["name"]
            if node["type"] == "Method":
                name = ".".join(node["qualified_name"].split(".")[-2:])
            if len(name) > 26:
                name = "…" + name[-25:]
            if self.layout_mode.get() == "Hierarchy" and scale >= 0.65:
                filename = node["source_ref"]["path"].rsplit("/", 1)[-1]
                if len(filename) > 22:
                    filename = "…" + filename[-21:]
                name = f"{name}\n{filename}"
            label = canvas.create_text(
                x * scale, y * scale, text=name, width=(NODE_WIDTH - 12) * scale,
                font=label_font, tags="nodes", state="normal" if scale >= 0.36 else "hidden",
            )
            self.node_items[node_id] = (box, label)
            self.graph_nodes[box] = self.graph_nodes[label] = node_id
            if node_id in self.notes_by_node:
                marker_x = (x + NODE_WIDTH / 2 - 5) * scale
                marker_y = (y - NODE_HEIGHT / 2 + 5) * scale
                radius = max(2, 4 * scale)
                marker = canvas.create_oval(marker_x - radius, marker_y - radius,
                                            marker_x + radius, marker_y + radius,
                                            fill=colors["both"], outline=colors["paper"], tags="nodes")
                self.note_markers[node_id] = marker
                self.graph_nodes[marker] = node_id
        if self.io_enabled.get():
            self.draw_io(positions, label_font)
        self.highlight_graph()

    def draw_io(self, positions, font):
        canvas, scale = self.graph, self.scale
        ports = edge_ports(self.io["edges"], positions)
        for edge in self.io["edges"]:
            start, end = positions[edge["from_id"]], positions[edge["to_id"]]
            points = edge_path(start, end, ports[edge["id"]])
            line = canvas.create_line(*(value * scale for value in points), arrow="last",
                                      smooth=True, dash=(2, 4), tags="edges")
            label = canvas.create_text((points[2] + points[4]) * scale / 2,
                                       (points[3] + points[5]) * scale / 2,
                                       text="READ" if edge["type"] == "READS" else "WRITE",
                                       font=font, state="hidden", tags="io_labels")
            self.io_items[edge["id"]] = line, label
        for resource_id, resource in self.resources.items():
            x, y = positions[resource_id]
            left, right = x - NODE_WIDTH / 2, x + NODE_WIDTH / 2
            top, bottom = y - NODE_HEIGHT / 2, y + NODE_HEIGHT / 2
            points = (left, top, right - 9, top, right, top + 9, right, bottom, left, bottom)
            box = canvas.create_polygon(*(value * scale for value in points), tags="nodes")
            name = resource["name"]
            if len(name) > 24:
                name = "…" + name[-23:]
            label = canvas.create_text(x * scale, y * scale, text=f'{resource["kind"].upper()} · {name}',
                                       font=font, width=(NODE_WIDTH - 12) * scale, tags="nodes",
                                       state="normal" if scale >= 0.36 else "hidden")
            self.resource_items[resource_id] = box, label
            self.graph_nodes[box] = self.graph_nodes[label] = resource_id

    def highlight_io(self, file_nodes, has_focus):
        if not self.io_enabled.get():
            self.io_summary.set("")
            return set()
        canvas, colors = self.graph, self.COLORS
        related = set()
        for edge in self.io["edges"]:
            origin, target = edge["from_id"], edge["to_id"]
            active = origin == self.focus_id or target == self.focus_resource or origin in file_nodes
            if active:
                related.update((origin, target))
            line, label = self.io_items[edge["id"]]
            key = "io_read" if edge["type"] == "READS" else "io_write"
            canvas.itemconfigure(line, fill=colors[key] if active or not has_focus else colors["muted_line"],
                                 width=max(2 if active else 1, self.scale * 2))
            canvas.itemconfigure(label, fill=colors[key], state="normal" if active else "hidden")
            if active:
                canvas.tag_raise(line)
                canvas.tag_raise(label)
        for resource_id, (box, label) in self.resource_items.items():
            active = resource_id in related or resource_id == self.focus_resource
            visible = active or not has_focus
            canvas.itemconfigure(box, fill=colors["io_fill" if visible else "muted_fill"],
                                 outline=colors["io_read" if visible else "muted_line"])
            canvas.itemconfigure(label, fill=colors["ink" if visible else "muted_ink"])
            if active:
                canvas.tag_raise(box)
                canvas.tag_raise(label)
        affected = sum(item["state"] != "CURRENT" for item in self.io["documents"])
        unresolved = len(self.io["unresolved"])
        parts = []
        if self.resources:
            parts.append(f'{len(self.resources)} I/O resources')
        if affected:
            parts.append(f"{affected} I/O documents need attention")
        if unresolved:
            parts.append(f"{unresolved} unresolved")
        self.io_summary.set(" · ".join(parts))
        return related

    def highlight_graph(self):
        canvas, colors = self.graph, self.COLORS
        file_nodes = {
            node["id"] for node in self.nodes
            if self.focus_path and node["source_ref"]["path"] == self.focus_path
        }
        has_focus = bool(self.focus_id or file_nodes or self.focus_resource)
        callers, callees = set(), set()
        canvas.tag_raise("edges")
        canvas.tag_raise("nodes")
        for edge in self.edges:
            origin, target = edge["from_id"], edge["to_id"]
            incoming, outgoing = target == self.focus_id, origin == self.focus_id
            within_file = origin in file_nodes and target in file_nodes
            if incoming:
                callers.add(origin)
            if outgoing:
                callees.add(target)
            item = self.edge_items[edge["id"]]
            if incoming or outgoing or within_file:
                color = colors["both" if incoming and outgoing else "caller" if incoming else "accent"]
                arrow = tuple(max(minimum, round(size * self.scale))
                              for minimum, size in ((6, 9), (8, 12), (3, 4)))
                canvas.itemconfigure(item, fill=color, width=max(2, 3 * self.scale), arrowshape=arrow)
                canvas.tag_raise(item)
            else:
                color = colors["muted_line" if has_focus else "line"]
                arrow = tuple(max(1, round(value * self.scale)) for value in (8, 10, 3))
                canvas.itemconfigure(item, fill=color, width=max(1, 1.5 * self.scale), arrowshape=arrow)
        if self.focus_path:
            self.callers_label.set("")
            self.callees_label.set(f"File symbols · {len(file_nodes)}")
        else:
            self.callers_label.set(f"Callers · {len(callers)}" if self.focus_id else "Callers")
            self.callees_label.set(f"Callees · {len(callees)}" if self.focus_id else "Callees")
        io_neighbors = self.highlight_io(file_nodes, has_focus)
        for node_id, (box, label) in self.node_items.items():
            if node_id == self.focus_id:
                fill, border, ink = ("selected", "accent", "selected_ink")
            elif node_id in callers and node_id in callees:
                fill, border, ink = ("both_fill", "both", "ink")
            elif node_id in callers:
                fill, border, ink = ("caller_fill", "caller", "ink")
            elif node_id in callees or node_id in file_nodes:
                fill, border, ink = ("related", "accent", "ink")
            elif node_id in io_neighbors:
                fill, border, ink = ("io_fill", "io_read", "ink")
            elif has_focus:
                fill, border, ink = ("muted_fill", "muted_line", "muted_ink")
            else:
                fill, border, ink = ("surface", "border", "ink")
            canvas.itemconfigure(box, fill=colors[fill], outline=colors[border])
            canvas.itemconfigure(label, fill=colors[ink])
            if node_id == self.focus_id or any(node_id in group for group in (callers, callees, file_nodes, io_neighbors)):
                canvas.tag_raise(box)
                canvas.tag_raise(label)
                if node_id in self.note_markers:
                    canvas.tag_raise(self.note_markers[node_id])

    def graph_clicked(self, _event):
        current = self.graph.find_withtag("current")
        if current:
            node_id = self.graph_nodes.get(current[0])
            if node_id:
                if node_id in self.resources:
                    self.select_resource(node_id)
                else:
                    self.select_symbol(node_id)
