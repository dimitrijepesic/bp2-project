"""
puna "siwiki" verzija roka 2024: Radi/Zaposleni, Petar.

    SELECT R.Procenat, R.IDPro, Z.Ime FROM Radi R, Zaposleni Z
    WHERE Z.IDZap = R.IDZap AND Z.Ime = 'Petar' AND R.IDPro = 5001
    ORDER BY R.Procenat

upit prepisan bez alijasa (tests/fixtures/upiti/zadatak_radi_petar_upit.sql).
katalog (tests/fixtures/zadatak_radi_petar.json) po postavci:
- Zaposleni 10000 redova / 40 po stranici -> 250 blokova; unclustered B+
  visine 2 (Ime); V(Ime)=2000
- Radi 20000 redova / 40 po stranici -> 500 blokova; unclustered B+ visine 2
  (IDZap); clustered B+ visine 2 po paru (IDPro, IDZap), kompozitni
- V(IDPro, Radi) = 50 izvedeno iz "tabela Projekat ima 50 redova" (strani kljuc)
- Procenat: domen ceo broj 1..100 => minValue=1, maxValue=100 (ovaj upit ga
  ne filtrira, ali je podatak iz postavke)
- M = 30 stranica
baza ima jos Odeljenje i Projekat, ali ih upit ne dira pa nisu u fixture-u
(jedino sto doprinose ceni je V(IDPro)=50). IDOde: V=50 je pretpostavka
(nebitno, atribut se ne pominje u upitu).

ocekivano po profesorovoj logici: Ime='Petar' = 7, IDPro=5001 preko
kompozitnog klaster indeksa (prefiks poklapanje po IDPro) = 12, spajanje u
memoriji = 0, projekcija+sortiranje = 0 => 19 I/O. nas plan isti po svakoj
operaciji i ukupno.
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, SelectionNode, SortNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/zadatak_radi_petar.json"
QUERY = "tests/fixtures/upiti/zadatak_radi_petar_upit.sql"


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


def test_zaposleni_selection_cost():
    # 10000/2000 = 5 redova za 'Petar'; a4 sekundarni B+: h(2) + ceil(5) = 7
    plan = _build()
    zaposleni = _find_selection(plan, "Zaposleni")
    assert zaposleni.algorithm == "A4_secondary_equality"
    assert zaposleni.index_names == ("idx_zaposleni_ime",)
    assert zaposleni.cost_blocks == 7
    assert zaposleni.output_rows == 5.0
    assert zaposleni.output_blocks == 1


def test_radi_selection_uses_composite_index_by_prefix():
    # 20000/50 = 400 redova za IDPro=5001; kompozitni klaster B+ (IDPro, IDZap)
    # se poklapa prefiksom (samo IDPro, jer je IDZap iz WHERE join predikat, ne
    # selekcija): a3 klaster jednakost = h(2) + ceil(400/40) = 12.
    # unclustered B+ po IDZap ne pomaze za ovu selekciju
    plan = _build()
    radi = _find_selection(plan, "Radi")
    assert radi.algorithm == "A3_clustering_equality_nonkey"
    assert radi.index_names == ("idx_radi_idpro_idzap",)
    assert radi.cost_blocks == 12
    assert radi.output_rows == 400.0
    assert radi.output_blocks == 10


def test_join_runs_in_memory_for_free():
    # oba filtrirana ulaza (10 + 1 blokova) staju u M=30 i ostaju u baferu posle
    # selekcija => spajanje u memoriji, 0 blok transfera. indeksna ugnjezdena
    # petlja preko idx_radi_idzap ne moze bolje od 0
    plan = _build()
    join = _find_join(plan)
    assert join.algorithm == "in_memory_join"
    assert join.cost_blocks == 0


def test_total_cost():
    # profesorova logika: 7 + 12 + 0 (spajanje u memoriji) + 0 (proj/sort) = 19
    plan = _build()
    assert isinstance(plan, SortNode)  # ORDER BY R.Procenat
    assert total_cost(plan) == 19


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Zaposleni)" in text
    assert "Selekcija(Radi)" in text
    assert "idx_radi_idpro_idzap" in text
    assert "Spajanje(Radi, Zaposleni)" in text
    assert "UKUPNA CENA PLANA: 19 blok transfera" in text
