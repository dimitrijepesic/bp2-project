"""
ispitni zadatak: Utakmica/Fudbaler/Igrao (ista postavka kao rok 2025
Osoba/Automobil).

SELECT O.Datum FROM Utakmica U, Fudbaler F, Igrao I
WHERE U.IDUta = I.IDUta AND F.IDFud = I.IDFud AND F.Ime = 'Nemanja Vidic'

"O.Datum" je ocigledno tipfeler za "U.Datum" (nema alijasa O u FROM listi),
tretirano kao Utakmica.Datum. upit prepisan bez alijasa, sa punim nazivima
tabela (tests/fixtures/upiti/zadatak_utakmica_upit.sql).

katalog (tests/fixtures/zadatak_utakmica_fudbaler_igrao.json) po datim
brojevima: Utakmica 10000/250blk, Fudbaler 50/2blk, Igrao 20000/500blk,
40 redova po stranici svuda, bafer=30. V(IDUta,Igrao)=10000 i V(IDFud,Igrao)=50
su pretpostavke (svaki mec/igrac se pojavljuje bar jednom u Igrao) jer nisu
date u tekstu zadatka.
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, ProjectionNode, SelectionNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/zadatak_utakmica_fudbaler_igrao.json"
QUERY = "tests/fixtures/upiti/zadatak_utakmica_upit.sql"


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


def test_fudbaler_selection_full_scan_beats_hash():
    # hes indeks = 1.2 (prosecna korpa, HASH_ACCESS_COST) + 1 (dohvat) = 2.2;
    # pun scan 2 stranice = 2, pa scan pobedjuje (isti obrazac kao
    # Automobil.Tablica u test_zadatak_osoba_automobil.py), cena ista kao rok
    plan = _build()
    fudbaler = _find_selection(plan, "Fudbaler")
    assert fudbaler.algorithm == "A1_full_scan"
    assert fudbaler.index_names == ()
    assert fudbaler.cost_blocks == 2
    assert fudbaler.output_rows == 1.0


def test_optimizer_joins_small_filtered_side_first():
    # dp treba da nadje da je (Fudbaler join Igrao) prvo mnogo jeftinije nego
    # (Utakmica join Igrao) prvo, posto je Fudbaler vec sveden na 1 red
    plan = _build()
    top_join = plan.input
    assert isinstance(top_join, JoinNode)
    assert set(top_join.tables) == {"Fudbaler", "Igrao", "Utakmica"}
    inner_tables = {frozenset(n.tables if isinstance(n, JoinNode) else {n.table})
                     for n in (top_join.left, top_join.right)}
    assert frozenset({"Fudbaler", "Igrao"}) in inner_tables
    assert frozenset({"Utakmica"}) in inner_tables


def test_index_usage_in_chosen_plan():
    # Igrao: pristup kroz kompozitni (IDUta, IDFud) po IDFud unutar indeks
    # ugnjezdene petlje (ne-prefiks poklapanje jednog atributa, kao u roku
    # 2025 Osoba/Automobil). Utakmica: bez WHERE uslova, cita je direktno
    # zavrsno spajanje
    plan = _build()
    igrao = _find_selection(plan, "Igrao")
    assert igrao.algorithm == "join_index_access"
    assert igrao.index_names == ("idx_igrao_iduta_idfud",)
    assert igrao.cost_blocks == 0
    utakmica = _find_selection(plan, "Utakmica")
    assert utakmica.algorithm == "no_selection"
    assert utakmica.cost_blocks == 0


def test_total_cost_matches_hand_derivation():
    # profesorova metodologija (ista kao rok 2025 Osoba/Automobil):
    # selekcija Fudbaler = 2 (kod profesora hes, kod nas scan, ista cena);
    # spajanje sa Igrao indeks ugnjezdenom petljom preko kompozitnog po
    # IDFud = 1 * (2 + ceil(400/40)) = 12; zavrsno spajanje = jedno citanje
    # Utakmice = 250 (medjurezultat od 10 blokova u memoriji);
    # projekcija 0 => 2 + 12 + 250 = 264
    plan = _build()
    assert isinstance(plan, ProjectionNode)
    assert total_cost(plan) == 264
    assert plan.output_rows == 400.0


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Fudbaler) [A1 linijsko skeniranje" in text
    assert "Spajanje(Fudbaler, Igrao)" in text
    assert "Spajanje(Fudbaler, Igrao, Utakmica)" in text
    assert "UKUPNA CENA PLANA: 264 blok transfera" in text
