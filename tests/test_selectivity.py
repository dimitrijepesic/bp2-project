import pytest

from src.catalog.loader import load_catalog
from src.cost.selectivity import (
    conjunction_selectivity,
    equality_selectivity,
    estimate_rows,
    range_selectivity,
    selectivity_for_op,
)

FIXTURE = "tests/fixtures/primer_ulaza.json"


def test_equality_selectivity_is_inverse_of_distinct_values():
    catalog = load_catalog(FIXTURE)
    smer = catalog.attribute("Student", "smer")  # distinctValues=5
    assert equality_selectivity(smer) == pytest.approx(1 / 5)


def test_equality_selectivity_on_unique_attribute_is_one_over_rowcount():
    catalog = load_catalog(FIXTURE)
    indeks = catalog.attribute("Student", "indeks")  # unique, rowCount=1000
    assert equality_selectivity(indeks) == pytest.approx(1 / 1000)


def test_range_selectivity_is_default_half():
    assert range_selectivity() == 0.5


def test_selectivity_for_op_equality_vs_comparison():
    catalog = load_catalog(FIXTURE)
    prosek = catalog.attribute("Student", "prosek")  # distinctValues=300, bez min/max
    assert selectivity_for_op("=", prosek) == pytest.approx(1 / 300)
    for op in ("<", ">", "<=", ">="):
        assert selectivity_for_op(op, prosek) == 0.5


# ---------- interpolaciona formula sa min/max statistikama ----------
def _attr_with_range(min_value, max_value):
    from src.catalog.model import Attribute
    return Attribute(name="Procenat", type="INT", unique=False,
                     distinct_values=100, min_value=min_value, max_value=max_value)


def test_range_selectivity_interpolation_greater_than():
    # ispitni zadatak Radi/Zaposleni: Procenat > 99, min=0, max=100 =>
    # (100-99)/(100-0) = 1/100 (profesorovih "20000 * 1/100 -> 200 redova")
    attr = _attr_with_range(0, 100)
    assert range_selectivity(attr, ">", 99) == pytest.approx(0.01)
    assert range_selectivity(attr, ">=", 99) == pytest.approx(0.01)  # kontinualna aproks.


def test_range_selectivity_interpolation_less_than():
    attr = _attr_with_range(0, 100)
    assert range_selectivity(attr, "<", 25) == pytest.approx(0.25)
    assert range_selectivity(attr, "<=", 25) == pytest.approx(0.25)


def test_range_selectivity_clamped_to_unit_interval():
    attr = _attr_with_range(0, 100)
    assert range_selectivity(attr, ">", 150) == 0.0   # v iznad max, nista ne prolazi
    assert range_selectivity(attr, "<", -5) == 0.0    # v ispod min, nista ne prolazi
    assert range_selectivity(attr, ">", -5) == 1.0    # v ispod min, sve prolazi
    assert range_selectivity(attr, "<", 150) == 1.0   # v iznad max, sve prolazi


def test_range_selectivity_without_stats_falls_back_to_half():
    attr = _attr_with_range(None, None)
    assert range_selectivity(attr, ">", 99) == 0.5
    # nenumericka vrednost (npr. poredjenje stringova), opet default 1/2
    attr_sa_stat = _attr_with_range(0, 100)
    assert range_selectivity(attr_sa_stat, ">", "abc") == 0.5


def test_range_selectivity_huge_int_beyond_max_no_overflow():
    # regresija: ogroman int literal ne sme da pukne sa OverflowError
    # ("integer division result too large for a float") u interpolacionom deljenju
    attr = _attr_with_range(0, 100)
    huge = int("9" * 400)
    assert range_selectivity(attr, ">", huge) == 0.0
    assert range_selectivity(attr, ">=", huge) == 0.0
    assert range_selectivity(attr, "<", huge) == 1.0
    assert range_selectivity(attr, "<=", huge) == 1.0
    # jednakost ne koristi vrednost (1/V), ogroman int ne sme da je obori
    assert selectivity_for_op("=", attr, huge) == pytest.approx(1 / 100)


def test_range_selectivity_int_below_min_boundary():
    attr = _attr_with_range(10, 100)
    assert range_selectivity(attr, "<", 5) == 0.0
    assert range_selectivity(attr, ">", 5) == 1.0


def test_range_selectivity_boundary_values_match_formula():
    # v == min i v == max: granicna grana daje isto sto i formula sa clamp-om
    attr = _attr_with_range(0, 100)
    assert range_selectivity(attr, ">", 0) == 1.0
    assert range_selectivity(attr, "<", 0) == 0.0
    assert range_selectivity(attr, ">", 100) == 0.0
    assert range_selectivity(attr, "<", 100) == 1.0


def test_huge_int_range_query_full_pipeline_no_crash():
    # e2e regresija: 'SELECT IDPro FROM Radi WHERE Procenat < 9...(400 cifara)...9'
    # ne sme da izadje sa OverflowError kroz build_plan (main.py ga ne hvata);
    # pazi: unarni cvorovi plana koriste `.input`, ne `.child`
    import math

    from src.optimizer.planner import build_plan
    from src.plan.nodes import SelectionNode, total_cost
    from src.semantic.analyzer import analyze
    from src.sqlparser.parser import parse

    catalog = load_catalog("tests/fixtures/zadatak_radi_zaposleni.json")
    sql = "SELECT IDPro FROM Radi WHERE Procenat < " + "9" * 400
    plan = build_plan(analyze(parse(sql), catalog), catalog)  # ne sme OverflowError

    visited = 0
    node = plan
    while hasattr(node, "input"):  # ProjectionNode/SortNode -> do dna stabla
        node = node.input
        visited += 1
    assert visited >= 1  # obilazak nije no-op, prosli smo bar ProjectionNode
    assert isinstance(node, SelectionNode)

    assert math.isfinite(node.output_rows)   # nije inf
    assert not math.isnan(node.output_rows)  # nije NaN
    assert node.output_rows == 20000  # v iznad max => granicna selektivnost 1 => cela tabela
    assert isinstance(node.output_blocks, int)
    assert node.output_blocks == 500  # 20000 redova / 40 po bloku, konacno i nenegativno
    assert math.isfinite(total_cost(plan))


def test_conjunction_selectivity_multiplies():
    assert conjunction_selectivity([0.5, 0.2, 0.1]) == pytest.approx(0.01)


def test_conjunction_selectivity_empty_is_one():
    assert conjunction_selectivity([]) == 1.0


def test_estimate_rows():
    assert estimate_rows(1000, 0.1) == pytest.approx(100)
