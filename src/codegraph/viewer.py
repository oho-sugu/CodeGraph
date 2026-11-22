"""Standalone Tk window for reading the local call graph and its source."""

import io
import keyword
import sqlite3
import tokenize
from collections import defaultdict
from math import ceil

from .core import CodeGraphError, Project


NODE_WIDTH = 154
NODE_HEIGHT = 30
COLUMN_STEP = 180
ROW_STEP = 43
SOURCE_COLORS = {
    "line_number": "#536782",
    "keyword": "#6231a1",
    "string": "#166044",
    "comment": "#526577",
    "number": "#93441a",
}
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
    COLORS = {
        "paper": "#f4f6fa", "white": "#ffffff", "ink": "#19253a",
        "selected": "#254c93", "related": "#dce9ff", "accent": "#254c93",
        "line": "#8798b3", "border": "#92a3bd", "group": "#edf1f7",
        "muted_fill": "#f3f5f9", "muted_ink": "#6e7c92", "muted_line": "#ccd5e3",
        "caller": "#97501e", "caller_fill": "#fff0dc",
        "both": "#694499", "both_fill": "#eee2fc",
    }

    def __init__(self, root, project: Project, tk, ttk):
        self.project = project
        self.tk = tk
        self.root = root
        root.title(f"CodeGraph · {project.root.name}")
        root.geometry("1280x740")
        root.minsize(740, 440)

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=str(project.root)).pack(side="left")
        ttk.Button(header, text="Refresh graph", command=self.refresh).pack(side="right")
        self.graph_summary = tk.StringVar()
        ttk.Label(header, textvariable=self.graph_summary).pack(side="right", padx=10)

        panes = ttk.Panedwindow(main, orient="horizontal")
        panes.pack(fill="both", expand=True)
        symbols = ttk.Labelframe(panes, text="Functions and methods", padding=6)
        graph = ttk.Labelframe(panes, text="Call graph")
        source = ttk.Labelframe(panes, text="Source", padding=6)
        panes.add(symbols, weight=1)
        panes.add(graph, weight=5)
        panes.add(source, weight=2)

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

        graph.columnconfigure(0, weight=1)
        graph.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(graph, padding=(6, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        for label, action in (
            ("Fit", self.fit_graph), ("−", lambda: self.zoom(0.8)),
            ("+", lambda: self.zoom(1.25)), ("Clear focus", self.clear_focus),
        ):
            ttk.Button(toolbar, text=label, command=action).pack(side="left", padx=(0, 4))
        legend = ttk.Frame(graph, padding=(7, 0, 7, 5))
        legend.grid(row=1, column=0, sticky="ew")
        self.callers_label = tk.StringVar()
        self.callees_label = tk.StringVar()
        ttk.Label(legend, textvariable=self.callers_label, foreground=self.COLORS["caller"]).pack(
            side="left", padx=(0, 12)
        )
        ttk.Label(legend, textvariable=self.callees_label, foreground=self.COLORS["accent"]).pack(side="left")
        viewport = ttk.Frame(graph, padding=(6, 0, 6, 6))
        viewport.grid(row=2, column=0, sticky="nsew")
        self.graph = tk.Canvas(viewport, width=640, background=self.COLORS["paper"], highlightthickness=0)
        self.scrollable(viewport, self.graph, ttk, horizontal=True)
        self.graph.bind("<Button-1>", self.graph_clicked)
        self.graph.bind("<Button-2>", lambda event: self.graph.scan_mark(event.x, event.y))
        self.graph.bind("<B2-Motion>", lambda event: self.graph.scan_dragto(event.x, event.y, gain=1))
        self.graph.bind("<Configure>", self.resize_graph)
        root.bind("<Escape>", lambda _event: self.clear_focus())
        self.nodes = []
        self.edges = []
        self.layout = ([], {}, (0, 0))
        self.scale = 1.0
        self.fitting = True
        self.focus_id = None
        self.graph_nodes = {}
        self.node_items = {}
        self.edge_items = {}

        self.source = tk.Text(
            source, font="TkFixedFont", width=42, wrap="none", state="disabled",
            relief="flat", padx=8, pady=8, background=self.COLORS["paper"],
            foreground=self.COLORS["ink"],
        )
        self.source.tag_configure("focus", background=self.COLORS["related"])
        for tag, color in SOURCE_COLORS.items():
            self.source.tag_configure(tag, foreground=color)
        self.scrollable(source, self.source, ttk, horizontal=True)
        self.status = tk.StringVar()
        ttk.Label(main, textvariable=self.status).pack(fill="x", pady=(8, 0))
        self.query.trace_add("write", self.update_symbol_list)
        self.refresh()

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
        try:
            data = self.project.call_graph()
        except (CodeGraphError, OSError, sqlite3.Error) as error:
            self.status.set(str(error))
            return
        self.nodes, self.edges = data["nodes"], data["edges"]
        self.layout = layout_graph(self.nodes)
        self.graph_summary.set(f"{len(self.nodes)} symbols · {len(self.edges)} calls")
        if self.focus_id not in self.layout[1]:
            self.focus_id = None
        self.update_symbol_list()
        if self.fitting:
            self.fit_graph()
        else:
            self.draw_graph()
        self.render_selection()

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
        self.search.focus_set()
        self.search.selection_range(0, "end")
        return "break"

    def clear_search(self, _event):
        if self.query.get():
            self.query.set("")
            return "break"
        return None

    def open_first_match(self, _event):
        matches = self.symbols.get_children()
        if matches:
            self.select_symbol(matches[0])
        return "break"

    def clear_focus(self):
        self.focus_id = None
        self.symbols.selection_remove(*self.symbols.selection())
        self.render_selection()

    def selection_changed(self, _event):
        selection = self.symbols.selection()
        if selection and selection[0] != self.focus_id:
            self.select_symbol(selection[0])

    def select_symbol(self, node_id):
        self.focus_id = node_id
        if self.symbols.exists(node_id):
            if self.symbols.selection() != (node_id,):
                self.symbols.selection_set(node_id)
            self.symbols.see(node_id)
        else:
            self.symbols.selection_remove(*self.symbols.selection())
        self.reveal_node(node_id)
        self.render_selection()

    def render_selection(self):
        self.highlight_graph()
        if not self.focus_id:
            self.show_source("", None)
            self.status.set("Select a function or method to see its calls and source." if self.nodes
                            else "Run cg analyze to add Python functions and methods.")
            return
        try:
            data = self.project.source(self.focus_id)
        except (CodeGraphError, OSError, sqlite3.Error) as error:
            self.show_source("", None)
            self.status.set(str(error))
            return
        node = data["node"]
        ref = node["source_ref"]
        current = data["source_current"]
        self.show_source(data["source"] or "", ref if current else None)
        location = f'{node["qualified_name"]} · {ref["path"]}:{ref["start_line"]}'
        self.status.set(location if current else f"{location} · Source changed. Run cg analyze and refresh.")

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

    def zoom(self, factor):
        if not self.nodes:
            return
        canvas = self.graph
        screen_x, screen_y = canvas.winfo_width() / 2, canvas.winfo_height() / 2
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
        box, _ = self.node_items[node_id]
        left, top, right, bottom = canvas.coords(box)
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
            if len(name) > 40:
                name = "…" + name[-39:]
            label = canvas.create_text(
                x * scale, y * scale, text=name, width=(NODE_WIDTH - 12) * scale,
                font=label_font, tags="nodes", state="normal" if scale >= 0.36 else "hidden",
            )
            self.node_items[node_id] = (box, label)
            self.graph_nodes[box] = self.graph_nodes[label] = node_id
        self.highlight_graph()

    def highlight_graph(self):
        canvas, colors = self.graph, self.COLORS
        callers, callees = set(), set()
        canvas.tag_raise("edges")
        canvas.tag_raise("nodes")
        for edge in self.edges:
            origin, target = edge["from_id"], edge["to_id"]
            incoming, outgoing = target == self.focus_id, origin == self.focus_id
            if incoming:
                callers.add(origin)
            if outgoing:
                callees.add(target)
            item = self.edge_items[edge["id"]]
            if incoming or outgoing:
                color = colors["both" if incoming and outgoing else "caller" if incoming else "accent"]
                arrow = tuple(max(minimum, round(size * self.scale))
                              for minimum, size in ((6, 9), (8, 12), (3, 4)))
                canvas.itemconfigure(item, fill=color, width=max(2, 3 * self.scale), arrowshape=arrow)
                canvas.tag_raise(item)
            else:
                color = colors["muted_line" if self.focus_id else "line"]
                arrow = tuple(max(1, round(value * self.scale)) for value in (8, 10, 3))
                canvas.itemconfigure(item, fill=color, width=max(1, 1.5 * self.scale), arrowshape=arrow)
        self.callers_label.set(f"Callers · {len(callers)}" if self.focus_id else "Callers")
        self.callees_label.set(f"Callees · {len(callees)}" if self.focus_id else "Callees")
        for node_id, (box, label) in self.node_items.items():
            if node_id == self.focus_id:
                fill, border, ink = ("selected", "accent", "white")
            elif node_id in callers and node_id in callees:
                fill, border, ink = ("both_fill", "both", "ink")
            elif node_id in callers:
                fill, border, ink = ("caller_fill", "caller", "ink")
            elif node_id in callees:
                fill, border, ink = ("related", "accent", "ink")
            elif self.focus_id:
                fill, border, ink = ("muted_fill", "muted_line", "muted_ink")
            else:
                fill, border, ink = ("white", "border", "ink")
            canvas.itemconfigure(box, fill=colors[fill], outline=colors[border])
            canvas.itemconfigure(label, fill=colors[ink])
            if node_id == self.focus_id or node_id in callers or node_id in callees:
                canvas.tag_raise(box)
                canvas.tag_raise(label)

    def graph_clicked(self, _event):
        current = self.graph.find_withtag("current")
        if current:
            node_id = self.graph_nodes.get(current[0])
            if node_id:
                self.select_symbol(node_id)
