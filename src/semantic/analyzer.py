from dataclasses import dataclass

from src.catalog.model import Attribute, Catalog, CatalogError
from src.sqlparser.query_ast import AttrRef, Condition, Literal, Query, TableRef

# tipovi iz kataloga (slobodan string) svrstani u dve porodice radi provere
# kompatibilnosti sa literal.kind ("number"/"string"); nepoznat tip -> preskace se provera
NUMERIC_TYPES = {"INT", "DOUBLE"}
TEXTUAL_TYPES = {"STRING", "DATE"}


class SemanticError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedAttr:
    table: str
    attribute: Attribute


@dataclass(frozen=True)
class SelectionPredicate:
    # jednotabelarni uslov: attr op literal, ili attr op attr iste tabele
    table: str
    attribute: Attribute
    op: str
    right: "Literal | ResolvedAttr"


@dataclass(frozen=True)
class JoinPredicate:
    op: str
    left: ResolvedAttr
    right: ResolvedAttr


@dataclass(frozen=True)
class ResolvedQuery:
    tables: tuple[str, ...]
    select: tuple[ResolvedAttr, ...]
    where: "tuple[SelectionPredicate | JoinPredicate, ...]"
    order_by: ResolvedAttr | None


def analyze(query: Query, catalog: Catalog) -> ResolvedQuery:
    tables, qualifiers = _validate_tables(query.tables, catalog)
    if query.select_star:
        # SELECT *: svi atributi svih FROM tabela, redosledom iz FROM pa iz kataloga
        select = tuple(
            ResolvedAttr(t, a) for t in tables for a in catalog.table(t).attributes
        )
    else:
        select = tuple(
            _resolve_attr(a, tables, qualifiers, catalog, f"SELECT, attribute {i + 1}")
            for i, a in enumerate(query.select)
        )
    where = tuple(
        _classify_condition(item, tables, qualifiers, catalog, f"WHERE, condition {i + 1}")
        for i, item in enumerate(query.where)
    )
    order_by = None
    if query.order_by is not None:
        order_by = _resolve_attr(query.order_by, tables, qualifiers, catalog, "ORDER BY")
    return ResolvedQuery(tables=tables, select=select, where=where, order_by=order_by)


def _validate_tables(
    refs: tuple[TableRef, ...], catalog: Catalog
) -> "tuple[tuple[str, ...], dict[str, str]]":
    # qualifiers: kvalifikator iz upita -> ime tabele; alijas sakriva ime tabele
    # (standardno sql ponasanje), bez alijasa kvalifikator je samo ime tabele
    names: list[str] = []
    qualifiers: dict[str, str] = {}
    for ref in refs:
        if ref.name in names:
            raise SemanticError(
                f"FROM: duplicate table {ref.name!r} (self-join nije podrzan, ni preko alijasa)"
            )
        try:
            catalog.table(ref.name)
        except CatalogError as e:
            raise SemanticError(f"FROM: {e}") from e
        qualifier = ref.alias if ref.alias is not None else ref.name
        if qualifier in qualifiers:
            raise SemanticError(
                f"FROM: kvalifikator {qualifier!r} nije jedinstven "
                f"(vec oznacava tabelu {qualifiers[qualifier]!r})"
            )
        names.append(ref.name)
        qualifiers[qualifier] = ref.name
    return tuple(names), qualifiers


def _resolve_attr(
    attr: AttrRef,
    tables: tuple[str, ...],
    qualifiers: "dict[str, str]",
    catalog: Catalog,
    ctx: str,
) -> ResolvedAttr:
    if attr.table is not None:
        table = qualifiers.get(attr.table)
        if table is None:
            hint = ""
            if attr.table in tables:  # tabela jeste u FROM, ali pod alijasom
                alias = next(q for q, t in qualifiers.items() if t == attr.table)
                hint = f" (tabela {attr.table!r} ima alijas {alias!r}, koristi alijas)"
            raise SemanticError(
                f"{ctx}: kvalifikator {attr.table!r} nije u FROM listi, "
                f"dostupni: {sorted(qualifiers)}{hint}"
            )
        try:
            attribute = catalog.attribute(table, attr.name)
        except CatalogError as e:
            raise SemanticError(f"{ctx}: {e}") from e
        return ResolvedAttr(table, attribute)

    matches = [
        (t, a)
        for t in tables
        for a in catalog.table(t).attributes
        if a.name == attr.name
    ]
    if not matches:
        raise SemanticError(
            f"{ctx}: nepoznat atribut {attr.name!r} (nije nadjen ni u jednoj FROM tabeli {tables})"
        )
    if len(matches) > 1:
        found_in = [t for t, _ in matches]
        raise SemanticError(
            f"{ctx}: dvosmislen atribut {attr.name!r}, postoji u tabelama {found_in}, "
            f"kvalifikuj sa nazivom tabele (tabela.atribut)"
        )
    table, attribute = matches[0]
    return ResolvedAttr(table, attribute)


def _literal_kind_for_type(attr_type: str) -> str | None:
    if attr_type in NUMERIC_TYPES:
        return "number"
    if attr_type in TEXTUAL_TYPES:
        return "string"
    return None


def _check_literal_type(resolved: ResolvedAttr, literal: Literal, ctx: str):
    expected = _literal_kind_for_type(resolved.attribute.type)
    if expected is not None and literal.kind != expected:
        raise SemanticError(
            f"{ctx}: tip se ne slaze: {resolved.table}.{resolved.attribute.name} "
            f"je {resolved.attribute.type}, literal je {literal.kind}"
        )


def _check_attr_pair_type(left: ResolvedAttr, right: ResolvedAttr, ctx: str):
    lk = _literal_kind_for_type(left.attribute.type)
    rk = _literal_kind_for_type(right.attribute.type)
    if lk is not None and rk is not None and lk != rk:
        raise SemanticError(
            f"{ctx}: tip se ne slaze: {left.table}.{left.attribute.name} ({left.attribute.type}) "
            f"vs {right.table}.{right.attribute.name} ({right.attribute.type})"
        )


def _classify_condition(
    cond: Condition,
    tables: tuple[str, ...],
    qualifiers: "dict[str, str]",
    catalog: Catalog,
    ctx: str,
) -> "SelectionPredicate | JoinPredicate":
    left = _resolve_attr(cond.left, tables, qualifiers, catalog, ctx)

    if isinstance(cond.right, Literal):
        _check_literal_type(left, cond.right, ctx)
        return SelectionPredicate(table=left.table, attribute=left.attribute, op=cond.op, right=cond.right)

    right = _resolve_attr(cond.right, tables, qualifiers, catalog, ctx)
    _check_attr_pair_type(left, right, ctx)
    if left.table == right.table:
        return SelectionPredicate(table=left.table, attribute=left.attribute, op=cond.op, right=right)
    return JoinPredicate(op=cond.op, left=left, right=right)
