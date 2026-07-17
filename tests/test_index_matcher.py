import pytest

from src.catalog.loader import load_catalog
from src.cost.index_matcher import SimpleCondition, match_index

FIXTURE = "tests/fixtures/primer_ulaza.json"


def _index(table, name):
    for idx in table.indexes:
        if idx.name == name:
            return idx
    raise AssertionError(f"Index not found: {name}")


def test_btree_matches_prefix_equality_and_range():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("smer", "=", "RTI"),
        SimpleCondition("prosek", ">", 8.5),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["smer", "prosek"]
    assert result.remaining == ()
    assert result.last_is_range is True


def test_btree_matches_single_non_prefix_attribute():
    # ne-prefiks poklapanje jednog atributa kompozitnog indeksa: odstupa od strogog
    # Silberschatz prefiks pravila, prati profesorovo resenje roka 2025
    # (klaster B+ (IdOso, IdAut) za pretragu samo po IdAut)
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("prosek", ">", 8.5),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["prosek"]
    assert result.remaining == ()
    assert result.last_is_range is True


def test_btree_non_prefix_fallback_prefers_equality_over_range():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("prosek", "=", 8.5),
        SimpleCondition("godinaUpisa", "=", 2022),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["prosek"]
    assert result.last_is_range is False
    assert [c.attribute for c in result.remaining] == ["godinaUpisa"]


def test_btree_matches_only_available_prefix_and_leaves_residuals():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("smer", "=", "RTI"),
        SimpleCondition("godinaUpisa", "=", 2022),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["smer"]
    assert [c.attribute for c in result.remaining] == ["godinaUpisa"]
    assert result.last_is_range is False


def test_btree_range_on_first_attr_stops_prefix():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("smer", ">", "A"),
        SimpleCondition("prosek", "=", 9.0),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["smer"]
    assert [c.attribute for c in result.remaining] == ["prosek"]
    assert result.last_is_range is True


def test_hash_matches_only_full_equality_on_all_attributes():
    catalog = load_catalog(FIXTURE)
    ispit = catalog.table("Ispit")
    index = _index(ispit, "idx_ispit_predmet_ocena_hash")
    result = match_index(index, [
        SimpleCondition("ocena", "=", 10),
        SimpleCondition("predmetId", "=", 13),
    ])
    assert result is not None
    assert {c.attribute for c in result.matched} == {"predmetId", "ocena"}
    assert result.remaining == ()
    assert result.last_is_range is False


def test_hash_does_not_match_partial_key():
    catalog = load_catalog(FIXTURE)
    ispit = catalog.table("Ispit")
    index = _index(ispit, "idx_ispit_predmet_ocena_hash")
    result = match_index(index, [
        SimpleCondition("predmetId", "=", 13),
    ])
    assert result is None


def test_hash_does_not_match_range():
    catalog = load_catalog(FIXTURE)
    predmet = catalog.table("Predmet")
    index = _index(predmet, "idx_predmet_katedra")
    result = match_index(index, [
        SimpleCondition("katedra", ">", "RTI"),
    ])
    assert result is None


def test_duplicate_conditions_one_used_rest_residual():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    result = match_index(index, [
        SimpleCondition("smer", "=", "RTI"),
        SimpleCondition("smer", "=", "SI"),
        SimpleCondition("prosek", ">", 8.5),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["smer", "prosek"]
    assert len(result.remaining) == 1
    assert result.remaining[0].attribute == "smer"


def test_two_range_conditions_same_attribute_one_matched_one_residual():
    # a >= x AND a <= y: kursni model nema closed-range formulu, indeks poklapa
    # prvi range uslov, drugi ostaje rezidual (a ulazi u selektivnost)
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_indeks_clustered")
    result = match_index(index, [
        SimpleCondition("indeks", ">=", "2020/0000"),
        SimpleCondition("indeks", "<=", "2021/9999"),
    ])
    assert result is not None
    assert [(c.attribute, c.op) for c in result.matched] == [("indeks", ">=")]
    assert [(c.attribute, c.op) for c in result.remaining] == [("indeks", "<=")]
    assert result.last_is_range is True


def test_empty_conditions_returns_none():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_smer_prosek")
    assert match_index(index, []) is None


def test_single_attribute_btree():
    catalog = load_catalog(FIXTURE)
    student = catalog.table("Student")
    index = _index(student, "idx_student_indeks_clustered")
    result = match_index(index, [
        SimpleCondition("indeks", "=", "2021/0123"),
    ])
    assert result is not None
    assert [c.attribute for c in result.matched] == ["indeks"]
    assert result.remaining == ()
    assert result.last_is_range is False


def test_invalid_operator_rejected():
    with pytest.raises(ValueError):
        SimpleCondition("smer", "!=", "RTI")