from src.plan.nodes import JoinNode, ProjectionNode, SelectionNode, SortNode, children, total_cost

# citljivi nazivi algoritama za ispis; kodovi prate a1-a9 numeraciju iz
# src/cost/selection.py (predavanje "obrada upita", slajd 5-9) i konstante iz
# src/cost/join.py; nepoznat kod se ispisuje doslovno (fallback u _algorithm_label)
_ALGORITHM_LABELS = {
    "A1_full_scan": "A1 linijsko skeniranje (pun scan)",
    "A2_clustering_equality_key": "A2 grupišući indeks, jednakost po ključu",
    "A3_clustering_equality_nonkey": "A3 grupišući indeks, jednakost po ne-ključnom atributu",
    "A4_secondary_equality": "A4 sekundarni indeks, jednakost",
    "A5_clustering_comparison": "A5 grupišući indeks, poređenje",
    "A6_secondary_comparison": "A6 sekundarni indeks, poređenje",
    "A8_composite_index": "A8 kompozitni indeks",
    "A9_conjunctive_intersection": "A9 presek preko više indeksa",
    "no_selection": "bez selekcije, baznu tabelu čita nadređena operacija",
    "join_index_access": "pristup kroz indeks spajanja, cena uračunata u spajanje",
    "in_memory_join": "spajanje u memoriji, oba ulaza staju u bafer",
    "nested_loop": "ugnježđena petlja",
    "block_nested_loop": "blok ugnježđena petlja",
    "index_nested_loop": "indeks ugnježđena petlja",
    "sort_merge": "obedinjeno spajanje (sort-merge)",
    "hash_join": "heš spajanje",
}


def _algorithm_label(code: str) -> str:
    return _ALGORITHM_LABELS.get(code, code)


def _format_cost(cost) -> str:
    # cene preko hesa su razlomljene (+0.2 po pristupu korpi); ispisi bez binarnih
    # artefakata (2.2+12+250 = 264.20000000000002), cele brojeve bez decimala
    rounded = round(cost, 6)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def _cost_suffix(node) -> str:
    return (
        f"- cena: {_format_cost(node.cost_blocks)} blok transfera, "
        f"izlaz: ~{node.output_rows:.1f} redova ({node.output_blocks} blokova)"
    )


def _selection_line(node: SelectionNode) -> str:
    label = _algorithm_label(node.algorithm)
    if node.index_names:
        label += f" (indeksi: {', '.join(node.index_names)})"
    return f"Selekcija({node.table}) [{label}] {_cost_suffix(node)}"


def _join_line(node: JoinNode) -> str:
    label = _algorithm_label(node.algorithm)
    return f"Spajanje({', '.join(node.tables)}) [{label}] {_cost_suffix(node)}"


def _projection_line(node: ProjectionNode) -> str:
    return f"Projekcija({', '.join(node.attributes)}) {_cost_suffix(node)}"


def _sort_line(node: SortNode) -> str:
    # postavka trazi algoritam za svaku operaciju: cena 0 znaci da je ulaz
    # vec bio u memoriji (ili prazan) pa se sortira u memoriji; inace je
    # spoljno objedinjeno sortiranje (sort_cost formula)
    algorithm = "Sortiranje u memoriji" if node.cost_blocks == 0 else "Spoljno objedinjeno sortiranje"
    return f"{algorithm} po {node.attribute} {_cost_suffix(node)}"


_LINE_BUILDERS = {
    SelectionNode: _selection_line,
    JoinNode: _join_line,
    ProjectionNode: _projection_line,
    SortNode: _sort_line,
}


def _node_line(node) -> str:
    builder = _LINE_BUILDERS.get(type(node))
    if builder is None:
        raise TypeError(f"nepoznat tip cvora plana: {type(node)!r}")
    return builder(node)


def _render(node, depth: int, lines: "list[str]") -> None:
    lines.append("  " * depth + _node_line(node))
    for child in children(node):
        _render(child, depth + 1, lines)


def format_plan(plan) -> str:
    lines: "list[str]" = []
    _render(plan, 0, lines)
    lines.append("")
    lines.append(f"UKUPNA CENA PLANA: {_format_cost(total_cost(plan))} blok transfera")
    return "\n".join(lines)


def print_plan(plan) -> None:
    print(format_plan(plan))
