import json
import math

import pytest

from src.catalog.loader import load_catalog
from src.cost.misc import output_blocks
from src.cost.selection import SelectionCost, select_best
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/primer_ulaza.json"


def _select_best(sql: str, table_name: str) -> SelectionCost:
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse(sql), catalog)
    conditions = [c for c in rq.where if getattr(c, "table", None) == table_name]
    return select_best(catalog.table(table_name), conditions)


# ---------------------------------------------------------------------------
# a1: linijsko skeniranje
# ---------------------------------------------------------------------------


def test_no_where_is_full_scan_over_whole_table():
    result = _select_best("SELECT ime FROM Student", "Student")
    assert result == SelectionCost("A1_full_scan", (), 100, 1000.0)


def test_unique_equality_without_index_is_full_scan():
    # bez n/2 proseka za jednakost nad unique (slajd 5), profesor to u rokovima
    # ne koristi, a1 je uvek svih n_blocks
    result = _select_best(
        "SELECT predmetId FROM Predmet WHERE naziv = 'Baze podataka'", "Predmet"
    )
    assert result == SelectionCost("A1_full_scan", (), 8, pytest.approx(1.0))


def test_expensive_composite_index_loses_to_full_scan():
    # idx_student_smer_prosek: 3 (visina) + ~100 poklopljenih (nekluster) = 103 > 100 (pun scan)
    result = _select_best(
        "SELECT ime FROM Student WHERE smer = 'RTI' AND prosek > 8.5", "Student"
    )
    assert result == SelectionCost("A1_full_scan", (), 100, pytest.approx(100.0))


def test_hash_index_not_used_for_range_condition():
    # idx_student_ime_hash pokriva samo jednakost; poredjenje ne moze kroz hes
    result = _select_best("SELECT ime FROM Student WHERE ime > 'M'", "Student")
    assert result.algorithm == "A1_full_scan"


# ---------------------------------------------------------------------------
# a2: klaster indeks, jednakost po kljucnom (unique) atributu
# ---------------------------------------------------------------------------


def test_unique_equality_with_clustered_index_beats_full_scan():
    result = _select_best(
        "SELECT ime FROM Student WHERE indeks = '2020/1234'", "Student"
    )
    assert result == SelectionCost(
        "A2_clustering_equality_key", ("idx_student_indeks_clustered",), 4, pytest.approx(1.0)
    )


# ---------------------------------------------------------------------------
# a6: sekundarni (nekluster) indeks, poredjenje (kompozitni prefiks + opseg)
# ---------------------------------------------------------------------------


def test_range_via_composite_index_wins_and_is_labeled_a6():
    # idx_stipendija_student_iznos: jednakost na studentIndeks (1/300) + opseg na iznos (0.5)
    # matched_rows = 300*(1/300)*0.5 = 0.5 -> ceil=1 ; cena = visina(3)+1 = 4, vs pun scan=30
    result = _select_best(
        "SELECT stipendijaId FROM Stipendija WHERE studentIndeks = 'ABC' AND iznos > 100",
        "Stipendija",
    )
    assert result == SelectionCost(
        "A6_secondary_comparison", ("idx_stipendija_student_iznos",), 4, pytest.approx(0.5)
    )


# ---------------------------------------------------------------------------
# a8: konjunkcija preko kompozitnog indeksa (2+ atributa kroz jedan indeks)
# ---------------------------------------------------------------------------


def test_hash_full_match_beats_full_scan_and_is_labeled_a8():
    result = _select_best(
        "SELECT ispitId FROM Ispit WHERE predmetId = 5 AND ocena = 9", "Ispit"
    )
    assert result.algorithm == "A8_composite_index"
    assert result.index_names == ("idx_ispit_predmet_ocena_hash",)
    assert result.cost_blocks == pytest.approx(1.2 + 17)  # prosecna korpa + ceil(16.67)
    assert result.output_rows == pytest.approx(8000 / 480)


# ---------------------------------------------------------------------------
# a3 / a9: treba sintetican katalog (fixture nema kluster indeks nad ne-kljucnim
# atributom ni dva nezavisna indeksa niske selektivnosti na istoj tabeli)
# ---------------------------------------------------------------------------


def _synthetic_catalog(tmp_path):
    data = {
        "bufferBlocks": 10,
        "schema": {
            "tables": [
                {
                    "name": "T",
                    "rowCount": 10000,
                    "blockCount": 1000,
                    "rowsPerBlock": 10,
                    "attributes": [
                        {"name": "id", "type": "INT", "unique": True, "distinctValues": 10000},
                        {"name": "a", "type": "INT", "unique": False, "distinctValues": 100},
                        {"name": "b", "type": "INT", "unique": False, "distinctValues": 100},
                        {"name": "c", "type": "INT", "unique": False, "distinctValues": 5},
                    ],
                    "indexes": [
                        {"name": "idx_a", "attributes": ["a"], "type": "B_PLUS_TREE",
                         "clustered": False, "treeHeight": 2},
                        {"name": "idx_b", "attributes": ["b"], "type": "B_PLUS_TREE",
                         "clustered": False, "treeHeight": 2},
                        {"name": "idx_c", "attributes": ["c"], "type": "B_PLUS_TREE",
                         "clustered": True, "treeHeight": 2},
                    ],
                }
            ]
        },
    }
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_catalog(str(path))


def test_clustered_equality_on_nonkey_attribute_is_labeled_a3(tmp_path):
    catalog = _synthetic_catalog(tmp_path)
    rq = analyze(parse("SELECT id FROM T WHERE c = 2"), catalog)
    result = select_best(catalog.table("T"), rq.where)
    # matched_rows = 10000*(1/5) = 2000 ; klaster => ceil(2000/10)=200 ; cena = 2+200 = 202
    assert result == SelectionCost("A3_clustering_equality_nonkey", ("idx_c",), 202, pytest.approx(2000.0))


def test_intersection_of_two_independent_indexes_beats_single_index_a9(tmp_path):
    catalog = _synthetic_catalog(tmp_path)
    rq = analyze(parse("SELECT id FROM T WHERE a = 5 AND b = 7"), catalog)
    result = select_best(catalog.table("T"), rq.where)
    # pojedinacno: idx_a ili idx_b kostaju 2+ceil(10000/100)=102 (mnogo losije od preseka)
    # a9: 2*visina(2+2) + ceil(10000*(1/100)*(1/100)) = 4+1 = 5
    assert result == SelectionCost("A9_conjunctive_intersection", ("idx_a", "idx_b"), 5, pytest.approx(1.0))


# ---------------------------------------------------------------------------
# fleksibilan sinteticki katalog za epsilon i a4/a5/a7/hash/M-granica testove
# ---------------------------------------------------------------------------


def _make_catalog(tmp_path, indexes, row_count=10000, rpb=10, buffer_blocks=10):
    data = {
        "bufferBlocks": buffer_blocks,
        "schema": {"tables": [{
            "name": "T", "rowCount": row_count,
            "blockCount": math.ceil(row_count / rpb), "rowsPerBlock": rpb,
            "attributes": [
                {"name": "id", "type": "INT", "unique": True, "distinctValues": row_count},
                {"name": "f", "type": "INT", "unique": False, "distinctValues": 10},
                {"name": "g", "type": "INT", "unique": False, "distinctValues": 10},
                {"name": "e", "type": "INT", "unique": False, "distinctValues": 16},
                {"name": "d", "type": "INT", "unique": False, "distinctValues": 8},
                {"name": "r", "type": "INT", "unique": False, "distinctValues": 100,
                 "minValue": 0, "maxValue": 1000},
            ],
            "indexes": indexes}]},
    }
    path = tmp_path / "sel_cat.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_catalog(str(path))


def _best(catalog, where: str) -> SelectionCost:
    rq = analyze(parse(f"SELECT id FROM T WHERE {where}"), catalog)
    return select_best(catalog.table("T"), rq.where)


# ---------------------------------------------------------------------------
# epsilon stabilizacija: round(9) pa ceil, isto kao u join.py/sort.py
# ---------------------------------------------------------------------------


def test_epsilon_composite_index_cost_regression(tmp_path):
    # bug koji se ovde cuva: n=1000, V(f)=V(g)=10 => tacno 1000/(10*10) = 10 redova,
    # ali conjunction_selectivity racuna 0.1*0.1 = 0.010000000000000002 pa je
    # 1000*sel tik iznad 10 i go ceil daje 11 (cena 13 umesto 12)
    assert 1000 // (10 * 10) == 10          # matematicki tacan broj redova
    assert 1000 * (0.1 * 0.1) > 10          # nestabilna medjuvrednost
    catalog = _make_catalog(tmp_path, [
        {"name": "i_fg", "attributes": ["f", "g"], "type": "B_PLUS_TREE",
         "clustered": False, "treeHeight": 2},
    ], row_count=1000)
    result = _best(catalog, "f = 1 AND g = 1")
    assert result.algorithm == "A8_composite_index"
    assert result.cost_blocks == 12  # h(2) + 10 pristupa po redu, ne 13
    # output_rows ostaje float procena, stabilizuje se samo diskretizacija
    # u blokove/pristupe
    assert 10.0 <= result.output_rows <= 10.000000001
    assert output_blocks(result.output_rows, 10) == 1  # ne 2


def test_epsilon_clustered_lookup_data_blocks(tmp_path):
    # klaster grana _lookup_cost: matched/rpb = 1.0000000000000002 mora dati
    # 1 data blok, ne 2
    catalog = _make_catalog(tmp_path, [
        {"name": "i_fg", "attributes": ["f", "g"], "type": "B_PLUS_TREE",
         "clustered": True, "treeHeight": 2},
    ], row_count=1000)
    result = _best(catalog, "f = 1 AND g = 1")
    assert result.algorithm == "A8_composite_index"
    assert result.cost_blocks == 3  # h(2) + ceil(10/10)=1, ne 4


def test_epsilon_intersection_a9(tmp_path):
    # a9 presek: procenjenih 10.000000000000002 pointera mora dati 10 pristupa, ne 11
    catalog = _make_catalog(tmp_path, [
        {"name": "i_f", "attributes": ["f"], "type": "B_PLUS_TREE",
         "clustered": False, "treeHeight": 2},
        {"name": "i_g", "attributes": ["g"], "type": "B_PLUS_TREE",
         "clustered": False, "treeHeight": 2},
    ], row_count=1000)
    result = _best(catalog, "f = 1 AND g = 1")
    assert result.algorithm == "A9_conjunctive_intersection"
    assert result.cost_blocks == 14  # 2+2 pointer + 10 redova, ne 15


# ---------------------------------------------------------------------------
# eksplicitni testovi formula
# ---------------------------------------------------------------------------


def test_a4_secondary_equality_explicit_formula(tmp_path):
    catalog = _synthetic_catalog(tmp_path)
    rq = analyze(parse("SELECT id FROM T WHERE a = 5"), catalog)
    result = select_best(catalog.table("T"), rq.where)
    # a4 = h + matched (po redu): 2 + 10000*(1/100) = 102, scan bi bio 1000
    assert result == SelectionCost("A4_secondary_equality", ("idx_a",), 102, pytest.approx(100.0))


def test_a5_clustered_range_explicit_formula(tmp_path):
    catalog = _synthetic_catalog(tmp_path)
    rq = analyze(parse("SELECT id FROM T WHERE c > 2"), catalog)
    result = select_best(catalog.table("T"), rq.where)
    # a5 = h + ceil(matched/rpb): bez min/max range=0.5 => 2 + ceil(5000/10) = 502
    assert result == SelectionCost("A5_clustering_comparison", ("idx_c",), 502, pytest.approx(5000.0))


def test_a7_single_index_with_residual_conditions(tmp_path):
    # a7 obrazac: indeks pokriva samo e=1; f=2 i r>750 su rezidualni (besplatan
    # filter u memoriji) ali ulaze u output_rows
    catalog = _make_catalog(tmp_path, [
        {"name": "i_e", "attributes": ["e"], "type": "B_PLUS_TREE",
         "clustered": False, "treeHeight": 2},
    ])
    result = _best(catalog, "e = 1 AND f = 2 AND r > 750")
    assert result.algorithm == "A4_secondary_equality"
    assert result.index_names == ("i_e",)
    assert result.cost_blocks == 627  # 2 + 625, cenu odredjuje samo indeksni pristup
    # svi uslovi u proceni: 10000 * (1/16) * (1/10) * ((1000-750)/1000) = 15.625
    assert result.output_rows == pytest.approx(15.625)


def test_clustered_hash_cost_coherent(tmp_path):
    # loader dozvoljava HASH+clustered, pa cost model mora biti koherentan:
    # 1.2 (korpa) + ceil(matched/rpb) kroz klaster granu
    catalog = _make_catalog(tmp_path, [
        {"name": "h_d", "attributes": ["d"], "type": "HASH", "clustered": True},
    ])
    result = _best(catalog, "d = 1")
    assert result.algorithm == "A3_clustering_equality_nonkey"
    assert result.cost_blocks == pytest.approx(1.2 + 125)  # 10000/8=1250 redova, rpb=10


def test_selection_output_blocks_equal_buffer_stays_in_memory(tmp_path):
    from src.optimizer.enumerator import _selection_node

    catalog = _make_catalog(tmp_path, [])  # M=10, rpb=10
    rq = analyze(parse("SELECT id FROM T WHERE r = 5"), catalog)
    node = _selection_node("T", catalog, rq.where, catalog.buffer_blocks)
    assert node.output_rows == pytest.approx(100.0)  # 10000 * 1/100
    assert node.output_blocks == 10 == catalog.buffer_blocks
    assert node.in_memory is True
    assert node.materialize is False


# ---------------------------------------------------------------------------
# selektivnost celog WHERE-a (konjunkcija vise uslova)
# ---------------------------------------------------------------------------


def test_where_selectivity_multiplies_across_conjunctive_predicates():
    from src.cost.selection import where_selectivity

    catalog = load_catalog(FIXTURE)
    sql = "SELECT ime FROM Student WHERE godinaUpisa = 2021 AND smer = 'RTI'"
    rq = analyze(parse(sql), catalog)
    # godinaUpisa: 1/10 ; smer: 1/5 -> ukupno 1/50 (pretpostavka nezavisnosti)
    assert where_selectivity(rq.where) == pytest.approx((1 / 10) * (1 / 5))
