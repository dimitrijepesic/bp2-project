"""
rok 2025 septembar: Osoba/Koristi/Automobil (resen na casu).

    SELECT Ime FROM Osoba o, Koristi k, Automobil a
    WHERE o.IdOso = k.IdOso AND k.IdAut = a.IdAut AND Tablica = 'BG-234-JR'

upit prepisan bez alijasa, sa kvalifikovanim imenima:
tests/fixtures/upiti/zadatak_osoba_upit.sql. katalog
(tests/fixtures/zadatak_osoba_automobil.json) po postavci: Osoba 10000/250blk
+ unclustered B+ visine 3 (Ime), V(Ime,Osoba)=2000; Koristi 20000/500blk +
clustered B+ po (IdOso, IdAut) sa treeHeight=2: postavka kaze "nivoa 3", ali
profesor racuna "1 pristup indeksu + 1 pristup listu" = 2 I/O uz pretpostavku
da su unutrasnji cvorovi u memoriji, pa fixture nosi efektivnu visinu 2;
Automobil 50/2blk + unclustered Hash(Tablica), unique; M=30.

resenje sa roka: selekcija Tablica = 2, spajanje Automobil-Koristi = 12
(indeks ugnjezdena petlja preko kompozitnog (IdOso, IdAut) po IdAut,
ne-prefiks poklapanje jednog atributa, kao kod profesora), spajanje sa
Osoba = 250 (jedno citanje cele Osobe, medjurezultat od 10 blokova u memoriji)
=> ukupno 264. nas plan ima iste cene po operaciji, isti redosled spajanja i
isto ukupno; jedina razlika je algoritam selekcije Automobila, pun scan (2)
umesto rokovog hesa (2), jer hes sa prosecnom korpom 1.2 (HASH_ACCESS_COST)
kosta 2.2.
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, ProjectionNode, SelectionNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/zadatak_osoba_automobil.json"
QUERY = "tests/fixtures/upiti/zadatak_osoba_upit.sql"


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


def test_automobil_selection_matches_exam_cost_via_full_scan():
    # rok: 1 pristup hashu + 1 blok sa podacima = 2 I/O. sa prosecnim hash
    # pristupom 1.2 (HASH_ACCESS_COST) hes kosta 2.2, pa pun scan tabele od
    # 2 bloka pobedjuje; cena (2) i procena izlaza (1 red) iste kao rok,
    # samo je algoritam drugaciji (rok racuna korpu kao 1 pa bira hes)
    plan = _build()
    automobil = _find_selection(plan, "Automobil")
    assert automobil.algorithm == "A1_full_scan"
    assert automobil.index_names == ()
    assert automobil.cost_blocks == 2
    assert automobil.output_rows == 1.0


def test_join_order_matches_exam():
    # rok spaja: prvo Automobil (filtriran na 1 red) sa Koristi, pa taj
    # rezultat sa Osoba; nas dp nadje isti redosled
    plan = _build()
    top = plan.input
    assert isinstance(top, JoinNode)
    assert set(top.tables) == {"Automobil", "Koristi", "Osoba"}
    inner_tables = {frozenset(n.tables if isinstance(n, JoinNode) else {n.table})
                    for n in (top.left, top.right)}
    assert frozenset({"Automobil", "Koristi"}) in inner_tables
    assert frozenset({"Osoba"}) in inner_tables


def test_inner_join_matches_exam_exactly():
    # rok: 20000/50 -> 400 redova; 400/40 -> 10 blokova + 1 indeksu + 1 pristup
    # listu = 12 I/O. nasa indeks ugnjezdena petlja preko kompozitnog
    # (IdOso, IdAut) po IdAut (ne-prefiks poklapanje jednog atributa):
    # 1 red spolja * (h(2) + ceil(400/40)) = 12. procena velicine spajanja
    # min(1*20000/V(IdAut,Automobil), 1*20000/V(IdAut,Koristi)) = 400, isto
    plan = _build()
    top = plan.input
    inner = top.left if isinstance(top.left, JoinNode) else top.right
    assert set(inner.tables) == {"Automobil", "Koristi"}
    assert inner.algorithm == "index_nested_loop"
    assert inner.cost_blocks == 12
    assert inner.output_rows == 400.0


def test_final_join_cost_matches_exam():
    # rok: dovlacimo svih 250 blokova Osobe i za svaki blok proverimo, ukupno
    # 250 I/O. nasa ugnjezdena petlja sa medjurezultatom od 10 blokova u
    # memoriji: cita se samo Osoba jednom = 250, isto
    plan = _build()
    top = plan.input
    assert top.algorithm == "nested_loop"
    assert top.cost_blocks == 250
    assert top.output_rows == 400.0


def test_total_cost_matches_exam_exactly():
    # rok: 2 + 12 + 250 = 264 (projekcija 0, rezultat u memoriji)
    plan = _build()
    assert isinstance(plan, ProjectionNode)  # nema ORDER BY
    assert total_cost(plan) == 264
    assert plan.output_rows == 400.0


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Automobil)" in text
    assert "Spajanje(Automobil, Koristi)" in text
    assert "Spajanje(Automobil, Koristi, Osoba)" in text
    assert "UKUPNA CENA PLANA: 264 blok transfera" in text
