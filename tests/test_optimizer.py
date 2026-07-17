import json
import math
from pathlib import Path

import pytest

from src.catalog.loader import load_catalog
from src.optimizer.enumerator import (
    _connecting_predicates,
    _index_nested_loop_candidates,
    _selection_node,
    find_best_join_plan,
)
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, ProjectionNode, SelectionNode, SortNode, total_cost
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/primer_ulaza.json"
UPITI_DIR = Path("tests/fixtures/upiti")


def _build(sql: str):
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse(sql), catalog)
    return build_plan(rq, catalog), catalog


def _leaf_tables(node) -> set:
    if isinstance(node, SelectionNode):
        return {node.table}
    if isinstance(node, JoinNode):
        return _leaf_tables(node.left) | _leaf_tables(node.right)
    return _leaf_tables(node.input)


# ---------------------------------------------------------------------------
# jedna tabela, bez spajanja
# ---------------------------------------------------------------------------


def test_single_table_no_where_is_projection_over_base_table():
    # bez WHERE uslova selekcija je identitet (cena 0, tabela je vec na disku);
    # projekcija cita tabelu direktno = 100
    plan, _ = _build("SELECT ime FROM Student")
    assert isinstance(plan, ProjectionNode)
    assert isinstance(plan.input, SelectionNode)
    assert plan.input.table == "Student"
    assert plan.input.algorithm == "no_selection"
    assert plan.input.cost_blocks == 0
    assert plan.cost_blocks == 100
    assert total_cost(plan) == 100


def test_single_table_with_index_selection_reduces_cost():
    plan, _ = _build("SELECT ime FROM Student WHERE indeks = '2020/1234'")
    assert isinstance(plan.input, SelectionNode)
    assert plan.input.algorithm == "A2_clustering_equality_key"
    assert plan.input.cost_blocks == 4
    # rezultat od 1 bloka ostaje u memoriji -> projekcija 0, ukupno = 4
    assert total_cost(plan) == 4


# ---------------------------------------------------------------------------
# ORDER BY dodaje SortNode preko ProjectionNode
# ---------------------------------------------------------------------------


def test_order_by_in_select_adds_sort_node_on_top():
    # kljuc sortiranja je u SELECT listi -> sort kao koren
    plan, _ = _build("SELECT ime FROM Student ORDER BY ime")
    assert isinstance(plan, SortNode)
    assert plan.attribute == "Student.ime"
    assert isinstance(plan.input, ProjectionNode)
    # sort_cost(100,10): initial_runs=10, merge_factor=9, log_9(10)~1.05 -> 2 prolaza -> 100*5=500
    assert plan.cost_blocks == 500
    # projekcija cita tabelu (100), njen izlaz od 100 blokova ne staje u M=10 pa se
    # materijalizuje (100), sortiranje ga eksterno sortira (500) => 700
    assert total_cost(plan) == 700


# ---------------------------------------------------------------------------
# dve tabele: bira se najjeftiniji algoritam spajanja
# ---------------------------------------------------------------------------


def test_two_table_join_picks_cheapest_algorithm_hash_join():
    # Student: bez svog WHERE uslova -> identitet (0), tabela od 100 blokova
    # Ispit: ocena>=8 (opseg, bez indeksa) -> pun scan 800, 4000 redova
    # (400 blokova > M=10 -> materijalizacija); hes spajanje 3*(400+100)=1500 pobedjuje
    plan, _ = _build(
        "SELECT Student.ime, Ispit.ocena FROM Student, Ispit "
        "WHERE Student.indeks = Ispit.studentIndeks AND Ispit.ocena >= 8"
    )
    assert isinstance(plan, ProjectionNode)
    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.tables == ("Ispit", "Student")
    assert join.algorithm == "hash_join"
    assert join.cost_blocks == 1500
    # join atribut je unique na Student strani -> rezultat = velicina Ispit strane (4000)
    assert join.output_rows == pytest.approx(4000.0)
    # projekcija(400) + hes(1500) + [scan Ispit 800 + mat 400] + [Student identitet 0]
    # + mat izlaza spajanja (400) = 3500
    assert total_cost(plan) == 3500


def test_join_result_size_bounded_by_unique_side():
    plan, _ = _build(
        "SELECT Student.ime FROM Student, Ispit WHERE Student.indeks = Ispit.studentIndeks"
    )
    join = plan.input
    assert isinstance(join, JoinNode)
    # bez dodatnog filtera na Ispit: 8000 redova, ogranicava unique strana (Student.indeks)
    assert join.output_rows == pytest.approx(8000.0)


# ---------------------------------------------------------------------------
# cetiri tabele: dp mora da pokrije sve tabele bez pucanja
# ---------------------------------------------------------------------------


def test_four_table_query_covers_all_tables_and_produces_positive_cost():
    sql = (UPITI_DIR / "q3_cetiri_tabele.sql").read_text(encoding="utf-8")
    plan, _ = _build(sql)
    assert isinstance(plan, ProjectionNode)
    assert _leaf_tables(plan) == {"Student", "Ispit", "Predmet", "Stipendija"}
    assert total_cost(plan) > 0


def test_all_fixture_queries_produce_a_plan():
    catalog = load_catalog(FIXTURE)
    for f in sorted(UPITI_DIR.glob("q*.sql")):
        rq = analyze(parse(f.read_text(encoding="utf-8")), catalog)
        plan = build_plan(rq, catalog)
        assert total_cost(plan) > 0, f.name


# ---------------------------------------------------------------------------
# globalna dp optimalnost: bushy optimum i greedy zamka (vrednosti potvrdjene
# nezavisnim brute-force oracle-om nad svim planovima)
# ---------------------------------------------------------------------------


def _tables_catalog(tmp_path, tables, buffer_blocks=10):
    # tables: ime -> (broj_redova, [(atribut, V), ...]); rpb=10, bez indeksa
    tabs = []
    for name, (rows, attrs) in tables.items():
        alist = [{"name": f"{name.lower()}id", "type": "INT", "unique": True,
                  "distinctValues": rows}]
        alist += [{"name": a, "type": "INT", "unique": False, "distinctValues": min(v, rows)}
                  for a, v in attrs]
        tabs.append({"name": name, "rowCount": rows, "blockCount": math.ceil(rows / 10),
                     "rowsPerBlock": 10, "attributes": alist, "indexes": []})
    data = {"bufferBlocks": buffer_blocks, "schema": {"tables": tabs}}
    path = tmp_path / "dp_cat.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_catalog(str(path))


def test_bushy_plan_joins_components_before_cartesian(tmp_path):
    # dve komponente bez medjusobnog predikata: optimum je bushy
    # (A join B) x (C join D), svaka prvo koristi svoj predikat, dekartov
    # proizvod tek na korenu; left-deep sa ranim dekartom je skuplji
    catalog = _tables_catalog(tmp_path, {
        "A": (800, [("j", 40)]), "B": (600, [("j", 40)]),
        "C": (500, [("k", 25)]), "D": (400, [("k", 25)]),
    })
    rq = analyze(parse("SELECT aid FROM A, B, C, D WHERE A.j = B.j AND C.k = D.k"), catalog)
    root = find_best_join_plan(rq.tables, rq.where, catalog, catalog.buffer_blocks)
    assert isinstance(root.left, JoinNode) and isinstance(root.right, JoinNode)  # bushy, ne left-deep
    assert {root.left.tables, root.right.tables} == {("A", "B"), ("C", "D")}
    assert root.left.algorithm == "hash_join" and root.right.algorithm == "hash_join"
    assert root.algorithm == "block_nested_loop"  # dekartov proizvod tek na korenu
    assert total_cost(root) == 123490  # oracle minimum nad kompletnim prostorom


def test_greedy_trap_picks_globally_optimal_first_pair(tmp_path):
    catalog = _tables_catalog(tmp_path, {
        "A": (100, [("j", 2)]),
        "B": (100, [("j", 2), ("k", 100)]),
        "C": (1000, [("k", 100)]),
    })

    def best(sql):
        rq = analyze(parse(sql), catalog)
        return find_best_join_plan(rq.tables, rq.where, catalog, catalog.buffer_blocks)

    # samostalno: A join B je lokalno jeftiniji (30 < 210), ali daje 5x veci rezultat
    ab = best("SELECT aid FROM A, B WHERE A.j = B.j")
    bc = best("SELECT bid FROM B, C WHERE B.k = C.k")
    assert total_cost(ab) == 30 and ab.output_blocks == 500
    assert total_cost(bc) == 210 and bc.output_blocks == 100

    # globalni optimum prvo spaja B join C, dp nije greedy po lokalnoj ceni
    full = best("SELECT aid FROM A, B, C WHERE A.j = B.j AND B.k = C.k")
    bottom = full.left if isinstance(full.left, JoinNode) else full.right
    assert bottom.tables == ("B", "C")
    assert total_cost(full) == 520  # oracle minimum

    # promenjen FROM i WHERE redosled ne menja minimum
    rev = best("SELECT aid FROM C, B, A WHERE B.k = C.k AND A.j = B.j")
    assert total_cost(rev) == 520


# ---------------------------------------------------------------------------
# ORDER BY kljuc van SELECT liste: sortiranje pre zavrsne projekcije
# (inace bi se sortirao atribut koji je projekcija vec uklonila)
# ---------------------------------------------------------------------------


def _available_attrs(node, catalog) -> set:
    # izlazna sema cvora, za proveru da sort kljuc fizicki postoji
    if isinstance(node, SelectionNode):
        return {f"{node.table}.{a.name}" for a in catalog.table(node.table).attributes}
    if isinstance(node, JoinNode):
        return _available_attrs(node.left, catalog) | _available_attrs(node.right, catalog)
    if isinstance(node, ProjectionNode):
        return set(node.attributes)
    return _available_attrs(node.input, catalog)  # SortNode ne menja semu


def test_order_by_outside_select_sorts_before_projection():
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse(
        "SELECT Student.ime FROM Student ORDER BY Student.prosek"), catalog), catalog)
    assert isinstance(plan, ProjectionNode)
    assert plan.attributes == ("Student.ime",)  # konacni izlaz bez sort kljuca
    sort = plan.input
    assert isinstance(sort, SortNode)
    assert sort.attribute == "Student.prosek"
    assert isinstance(sort.input, SelectionNode)
    # sort kljuc fizicki postoji na ulazu sortiranja, a uklanja se tek projekcijom
    assert sort.attribute in _available_attrs(sort.input, catalog)
    assert sort.attribute not in _available_attrs(plan, catalog)
    assert total_cost(plan) == 700  # redosled ne menja ukupnu cenu


def test_order_by_in_select_keeps_existing_shape_and_cost():
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse(
        "SELECT Student.ime, Student.prosek FROM Student ORDER BY Student.prosek"), catalog), catalog)
    assert isinstance(plan, SortNode)  # kljuc u SELECT listi -> sort kao koren
    assert plan.attribute in _available_attrs(plan.input, catalog)  # kljuc u projekciji
    assert total_cost(plan) == 700


def test_select_star_order_by_key_available():
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse("SELECT * FROM Student ORDER BY prosek"), catalog), catalog)
    assert isinstance(plan, SortNode)  # * sadrzi kljuc -> sort kao koren
    assert plan.attribute == "Student.prosek"
    assert plan.attribute in _available_attrs(plan.input, catalog)
    assert total_cost(plan) == 700


def test_alias_order_by_outside_select_uses_catalog_identity():
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse(
        "SELECT s.ime FROM Student s ORDER BY s.prosek"), catalog), catalog)
    assert isinstance(plan, ProjectionNode)
    assert plan.attributes == ("Student.ime",)         # pravo ime tabele, ne alias
    assert plan.input.attribute == "Student.prosek"    # katalog identitet kljuca


@pytest.mark.parametrize("select_attr, sort_attr", [
    ("Ispit.ocena", "Student.ime"),   # ORDER BY neprojektovan atribut leve strane
    ("Student.ime", "Ispit.ocena"),   # ORDER BY neprojektovan atribut desne strane
])
def test_join_order_by_unprojected_side_sorts_before_projection(select_attr, sort_attr):
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse(
        f"SELECT {select_attr} FROM Student, Ispit "
        f"WHERE Student.indeks = Ispit.studentIndeks ORDER BY {sort_attr}"), catalog), catalog)
    assert isinstance(plan, ProjectionNode)
    assert plan.attributes == (select_attr,)
    sort = plan.input
    assert isinstance(sort, SortNode)
    assert sort.attribute == sort_attr
    assert isinstance(sort.input, JoinNode)
    assert sort.attribute in _available_attrs(sort.input, catalog)  # join sema sadrzi kljuc


def test_same_named_attributes_distinguished_by_table_identity():
    # SELECT nosi Ispit.studentIndeks, a sortira se po istoimenom atributu
    # druge tabele: semanticki identitet (tabela+atribut) ih razlikuje
    catalog = load_catalog(FIXTURE)
    plan = build_plan(analyze(parse(
        "SELECT Ispit.studentIndeks FROM Ispit, Stipendija "
        "WHERE Ispit.studentIndeks = Stipendija.studentIndeks "
        "ORDER BY Stipendija.studentIndeks"), catalog), catalog)
    assert isinstance(plan, ProjectionNode)  # kljuc nije u SELECT listi
    assert plan.attributes == ("Ispit.studentIndeks",)
    assert plan.input.attribute == "Stipendija.studentIndeks"


def test_order_by_outside_select_in_memory_result_costs_nothing(tmp_path):
    catalog = _rs_catalog(tmp_path)
    plan = build_plan(analyze(parse(
        "SELECT rid FROM R WHERE R.rx = 5 ORDER BY R.rj"), catalog), catalog)
    # 10 redova/1 blok u memoriji: sortiranje u memoriji 0, projekcija 0
    assert isinstance(plan, ProjectionNode) and isinstance(plan.input, SortNode)
    assert plan.input.cost_blocks == 0
    assert plan.cost_blocks == 0
    assert total_cost(plan) == 100  # samo a1 scan koji pravi medjurezultat


def test_order_by_outside_select_external_sort_no_extra_io(tmp_path):
    catalog = _rs_catalog(tmp_path)
    plan = build_plan(analyze(parse("SELECT rid FROM R ORDER BY R.rj"), catalog), catalog)
    sort = plan.input
    assert sort.cost_blocks == 500   # puna eksterna formula: 100*(2*2+1)
    assert sort.materialize is True  # izlaz sortiranja (100 blk > M) ide na disk
    assert plan.cost_blocks == 100   # zavrsna projekcija ga cita tacno jednom
    # 0 (identitetska selekcija) + 500 + 100 (upis) + 100 (citanje),
    # bez dodatnog write/read para
    assert total_cost(plan) == 700


def test_order_by_outside_select_empty_result_still_valid(tmp_path):
    catalog = _rs_catalog(tmp_path)
    plan = build_plan(analyze(parse(
        "SELECT rid FROM R WHERE R.rx > 200 ORDER BY R.rj"), catalog), catalog)
    assert isinstance(plan, ProjectionNode) and isinstance(plan.input, SortNode)
    assert plan.output_rows == 0.0
    assert plan.output_blocks == 0
    assert total_cost(plan) == 100  # samo scan koji utvrdjuje prazan rezultat


# ---------------------------------------------------------------------------
# dekartov proizvod: nepovezan join graf je validan plan (postavka ga ne brani)
# ---------------------------------------------------------------------------


def test_cartesian_product_without_join_predicate_uses_loop_join():
    from src.semantic.analyzer import SelectionPredicate

    catalog = load_catalog(FIXTURE)
    rq = analyze(parse(
        "SELECT Student.ime, Predmet.naziv FROM Student, Predmet "
        "WHERE Student.godinaUpisa = 2021 AND Predmet.espb > 4"
    ), catalog)
    # semantika: dve lokalne selekcije, nula join predikata
    assert all(isinstance(c, SelectionPredicate) for c in rq.where)
    assert {c.table for c in rq.where} == {"Student", "Predmet"}

    plan = build_plan(rq, catalog)
    join = plan.input
    assert isinstance(join, JoinNode)
    # bez jednakosnog predikata nema hes/sort-merge/inlj, ostaju petlje
    assert join.algorithm in ("nested_loop", "block_nested_loop")
    # kardinalnost = proizvod dva filtrirana ulaza: (1000/10) * (80*0.5) = 4000
    assert join.output_rows == pytest.approx(100.0 * 40.0)
    assert total_cost(plan) > 0


# ---------------------------------------------------------------------------
# orijentacija predikata: zamenjen zapis i FROM redosled daju isti plan
# ---------------------------------------------------------------------------


def _join_node(plan) -> JoinNode:
    node = plan
    while not isinstance(node, JoinNode):
        node = node.input
    return node


@pytest.mark.parametrize("sql_a, sql_b", [
    ("SELECT ispitId FROM Ispit, Predmet WHERE Ispit.ocena < Predmet.espb",
     "SELECT ispitId FROM Predmet, Ispit WHERE Predmet.espb > Ispit.ocena"),
    ("SELECT ispitId FROM Ispit, Predmet WHERE Ispit.ocena <= Predmet.espb",
     "SELECT ispitId FROM Predmet, Ispit WHERE Predmet.espb >= Ispit.ocena"),
])
def test_inequality_join_orientation_equivalent_full_plan(sql_a, sql_b):
    # A.x < B.y i B.y > A.x su isti predikat, samo drugacije zapisan;
    # normalizacija u enumeratoru (_FLIP_OP) mora dati isti konacan plan
    plan_a, _ = _build(sql_a)
    plan_b, _ = _build(sql_b)
    join_a, join_b = _join_node(plan_a), _join_node(plan_b)
    assert join_a.output_rows == join_b.output_rows
    assert join_a.output_blocks == join_b.output_blocks
    assert join_a.algorithm == join_b.algorithm
    assert total_cost(plan_a) == total_cost(plan_b)


# ---------------------------------------------------------------------------
# sinteticki katalog (R, S) za join testove na nivou enumeratora
# ---------------------------------------------------------------------------


def _rs_catalog(tmp_path, r_idx=(), s_idx=(), r_rows=1000, s_rows=2000, rpb=10,
                vsj=100, buffer_blocks=10):
    data = {"bufferBlocks": buffer_blocks, "schema": {"tables": [
        {"name": "R", "rowCount": r_rows, "blockCount": math.ceil(r_rows / rpb),
         "rowsPerBlock": rpb,
         "attributes": [
             {"name": "rid", "type": "INT", "unique": True, "distinctValues": r_rows},
             {"name": "rj", "type": "INT", "unique": False, "distinctValues": 50},
             {"name": "rk", "type": "INT", "unique": False, "distinctValues": 20},
             {"name": "rx", "type": "INT", "unique": False, "distinctValues": 100,
              "minValue": 0, "maxValue": 100},
         ], "indexes": list(r_idx)},
        {"name": "S", "rowCount": s_rows, "blockCount": math.ceil(s_rows / rpb),
         "rowsPerBlock": rpb,
         "attributes": [
             {"name": "sid", "type": "INT", "unique": True, "distinctValues": s_rows},
             {"name": "sj", "type": "INT", "unique": False, "distinctValues": vsj},
             {"name": "sk", "type": "INT", "unique": False, "distinctValues": 20},
             {"name": "sy", "type": "INT", "unique": False, "distinctValues": 40},
         ], "indexes": list(s_idx)},
    ]}}
    path = tmp_path / "rs_cat.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_catalog(str(path))


def _inlj_candidates(catalog, sql):
    # (kandidati sa S kao unutrasnjom, kandidati sa R kao unutrasnjom)
    rq = analyze(parse(sql), catalog)
    p_r = _selection_node("R", catalog, rq.where, catalog.buffer_blocks)
    p_s = _selection_node("S", catalog, rq.where, catalog.buffer_blocks)
    preds = _connecting_predicates(rq.where, {"R"}, {"S"})
    eq = [p for p in preds if p.op == "="]
    return (
        _index_nested_loop_candidates(catalog, p_r, ("S",), eq),
        _index_nested_loop_candidates(catalog, p_s, ("R",), eq),
    )


# ---------------------------------------------------------------------------
# inlj orijentacija: indeksirana tabela postaje unutrasnja bez obzira na zapis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index_def, index_name", [
    ({"name": "b_sj", "attributes": ["sj"], "type": "B_PLUS_TREE",
      "clustered": False, "treeHeight": 2}, "b_sj"),
    ({"name": "h_sj", "attributes": ["sj"], "type": "HASH", "clustered": False}, "h_sj"),
])
def test_inlj_orientation_indexed_table_becomes_inner(tmp_path, index_def, index_name):
    catalog = _rs_catalog(tmp_path, s_idx=[index_def], vsj=2000)
    sql_a = "SELECT rid FROM R, S WHERE S.sj = R.rj AND R.rx = 5"  # indeksirana strana levo u zapisu
    sql_b = "SELECT rid FROM R, S WHERE R.rj = S.sj AND R.rx = 5"

    # enumerator razmatra obe orijentacije: kandidat postoji samo sa S kao inner
    with_s_inner, with_r_inner = _inlj_candidates(catalog, sql_a)
    assert with_s_inner and with_s_inner[0][1] == index_name
    assert with_r_inner == []

    plan_a = build_plan(analyze(parse(sql_a), catalog), catalog)
    plan_b = build_plan(analyze(parse(sql_b), catalog), catalog)
    join_a, join_b = _join_node(plan_a), _join_node(plan_b)
    assert join_a.algorithm == "index_nested_loop"
    absorbed = join_a.left if join_a.left.algorithm == "join_index_access" else join_a.right
    assert absorbed.table == "S"
    assert absorbed.index_names == (index_name,)
    # obrnut zapis predikata daje ekvivalentan plan
    assert join_b.algorithm == join_a.algorithm
    assert join_a.output_rows == join_b.output_rows
    assert join_a.output_blocks == join_b.output_blocks
    assert total_cost(plan_a) == total_cost(plan_b)


# ---------------------------------------------------------------------------
# rubni rezimi ulaza: 0 redova, oba staju u M, zbir preko M (split-fit)
# ---------------------------------------------------------------------------


def test_join_with_zero_row_input_produces_valid_plan(tmp_path):
    catalog = _rs_catalog(tmp_path)
    plan = build_plan(analyze(parse(
        "SELECT rid FROM R, S WHERE R.rx > 200 AND R.rj = S.sj"), catalog), catalog)
    join = _join_node(plan)
    assert join.output_rows == 0.0
    assert not math.isnan(join.output_rows) and math.isfinite(join.output_rows)
    assert join.output_blocks == 0
    total = total_cost(plan)
    assert math.isfinite(total) and total >= 0
    # placa se samo a1 scan tabele R (100 blokova) koji utvrdi prazan rezultat;
    # 0-blokovski medjurezultat i S se ne naplacuju
    assert total == 100


def test_both_inputs_fit_together_gives_in_memory_join(tmp_path):
    # rezim D: 1 blk (R filtriran) + 2 blk (S filtriran) <= M=10
    catalog = _rs_catalog(tmp_path)
    plan = build_plan(analyze(parse(
        "SELECT rid FROM R, S WHERE R.rx = 5 AND S.sj = 7 AND R.rj = S.sj"), catalog), catalog)
    join = _join_node(plan)
    assert join.algorithm == "in_memory_join"
    assert join.cost_blocks == 0
    assert join.left.materialize is False and join.right.materialize is False


def test_sum_over_buffer_uses_split_fit_not_fake_zero_io(tmp_path):
    # rezim E: 8 blk + 6 blk, pojedinacno staju ali zbir 14 > M=10;
    # nema laznog in_memory_join kandidata, tacno jedna (manja) strana na disk
    catalog = _rs_catalog(tmp_path, r_rows=8000, s_rows=6000)
    plan = build_plan(analyze(parse(
        "SELECT rid FROM R, S WHERE R.rx = 3 AND S.sj = 4 AND R.rj = S.sj"), catalog), catalog)
    join = _join_node(plan)
    assert join.algorithm != "in_memory_join"
    assert [join.left.materialize, join.right.materialize].count(True) == 1
    materialized = join.left if join.left.materialize else join.right
    assert materialized.output_blocks == 6  # manja strana (S) se materijalizuje


# ---------------------------------------------------------------------------
# kompozitni hash indeks i inlj; range inlj svesno van modela
# ---------------------------------------------------------------------------


def test_composite_hash_partial_key_gives_no_inlj_candidate(tmp_path):
    # HASH(sj, sk), predikat samo po sj: hes indeks trazi pun kljuc, nema inlj;
    # hes join (algoritam) i dalje postoji zbog jednakosti, ne mesati sa indeksom
    catalog = _rs_catalog(tmp_path, s_idx=[
        {"name": "h_sjsk", "attributes": ["sj", "sk"], "type": "HASH", "clustered": False},
    ])
    with_s_inner, with_r_inner = _inlj_candidates(catalog, "SELECT rid FROM R, S WHERE R.rj = S.sj")
    assert with_s_inner == [] and with_r_inner == []
    plan = build_plan(analyze(parse("SELECT rid FROM R, S WHERE R.rj = S.sj"), catalog), catalog)
    assert _join_node(plan).algorithm == "hash_join"


def test_composite_hash_with_both_equalities_still_no_inlj_by_design(tmp_path):
    # kontrolni slucaj: i sa oba jednakosna uslova (pun kljuc HASH(sj,sk)) inlj
    # se ne generise, proba ide po jednom join atributu; visekljucna proba bi
    # bila "spajanje sa konjunkcijom" koje je precrtano u postavci (svesno van
    # obima); hes join ostaje, oba predikata ulaze u kardinalnost
    catalog = _rs_catalog(tmp_path, s_idx=[
        {"name": "h_sjsk", "attributes": ["sj", "sk"], "type": "HASH", "clustered": False},
    ])
    sql = "SELECT rid FROM R, S WHERE R.rj = S.sj AND R.rk = S.sk"
    with_s_inner, with_r_inner = _inlj_candidates(catalog, sql)
    assert with_s_inner == [] and with_r_inner == []
    join = _join_node(build_plan(analyze(parse(sql), catalog), catalog))
    assert join.algorithm == "hash_join"
    assert join.output_rows == pytest.approx(1000 * 2000 / (100 * 20))


def test_inequality_join_generates_no_inlj_even_with_btree(tmp_path):
    # B+ range inlj je svesno van modela (nema ga u kursnim materijalima, cena
    # po probi bi trazila 0.5 konvenciju); enumerator filtrira predikate na
    # op == "=", pa kandidata nema ni sa B+ indeksom; nema ni hes/sort-merge
    catalog = _rs_catalog(tmp_path, s_idx=[
        {"name": "b_sy", "attributes": ["sy"], "type": "B_PLUS_TREE",
         "clustered": False, "treeHeight": 2},
    ])
    sql = "SELECT rid FROM R, S WHERE R.rx < S.sy"
    with_s_inner, with_r_inner = _inlj_candidates(catalog, sql)
    assert with_s_inner == [] and with_r_inner == []
    join = _join_node(build_plan(analyze(parse(sql), catalog), catalog))
    assert join.algorithm in ("nested_loop", "block_nested_loop")


def test_two_equality_predicates_full_plan_order_independent():
    sql_a = ("SELECT ispitId FROM Ispit, Predmet "
             "WHERE Ispit.predmetId = Predmet.predmetId AND Ispit.ocena = Predmet.espb")
    sql_b = ("SELECT ispitId FROM Ispit, Predmet "
             "WHERE Ispit.ocena = Predmet.espb AND Ispit.predmetId = Predmet.predmetId")
    plan_a, _ = _build(sql_a)
    plan_b, _ = _build(sql_b)
    join_a, join_b = _join_node(plan_a), _join_node(plan_b)
    # oba predikata ucestvuju u proceni: 8000*80 / (max(80,80) * max(6,5));
    # izgubljen predikat dao bi 8000, dupliran bi jos jednom podelio sa 6
    assert join_a.output_rows == pytest.approx(8000 * 80 / (80 * 6))
    assert join_a.output_rows == join_b.output_rows
    assert join_a.output_blocks == join_b.output_blocks
    assert total_cost(plan_a) == total_cost(plan_b)
