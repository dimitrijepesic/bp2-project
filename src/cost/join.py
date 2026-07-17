import math

from src.catalog.model import Attribute, Table
from src.cost.index_matcher import SimpleCondition, match_index
from src.cost.selection import index_equality_probe_cost
from src.cost.selectivity import DEFAULT_RANGE_SELECTIVITY
from src.cost.sort import sort_cost
from src.semantic.analyzer import ResolvedAttr

ALGO_NESTED_LOOP = "nested_loop"
ALGO_BLOCK_NESTED_LOOP = "block_nested_loop"
ALGO_INDEX_NESTED_LOOP = "index_nested_loop"
ALGO_SORT_MERGE = "sort_merge"
ALGO_HASH_JOIN = "hash_join"
# oba ulaza vec u baferu (pipelining), spajanje ne kosta nijedan blok transfer
ALGO_IN_MEMORY_JOIN = "in_memory_join"

# *_in_memory: ulaz je rezultat prethodne operacije koji je ostao u baferu
# (pipelining), pa se njegovo citanje ne placa, odgovarajuci clan formule je 0

# nejednakosni ("<","<=",">",">=") join predikat izmedju dve tabele: nemam
# statistiku o korelaciji (isto obrazlozenje kao attr op attr unutar jedne
# tabele u selection.py), pa ide ista podrazumevana selektivnost 0.5;
# minValue/maxValue se ovde ne koriste
INEQUALITY_JOIN_SELECTIVITY = DEFAULT_RANGE_SELECTIVITY


def nested_loop_cost(
    outer_rows: float,
    outer_blocks: int,
    inner_blocks: int,
    outer_in_memory: bool = False,
    inner_in_memory: bool = False,
) -> int:
    # za svaki slog spoljasnje relacije citamo celu unutrasnju; spoljasnju citamo jednom
    outer_io = 0 if outer_in_memory else outer_blocks
    inner_io = 0 if inner_in_memory else inner_blocks
    return math.ceil(outer_rows * inner_io + outer_io)


def block_nested_loop_cost(
    outer_blocks: int,
    inner_blocks: int,
    buffer_blocks: int,
    outer_in_memory: bool = False,
    inner_in_memory: bool = False,
) -> int:
    # (M-2) blokova spoljasnje odjednom u memoriji, unutrasnju skeniramo po grupi;
    # spoljasnja vec u memoriji -> jedna grupa i nema njenog citanja
    inner_io = 0 if inner_in_memory else inner_blocks
    if outer_in_memory:
        return inner_io
    outer_chunks = math.ceil(outer_blocks / (buffer_blocks - 2))
    return outer_chunks * inner_io + outer_blocks


def index_nested_loop_cost(
    outer_rows: float,
    outer_blocks: int,
    inner_table: Table,
    join_attribute: Attribute,
    outer_in_memory: bool = False,
) -> "tuple[int, str] | None":
    # za svaki slog spoljasnje, jedna jednakosna pretraga indeksa nad unutrasnjom
    # baznom tabelom; vraca (cena, ime indeksa) ili None ako nema upotrebljivog indeksa
    best_lookup: "tuple[float, str] | None" = None
    for index in inner_table.indexes:
        if match_index(index, [SimpleCondition(join_attribute.name, "=")]) is None:
            continue
        lookup = index_equality_probe_cost(inner_table, index, join_attribute)
        if best_lookup is None or lookup < best_lookup[0]:
            best_lookup = (lookup, index.name)
    if best_lookup is None:
        return None
    outer_io = 0 if outer_in_memory else outer_blocks
    # round pre ceil (kao u sort.py): hash proba 1.2 unosi binarnu gresku
    # (20*11.2 = 224.0000...3) koja bi inace pogresno podigla ceil za 1
    return math.ceil(round(outer_rows * best_lookup[0] + outer_io, 9)), best_lookup[1]


def sort_merge_join_cost(
    r_blocks: int,
    s_blocks: int,
    buffer_blocks: int,
    r_sorted: bool = False,
    s_sorted: bool = False,
    r_in_memory: bool = False,
    s_in_memory: bool = False,
) -> int:
    # spajanje: po jedno citanje svake vec sortirane relacije; sortiranje samo ako je
    # potrebno; strana koja je vec u memoriji se sortira u memoriji i ne cita sa diska
    cost = 0
    for blocks, is_sorted, in_memory in ((r_blocks, r_sorted, r_in_memory), (s_blocks, s_sorted, s_in_memory)):
        if in_memory:
            continue
        cost += blocks
        if not is_sorted:
            cost += sort_cost(blocks, buffer_blocks)
    return cost


def hash_join_cost(
    r_blocks: int, s_blocks: int, r_in_memory: bool = False, s_in_memory: bool = False
) -> int:
    # osnovno (ne-rekurzivno) hes spajanje, particije staju u memoriju; ako je jedna
    # strana vec cela u memoriji, hes se gradi nad njom pa je cena samo jedan prolaz
    # kroz drugu stranu (particionisanje nije ni potrebno)
    if r_in_memory and s_in_memory:
        return 0
    if r_in_memory:
        return s_blocks
    if s_in_memory:
        return r_blocks
    return 3 * (r_blocks + s_blocks)


def estimate_join_rows(r_rows: float, s_rows: float, left: Attribute, right: Attribute) -> float:
    # procena velicine jednakosnog spajanja (optimizacija upita, slajd 27):
    # min(n_r*n_s/V(A,r), n_r*n_s/V(A,s)). posebna grana za unique atribut nije
    # potrebna: V(A,r) za unique atribut je broj redova cele tabele iz kataloga,
    # pa r_rows/V(left) postane udeo redova koji su preziveli where filter.
    # kad r nije filtriran izraz se svede tacno na s_rows (slajdov poseban slucaj
    # "unique => rezultat = s"), a kad jeste, srazmerno smanji procenu umesto
    # da je preceni
    return min(r_rows * s_rows / left.distinct_values, r_rows * s_rows / right.distinct_values)


def estimate_join_rows_conjunction(
    r_rows: float, s_rows: float, attr_pairs: list[tuple[ResolvedAttr, str, ResolvedAttr]]
) -> float:
    # vise nezavisnih join predikata izmedju istog para strana, jednakosnih i/ili
    # nejednakosnih (npr. A.x=B.x AND A.y<B.y); ista pretpostavka nezavisnosti kao
    # conjunction_selectivity. jednakosni predikat deli sa max(V(levi), V(desni)),
    # nejednakosni mnozi fiksnim faktorom 0.5 (nema statistike o korelaciji).
    # lista je vec deduplikovana u _oriented_predicate_pairs, ovde se duplikati
    # ne proveravaju.
    equality_denominator = 1
    inequality_factor = 1.0
    for left, op, right in attr_pairs:
        if op == "=":
            equality_denominator *= max(left.attribute.distinct_values, right.attribute.distinct_values)
        else:
            inequality_factor *= INEQUALITY_JOIN_SELECTIVITY
    return r_rows * s_rows / equality_denominator * inequality_factor
