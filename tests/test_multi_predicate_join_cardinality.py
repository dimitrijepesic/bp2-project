import pytest

from src.catalog.loader import load_catalog
from src.optimizer.enumerator import _oriented_predicate_pairs
from src.optimizer.planner import build_plan
from src.plan.nodes import JoinNode, total_cost
from src.semantic.analyzer import JoinPredicate, analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/multi_predikat_join.json"


def _build(sql: str):
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse(sql), catalog)
    return build_plan(rq, catalog), catalog


def test_single_equality_predicate_join_unchanged():
    # regresija: sa tacno jednim jednakosnim predikatom rezultat mora biti isti
    # kao kod obicnog estimate_join_rows
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x")
    join = plan.input
    assert isinstance(join, JoinNode)
    # 100*100 / V(x)=10 = 1000
    assert join.output_rows == pytest.approx(1000.0)


def test_two_equality_predicates_both_reduce_output_rows():
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND A.y = B.y")
    join = plan.input
    assert isinstance(join, JoinNode)
    # 100*100 / (V(x)=10 * V(y)=20) = 50, ne 1000 (to bi znacilo da je drugi
    # predikat ignorisan)
    assert join.output_rows == pytest.approx(50.0)


def test_where_condition_order_does_not_change_output_rows_or_cost():
    plan1, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND A.y = B.y")
    plan2, _ = _build("SELECT A.x FROM A, B WHERE A.y = B.y AND A.x = B.x")
    join1, join2 = plan1.input, plan2.input
    assert isinstance(join1, JoinNode) and isinstance(join2, JoinNode)
    assert join1.output_rows == pytest.approx(50.0)
    assert join2.output_rows == pytest.approx(50.0)
    assert join1.output_rows == join2.output_rows
    assert total_cost(plan1) == total_cost(plan2)


def test_duplicate_equality_predicate_applies_selectivity_once():
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND A.x = B.x")
    join = plan.input
    assert isinstance(join, JoinNode)
    # ne 100*100/(10*10)=100 (to bi bilo da se isti predikat racuna dvaput)
    assert join.output_rows == pytest.approx(1000.0)


def test_swapped_duplicate_equality_predicate_applies_selectivity_once():
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND B.x = A.x")
    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.output_rows == pytest.approx(1000.0)


def test_equality_attr_pairs_does_not_merge_predicates_across_different_tables():
    # namerno direktan test (ne end-to-end): A.x=B.x i C.x=D.x su dve nepovezane
    # komponente join grafa, pa DP uvek nadje jeftinu putanju koja ne prolazi kroz
    # "opasnu" podelu tables1={A,C}/tables2={B,D}; build_plan zato ne bi pouzdano
    # otkrio regresiju, pa se _oriented_predicate_pairs zove direktno sa tom podelom
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse("SELECT A.x FROM A, B, C, D WHERE A.x = B.x AND C.x = D.x"), catalog)
    equality_preds = [p for p in rq.where if isinstance(p, JoinPredicate) and p.op == "="]
    assert len(equality_preds) == 2  # A.x=B.x, C.x=D.x

    pairs = _oriented_predicate_pairs(equality_preds, {"A", "C"})

    # A.x, B.x, C.x, D.x imaju identicne metapodatke (INT, unique=False,
    # distinctValues=10) u razlicitim tabelama; dedup preko golog Attribute bi ih
    # pogresno spojio u jedan par, dedup preko ResolvedAttr (nosi ime tabele)
    # mora ih ostaviti kao dva odvojena para
    assert len(pairs) == 2
    assert {left.table for left, _op, _right in pairs} == {"A", "C"}
    assert {right.table for _left, _op, right in pairs} == {"B", "D"}


def test_equality_attr_pairs_dedups_swapped_duplicate_by_full_resolved_attr():
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse("SELECT A.x FROM A, B WHERE A.x = B.x AND B.x = A.x"), catalog)
    equality_preds = [p for p in rq.where if isinstance(p, JoinPredicate) and p.op == "="]
    assert len(equality_preds) == 2

    pairs = _oriented_predicate_pairs(equality_preds, {"A"})

    assert len(pairs) == 1
    left, op, right = pairs[0]
    assert (left.table, op, right.table) == ("A", "=", "B")


# ---------------------------------------------------------------------------
# nejednakosni ("<","<=",">",">=") join predikati: fiksna selektivnost 0.5,
# ne koristi minValue/maxValue (fixture ih ni nema)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_single_inequality_predicate_applies_fixed_half(op):
    plan, _ = _build(f"SELECT A.x FROM A, B WHERE A.x {op} B.y")
    join = plan.input
    assert isinstance(join, JoinNode)
    # A_rows * B_rows * 0.5 = 100*100*0.5 = 5000
    assert join.output_rows == pytest.approx(5000.0)


def test_equality_and_inequality_predicates_combine_by_multiplication():
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND A.y < B.y")
    join = plan.input
    assert isinstance(join, JoinNode)
    # A_rows*B_rows / max(V(A.x),V(B.x)) * 0.5 = 100*100/10*0.5 = 500
    assert join.output_rows == pytest.approx(500.0)


def test_mixed_where_order_does_not_change_output_rows_or_cost():
    plan1, _ = _build("SELECT A.x FROM A, B WHERE A.x = B.x AND A.y < B.y")
    plan2, _ = _build("SELECT A.x FROM A, B WHERE A.y < B.y AND A.x = B.x")
    join1, join2 = plan1.input, plan2.input
    assert isinstance(join1, JoinNode) and isinstance(join2, JoinNode)
    assert join1.output_rows == pytest.approx(500.0)
    assert join2.output_rows == pytest.approx(500.0)
    assert join1.output_rows == join2.output_rows
    assert total_cost(plan1) == total_cost(plan2)


def test_swapped_duplicate_inequality_predicate_applies_half_once():
    # A.x<B.y i B.y>A.x su isti predikat zapisan naopako (flip operatora pri
    # orijentaciji), faktor 0.5 se primenjuje samo jednom
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x < B.y AND B.y > A.x")
    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.output_rows == pytest.approx(5000.0)  # ne 100*100*0.25=2500


def test_opposite_inequality_predicates_are_not_deduplicated():
    # A.x<B.y i A.x>B.y su razliciti (kontradiktorni) predikati nad istim parom
    # atributa, oba daju svoj faktor 0.5 (nezavisnost, ne logicka analiza)
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x < B.y AND A.x > B.y")
    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.output_rows == pytest.approx(2500.0)  # 100*100*0.5*0.5


def test_inequality_predicates_across_different_tables_not_merged():
    # direktan test (isti razlog kao kod jednakosnih parova gore): A.x<B.x i
    # C.x<D.x imaju identicne metapodatke (INT, distinctValues=10) u razlicitim
    # tabelama i ne smeju se stopiti u dedup-u
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse("SELECT A.x FROM A, B, C, D WHERE A.x < B.x AND C.x < D.x"), catalog)
    inequality_preds = [p for p in rq.where if isinstance(p, JoinPredicate) and p.op == "<"]
    assert len(inequality_preds) == 2

    pairs = _oriented_predicate_pairs(inequality_preds, {"A", "C"})

    assert len(pairs) == 2
    assert {(left.table, op, right.table) for left, op, right in pairs} == {
        ("A", "<", "B"),
        ("C", "<", "D"),
    }


def test_inequality_join_does_not_pick_hash_sort_merge_or_index_nested_loop():
    plan, _ = _build("SELECT A.x FROM A, B WHERE A.x < B.y")
    join = plan.input
    assert isinstance(join, JoinNode)
    assert join.algorithm not in ("hash_join", "sort_merge", "index_nested_loop")
    assert join.algorithm in ("nested_loop", "block_nested_loop", "in_memory_join")
