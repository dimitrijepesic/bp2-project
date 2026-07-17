from dataclasses import dataclass

from src.catalog.model import Attribute, Index, Table
from src.cost.index_matcher import IndexMatch, SimpleCondition, match_index
from src.cost.misc import stable_ceil
from src.cost.selectivity import (
    conjunction_selectivity,
    equality_selectivity,
    range_selectivity,
)
from src.semantic.analyzer import SelectionPredicate
from src.sqlparser.query_ast import Literal

# oznake prate numeraciju algoritama selekcije sa predavanja "obrada upita" (slajd 5-9).
# a7 nema svoju formulu, dobija se sam od sebe u select_best: kad indeks pokrije samo
# jedan uslov, ostali uslovi se filtriraju besplatno u memoriji (nema dodatnih
# blok transfera), a to je bas definicija a7.
ALGO_A1_FULL_SCAN = "A1_full_scan"
ALGO_A2_CLUSTER_EQ_KEY = "A2_clustering_equality_key"
ALGO_A3_CLUSTER_EQ_NONKEY = "A3_clustering_equality_nonkey"
ALGO_A4_SECONDARY_EQ = "A4_secondary_equality"
ALGO_A5_CLUSTER_CMP = "A5_clustering_comparison"
ALGO_A6_SECONDARY_CMP = "A6_secondary_comparison"
ALGO_A8_COMPOSITE = "A8_composite_index"
ALGO_A9_INTERSECTION = "A9_conjunctive_intersection"
# bazna tabela bez where uslova: selekcija je identitet (cena 0), tabela je vec
# na disku i cita je direktno operacija iznad (spajanje/projekcija)
ALGO_NO_SELECTION = "no_selection"
# unutrasnja strana indeks ugnjezdene petlje: tabeli se pristupa kroz indeks
# spajanja (ta cena je u ceni spajanja), a njeni where uslovi se filtriraju u
# memoriji nad dovucenim redovima (kao a7), pa cvor nosi cenu 0
ALGO_JOIN_INDEX_ACCESS = "join_index_access"


# prosecan broj i/o za jedan pristup hes indeksu, konvencija sa slajda
# "odredjivanje cene upita - primer" (clan "1,2*20"): prosek ukljucuje preliv korpi.
# zbog ovoga cena preko hesa nije ceo broj (npr. 1.2+2=3.2), pa na tabeli od
# 2 bloka pun scan (2) pobedjuje hes (2.2). b+ koristi visinu stabla iz kataloga.
HASH_ACCESS_COST = 1.2


@dataclass(frozen=True)
class SelectionCost:
    algorithm: str
    index_names: "tuple[str, ...]"  # prazno za a1; jedno ime za a2-a6/a8; vise za a9
    cost_blocks: float  # ceo broj osim kod hesa (+0.2 po pristupu korpi)
    output_rows: float  # procena broja redova rezultata, treba kasnije za join/sort


def predicate_selectivity(pred: SelectionPredicate) -> float:
    if isinstance(pred.right, Literal):
        if pred.op == "=":
            return equality_selectivity(pred.attribute)
        return range_selectivity(pred.attribute, pred.op, pred.right.value)
    # poredjenje dva atributa iste tabele, nemam statistiku o korelaciji pa ide default
    return range_selectivity()


def where_selectivity(conditions: "list[SelectionPredicate]") -> float:
    return conjunction_selectivity([predicate_selectivity(c) for c in conditions])


def _simple_conditions(predicates: "list[SelectionPredicate]") -> "list[SimpleCondition]":
    # samo attr op literal moze kroz indeks; attr op attr (ista tabela) ne moze
    return [
        SimpleCondition(p.attribute.name, p.op, p.right.value)
        for p in predicates
        if isinstance(p.right, Literal)
    ]


def _index_match_selectivity(table: Table, match: IndexMatch) -> float:
    selectivities = []
    last = len(match.matched) - 1
    for i, cond in enumerate(match.matched):
        if match.last_is_range and i == last:
            selectivities.append(range_selectivity(table.attribute(cond.attribute), cond.op, cond.value))
        else:
            selectivities.append(equality_selectivity(table.attribute(cond.attribute)))
    return conjunction_selectivity(selectivities)


def _lookup_cost(table: Table, index: Index, matched_rows: float) -> float:
    # hes nema visinu stabla, racuna se prosecan pristup korpi (1.2)
    base = index.tree_height if index.kind == "btree" else HASH_ACCESS_COST
    if index.clustered:
        retrieval = stable_ceil(matched_rows / table.rows_per_block)
    else:
        retrieval = stable_ceil(matched_rows)  # neklaster: po jedan blok po slogu (najgori slucaj)
    return base + retrieval


def _index_pointer_cost(index: Index) -> float:
    # za a9: cena samo dobavljanja pokazivaca iz listova indeksa, bez citanja podataka
    # (kad se kombinuje vise indeksa, podaci se citaju tek posle preseka pokazivaca)
    return index.tree_height if index.kind == "btree" else HASH_ACCESS_COST


def _index_access_cost(table: Table, index: Index, match: IndexMatch) -> float:
    matched_rows = table.n_rows * _index_match_selectivity(table, match)
    return _lookup_cost(table, index, matched_rows)


def index_equality_probe_cost(table: Table, index: Index, attribute: Attribute) -> float:
    # cena jedne jednakosne pretrage indeksa (koristi je index nested loop join)
    matched_rows = table.n_rows * equality_selectivity(attribute)
    return _lookup_cost(table, index, matched_rows)


def _single_index_algorithm(table: Table, index: Index, match: IndexMatch) -> str:
    if match.last_is_range:
        return ALGO_A5_CLUSTER_CMP if index.clustered else ALGO_A6_SECONDARY_CMP
    if len(match.matched) >= 2:
        return ALGO_A8_COMPOSITE
    attr = table.attribute(match.matched[0].attribute)
    if index.clustered:
        return ALGO_A2_CLUSTER_EQ_KEY if attr.unique else ALGO_A3_CLUSTER_EQ_NONKEY
    return ALGO_A4_SECONDARY_EQ


def _full_scan_cost(table: Table) -> int:
    # a1: linijsko skeniranje, uvek svih n_blocks. prosecni slucaj n/2 za jednakost
    # nad unique atributom (slajd 5) ne koristim jer ga ni profesor ne koristi u
    # resenjima rokova
    return table.n_blocks


def _intersection_candidate(
    table: Table, predicates: "list[SelectionPredicate]", matches: "list[tuple[Index, IndexMatch]]"
) -> "SelectionCost | None":
    # a9: presek pokazivaca iz vise nezavisnih indeksa, svaki pokriva deo konjunkcije
    if len(matches) < 2:
        return None
    covered_attrs = {cond.attribute for _, m in matches for cond in m.matched}
    covered = [p for p in predicates if isinstance(p.right, Literal) and p.attribute.name in covered_attrs]
    intersected_rows = table.n_rows * conjunction_selectivity([predicate_selectivity(p) for p in covered])
    pointer_cost = sum(_index_pointer_cost(index) for index, _ in matches)
    cost = pointer_cost + stable_ceil(intersected_rows)
    names = tuple(index.name for index, _ in matches)
    return SelectionCost(ALGO_A9_INTERSECTION, names, cost, 0.0)


def select_best(table: Table, conditions: "list[SelectionPredicate]") -> SelectionCost:
    predicates = list(conditions)
    simple_conditions = _simple_conditions(predicates)

    best = SelectionCost(ALGO_A1_FULL_SCAN, (), _full_scan_cost(table), 0.0)

    matches: "list[tuple[Index, IndexMatch]]" = []
    for index in table.indexes:
        match = match_index(index, simple_conditions)
        if match is None:
            continue
        matches.append((index, match))
        candidate_cost = _index_access_cost(table, index, match)
        # kod jednake cene indeks pobedjuje pun scan (profesor u rokovima uvek bira
        # indeks kad ga ima), ali ne istiskuje ranije nadjen jednako dobar indeks
        beats_best = candidate_cost < best.cost_blocks or (
            candidate_cost == best.cost_blocks and best.algorithm == ALGO_A1_FULL_SCAN
        )
        if beats_best:
            best = SelectionCost(_single_index_algorithm(table, index, match), (index.name,), candidate_cost, 0.0)

    intersection = _intersection_candidate(table, predicates, matches)
    if intersection is not None and intersection.cost_blocks < best.cost_blocks:
        best = intersection

    output_rows = table.n_rows * where_selectivity(conditions)
    return SelectionCost(best.algorithm, best.index_names, best.cost_blocks, output_rows)
