"""Viewer controls for finding and editing notes."""

import tkinter as tk
from tkinter import ttk

from .core import CodeGraphError


def open_note_editor(root, colors, title, save, *, body=None):
    dialog = tk.Toplevel(root)
    action = "Add note" if body is None else "Edit note"
    dialog.title(f"{action} · {title}")
    dialog.transient(root)
    fields = ttk.Frame(dialog, padding=12)
    fields.pack(fill="both", expand=True)
    editor = tk.Text(fields, width=54, height=8, wrap="word", padx=8, pady=8,
                     background=colors["paper"], foreground=colors["ink"], insertbackground=colors["ink"])
    editor.pack(fill="both", expand=True)
    if body is not None:
        editor.insert("1.0", body)
    message = tk.StringVar(master=dialog)
    ttk.Label(fields, textvariable=message, foreground=colors["caller"]).pack(anchor="w", pady=(5, 0))

    def submit(_event=None):
        body = editor.get("1.0", "end-1c")
        if not body.strip():
            message.set("Enter some text for the note.")
            return "break"
        try:
            save(body)
        except CodeGraphError as error:
            message.set(str(error))
        else:
            dialog.destroy()
        return "break"

    actions = ttk.Frame(fields)
    actions.pack(fill="x")
    ttk.Button(actions, text="Save note", command=submit).pack(side="right")
    ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right", padx=5)
    dialog.bind("<Control-Return>", submit)
    dialog.bind("<Escape>", lambda _event: dialog.destroy() or "break")
    dialog.grab_set()
    editor.focus_set()


class NotesBrowser(ttk.Frame):
    def __init__(self, parent, select, edit, delete):
        super().__init__(parent, padding=6)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.entries = {}
        self.targets = {}
        self.selected_id = None
        self.query = tk.StringVar(master=self)
        ttk.Label(self, text="Search notes").grid(row=0, column=0, sticky="w")
        self.search = ttk.Entry(self, textvariable=self.query)
        self.search.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        self.search.bind("<Return>", self.open_first)
        self.search.bind("<Escape>", self.clear_search)

        self.tree = ttk.Treeview(self, show="tree", selectmode="browse")
        self.tree.column("#0", width=225, minwidth=140)
        self.tree.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        self.edit_button = ttk.Button(actions, text="Edit", command=lambda: edit(self.selected()), state="disabled")
        self.edit_button.pack(side="left")
        self.delete_button = ttk.Button(
            actions, text="Delete", command=lambda: delete(self.selected()), state="disabled"
        )
        self.delete_button.pack(side="left", padx=4)
        self.summary = tk.StringVar(master=self)
        ttk.Label(self, textvariable=self.summary).grid(row=4, column=0, sticky="w")
        self.on_select = select
        self.query.trace_add("write", self.update_list)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)

    def set_notes(self, notes, nodes):
        self.entries = {note["id"]: note for note in sorted(notes, key=lambda item: (
            item["created_at"], item["id"]
        ), reverse=True)}
        self.targets = {
            node["id"]: (node["qualified_name"], node["source_ref"]["path"]) for node in nodes
        }
        self.update_list()

    def target_name(self, note):
        return self.targets.get(note["target_node_id"], ("Missing symbol", ""))[0]

    def update_list(self, *_trace):
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        for note_id, note in self.entries.items():
            name, path = self.targets.get(note["target_node_id"], ("Missing symbol", ""))
            fields = (name, path, note["target_node_id"], note["text"], note["status"])
            if any(query in field.casefold() for field in fields):
                snippet = " ".join(note["text"].split())
                if len(snippet) > 55:
                    snippet = snippet[:54] + "…"
                self.tree.insert("", "end", iid=note_id, text=f'{note["status"]} · {snippet} · {name}')
        if self.selected_id and self.tree.exists(self.selected_id):
            self.tree.selection_set(self.selected_id)
            self.tree.see(self.selected_id)
        else:
            self.selected_id = None
        self.set_action_state()
        if not self.tree.get_children():
            self.summary.set("No matching notes." if query else "No notes yet.")
        else:
            self.summary.set("")

    def selected(self):
        selection = self.tree.selection()
        return self.entries.get(selection[0]) if selection else None

    def set_action_state(self):
        state = "!disabled" if self.selected() else "disabled"
        self.edit_button.state([state])
        self.delete_button.state([state])

    def selection_changed(self, _event):
        self.set_action_state()
        note = self.selected()
        note_id = note["id"] if note else None
        if note_id != self.selected_id:
            self.selected_id = note_id
            if note:
                self.on_select(note)

    def open_first(self, _event):
        children = self.tree.get_children()
        if children:
            self.selected_id = children[0]
            self.tree.selection_set(self.selected_id)
            self.tree.see(self.selected_id)
            self.set_action_state()
            self.on_select(self.entries[self.selected_id])
        return "break"

    def clear_search(self, _event):
        if self.query.get():
            self.query.set("")
            return "break"
        return None
