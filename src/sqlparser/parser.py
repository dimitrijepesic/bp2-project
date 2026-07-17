import math

from src.sqlparser.query_ast import AttrRef, Condition, Literal, Query, TableRef
from src.sqlparser.tokenizer import ParseError, Token, TokenKind, tokenize

MAX_TABLES = 4
MAX_CONDITIONS = 6  # konjunkti, where je po postavci konjunkcija AND uslova


def parse(sql: str) -> Query:
    return _Parser(tokenize(sql)).parse_query()


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

    # -- helperi ------------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        tok = self.tokens[self.i]
        if tok.kind is not TokenKind.EOF:  # na EOF kursor stoji
            self.i += 1
        return tok

    def error(self, expected: str):
        tok = self.peek()
        if tok.kind is TokenKind.EOF:
            got = "end of input"
        else:
            got = f"{tok.kind.value} {tok.text!r}"
        raise ParseError(f"parser: expected {expected} at position {tok.pos}, got {got}")

    def expect(self, kind: TokenKind, expected: str) -> Token:
        if self.peek().kind is not kind:
            self.error(expected)
        return self.advance()

    def at_keyword(self, word: str) -> bool:
        tok = self.peek()
        return tok.kind is TokenKind.KEYWORD and tok.text == word

    def expect_keyword(self, word: str) -> Token:
        if not self.at_keyword(word):
            self.error(word)
        return self.advance()

    # -- po jedna funkcija po gramatickom pravilu -----------------------------

    def parse_query(self) -> Query:
        self.expect_keyword("SELECT")
        select_star = False
        select: tuple = ()
        if self.peek().kind is TokenKind.STAR:
            self.advance()
            select_star = True
        else:
            select = self.parse_select_list()
        self.expect_keyword("FROM")
        tables = self.parse_table_list()

        where: tuple = ()
        if self.at_keyword("WHERE"):
            self.advance()
            where = self.parse_where()

        order_by = None
        if self.at_keyword("ORDER"):
            self.advance()
            order_by = self.parse_order_by()

        if self.peek().kind is TokenKind.SEMICOLON:
            self.advance()
        if self.peek().kind is not TokenKind.EOF:
            self.error("end of query")

        return Query(
            select=select, tables=tables, where=where, order_by=order_by, select_star=select_star
        )

    def parse_select_list(self) -> tuple:
        attrs = [self._parse_attr()]
        while self.peek().kind is TokenKind.COMMA:
            self.advance()
            attrs.append(self._parse_attr())
        return tuple(attrs)

    def parse_table_list(self) -> tuple:
        tables = [self._parse_table_ref()]
        while self.peek().kind is TokenKind.COMMA:
            self.advance()
            tables.append(self._parse_table_ref())
        # limit posle cele liste, bolja poruka nego pucanje na 5. tabeli
        if len(tables) > MAX_TABLES:
            raise ParseError(
                f"parser: at most {MAX_TABLES} tables allowed, got {len(tables)}"
            )
        return tuple(tables)

    def _parse_table_ref(self) -> TableRef:
        name = self.expect(TokenKind.IDENT, "table name").text
        alias = None
        if self.peek().kind is TokenKind.IDENT:  # FROM Student s (bez AS)
            alias_tok = self.advance()
            if alias_tok.text == name:
                raise ParseError(
                    f"parser: alias must differ from table name at position "
                    f"{alias_tok.pos}, got {alias_tok.text!r}"
                )
            alias = alias_tok.text
        return TableRef(name=name, alias=alias)

    def parse_where(self) -> tuple:
        conditions = [self.parse_condition()]
        while self.at_keyword("AND"):
            self.advance()
            conditions.append(self.parse_condition())
        if self.at_keyword("OR"):
            # disjunkcija je van obima postavke, ciljana poruka umesto
            # genericke "expected end of query"
            raise ParseError(
                f"parser: OR is not supported (WHERE must be a conjunction "
                f"of AND conditions) at position {self.peek().pos}"
            )
        if len(conditions) > MAX_CONDITIONS:
            raise ParseError(
                f"parser: at most {MAX_CONDITIONS} WHERE conditions allowed, "
                f"got {len(conditions)}"
            )
        return tuple(conditions)

    def parse_condition(self) -> Condition:
        left = self._parse_attr()
        op = self.expect(TokenKind.OP, "comparison operator").text
        right = self._parse_literal_or_attr()
        return Condition(left, op, right)

    def parse_order_by(self) -> AttrRef:
        # ORDER je vec pojeden u parse_query
        self.expect_keyword("BY")
        return self._parse_attr()

    # -- privatni delovi pravila ----------------------------------------------

    def _parse_attr(self) -> AttrRef:
        first = self.expect(TokenKind.IDENT, "attribute name").text
        if self.peek().kind is TokenKind.DOT:
            self.advance()
            second = self.expect(TokenKind.IDENT, "attribute name").text
            return AttrRef(name=second, table=first)
        return AttrRef(name=first)

    def _parse_literal(self) -> Literal:
        tok = self.peek()
        if tok.kind is TokenKind.NUMBER:
            self.advance()
            if "." in tok.text:
                # string->float ne baca izuzetak nego saturira u inf, a ast sme
                # da nosi samo konacne vrednosti
                value = float(tok.text)
                if not math.isfinite(value):
                    raise ParseError(
                        f"parser: numeric literal is not finite "
                        f"(not supported) at position {tok.pos}"
                    )
            else:
                value = int(tok.text)
            return Literal(value, "number")
        if tok.kind is TokenKind.STRING:
            self.advance()
            return Literal(tok.text, "string")
        self.error("literal")

    def _parse_literal_or_attr(self):
        kind = self.peek().kind
        if kind is TokenKind.IDENT:
            return self._parse_attr()
        if kind is TokenKind.NUMBER or kind is TokenKind.STRING:
            return self._parse_literal()
        # ovde prirodno pada i '<>': posle pojedenog '<' sledi OP '>'
        self.error("attribute or literal")
