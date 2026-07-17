import pytest

from src.catalog.loader import load_catalog
from src.catalog.model import Attribute
from src.cost.join import (
    block_nested_loop_cost,
    estimate_join_rows,
    estimate_join_rows_conjunction,
    hash_join_cost,
    index_nested_loop_cost,
    nested_loop_cost,
    sort_merge_join_cost,
)
from src.semantic.analyzer import ResolvedAttr

FIXTURE = "tests/fixtures/primer_ulaza.json"


def test_nested_loop_cost():
    # 1000 spoljasnjih slogova * 800 blokova unutrasnje + 100 blokova spoljasnje
    assert nested_loop_cost(outer_rows=1000, outer_blocks=100, inner_blocks=800) == 800_100


def test_block_nested_loop_cost():
    # (M-2)=8 -> ceil(100/8)=13 grupa; 13*800 + 100
    assert block_nested_loop_cost(outer_blocks=100, inner_blocks=800, buffer_blocks=10) == 10_500


def test_block_nested_loop_cheaper_than_nested_loop():
    nl = nested_loop_cost(outer_rows=1000, outer_blocks=100, inner_blocks=800)
    bnl = block_nested_loop_cost(outer_blocks=100, inner_blocks=800, buffer_blocks=10)
    assert bnl < nl


def test_block_nested_loop_boundary_chunk_counts():
    # M=10 => M-2=8 bafera za spoljasnju; broj prolaza = ceil(b_outer/8)
    assert block_nested_loop_cost(8, 20, 10) == 1 * 20 + 8    # tacno M-2 -> 1 prolaz
    assert block_nested_loop_cost(9, 20, 10) == 2 * 20 + 9    # M-1 -> 2 prolaza
    assert block_nested_loop_cost(16, 20, 10) == 2 * 20 + 16  # 2*(M-2) -> 2 prolaza
    assert block_nested_loop_cost(17, 20, 10) == 3 * 20 + 17  # 2*(M-2)+1 -> 3 prolaza
    # obrnuta orijentacija R/S: ceil(20/8)=3 prolaza
    assert block_nested_loop_cost(20, 8, 10) == 3 * 8 + 20
    assert block_nested_loop_cost(20, 9, 10) == 3 * 9 + 20


def test_nested_loop_in_memory_modes():
    assert nested_loop_cost(1000, 100, 20) == 1000 * 20 + 100  # obe na disku
    # outer u memoriji: nema pocetnog citanja spoljasnje
    assert nested_loop_cost(1000, 100, 20, outer_in_memory=True) == 1000 * 20
    # inner u memoriji: unutrasnja se ne cita ponovo sa diska ni za jedan red
    assert nested_loop_cost(1000, 100, 20, inner_in_memory=True) == 100
    assert nested_loop_cost(1000, 100, 20, True, True) == 0


def test_block_nested_loop_in_memory_modes():
    assert block_nested_loop_cost(100, 20, 10) == 13 * 20 + 100  # obe na disku
    # outer u memoriji: jedna "grupa", ostaje samo prolaz kroz unutrasnju
    assert block_nested_loop_cost(100, 20, 10, outer_in_memory=True) == 20
    # inner u memoriji: placa se samo citanje spoljasnje
    assert block_nested_loop_cost(100, 20, 10, inner_in_memory=True) == 100
    assert block_nested_loop_cost(100, 20, 10, True, True) == 0


def test_index_nested_loop_cost_uses_cheapest_matching_index():
    catalog = load_catalog(FIXTURE)
    ispit = catalog.table("Ispit")
    student_indeks = catalog.attribute("Ispit", "studentIndeks")
    # idx_ispit_student: h=3, selektivnost 1/1000 -> matched=8000/1000=8, nekluster => 3+8=11
    result = index_nested_loop_cost(
        outer_rows=1000, outer_blocks=100, inner_table=ispit, join_attribute=student_indeks
    )
    assert result == (1000 * 11 + 100, "idx_ispit_student")


def test_index_nested_loop_cost_skips_outer_read_when_in_memory():
    catalog = load_catalog(FIXTURE)
    ispit = catalog.table("Ispit")
    student_indeks = catalog.attribute("Ispit", "studentIndeks")
    result = index_nested_loop_cost(
        outer_rows=1000, outer_blocks=100, inner_table=ispit,
        join_attribute=student_indeks, outer_in_memory=True,
    )
    assert result == (1000 * 11, "idx_ispit_student")


def test_index_nested_loop_cost_none_without_usable_index():
    catalog = load_catalog(FIXTURE)
    ispit = catalog.table("Ispit")
    datum = catalog.attribute("Ispit", "datum")  # nema indeks
    assert index_nested_loop_cost(1000, 100, ispit, datum) is None


def test_sort_merge_join_cost_sorts_both_sides():
    cost = sort_merge_join_cost(r_blocks=100, s_blocks=800, buffer_blocks=11)
    # sort_cost(100,11)=300, sort_cost(800,11)=800*(2*2+1)=4000
    assert cost == 100 + 800 + 300 + 4000


def test_sort_merge_join_cost_skips_sort_when_already_sorted():
    cost = sort_merge_join_cost(
        r_blocks=100, s_blocks=800, buffer_blocks=11, r_sorted=True, s_sorted=True
    )
    assert cost == 900


def test_hash_join_cost():
    assert hash_join_cost(r_blocks=100, s_blocks=800) == 2700


def test_estimate_join_rows_unique_on_left_gives_size_of_s():
    left = Attribute(name="id", type="INT", unique=True, distinct_values=1000)
    right = Attribute(name="fk", type="INT", unique=False, distinct_values=500)
    assert estimate_join_rows(r_rows=1000, s_rows=8000, left=left, right=right) == 8000


def test_estimate_join_rows_unique_on_right_gives_size_of_r():
    left = Attribute(name="fk", type="INT", unique=False, distinct_values=500)
    right = Attribute(name="id", type="INT", unique=True, distinct_values=1000)
    assert estimate_join_rows(r_rows=8000, s_rows=1000, left=left, right=right) == 8000


def test_estimate_join_rows_general_formula_takes_minimum():
    left = Attribute(name="a", type="INT", unique=False, distinct_values=50)
    right = Attribute(name="b", type="INT", unique=False, distinct_values=20)
    # min(1000*200/50, 1000*200/20) = min(4000, 10000) = 4000
    assert estimate_join_rows(r_rows=1000, s_rows=200, left=left, right=right) == pytest.approx(4000)


def test_estimate_join_rows_conjunction_single_pair_matches_estimate_join_rows():
    left = Attribute(name="a", type="INT", unique=False, distinct_values=50)
    right = Attribute(name="b", type="INT", unique=False, distinct_values=20)
    pair = (ResolvedAttr(table="R", attribute=left), "=", ResolvedAttr(table="S", attribute=right))
    conjunction = estimate_join_rows_conjunction(1000, 200, [pair])
    single = estimate_join_rows(r_rows=1000, s_rows=200, left=left, right=right)
    assert conjunction == pytest.approx(single)


def test_estimate_join_rows_conjunction_multiplies_independent_predicates():
    x_left = Attribute(name="x", type="INT", unique=False, distinct_values=10)
    x_right = Attribute(name="x", type="INT", unique=False, distinct_values=10)
    y_left = Attribute(name="y", type="INT", unique=False, distinct_values=20)
    y_right = Attribute(name="y", type="INT", unique=False, distinct_values=20)
    pairs = [
        (ResolvedAttr(table="A", attribute=x_left), "=", ResolvedAttr(table="B", attribute=x_right)),
        (ResolvedAttr(table="A", attribute=y_left), "=", ResolvedAttr(table="B", attribute=y_right)),
    ]
    # 100*100 / (max(10,10) * max(20,20)) = 10000/200 = 50
    assert estimate_join_rows_conjunction(100, 100, pairs) == pytest.approx(50.0)


def test_estimate_join_rows_conjunction_inequality_pair_applies_fixed_half():
    left = Attribute(name="x", type="INT", unique=False, distinct_values=10)
    right = Attribute(name="y", type="INT", unique=False, distinct_values=10)
    for op in ("<", "<=", ">", ">="):
        pair = (ResolvedAttr(table="A", attribute=left), op, ResolvedAttr(table="B", attribute=right))
        assert estimate_join_rows_conjunction(100, 50, [pair]) == pytest.approx(100 * 50 * 0.5), op


def test_estimate_join_rows_conjunction_equality_and_inequality_combine():
    x_left = Attribute(name="x", type="INT", unique=False, distinct_values=10)
    x_right = Attribute(name="x", type="INT", unique=False, distinct_values=10)
    y_left = Attribute(name="y", type="INT", unique=False, distinct_values=20)
    y_right = Attribute(name="y", type="INT", unique=False, distinct_values=20)
    pairs = [
        (ResolvedAttr(table="A", attribute=x_left), "=", ResolvedAttr(table="B", attribute=x_right)),
        (ResolvedAttr(table="A", attribute=y_left), "<", ResolvedAttr(table="B", attribute=y_right)),
    ]
    # A_rows*B_rows / max(V(A.x),V(B.x)) * 0.5 = 100*100/10*0.5 = 500
    assert estimate_join_rows_conjunction(100, 100, pairs) == pytest.approx(500.0)
