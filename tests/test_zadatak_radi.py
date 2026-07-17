"""
rok ~2023, zadatak Radi/Zaposleni (resen na casu):

    SELECT R.Procenat, R.IDPro, Z.Ime FROM Radi R, Zaposleni Z
    WHERE Z.IDZap = R.IDZap AND R.Procenat > 99 AND Z.Ime = 'Ivan'
    ORDER BY R.Procenat

upit prepisan bez alijasa (tests/fixtures/upiti/zadatak_radi_upit.sql).
katalog po postavci (tests/fixtures/zadatak_radi_zaposleni.json): Zaposleni
10000/250blk + unclustered Hash(Ime), Radi 20000/500blk + unclustered
Hash(IDZap) + clustered B+ visine 2 (Procenat), V(Ime,Zaposleni)=1000,
V(Procenat,Radi)=100, M=30. Procenat ima minValue=0/maxValue=100 (bez toga bi
opseg dobio default 1/2); profesorovo "20000 * 1/100 -> 200 redova" je bas
interpolacija (max-v)/(max-min) = (100-99)/(100-0) = 1/100.

resenje sa roka: Ime='Ivan' = 11, Procenat>99 = 7, spajanje u memoriji = 0,
projekcija+sortiranje = 0 => ukupno 18 I/O. nas plan isti po operacijama,
samo hes korpu racunamo 1.2 umesto 1, pa ukupno ispadne 18.2.
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, SelectionNode, SortNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/zadatak_radi_zaposleni.json"
QUERY = "tests/fixtures/upiti/zadatak_radi_upit.sql"


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


def test_zaposleni_selection_matches_exam_modulo_hash_probe():
    # rok: 10000/1000 -> 10 redova za Ivana + 1 pristup hash indeksu = 11
    # nas a4 (sekundarni indeks, jednakost): 1.2 (HASH_ACCESS_COST, prosecna
    # korpa) + ceil(10) = 11.2; rok racuna korpu kao 1 pa dobija 11
    plan = _build()
    zaposleni = _find_selection(plan, "Zaposleni")
    assert zaposleni.algorithm == "A4_secondary_equality"
    assert zaposleni.index_names == ("idx_zaposleni_ime_hash",)
    assert round(zaposleni.cost_blocks, 6) == 11.2
    assert zaposleni.output_rows == 10.0


def test_radi_selection_matches_exam_exactly():
    # rok: 20000 * 1/100 -> 200 redova, 200/fr -> 5 blokova; 1 pristup indeksu
    # + 1 listu = 2; ukupno 7. nas a5 (klaster B+, poredjenje) sa interpolacijom
    # (100-99)/(100-0)=1/100: h(2) + ceil(200/40) = 7, isto
    plan = _build()
    radi = _find_selection(plan, "Radi")
    assert radi.algorithm == "A5_clustering_comparison"
    assert radi.index_names == ("idx_radi_procenat",)
    assert radi.cost_blocks == 7
    assert radi.output_rows == 200.0
    assert radi.output_blocks == 5


def test_join_runs_in_memory_for_free():
    # rok: sve staje u memoriju (M=30), cena spajanja = 0. oba filtrirana
    # ulaza (1 + 5 blokova) ostaju u baferu posle selekcija, pa je spajanje
    # besplatno, isto kao rok
    plan = _build()
    join = _find_join(plan)
    assert join.algorithm == "in_memory_join"
    assert join.cost_blocks == 0


def test_total_cost_matches_exam_modulo_hash_probe():
    # rok: 11 + 7 + 0 (spajanje u memoriji) + 0 (projekcija i sortiranje) = 18;
    # mi 11.2 + 7 = 18.2, jedina razlika je hes korpa 1.2 umesto rokove 1
    plan = _build()
    assert isinstance(plan, SortNode)  # ORDER BY R.Procenat
    assert round(total_cost(plan), 6) == 18.2


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Zaposleni)" in text
    assert "Selekcija(Radi)" in text
    assert "Spajanje(Radi, Zaposleni)" in text
    assert "UKUPNA CENA PLANA: 18.2 blok transfera" in text
