"""
provera protiv radjenog primera sa predavanja "Optimizacija upita"
(slajd 32-35, "Odredjivanje cene upita - primer"): spajanje Profesor/Predaje.

upit sa slajda koristi alijase (P, T), ovde prepisan sa punim nazivima tabela:

    SELECT P.Ime FROM Profesor P, Predaje T
    WHERE P.SifP = T.SifPro AND P.SifD='RTI' AND T.Semestar='L1994'
    ->
    SELECT Profesor.Ime FROM Profesor, Predaje
    WHERE Profesor.SifP = Predaje.SifPro AND Profesor.SifD='RTI' AND Predaje.Semestar='L1994'

katalog (tests/fixtures/slajd_profesor_predaje.json) tacno po statistikama sa
slajda 32: nProfesor=1000, bProfesor=200, Hash(SifP), B+ visina=2 nad SifD
(mora biti klasterovan da bi ispalo 4+2=6, vidi nize); nPredaje=10000,
bPredaje=1000, B+ visina=2 nad Semestar, Hash(SifPro), M=52.
V(SifD,Profesor)=50 i V(SifPro,Predaje)=1000 su izvedeni iz racuna na slajdu
34 (20 = 1000/50 profesora po katedri; 10 = 10000/1000 casova po profesoru).
"""

from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, ProjectionNode, SelectionNode, total_cost
from src.plan.printer import format_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/slajd_profesor_predaje.json"
QUERY = "tests/fixtures/upiti/slajd_profesor_upit.sql"


def _build():
    catalog = load_catalog(FIXTURE)
    sql = open(QUERY, encoding="utf-8").read()
    rq = analyze(parse(sql), catalog)
    return build_plan(rq, catalog)


def test_profesor_selection_cost_matches_slide_exactly():
    # slajd 34: "h=2 ... za 20 je potrebno dohvatiti 4 stranice / za selekciju
    # ukupno: 4+2=6"; nas a3 (klaster, jednakost, ne-kljucni atribut SifD)
    # mora dati isti broj
    plan = _build()
    profesor = _find_selection(plan, "Profesor")
    assert profesor.algorithm == "A3_clustering_equality_nonkey"
    assert profesor.cost_blocks == 6
    assert profesor.output_rows == 20.0  # 1000 * (1/50)


def test_join_cost_matches_slide_index_nested_loop():
    # slajd 34: "1,2*20 + 20*10 = 224" za spajanje Profesor/Predaje preko
    # Hash(SifPro). nas racun: 20 redova spolja (rezultat selekcije ostaje u
    # memoriji pa se ne cita ponovo) * (1.2 prosecna korpa (HASH_ACCESS_COST)
    # + 10 blokova) = 224, isto kao slajd
    plan = _build()
    join = _find_join(plan)
    assert join.algorithm == "index_nested_loop"
    assert join.cost_blocks == 224


def test_total_cost_matches_slide_plan2_exactly():
    # slajd (Plan 2, pipelining): selekcija 6 + spajanje 224 = 230; filter
    # Semestar se primenjuje besplatno posle spajanja. nas plan je isti Plan 2:
    # 6 + 224 + projekcija 0 (rezultat u memoriji) = 230, isto kao slajd
    plan = _build()
    assert total_cost(plan) == 230


def test_predaje_filter_absorbed_into_index_join():
    # Predaje se ne skenira: pristupa joj se kroz Hash(SifPro) u indeks
    # ugnjezdenoj petlji, a Semestar='L1994' se filtrira u memoriji nad
    # dovucenim redovima (a7 obrazac), bas kao pipelining na slajdu
    plan = _build()
    predaje = _find_selection(plan, "Predaje")
    assert predaje.algorithm == "join_index_access"
    assert predaje.index_names == ("idx_predaje_sifpro_hash",)
    assert predaje.cost_blocks == 0
    assert predaje.output_rows == 2500.0  # 10000 * (1/4)


def test_printed_plan_is_readable():
    plan = _build()
    text = format_plan(plan)
    assert "Selekcija(Profesor)" in text
    assert "Selekcija(Predaje)" in text
    assert "Spajanje(Predaje, Profesor)" in text
    assert "UKUPNA CENA PLANA: 230 blok transfera" in text


def _find_selection(node, table: str) -> SelectionNode:
    if isinstance(node, SelectionNode):
        if node.table == table:
            return node
        raise AssertionError(f"selection for {table!r} not found here")
    if isinstance(node, JoinNode):
        try:
            return _find_selection(node.left, table)
        except AssertionError:
            return _find_selection(node.right, table)
    return _find_selection(node.input, table)


def _find_join(node) -> JoinNode:
    if isinstance(node, JoinNode):
        return node
    if isinstance(node, ProjectionNode) or hasattr(node, "input"):
        return _find_join(node.input)
    raise AssertionError("no join node found in plan")
