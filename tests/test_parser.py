import dataclasses

import pytest

from src.sqlparser.query_ast import AttrRef, Condition, Disjunction, Literal, Query
from src.sqlparser.tokenizer import ParseError, TokenKind, tokenize

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
