from dataclasses import dataclass

# AST verno beleži šta piše u upitu — klasifikaciju uslova (selekcija vs join)
# i razrešavanje imena radi semantika, ne parser.

COMPARISON_OPS = {"=", "<", ">", "<=", ">="}
LITERAL_KINDS = {"number", "string"}


@dataclass(frozen=True)
class AttrRef:
    name: str
    table: str | None = None  # kvalifikator iz upita (Student.ime); None ako nije pisan


@dataclass(frozen=True)
class Literal:
    value: object  # int | float za number, str za string
    kind: str      # "number" | "string" — semantika koristi za proveru tipova

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
class Disjunction:
    # samo poređenja unutar zagrada — po gramatici tu nema ugnježdene konjunkcije
    conditions: tuple[Condition, ...]

    def __post_init__(self):
        if not self.conditions:
            raise ValueError("disjunction must have at least one condition")


@dataclass(frozen=True)
class Query:
    select: tuple[AttrRef, ...]
    tables: tuple[str, ...]
    where: "tuple[Condition | Disjunction, ...]" = ()  # implicitna konjunkcija članova
    order_by: AttrRef | None = None

    def __post_init__(self):
        if not self.select:
            raise ValueError("query must select at least one attribute")
        if not self.tables:
            raise ValueError("query must reference at least one table")
