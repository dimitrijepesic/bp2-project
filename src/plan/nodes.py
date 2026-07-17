from dataclasses import dataclass

from src.cost.misc import materialization_cost

# rows_per_block za join/projection/sort cvorove je pretpostavka: nemam statistiku o
# sirini sloga posle spajanja/projekcije u ovako pojednostavljenom katalogu, pa join
# uzima min(rows_per_block) svojih strana (siri spojeni slog, manje staje po bloku),
# a projection/sort ga preuzimaju nepromenjenog od ulaza.
#
# pipelining sa materijalizacijom po potrebi (kao u profesorovim resenjima rokova):
# - in_memory: rezultat staje u bafer (output_blocks <= M) i tu ostaje, operacija
#   iznad ga cita besplatno i nista se ne pise na disk
# - materialize: rezultat ne staje u memoriju pa se pise na disk (upis se placa ovde,
#   citanje u formuli operacije iznad)
# - ni jedno ni drugo (oba False uz cost_blocks=0): identitetska selekcija nad baznom
#   tabelom bez where uslova, tabela je vec na disku (nema upisa) i cita je operacija
#   iznad kroz svoju formulu; isto vazi za unutrasnju stranu indeks ugnjezdene
#   petlje (pristup kroz indeks, cena u ceni spajanja, in_memory=True)


@dataclass(frozen=True)
class SelectionNode:
    table: str
    algorithm: str
    index_names: "tuple[str, ...]"
    cost_blocks: float  # ceo broj osim kod hesa (pristup korpi je 1.2)
    output_rows: float
    output_blocks: int
    rows_per_block: int
    in_memory: bool = False
    materialize: bool = False


@dataclass(frozen=True)
class JoinNode:
    left: "SelectionNode | JoinNode"
    right: "SelectionNode | JoinNode"
    algorithm: str
    cost_blocks: int
    output_rows: float
    output_blocks: int
    rows_per_block: int
    tables: "tuple[str, ...]"
    in_memory: bool = False
    materialize: bool = False


@dataclass(frozen=True)
class ProjectionNode:
    # sort moze biti ulaz: order by kljuc van select liste sortira pre projekcije
    input: "SelectionNode | JoinNode | SortNode"
    attributes: "tuple[str, ...]"
    cost_blocks: int
    output_rows: float
    output_blocks: int
    rows_per_block: int
    in_memory: bool = False
    materialize: bool = False


@dataclass(frozen=True)
class SortNode:
    input: "SelectionNode | JoinNode | ProjectionNode"
    attribute: str
    cost_blocks: int
    output_rows: float
    output_blocks: int
    rows_per_block: int
    in_memory: bool = False
    materialize: bool = False


def children(
    node: "SelectionNode | JoinNode | ProjectionNode | SortNode",
) -> "tuple[SelectionNode | JoinNode | ProjectionNode | SortNode, ...]":
    if isinstance(node, SelectionNode):
        return ()
    if isinstance(node, JoinNode):
        return (node.left, node.right)
    if isinstance(node, (ProjectionNode, SortNode)):
        return (node.input,)
    raise TypeError(f"nepoznat tip cvora plana: {type(node)!r}")


def total_cost(node: "SelectionNode | JoinNode | ProjectionNode | SortNode") -> float:
    # cena ovog cvora + za svako dete njegova ukupna cena + upis njegovog rezultata
    # na disk samo ako ne staje u memoriju (child.materialize). koren stabla se
    # nikad ne pise na disk, izlaz ide pozivaocu (kao poslednji prolaz sortiranja)
    return node.cost_blocks + sum(
        total_cost(child) + (materialization_cost(child.output_blocks) if child.materialize else 0)
        for child in children(node)
    )
