"""
regresija za prelivni slucaj pipelininga: oba ulaza spajanja pojedinacno staju
u bafer (in_memory=True), ali zbir blokova prelazi M, pa jedna strana mora na disk.

ocekivano (_best_join_node):
- zbir staje u M -> ALGO_IN_MEMORY_JOIN postoji, cena 0, deca netaknuta;
- zbir ne staje -> nema in-memory spajanja; za svaki algoritam dva kandidata
  (materijalizuj levu ili desnu stranu) sa kopijama dece (dataclasses.replace)
  i zastavicama formule koje odgovaraju kopijama;
- poredi se kompletna cena stabla (sa upisom kopije), pa pobedjuje
  materijalizacija manje strane;
- originalni memoizovani planovi se ne mutiraju;
- inlj mehanizam (apsorpcija unutrasnje strane) ostaje netaknut.
"""

from src.catalog.loader import load_catalog
from src.cost.join import (
    ALGO_BLOCK_NESTED_LOOP,
    ALGO_HASH_JOIN,
    ALGO_IN_MEMORY_JOIN,
    ALGO_INDEX_NESTED_LOOP,
    ALGO_NESTED_LOOP,
    ALGO_SORT_MERGE,
    block_nested_loop_cost,
    nested_loop_cost,
)
from src.cost.selection import ALGO_A1_FULL_SCAN
from src.optimizer.enumerator import _best_join_node
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, SelectionNode, total_cost
from src.semantic.analyzer import JoinPredicate, ResolvedAttr, analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/primer_ulaza.json"
M = 10


def _catalog():
    return load_catalog(FIXTURE)


def _resident(table: str, blocks: int, rows_per_block: int = 10) -> SelectionNode:
    # sinteticki rezultat selekcije koji je ostao u baferu (pipelining):
    # cena proizvodnje 0 da bi razlike medju kandidatima bile cist join racun
    return SelectionNode(
        table=table,
        algorithm=ALGO_A1_FULL_SCAN,
        index_names=(),
        cost_blocks=0,
        output_rows=float(blocks * rows_per_block),
        output_blocks=blocks,
        rows_per_block=rows_per_block,
        in_memory=True,
        materialize=False,
    )


def _formula_costs_for_children(join: JoinNode, buffer_blocks: int) -> set:
    # sve cene koje nl/bnl formule daju za zastavice kakve nose deca plana
    # (oba redosleda spoljasnja/unutrasnja): cena cvora mora biti jedna od njih,
    # tj. formula ne dobija rezidentnost koja nije u deci
    l, r = join.left, join.right
    return {
        nested_loop_cost(l.output_rows, l.output_blocks, r.output_blocks, l.in_memory, r.in_memory),
        nested_loop_cost(r.output_rows, r.output_blocks, l.output_blocks, r.in_memory, l.in_memory),
        block_nested_loop_cost(l.output_blocks, r.output_blocks, buffer_blocks, l.in_memory, r.in_memory),
        block_nested_loop_cost(r.output_blocks, l.output_blocks, buffer_blocks, r.in_memory, l.in_memory),
    }


# ---------------------------------------------------------------------------
# test 1: 8+8 > M=10, nema in-memory spajanja, tacno jedna strana na disk
# ---------------------------------------------------------------------------


def test_overflow_8_8_materializes_exactly_one_side():
    p1 = _resident("Student", 8)
    p2 = _resident("Ispit", 8)
    join = _best_join_node(_catalog(), M, p1, ("Student",), p2, ("Ispit",), [])

    assert join.algorithm != ALGO_IN_MEMORY_JOIN

    materialized = [c for c in (join.left, join.right) if c.materialize]
    assert len(materialized) == 1
    forced = materialized[0]
    kept = join.right if forced is join.left else join.left
    assert forced.in_memory is False and forced.materialize is True
    assert kept.in_memory is True and kept.materialize is False
    # rezidentna strana je originalan memoizovan cvor, materijalizovana je kopija
    assert kept is p1 or kept is p2
    assert forced is not p1 and forced is not p2
    # originali nisu mutirani
    assert p1.in_memory is True and p1.materialize is False
    assert p2.in_memory is True and p2.materialize is False

    # koherentnost: cena spajanja odgovara zastavicama koje nose deca plana
    assert join.cost_blocks in _formula_costs_for_children(join, M)
    # jedan prolaz kroz materijalizovanu stranu (8) + njen upis (8) = 16 ukupno
    assert join.cost_blocks == 8
    assert total_cost(join) == 16


# ---------------------------------------------------------------------------
# test 2: 8+6 > M=10, pobedjuje kandidat koji materijalizuje manju stranu
# ---------------------------------------------------------------------------


def test_overflow_8_6_materializes_smaller_side():
    p1 = _resident("Student", 8)
    p2 = _resident("Ispit", 6)
    join = _best_join_node(_catalog(), M, p1, ("Student",), p2, ("Ispit",), [])

    assert join.algorithm != ALGO_IN_MEMORY_JOIN
    materialized = [c for c in (join.left, join.right) if c.materialize]
    assert len(materialized) == 1
    # kandidat "materijalizuj 6" (upis 6 + citanje 6 = 12) je jeftiniji od
    # kandidata "materijalizuj 8" (upis 8 + citanje 8 = 16)
    assert materialized[0].output_blocks == 6
    assert join.cost_blocks == 6
    assert total_cost(join) == 12
    # ne sme se pojaviti nekoherentna cena 14 iz vestackog (False, False) bnl
    # poziva nad originalnim (nominalno rezidentnim) cvorovima:
    # ceil(8/(10-2))*6 + 8 = 14
    assert join.cost_blocks in _formula_costs_for_children(join, M)


def test_overflow_8_6_with_equality_predicate_keeps_gates_and_coherence():
    # jednakosni predikat -> sort-merge/hash kandidati postoje i u prelivnom
    # slucaju, ali samo preko koherentnih konfiguracija dece; pobednik i dalje
    # placa upis+citanje manje strane (hash sa jednom rezidentnom stranom = 6
    # izjednacen sa nl/bnl = 6, ukupno 12)
    catalog = _catalog()
    p1 = _resident("Student", 8)
    p2 = _resident("Ispit", 6)
    pred = JoinPredicate(
        op="=",
        left=ResolvedAttr("Student", catalog.attribute("Student", "indeks")),
        right=ResolvedAttr("Ispit", catalog.attribute("Ispit", "studentIndeks")),
    )
    join = _best_join_node(catalog, M, p1, ("Student",), p2, ("Ispit",), [pred])

    assert join.algorithm != ALGO_IN_MEMORY_JOIN
    materialized = [c for c in (join.left, join.right) if c.materialize]
    assert len(materialized) == 1
    assert materialized[0].output_blocks == 6
    assert join.cost_blocks == 6
    assert total_cost(join) == 12


def test_overflow_inequality_join_gets_no_hash_or_sort_merge():
    # nejednakosni connecting predikat ne sme ni u prelivnom slucaju da otvori
    # hash/sort-merge kandidate (postojeci gate: samo jednakosno spajanje)
    catalog = _catalog()
    p1 = _resident("Student", 8)
    p2 = _resident("Ispit", 8)
    pred = JoinPredicate(
        op="<",
        left=ResolvedAttr("Student", catalog.attribute("Student", "godinaUpisa")),
        right=ResolvedAttr("Ispit", catalog.attribute("Ispit", "ocena")),
    )
    join = _best_join_node(catalog, M, p1, ("Student",), p2, ("Ispit",), [pred])

    assert join.algorithm in (ALGO_NESTED_LOOP, ALGO_BLOCK_NESTED_LOOP)
    assert join.algorithm not in (ALGO_HASH_JOIN, ALGO_SORT_MERGE, ALGO_IN_MEMORY_JOIN)


def test_split_fit_total_cost_decomposition_identity():
    # dekompozicija ukupne cene split-fit pobednika: cena cvora + cene dece
    # + upis (output_blocks) svakog deteta sa materialize=True; pada ako se
    # upis kopije duplo uracuna u JoinNode.cost_blocks ili se ne naplati uopste
    p1 = _resident("Student", 8)
    p2 = _resident("Ispit", 6)
    join = _best_join_node(_catalog(), M, p1, ("Student",), p2, ("Ispit",), [])

    expected = (
        total_cost(join.left)
        + total_cost(join.right)
        + join.cost_blocks
        + sum(c.output_blocks for c in (join.left, join.right) if c.materialize)
    )
    assert total_cost(join) == expected


# ---------------------------------------------------------------------------
# test 3: 4+4 <= M=10, zbir staje, normalan in-memory join
# ---------------------------------------------------------------------------


def test_both_fit_together_in_memory_join_unchanged():
    p1 = _resident("Student", 4)
    p2 = _resident("Ispit", 4)
    join = _best_join_node(_catalog(), M, p1, ("Student",), p2, ("Ispit",), [])

    assert join.algorithm == ALGO_IN_MEMORY_JOIN
    assert join.cost_blocks == 0
    assert total_cost(join) == 0
    # deca su originalni cvorovi, nista nije prinudno materijalizovano
    assert join.left is p1 and join.right is p2
    assert join.left.materialize is False and join.right.materialize is False


# ---------------------------------------------------------------------------
# test 4: dete je JoinNode, mehanizam ne zavisi od tipa cvora
# ---------------------------------------------------------------------------


def test_overflow_with_join_node_child_works_identically():
    inner_join = JoinNode(
        left=_resident("Predmet", 2),
        right=_resident("Stipendija", 2),
        algorithm=ALGO_IN_MEMORY_JOIN,
        cost_blocks=0,
        output_rows=80.0,
        output_blocks=8,
        rows_per_block=10,
        tables=("Predmet", "Stipendija"),
        in_memory=True,
        materialize=False,
    )
    other = _resident("Student", 8)
    join = _best_join_node(
        _catalog(), M, inner_join, ("Predmet", "Stipendija"), other, ("Student",), []
    )

    assert join.algorithm != ALGO_IN_MEMORY_JOIN
    materialized = [c for c in (join.left, join.right) if c.materialize]
    assert len(materialized) == 1
    forced = materialized[0]
    # kopija zadrzava tip originala (JoinNode ostaje JoinNode) i original se ne mutira
    if forced is join.left:
        assert isinstance(forced, JoinNode) and forced is not inner_join
    assert inner_join.in_memory is True and inner_join.materialize is False
    assert other.in_memory is True and other.materialize is False
    # simetrican 8+8 slucaj: jedan upis + jedno citanje iste 8-blokovne strane
    assert join.cost_blocks == 8
    assert total_cost(join) == 16


# ---------------------------------------------------------------------------
# test 5: inlj profesorski primer (slajd 32-35), algoritam i cena kao kod profesora
# ---------------------------------------------------------------------------


def test_slide_inlj_example_unchanged():
    catalog = load_catalog("tests/fixtures/slajd_profesor_predaje.json")
    sql = open("tests/fixtures/upiti/slajd_profesor_upit.sql", encoding="utf-8").read()
    plan = build_plan(analyze(parse(sql), catalog), catalog)

    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.algorithm == ALGO_INDEX_NESTED_LOOP
    assert join.cost_blocks == 224
    assert total_cost(plan) == 230
