"""Layer a call graph from callers to callees, keeping cycles in one column."""

from collections import defaultdict
from math import ceil


def components(outgoing, incoming):
    """Yield strongly connected components in topological order."""
    visited, finished = set(), []
    for node in outgoing:
        if node in visited:
            continue
        visited.add(node)
        stack = [(node, iter(outgoing[node]))]
        while stack:
            current, neighbors = stack[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                finished.append(current)
                stack.pop()
            elif neighbor not in visited:
                visited.add(neighbor)
                stack.append((neighbor, iter(outgoing[neighbor])))

    visited.clear()
    for node in reversed(finished):
        if node in visited:
            continue
        visited.add(node)
        stack, members = [node], []
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbor in incoming[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        yield members


def hierarchy_layout(nodes, edges, node_width, node_height):
    order = {node["id"]: index for index, node in enumerate(nodes)}
    outgoing = {node: [] for node in order}
    incoming = {node: [] for node in order}
    for edge in edges:
        origin, target = edge["from_id"], edge["to_id"]
        outgoing[origin].append(target)
        incoming[target].append(origin)

    layers = defaultdict(list)
    depth = {}
    isolated = []
    for members in components(outgoing, incoming):
        if len(members) == 1 and not (outgoing[members[0]] or incoming[members[0]]):
            isolated.extend(members)
            continue
        layer = max((depth[parent] + 1 for member in members for parent in incoming[member]
                     if parent in depth), default=0)
        layers[layer].append(sorted(members, key=order.__getitem__))
        depth.update(dict.fromkeys(members, layer))

    # Keep a cycle together and order each layer by the positions of its callers.
    rows = {}
    columns = []
    for layer in sorted(layers):
        def group_order(group):
            callers = [rows[parent] for node in group for parent in incoming[node] if parent in rows]
            return (sum(callers) / len(callers) if callers else 0, order[group[0]])

        column = [node for group in sorted(layers[layer], key=group_order) for node in group]
        rows.update({node: index for index, node in enumerate(column)})
        columns.append(column)

    positions = {}
    margin = 26
    step_x, step_y = node_width + 82, node_height + 16
    tallest = max(map(len, columns), default=0)
    column_count = max(len(columns), min(len(isolated), 4))
    for col, column in enumerate(columns):
        for row, node in enumerate(column):
            positions[node] = (
                margin + node_width / 2 + col * step_x,
                margin + node_height / 2 + (row + (tallest - len(column)) / 2) * step_y,
            )
    connected_height = (tallest - 1) * step_y + node_height if tallest else 0
    bottom = margin + connected_height
    if isolated:
        if tallest:
            bottom += 24
        for index, node in enumerate(sorted(isolated, key=order.__getitem__)):
            row, col = divmod(index, column_count)
            positions[node] = (margin + node_width / 2 + col * step_x,
                               bottom + node_height / 2 + row * step_y)
        bottom += (ceil(len(isolated) / column_count) - 1) * step_y + node_height
    width = (column_count - 1) * step_x + node_width + 2 * margin if column_count else 0
    return [], positions, (width, bottom + margin)
