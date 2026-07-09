from dataclasses import dataclass
from enum import Enum


class ParseError(Exception):
    """Greska u tokenizaciji/parsiranju SQL upita (koristi je ceo sqlparser paket)."""


class TokenKind(Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OP"
    COMMA = "COMMA"
    DOT = "DOT"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    SEMICOLON = "SEMICOLON"
    EOF = "EOF"


KEYWORDS = {"SELECT", "FROM", "WHERE", "AND", "OR", "ORDER", "BY"}

PUNCT = {
    ",": TokenKind.COMMA,
    ".": TokenKind.DOT,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    ";": TokenKind.SEMICOLON,
}


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str  # keyword normalizovan na velika slova; string bez navodnika; ostalo doslovno
    pos: int   # 1-bazirana kolona u ulaznom stringu


def _error(msg: str, pos: int):
    raise ParseError(f"tokenizer: {msg} at position {pos}")


# eksplicitno ASCII (isalpha/isdigit bi propustili unicode)
def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"


def _is_ident_start(c: str) -> bool:
    return c == "_" or "a" <= c <= "z" or "A" <= c <= "Z"


def _is_ident_char(c: str) -> bool:
    return _is_ident_start(c) or _is_digit(c)


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(sql)

    while i < n:
        c = sql[i]
        if c in " \t\r\n":
            i += 1
            continue
        pos = i + 1

        if _is_ident_start(c):
            j = i + 1
            while j < n and _is_ident_char(sql[j]):
                j += 1
            word = sql[i:j]
            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token(TokenKind.KEYWORD, upper, pos))
            else:
                tokens.append(Token(TokenKind.IDENT, word, pos))
            i = j

        elif _is_digit(c):
            j = i + 1
            while j < n and _is_digit(sql[j]):
                j += 1
            # decimalna tacka zahteva cifre sa obe strane ('3.' je greska)
            if j < n and sql[j] == ".":
                if j + 1 >= n or not _is_digit(sql[j + 1]):
                    _error(f"invalid number {sql[i:j + 1]!r} "
                           f"(digits required after decimal point)", pos)
                j += 2
                while j < n and _is_digit(sql[j]):
                    j += 1
            # '123abc' i '1.2.3' su tvrde greske, ne dva tokena
            if j < n and (_is_ident_char(sql[j]) or sql[j] == "."):
                _error(f"invalid number starting with {sql[i:j + 1]!r}", pos)
            tokens.append(Token(TokenKind.NUMBER, sql[i:j], pos))
            i = j

        elif c == "'":
            end = sql.find("'", i + 1)
            if end == -1:
                _error("unterminated string literal", pos)
            tokens.append(Token(TokenKind.STRING, sql[i + 1:end], pos))
            i = end + 1

        elif c in "<>":
            if i + 1 < n and sql[i + 1] == "=":
                tokens.append(Token(TokenKind.OP, c + "=", pos))
                i += 2
            else:
                tokens.append(Token(TokenKind.OP, c, pos))
                i += 1

        elif c == "=":
            tokens.append(Token(TokenKind.OP, "=", pos))
            i += 1

        elif c in PUNCT:
            tokens.append(Token(PUNCT[c], c, pos))
            i += 1

        else:
            _error(f"unexpected character {c!r}", pos)

    tokens.append(Token(TokenKind.EOF, "", n + 1))
    return tokens
