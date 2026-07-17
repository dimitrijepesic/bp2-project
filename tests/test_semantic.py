from pathlib import Path

import pytest

from src.catalog.loader import load_catalog
from src.semantic.analyzer import (
    JoinPredicate,
    ResolvedAttr,
    ResolvedQuery,
    SelectionPredicate,
    SemanticError,
    analyze,
)
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/primer_ulaza.json"
UPITI_DIR = Path("tests/fixtures/upiti")


def _analyze(sql: str) -> ResolvedQuery:
    catalog = load_catalog(FIXTURE)
    return analyze(parse(sql), catalog)


# ---------------------------------------------------------------------------
# razresavanje imena: SELECT / ORDER BY
# ---------------------------------------------------------------------------


def test_qualified_select_attr_resolved():
    rq = _analyze("SELECT Student.ime FROM Student")
    catalog = load_catalog(FIXTURE)
    assert rq.select == (ResolvedAttr("Student", catalog.attribute("Student", "ime")),)


def test_unqualified_select_attr_resolved_when_unambiguous():
    rq = _analyze("SELECT ime FROM Student")
    catalog = load_catalog(FIXTURE)
    assert rq.select == (ResolvedAttr("Student", catalog.attribute("Student", "ime")),)


def test_select_list_mixed_qualifiers():
    rq = _analyze("SELECT Student.ime, prosek FROM Student")
    catalog = load_catalog(FIXTURE)
    assert rq.select == (
        ResolvedAttr("Student", catalog.attribute("Student", "ime")),
        ResolvedAttr("Student", catalog.attribute("Student", "prosek")),
    )


def test_select_star_expands_to_all_table_attributes():
    rq = _analyze("SELECT * FROM Student")
    catalog = load_catalog(FIXTURE)
    assert rq.select == tuple(
        ResolvedAttr("Student", a) for a in catalog.table("Student").attributes
    )


def test_select_star_expands_over_all_from_tables_in_order():
    rq = _analyze("SELECT * FROM Student, Ispit")
    catalog = load_catalog(FIXTURE)
    expected = tuple(
        ResolvedAttr(t, a) for t in ("Student", "Ispit") for a in catalog.table(t).attributes
    )
    assert rq.select == expected


def test_unqualified_attr_ambiguous_across_two_tables():
    # studentIndeks postoji i u Ispit i u Stipendija
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ispitId FROM Ispit, Stipendija WHERE studentIndeks = 'X'")
    assert "dvosmislen" in str(e.value)
    assert "studentIndeks" in str(e.value)


def test_unknown_attribute_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT nepostoji FROM Student")
    assert "nepoznat atribut" in str(e.value)


def test_qualified_attr_table_not_in_from_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT Predmet.naziv FROM Student")
    assert "Predmet" in str(e.value)
    assert "FROM" in str(e.value)


def test_order_by_resolved():
    rq = _analyze("SELECT ime FROM Student ORDER BY prosek")
    catalog = load_catalog(FIXTURE)
    assert rq.order_by == ResolvedAttr("Student", catalog.attribute("Student", "prosek"))


def test_order_by_unknown_attribute_rejected():
    with pytest.raises(SemanticError):
        _analyze("SELECT ime FROM Student ORDER BY nepostoji")


def test_no_order_by_gives_none():
    rq = _analyze("SELECT ime FROM Student")
    assert rq.order_by is None


def test_order_by_attribute_not_in_select_list_allowed():
    # ORDER BY po atributu van SELECT liste je dozvoljen; planner tada sortira
    # pre zavrsne projekcije da sort kljuc postoji na ulazu sortiranja, a
    # konacni izlaz ima samo SELECT atribute
    from src.optimizer.planner import build_plan
    from src.plan.nodes import ProjectionNode, SortNode

    rq = _analyze("SELECT ime FROM Student ORDER BY prosek")
    catalog = load_catalog(FIXTURE)
    assert [a.attribute.name for a in rq.select] == ["ime"]
    assert rq.order_by == ResolvedAttr("Student", catalog.attribute("Student", "prosek"))

    plan = build_plan(rq, catalog)
    assert isinstance(plan, ProjectionNode)
    assert plan.attributes == ("Student.ime",)  # konacni rezultat bez sort kljuca
    assert isinstance(plan.input, SortNode)
    assert plan.input.attribute == "Student.prosek"  # sortiranje pre projekcije


# ---------------------------------------------------------------------------
# FROM validacija
# ---------------------------------------------------------------------------


def test_unknown_table_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Studentt")
    assert "Studentt" in str(e.value)


def test_duplicate_table_in_from_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Student, Student")
    assert "duplicate" in str(e.value)


# ---------------------------------------------------------------------------
# alijasi tabela
# ---------------------------------------------------------------------------


def test_alias_qualifier_resolves_to_real_table():
    rq = _analyze("SELECT s.ime FROM Student s")
    catalog = load_catalog(FIXTURE)
    assert rq.tables == ("Student",)  # ResolvedQuery nosi prava imena, ne alijase
    assert rq.select == (ResolvedAttr("Student", catalog.attribute("Student", "ime")),)


def test_alias_join_classified_with_real_table_names():
    rq = _analyze(
        "SELECT s.ime FROM Student s, Ispit i WHERE s.indeks = i.studentIndeks"
    )
    catalog = load_catalog(FIXTURE)
    assert rq.where == (
        JoinPredicate(
            op="=",
            left=ResolvedAttr("Student", catalog.attribute("Student", "indeks")),
            right=ResolvedAttr("Ispit", catalog.attribute("Ispit", "studentIndeks")),
        ),
    )


def test_unqualified_attr_still_resolves_with_alias():
    rq = _analyze("SELECT ime FROM Student s")
    catalog = load_catalog(FIXTURE)
    assert rq.select == (ResolvedAttr("Student", catalog.attribute("Student", "ime")),)


def test_alias_hides_table_name_as_qualifier():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT Student.ime FROM Student s")
    assert "alijas" in str(e.value)
    assert "'s'" in str(e.value)


def test_duplicate_alias_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Student s, Ispit s")
    assert "nije jedinstven" in str(e.value)


def test_alias_colliding_with_other_table_name_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Student Ispit, Ispit")
    assert "nije jedinstven" in str(e.value)


def test_self_join_via_aliases_still_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT a.ime FROM Student a, Student b")
    assert "self-join" in str(e.value)


# ---------------------------------------------------------------------------
# klasifikacija WHERE uslova: selekcija vs join
# ---------------------------------------------------------------------------


def test_attr_op_literal_is_selection():
    rq = _analyze("SELECT ime FROM Student WHERE smer = 'RTI'")
    catalog = load_catalog(FIXTURE)
    assert rq.where == (
        SelectionPredicate(
            table="Student",
            attribute=catalog.attribute("Student", "smer"),
            op="=",
            right=parse("SELECT a FROM T WHERE a = 'RTI'").where[0].right,
        ),
    )


def test_cross_table_attr_op_attr_is_join():
    rq = _analyze(
        "SELECT Student.ime FROM Student, Ispit "
        "WHERE Student.indeks = Ispit.studentIndeks"
    )
    catalog = load_catalog(FIXTURE)
    assert rq.where == (
        JoinPredicate(
            op="=",
            left=ResolvedAttr("Student", catalog.attribute("Student", "indeks")),
            right=ResolvedAttr("Ispit", catalog.attribute("Ispit", "studentIndeks")),
        ),
    )


def test_same_table_attr_op_attr_is_selection_not_join():
    rq = _analyze("SELECT ispitId FROM Ispit WHERE predmetId = ocena")
    catalog = load_catalog(FIXTURE)
    assert len(rq.where) == 1
    pred = rq.where[0]
    assert isinstance(pred, SelectionPredicate)
    assert pred.table == "Ispit"
    assert pred.attribute == catalog.attribute("Ispit", "predmetId")
    assert pred.right == ResolvedAttr("Ispit", catalog.attribute("Ispit", "ocena"))


def test_where_condition_unknown_attr_reports_position_context():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Student WHERE nepostoji = 1")
    assert "WHERE" in str(e.value)
    assert "condition 1" in str(e.value)


# ---------------------------------------------------------------------------
# provera tipova
# ---------------------------------------------------------------------------


def test_numeric_attribute_with_string_literal_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze("SELECT ime FROM Student WHERE godinaUpisa = 'RTI'")
    assert "tip se ne slaze" in str(e.value)


def test_textual_attribute_with_number_literal_rejected():
    with pytest.raises(SemanticError):
        _analyze("SELECT ime FROM Student WHERE ime = 5")


def test_cross_table_join_type_mismatch_rejected():
    with pytest.raises(SemanticError) as e:
        _analyze(
            "SELECT ime FROM Student, Ispit WHERE Student.ime = Ispit.ocena"
        )
    assert "tip se ne slaze" in str(e.value)


def test_compatible_numeric_types_join_allowed():
    # Ispit.ocena (INT) i Predmet.espb (INT): razlicite tabele, kompatibilni tipovi
    rq = _analyze(
        "SELECT ispitId FROM Ispit, Predmet WHERE Ispit.ocena = Predmet.espb"
    )
    assert isinstance(rq.where[0], JoinPredicate)


# ---------------------------------------------------------------------------
# golden: fixture upiti prolaze celu semanticku analizu
# ---------------------------------------------------------------------------


def test_valid_fixture_queries_analyze_cleanly():
    catalog = load_catalog(FIXTURE)
    files = sorted(UPITI_DIR.glob("q*.sql"))
    assert len(files) == 4
    for f in files:
        rq = analyze(parse(f.read_text(encoding="utf-8")), catalog)
        assert isinstance(rq, ResolvedQuery), f.name


def test_q3_four_tables_full_classification():
    catalog = load_catalog(FIXTURE)
    sql = (UPITI_DIR / "q3_cetiri_tabele.sql").read_text(encoding="utf-8")
    rq = analyze(parse(sql), catalog)

    joins = [c for c in rq.where if isinstance(c, JoinPredicate)]
    selections = [c for c in rq.where if isinstance(c, SelectionPredicate)]

    assert len(joins) == 3
    assert len(selections) == 2
    assert {(s.table, s.attribute.name) for s in selections} == {
        ("Ispit", "ocena"), ("Predmet", "espb")
    }


def test_q4_order_by_and_all_selections_single_table():
    catalog = load_catalog(FIXTURE)
    sql = (UPITI_DIR / "q4_order_by.sql").read_text(encoding="utf-8")
    rq = analyze(parse(sql), catalog)

    assert all(isinstance(c, SelectionPredicate) and c.table == "Student" for c in rq.where)
    assert rq.order_by == ResolvedAttr("Student", catalog.attribute("Student", "prosek"))
