import dataclasses
from pathlib import Path

import pytest

from src.sqlparser.parser import parse
from src.sqlparser.query_ast import AttrRef, Condition, Literal, Query, TableRef
from src.sqlparser.tokenizer import ParseError, TokenKind, tokenize

UPITI_DIR = Path("tests/fixtures/upiti")

# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------


def kinds(tokens):
    return [t.kind for t in tokens]


def texts(tokens):
    return [t.text for t in tokens]


def test_tokenize_full_query():
    sql = ("SELECT ime, prosek FROM Student "
           "WHERE prosek > 8.5 AND smer = 'RTI' ORDER BY ime;")
    tokens = tokenize(sql)
    assert kinds(tokens) == [
        TokenKind.KEYWORD, TokenKind.IDENT, TokenKind.COMMA, TokenKind.IDENT,
        TokenKind.KEYWORD, TokenKind.IDENT,
        TokenKind.KEYWORD, TokenKind.IDENT, TokenKind.OP, TokenKind.NUMBER,
        TokenKind.KEYWORD, TokenKind.IDENT, TokenKind.OP, TokenKind.STRING,
        TokenKind.KEYWORD, TokenKind.KEYWORD, TokenKind.IDENT,
        TokenKind.SEMICOLON, TokenKind.EOF,
    ]
    assert texts(tokens)[:2] == ["SELECT", "ime"]
    assert tokens[9].text == "8.5"
    assert tokens[13].text == "RTI"


def test_keywords_case_insensitive_and_normalized():
    tokens = tokenize("select FrOm WHERE and Or oRdEr by")
    assert kinds(tokens)[:-1] == [TokenKind.KEYWORD] * 7
    assert texts(tokens)[:-1] == ["SELECT", "FROM", "WHERE", "AND", "OR", "ORDER", "BY"]


def test_identifier_case_preserved():
    tokens = tokenize("Student sTuDeNt")
    assert kinds(tokens)[:-1] == [TokenKind.IDENT, TokenKind.IDENT]
    assert texts(tokens)[:-1] == ["Student", "sTuDeNt"]


def test_operator_maximal_munch_without_spaces():
    tokens = tokenize("a<=5")
    assert kinds(tokens) == [
        TokenKind.IDENT, TokenKind.OP, TokenKind.NUMBER, TokenKind.EOF,
    ]
    assert tokens[1].text == "<="


def test_all_operators():
    tokens = tokenize("= < > <= >=")
    assert all(t.kind is TokenKind.OP for t in tokens[:-1])
    assert texts(tokens)[:-1] == ["=", "<", ">", "<=", ">="]


def test_qualified_name_is_three_tokens():
    tokens = tokenize("Student.ime")
    assert kinds(tokens) == [
        TokenKind.IDENT, TokenKind.DOT, TokenKind.IDENT, TokenKind.EOF,
    ]


def test_numbers_integer_and_decimal():
    tokens = tokenize("42 3.14")
    assert kinds(tokens)[:-1] == [TokenKind.NUMBER, TokenKind.NUMBER]
    assert texts(tokens)[:-1] == ["42", "3.14"]


def test_string_literal_content_without_quotes():
    tokens = tokenize("'RTI' ''")
    assert kinds(tokens)[:-1] == [TokenKind.STRING, TokenKind.STRING]
    assert texts(tokens)[:-1] == ["RTI", ""]


def test_parentheses_rejected_by_tokenizer():
    # '(' i ')' nisu deo jezika, tokenizer ih odbija
    for sql in ("(b=2 OR c=3)", "SELECT a FROM T WHERE (b = 2)"):
        with pytest.raises(ParseError) as e:
            tokenize(sql)
        assert "unexpected character" in str(e.value)


def test_token_positions_are_one_based():
    tokens = tokenize("ab cd")
    assert tokens[0].pos == 1
    assert tokens[1].pos == 4


def test_empty_input_gives_only_eof():
    for sql in ("", "   \n\t "):
        assert kinds(tokenize(sql)) == [TokenKind.EOF]


def test_unknown_character_raises_with_position():
    with pytest.raises(ParseError) as e:
        tokenize("a != 1")
    assert "position 3" in str(e.value)


def test_unterminated_string_raises_with_position():
    with pytest.raises(ParseError) as e:
        tokenize("smer = 'RTI")
    assert "unterminated" in str(e.value)
    assert "position 8" in str(e.value)


def test_invalid_number_letters_after_digits():
    with pytest.raises(ParseError) as e:
        tokenize("12ab")
    assert "invalid number" in str(e.value)


def test_invalid_number_trailing_dot():
    with pytest.raises(ParseError):
        tokenize("x = 3.")


def test_invalid_number_double_decimal_point():
    with pytest.raises(ParseError):
        tokenize("1.2.3")


def test_star_token():
    tokens = tokenize("SELECT * FROM Student")
    assert kinds(tokens) == [
        TokenKind.KEYWORD, TokenKind.STAR, TokenKind.KEYWORD,
        TokenKind.IDENT, TokenKind.EOF,
    ]


# ---------------------------------------------------------------------------
# AST cvorovi
# ---------------------------------------------------------------------------


def test_ast_value_equality():
    q1 = Query(
        select=(AttrRef("ime"),),
        tables=(TableRef("Student"),),
        where=(Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),),
        order_by=AttrRef("ime", "Student"),
    )
    q2 = Query(
        select=(AttrRef("ime"),),
        tables=(TableRef("Student"),),
        where=(Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),),
        order_by=AttrRef("ime", "Student"),
    )
    assert q1 == q2


def test_ast_nodes_are_frozen():
    attr = AttrRef("ime")
    with pytest.raises(dataclasses.FrozenInstanceError):
        attr.name = "prezime"


def test_query_defaults_no_where_no_order_by():
    q = Query(select=(AttrRef("ime"),), tables=(TableRef("Student"),))
    assert q.where == ()
    assert q.order_by is None


def test_condition_right_side_can_be_attr_or_literal():
    join = Condition(AttrRef("id", "Student"), "=", AttrRef("studentId", "Ispit"))
    sel = Condition(AttrRef("smer"), "=", Literal("RTI", "string"))
    assert isinstance(join.right, AttrRef)
    assert isinstance(sel.right, Literal)


def test_condition_invalid_op_rejected():
    with pytest.raises(ValueError):
        Condition(AttrRef("a"), "!=", Literal(1, "number"))


def test_literal_invalid_kind_and_mismatched_value_rejected():
    with pytest.raises(ValueError):
        Literal(1, "bool")
    with pytest.raises(ValueError):
        Literal("RTI", "number")
    with pytest.raises(ValueError):
        Literal(True, "number")
    with pytest.raises(ValueError):
        Literal(5, "string")


def test_query_empty_select_or_tables_rejected():
    with pytest.raises(ValueError):
        Query(select=(), tables=(TableRef("Student"),))
    with pytest.raises(ValueError):
        Query(select=(AttrRef("ime"),), tables=())


def test_table_ref_alias_same_as_name_rejected():
    with pytest.raises(ValueError):
        TableRef("Student", alias="Student")


# ---------------------------------------------------------------------------
# parser: validni upiti
# ---------------------------------------------------------------------------


def test_parse_minimal_query():
    q = parse("SELECT ime FROM Student")
    assert q == Query(select=(AttrRef("ime"),), tables=(TableRef("Student"),))


def test_parse_full_query_ast_equality():
    q = parse(
        "SELECT Student.ime, prosek FROM Student, Ispit "
        "WHERE Student.indeks = Ispit.studentIndeks AND prosek > 8.5 "
        "AND ocena >= 9 ORDER BY Student.prosek;"
    )
    assert q == Query(
        select=(AttrRef("ime", "Student"), AttrRef("prosek")),
        tables=(TableRef("Student"), TableRef("Ispit")),
        where=(
            Condition(AttrRef("indeks", "Student"), "=", AttrRef("studentIndeks", "Ispit")),
            Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),
            Condition(AttrRef("ocena"), ">=", Literal(9, "number")),
        ),
        order_by=AttrRef("prosek", "Student"),
    )


def test_parenthesized_condition_rejected():
    # zagrade oko uslova nisu podrzane
    with pytest.raises(ParseError):
        parse("SELECT a FROM T WHERE (b = 2)")


def test_number_literal_int_vs_float():
    q = parse("SELECT a FROM T WHERE b = 2 AND c = 2.5")
    assert isinstance(q.where[0].right.value, int)
    assert q.where[0].right.value == 2
    assert isinstance(q.where[1].right.value, float)
    assert q.where[1].right.value == 2.5


def test_string_literal_condition():
    q = parse("SELECT ime FROM Student WHERE smer = 'RTI'")
    assert q.where == (Condition(AttrRef("smer"), "=", Literal("RTI", "string")),)


def test_semicolon_is_optional():
    assert parse("SELECT a FROM T") == parse("SELECT a FROM T;")


def test_select_star():
    q = parse("SELECT * FROM Student WHERE prosek > 8 ORDER BY ime")
    assert q.select_star is True
    assert q.select == ()
    assert q.tables == (TableRef("Student"),)


def test_select_star_multiple_tables():
    q = parse("SELECT * FROM Student, Ispit")
    assert q.select_star is True
    assert q.tables == (TableRef("Student"), TableRef("Ispit"))


def test_parse_table_alias():
    q = parse("SELECT s.ime FROM Student s")
    assert q.tables == (TableRef("Student", alias="s"),)
    assert q.select == (AttrRef("ime", "s"),)  # kvalifikator ostaje alijas, resava ga semantika


def test_parse_aliases_mixed_with_plain_tables():
    q = parse("SELECT s.ime FROM Student s, Ispit WHERE s.indeks = studentIndeks")
    assert q.tables == (TableRef("Student", alias="s"), TableRef("Ispit"))


def test_parse_alias_in_where_and_order_by():
    q = parse("SELECT s.ime FROM Student s WHERE s.prosek > 8.5 ORDER BY s.ime")
    assert q.where == (Condition(AttrRef("prosek", "s"), ">", Literal(8.5, "number")),)
    assert q.order_by == AttrRef("ime", "s")


def test_parse_alias_same_as_table_name_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT ime FROM Student Student")
    assert "alias must differ" in str(e.value)


def test_six_conjunctive_conditions_accepted():
    # najvise sto sme: tacno 6 AND uslova u WHERE konjunkciji
    q = parse(
        "SELECT a FROM T WHERE a=1 AND b=2 AND c=3 AND d=4 AND e=5 AND f=6"
    )
    assert len(q.where) == 6
    assert all(isinstance(c, Condition) for c in q.where)


# ---------------------------------------------------------------------------
# parser: greske
# ---------------------------------------------------------------------------


def test_missing_from_raises_with_position():
    with pytest.raises(ParseError) as e:
        parse("SELECT ime Student")
    assert "expected FROM" in str(e.value)
    assert "position 12" in str(e.value)
    assert "IDENT 'Student'" in str(e.value)


def test_or_rejected_with_targeted_message():
    # disjunkcija je van obima, WHERE mora biti konjunkcija
    with pytest.raises(ParseError) as e:
        parse("SELECT ime FROM Student WHERE smer = 'RTI' OR smer = 'SI'")
    assert "OR is not supported" in str(e.value)
    assert "conjunction" in str(e.value)
    assert "position 44" in str(e.value)


def test_invalid_operator_sequence_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T WHERE b <> 2")
    assert "got OP '>'" in str(e.value)


def test_five_tables_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T1, T2, T3, T4, T5")
    assert "at most 4 tables" in str(e.value)
    assert "got 5" in str(e.value)


def test_seven_conditions_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T WHERE a=1 AND b=2 AND c=3 AND d=4 AND e=5 AND f=6 AND g=7")
    assert "at most 6 WHERE conditions" in str(e.value)
    assert "got 7" in str(e.value)


def test_trailing_tokens_after_semicolon_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T; SELECT")
    assert "expected end of query" in str(e.value)


def test_empty_where_rejected():
    with pytest.raises(ParseError):
        parse("SELECT a FROM T WHERE")
    with pytest.raises(ParseError):
        parse("SELECT a FROM T WHERE ORDER BY a")


def test_order_without_by_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T ORDER a")
    assert "expected BY" in str(e.value)


def test_trailing_comma_in_select_rejected():
    with pytest.raises(ParseError):
        parse("SELECT a, FROM T")


def test_star_mixed_with_attrs_rejected():
    # * je ili sama u SELECT listi ili je nema
    with pytest.raises(ParseError):
        parse("SELECT *, ime FROM Student")
    with pytest.raises(ParseError):
        parse("SELECT ime, * FROM Student")


def test_star_outside_select_rejected():
    with pytest.raises(ParseError):
        parse("SELECT a FROM T WHERE b = *")


# ---------------------------------------------------------------------------
# van obima postavke: forme koje se odbijaju
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(a) FROM T",                        # agregacija
    "SELECT SUM(a) FROM T",                          # agregacija
    "SELECT a FROM T GROUP BY a",                    # GROUP BY
    "SELECT a FROM T GROUP BY a HAVING b = 1",       # HAVING
    "SELECT DISTINCT a FROM T",                      # DISTINCT
    "SELECT a FROM T JOIN U ON a = b",               # JOIN ... ON
    "SELECT a FROM T UNION SELECT b FROM U",         # skupovna operacija
    "SELECT a FROM T INTERSECT SELECT b FROM U",     # skupovna operacija
    "SELECT a FROM T EXCEPT SELECT b FROM U",        # skupovna operacija
    "SELECT a FROM (SELECT b FROM U)",               # podupit
])
def test_out_of_scope_sql_forms_rejected(sql):
    with pytest.raises(ParseError):
        parse(sql)


# ---------------------------------------------------------------------------
# numericki literali: konacnost
# ---------------------------------------------------------------------------


def test_normal_numeric_literals_still_parse():
    q = parse("SELECT a FROM T WHERE b = 42 AND c = 8.5")
    assert q.where[0].right.value == 42
    assert q.where[1].right.value == 8.5


def test_huge_decimal_literal_rejected_as_not_finite():
    # float('9'*400 + '.5') ode u inf, hocemo ParseError a ne inf u AST-u
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T WHERE b = " + "9" * 400 + ".5")
    assert "not finite" in str(e.value)
    assert "position 27" in str(e.value)


def test_largest_finite_decimal_literal_accepted():
    q = parse("SELECT a FROM T WHERE b = 1" + "0" * 308 + ".0")  # 1e308 < float max
    assert q.where[0].right.value == 1e308


def test_huge_integer_literal_stays_exact_int():
    q = parse("SELECT a FROM T WHERE b = " + "9" * 400)
    value = q.where[0].right.value
    assert isinstance(value, int)
    assert value == int("9" * 400)


def test_tiny_decimal_underflows_to_zero_by_convention():
    # underflow ka 0.0 se prihvata, takva je konvencija
    q = parse("SELECT a FROM T WHERE b = 0." + "0" * 400 + "1")
    assert q.where[0].right.value == 0.0


# ---------------------------------------------------------------------------
# golden-ish: fixture upiti
# ---------------------------------------------------------------------------


def test_valid_fixture_queries_parse():
    files = sorted(UPITI_DIR.glob("q*.sql"))
    assert len(files) == 4, f"expected 4 valid fixtures, found {[f.name for f in files]}"
    for f in files:
        q = parse(f.read_text(encoding="utf-8"))
        assert isinstance(q, Query), f.name


def test_invalid_fixture_queries_raise():
    files = sorted(UPITI_DIR.glob("bad_*.sql"))
    assert len(files) == 4, f"expected 4 bad fixtures, found {[f.name for f in files]}"
    for f in files:
        with pytest.raises(ParseError):
            parse(f.read_text(encoding="utf-8"))
