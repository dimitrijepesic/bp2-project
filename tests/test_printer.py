from src.catalog.loader import load_catalog
from src.optimizer.planner import build_plan
from src.plan.nodes import total_cost
from src.plan.printer import format_plan, print_plan
from src.semantic.analyzer import analyze
from src.sqlparser.parser import parse

FIXTURE = "tests/fixtures/primer_ulaza.json"


def _plan(sql: str):
    catalog = load_catalog(FIXTURE)
    rq = analyze(parse(sql), catalog)
    return build_plan(rq, catalog)


def test_single_table_index_selection_shows_algorithm_and_cost():
    plan = _plan("SELECT ime FROM Student WHERE indeks = '2020/1234'")
    text = format_plan(plan)
    assert "Selekcija(Student)" in text
    assert "A2 grupišući indeks, jednakost po ključu" in text
    assert "indeksi: idx_student_indeks_clustered" in text
    assert "cena: 4 blok transfera" in text
    assert "Projekcija(Student.ime)" in text
    assert f"UKUPNA CENA PLANA: {total_cost(plan)} blok transfera" in text
    # rezultat od 1 bloka ostaje u memoriji -> projekcija 0, ukupno = samo selekcija
    assert total_cost(plan) == 4


def test_selection_line_is_indented_under_projection():
    plan = _plan("SELECT ime FROM Student WHERE indeks = '2020/1234'")
    lines = format_plan(plan).splitlines()
    projection_idx = next(i for i, l in enumerate(lines) if l.startswith("Projekcija"))
    selection_idx = next(i for i, l in enumerate(lines) if "Selekcija(Student)" in l)
    assert selection_idx == projection_idx + 1
    assert lines[selection_idx].startswith("  ")  # uvucena za jedan nivo
    assert not lines[projection_idx].startswith(" ")  # koren nije uvucen


def test_join_plan_shows_algorithm_label_and_nested_selections():
    plan = _plan(
        "SELECT Student.ime, Ispit.ocena FROM Student, Ispit "
        "WHERE Student.indeks = Ispit.studentIndeks AND Ispit.ocena >= 8"
    )
    text = format_plan(plan)
    assert "Spajanje(Ispit, Student) [heš spajanje]" in text
    assert "cena: 1500 blok transfera" in text
    assert "Selekcija(Ispit)" in text
    assert "Selekcija(Student)" in text
    assert f"UKUPNA CENA PLANA: {total_cost(plan)} blok transfera" in text
    # projekcija(400) + hes(1500) + scan Ispit(800) + mat Ispit(400) + mat spajanja(400)
    assert total_cost(plan) == 3500

    lines = text.splitlines()
    join_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("Spajanje"))
    child_lines = [l for l in lines[join_idx + 1:] if "Selekcija(" in l]
    assert len(child_lines) == 2
    for line in child_lines:
        assert line.startswith("  ")  # oba deteta uvucena ispod spajanja


def test_order_by_outside_select_prints_projection_above_sort():
    # kljuc sortiranja (prosek) nije u SELECT listi: sortira se pre zavrsne
    # projekcije, ne sme izgledati kao da se sortira vec uklonjen atribut
    plan = _plan("SELECT ime FROM Student ORDER BY prosek")
    lines = format_plan(plan).splitlines()
    assert lines[0].startswith("Projekcija(Student.ime)")  # koren: konacna projekcija
    assert lines[1].startswith("  Spoljno objedinjeno sortiranje po Student.prosek")


def test_order_by_in_select_prints_sort_as_root():
    plan = _plan("SELECT ime FROM Student ORDER BY ime")
    lines = format_plan(plan).splitlines()
    assert lines[0].startswith("Spoljno objedinjeno sortiranje po Student.ime")  # koren stabla
    assert lines[1].startswith("  Projekcija(Student.ime)")


def test_in_memory_sort_labeled_distinctly_from_external():
    # rezultat od 1 bloka ostaje u memoriji -> sortiranje u memoriji (cena 0),
    # ne sme se lazno predstaviti kao spoljno objedinjeno sortiranje
    plan = _plan("SELECT ime FROM Student WHERE indeks = '2020/1234' ORDER BY ime")
    text = format_plan(plan)
    assert "Sortiranje u memoriji po Student.ime - cena: 0 blok transfera" in text
    assert "Spoljno objedinjeno" not in text


def test_print_plan_writes_to_stdout(capsys):
    plan = _plan("SELECT ime FROM Student")
    print_plan(plan)
    captured = capsys.readouterr()
    assert "Selekcija(Student)" in captured.out
    assert "UKUPNA CENA PLANA" in captured.out


def test_unknown_algorithm_code_falls_back_to_raw_string():
    from src.plan.printer import _algorithm_label

    assert _algorithm_label("nepoznat_kod") == "nepoznat_kod"
