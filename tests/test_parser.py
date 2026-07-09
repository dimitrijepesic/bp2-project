import dataclasses
from pathlib import Path

import pytest

from src.sqlparser.parser import parse
from src.sqlparser.query_ast import AttrRef, Condition, Disjunction, Literal, Query
from src.sqlparser.tokenizer import ParseError, TokenKind, tokenize

UPITI_DIR = Path("tests/fixtures/upiti")

# ---------------------------------------------------------------------------
# Tokenizer
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


def test_parenthesized_disjunction():
    tokens = tokenize("(b=2 OR c=3)")
    assert kinds(tokens) == [
        TokenKind.LPAREN, TokenKind.IDENT, TokenKind.OP, TokenKind.NUMBER,
        TokenKind.KEYWORD, TokenKind.IDENT, TokenKind.OP, TokenKind.NUMBER,
        TokenKind.RPAREN, TokenKind.EOF,
    ]


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


# ---------------------------------------------------------------------------
# AST čvorovi
# ---------------------------------------------------------------------------


def test_ast_value_equality():
    q1 = Query(
        select=(AttrRef("ime"),),
        tables=("Student",),
        where=(Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),),
        order_by=AttrRef("ime", "Student"),
    )
    q2 = Query(
        select=(AttrRef("ime"),),
        tables=("Student",),
        where=(Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),),
        order_by=AttrRef("ime", "Student"),
    )
    assert q1 == q2


def test_ast_nodes_are_frozen():
    attr = AttrRef("ime")
    with pytest.raises(dataclasses.FrozenInstanceError):
        attr.name = "prezime"


def test_query_defaults_no_where_no_order_by():
    q = Query(select=(AttrRef("ime"),), tables=("Student",))
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


def test_empty_disjunction_rejected():
    with pytest.raises(ValueError):
        Disjunction(())


def test_query_empty_select_or_tables_rejected():
    with pytest.raises(ValueError):
        Query(select=(), tables=("Student",))
    with pytest.raises(ValueError):
        Query(select=(AttrRef("ime"),), tables=())


# ---------------------------------------------------------------------------
# Parser — validni upiti
# ---------------------------------------------------------------------------


def test_parse_minimal_query():
    q = parse("SELECT ime FROM Student")
    assert q == Query(select=(AttrRef("ime"),), tables=("Student",))


def test_parse_full_query_ast_equality():
    q = parse(
        "SELECT Student.ime, prosek FROM Student, Ispit "
        "WHERE Student.indeks = Ispit.studentIndeks AND prosek > 8.5 "
        "AND (ocena = 9 OR ocena = 10) ORDER BY Student.prosek;"
    )
    assert q == Query(
        select=(AttrRef("ime", "Student"), AttrRef("prosek")),
        tables=("Student", "Ispit"),
        where=(
            Condition(AttrRef("indeks", "Student"), "=", AttrRef("studentIndeks", "Ispit")),
            Condition(AttrRef("prosek"), ">", Literal(8.5, "number")),
            Disjunction((
                Condition(AttrRef("ocena"), "=", Literal(9, "number")),
                Condition(AttrRef("ocena"), "=", Literal(10, "number")),
            )),
        ),
        order_by=AttrRef("prosek", "Student"),
    )


def test_singleton_parenthesized_condition_normalized_to_condition():
    q = parse("SELECT a FROM T WHERE (b = 2)")
    assert q.where == (Condition(AttrRef("b"), "=", Literal(2, "number")),)
    assert not isinstance(q.where[0], Disjunction)


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


def test_disjunction_counts_as_one_condition():
    # 5 prostih + cela zagrada = 6 clanova — mora da prodje
    q = parse(
        "SELECT a FROM T WHERE a=1 AND b=2 AND c=3 AND d=4 AND e=5 "
        "AND (f=6 OR f=7)"
    )
    assert len(q.where) == 6
    assert isinstance(q.where[5], Disjunction)


# ---------------------------------------------------------------------------
# Parser — greske
# ---------------------------------------------------------------------------


def test_missing_from_raises_with_position():
    with pytest.raises(ParseError) as e:
        parse("SELECT ime Student")
    assert "expected FROM" in str(e.value)
    assert "position 12" in str(e.value)
    assert "IDENT 'Student'" in str(e.value)


def test_or_outside_parentheses_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT ime FROM Student WHERE smer = 'RTI' OR smer = 'SI'")
    assert "expected end of query" in str(e.value)
    assert "KEYWORD 'OR'" in str(e.value)


def test_attr_right_side_inside_parentheses_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T WHERE (b = c OR d = 1)")
    assert "expected literal" in str(e.value)


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


def test_unclosed_parenthesis_rejected():
    with pytest.raises(ParseError) as e:
        parse("SELECT a FROM T WHERE (b = 2 OR c = 3")
    assert "')'" in str(e.value)


# ---------------------------------------------------------------------------
# Golden-ish: fixture upiti
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
