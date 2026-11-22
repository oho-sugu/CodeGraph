"""Palettes for the Tk controls, graph, and Python source."""

LIGHT = {
    "chrome": "#edf1f7", "paper": "#f4f6fa", "surface": "#ffffff", "ink": "#19253a",
    "selected_ink": "#ffffff", "selected": "#254c93", "related": "#dce9ff", "accent": "#254c93",
    "line": "#8798b3", "border": "#92a3bd", "group": "#edf1f7",
    "muted_fill": "#f3f5f9", "muted_ink": "#6e7c92", "muted_line": "#ccd5e3",
    "caller": "#97501e", "caller_fill": "#fff0dc", "both": "#694499", "both_fill": "#eee2fc",
    "source_focus": "#dce9ff", "line_number": "#435675", "keyword": "#54208c",
    "string": "#0d563b", "comment": "#3f556b", "number": "#7f350c",
    "io_read": "#087466", "io_write": "#9b265c", "io_fill": "#e0f5ee",
}
DARK = {
    "chrome": "#171e2c", "paper": "#101621", "surface": "#202a3b", "ink": "#e7eefb",
    "selected_ink": "#ffffff", "selected": "#29579c", "related": "#223e63", "accent": "#8abaff",
    "line": "#7890ad", "border": "#546985", "group": "#1b2535",
    "muted_fill": "#172130", "muted_ink": "#8293aa", "muted_line": "#3e4d64",
    "caller": "#ffb779", "caller_fill": "#4a3028", "both": "#d9b3ff", "both_fill": "#3b3056",
    "source_focus": "#243449", "line_number": "#a7bad4", "keyword": "#e6bbff",
    "string": "#8ee6b3", "comment": "#a5bdd9", "number": "#ffc381",
    "io_read": "#62dfc2", "io_write": "#ff9ac4", "io_fill": "#193934",
}
SOURCE_TAGS = ("line_number", "keyword", "string", "comment", "number")


def style_widgets(style, colors):
    chrome, surface, ink = (colors[key] for key in ("chrome", "surface", "ink"))
    selected, accent, border = (colors[key] for key in ("selected", "accent", "border"))
    style.configure(".", background=chrome, foreground=ink, bordercolor=border,
                    lightcolor=chrome, darkcolor=chrome, troughcolor=chrome)
    style.configure("TButton", background=surface, focuscolor=accent)
    style.map("TButton", background=[("active", colors["related"])])
    style.configure("TCheckbutton", indicatorbackground=surface, focuscolor=accent)
    style.map("TCheckbutton", indicatorbackground=[("selected", selected)])
    style.configure("TRadiobutton", indicatorbackground=surface, focuscolor=accent)
    style.map("TRadiobutton", indicatorbackground=[("selected", selected)])
    style.configure("TEntry", fieldbackground=surface, insertcolor=ink,
                    selectbackground=selected, selectforeground=colors["selected_ink"])
    style.configure("TNotebook", background=chrome, bordercolor=border)
    style.configure("TNotebook.Tab", background=chrome, foreground=ink)
    style.map("TNotebook.Tab", background=[("selected", surface), ("active", colors["related"])])
    style.configure("Treeview", background=colors["paper"], fieldbackground=colors["paper"])
    style.map("Treeview", background=[("selected", selected)],
              foreground=[("selected", colors["selected_ink"])])
    style.configure("Vertical.TScrollbar", background=surface, arrowcolor=ink)
    style.configure("Horizontal.TScrollbar", background=surface, arrowcolor=ink)
