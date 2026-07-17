"""
rok 2024, zadatak Radi/Zaposleni (resen na casu):

    SELECT R.Procenat, R.IDPro, Z.Ime FROM Radi R, Zaposleni Z
    WHERE Z.IDZap = R.IDZap AND Z.Ime = 'Nenad' AND R.IDPro = 5001
    ORDER BY R.Procenat

upit prepisan bez alijasa (tests/fixtures/upiti/zadatak_radi_2024_upit.sql).
katalog (tests/fixtures/zadatak_radi_2024.json) po postavci: Zaposleni
10000/250blk + unclustered B+ visine 2 (Ime), V(Ime)=2000; Radi 20000/500blk +
unclustered B+ (IDZap) + clustered B+ (IDPro), V(IDPro)=50 ("znamo da postoji
50 projekata"), M=30. visina klaster B+ nad IDPro = 2 (resenje racuna
"2 pristupa indeksu").

resenje sa roka: Ime='Nenad' = 7, IDPro=5001 = 12, spajanje u memoriji = 0
=> ukupno 19 I/O. nas plan daje isto po svakoj operaciji i ukupno
(sto stane u M ostaje u memoriji, pa su spajanje/projekcija/sortiranje 0).
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, SelectionNode, SortNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/zadatak_radi_2024.json"
QUERY = "tests/fixtures/upiti/zadatak_radi_2024_upit.sql"


def _build():
    catalog = load_catalog(FIXTURE)
    sql = open(QUERY, encoding="utf-8").read()
    rq = analyze(parse(sql), catalog)
    return build_plan(rq, catalog)


def _find_selection(node, table: str) -> SelectionNode:
    if isinstance(node, SelectionNode):
        if node.table == table:
            return node
        raise AssertionError(f"selekcija za {table!r} nije ovde")
    if isinstance(node, JoinNode):
        try:
            return _find_selection(node.left, table)
        except AssertionError:
            return _find_selection(node.right, table)
    return _find_selection(node.input, table)


def _find_join(node) -> JoinNode:
    if isinstance(node, JoinNode):
        return node
    return _find_join(node.input)


def test_zaposleni_selection_matches_exam_exactly():
    # rok: 10000/2000 -> 5 redova za Nenada; 2 pristupa indeksu + 5 blokova = 7
    # nas a4 (sekundarni B+, jednakost): h(2) + ceil(5) = 7, isto
    plan = _build()
    zaposleni = _find_selection(plan, "Zaposleni")
    assert zaposleni.algorithm == "A4_secondary_equality"
    assert zaposleni.index_names == ("idx_zaposleni_ime",)
    assert zaposleni.cost_blocks == 7
    assert zaposleni.output_rows == 5.0


def test_radi_selection_matches_exam_exactly():
    # rok: 20000/50 -> 400 redova po projektu; 400/40 -> 10 blokova;
    # 10 blokova + 2 pristupa indeksu = 12. nas a3 (klaster B+, jednakost,
    # ne-kljucni atribut): h(2) + ceil(400/40) = 12, isto
    plan = _build()
    radi = _find_selection(plan, "Radi")
    assert radi.algorithm == "A3_clustering_equality_nonkey"
    assert radi.index_names == ("idx_radi_idpro",)
    assert radi.cost_blocks == 12
    assert radi.output_rows == 400.0
    assert radi.output_blocks == 10


def test_join_runs_in_memory_for_free():
    # rok: spajanje se obradjuje u memoriji, cena 0. oba filtrirana ulaza
    # (1 + 10 blokova) ostaju u baferu posle selekcija, isto kod nas
    plan = _build()
    join = _find_join(plan)
    assert join.algorithm == "in_memory_join"
    assert join.cost_blocks == 0


def test_total_cost_matches_exam_exactly():
    # rok: 7 + 12 + 0 (spajanje u memoriji) + 0 (projekcija i sortiranje) = 19
    plan = _build()
    assert isinstance(plan, SortNode)  # ORDER BY R.Procenat
    assert total_cost(plan) == 19


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Zaposleni)" in text
    assert "Selekcija(Radi)" in text
    assert "Spajanje(Radi, Zaposleni)" in text
    assert "UKUPNA CENA PLANA: 19 blok transfera" in text
