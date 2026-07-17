from dataclasses import dataclass

# ast verno belezi sta pise u upitu; klasifikaciju uslova (selekcija vs join)
# i razresavanje imena radi semantika, ne parser

COMPARISON_OPS = {"=", "<", ">", "<=", ">="}
LITERAL_KINDS = {"number", "string"}


@dataclass(frozen=True)
class AttrRef:
    name: str
    table: str | None = None  # kvalifikator iz upita (Student.ime ili alijas s.ime); None ako nije pisan


@dataclass(frozen=True)
class TableRef:
    name: str
    alias: str | None = None  # FROM Student s: alijas sakriva ime tabele kao kvalifikator

    def __post_init__(self):
        if self.alias == self.name:
            raise ValueError(f"alias must differ from table name, got {self.name!r}")


@dataclass(frozen=True)
class Literal:
    value: object  # int | float za number, str za string
    kind: str      # "number" ili "string", semantika koristi za proveru tipova

    def __post_init__(self):
        if self.kind not in LITERAL_KINDS:
            raise ValueError(
                f"literal kind must be one of {sorted(LITERAL_KINDS)}, got {self.kind!r}"
            )
        if self.kind == "number" and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError(
                f"number literal must hold int/float, got {type(self.value).__name__}"
            )
        if self.kind == "string" and not isinstance(self.value, str):
            raise ValueError(
                f"string literal must hold str, got {type(self.value).__name__}"
            )


@dataclass(frozen=True)
class Condition:
    left: AttrRef
    op: str  # =, <, >, <=, >=
    right: "AttrRef | Literal"

    def __post_init__(self):
        if self.op not in COMPARISON_OPS:
            raise ValueError(
                f"op must be one of {sorted(COMPARISON_OPS)}, got {self.op!r}"
            )


@dataclass(frozen=True)
class Query:
    select: tuple[AttrRef, ...]
    tables: tuple[TableRef, ...]
    where: tuple[Condition, ...] = ()  # where je konjunkcija, clanovi vezani sa AND
    order_by: AttrRef | None = None
    select_star: bool = False  # kod SELECT * je select lista prazna, siri je semantika

    def __post_init__(self):
        if self.select_star and self.select:
            raise ValueError("SELECT * query must not also list attributes")
        if not self.select_star and not self.select:
            raise ValueError("query must select at least one attribute")
        if not self.tables:
            raise ValueError("query must reference at least one table")
