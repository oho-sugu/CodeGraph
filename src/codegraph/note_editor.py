"""A modal editor for adding a note to the selected symbol."""

import tkinter as tk
from tkinter import ttk

from .core import CodeGraphError


def open_note_editor(root, colors, title, save):
    dialog = tk.Toplevel(root)
    dialog.title(f"Add note · {title}")
    dialog.transient(root)
    fields = ttk.Frame(dialog, padding=12)
    fields.pack(fill="both", expand=True)
    editor = tk.Text(fields, width=54, height=8, wrap="word", padx=8, pady=8,
                     background=colors["paper"], foreground=colors["ink"], insertbackground=colors["ink"])
    editor.pack(fill="both", expand=True)
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
