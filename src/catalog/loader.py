import math
import json
from src.catalog.model import Attribute, Index, Table, Catalog, CatalogError

ATTR_KEYS = {"name", "type", "unique", "distinctValues"} # enum za sve atribute dataklase Attribute
INDEX_KEYS = {"name", "attributes", "type", "clustered", "treeHeight"} # enum za sve atribute dataklase Index
KIND_MAP = {"B_PLUS_TREE": "btree", "HASH": "hash"} # mapiranje json vrednosti
TABLE_KEYS = {"name", "rowCount", "blockCount", "rowsPerBlock", "attributes", "indexes"} # atributi u tabeli
CATALOG_KEYS = {"bufferBlocks", "schema"}
SCHEMA_KEYS = {"tables"}

# izvuci obavezno polje ili baci catalogerror sa kontekstom
def require(d: dict, key: str, expected_type: type, ctx: str):
    if key not in d:
        raise CatalogError(f"{ctx}: missing required field {key!r}")
    value = d[key]
    if expected_type is int and isinstance(value, bool):
        raise CatalogError(f"{ctx}: field {key!r} must be int, got bool")
    if not isinstance(value, expected_type):
        raise CatalogError(
            f"{ctx}: field {key!r} must be {expected_type.__name__},"
            f"got {type(value).__name__}"
        )
    return value

def check_unknown_keys(d: dict, allowed: set, ctx: str):
    unknown = set(d) - allowed
    if unknown:
        raise CatalogError(f"{ctx}: unknown field(s): {sorted(unknown)}")

def positive(value: int, name: str, ctx: str) -> int:
    if value <= 0:
        raise CatalogError(f"{ctx}: {name} must be positive, got {value}")
    return value

def parse_attribute(d: dict, ctx: str) -> Attribute:
    check_unknown_keys(d, ATTR_KEYS, ctx) # proveri da nema unknown reci
    name = require(d, "name", str, ctx)
    ctx = f"{ctx}, attribute {name!r}"
    distinct = require(d, "distinctValues", int, ctx)
    positive(distinct, "distinctValues", ctx)
    return Attribute(
        name=name,
        type=require(d, "type", str, ctx),
        unique=require(d, "unique", bool, ctx),
        distinct_values=distinct,
    )

def parse_index(d: dict, ctx: str) -> Index:
    check_unknown_keys(d, INDEX_KEYS, ctx)
    name = require(d, "name", str, ctx)
    ctx = f"{ctx}, index {name!r}"

    raw_attrs = require(d, "attributes", list, ctx)
    if not raw_attrs:
        raise CatalogError(f"{ctx}: index has empty attributes list")
    if not all(isinstance(a, str) for a in raw_attrs):
        raise CatalogError(f"{ctx}: all attributes must be strings")

    raw_type = require(d, "type", str, ctx)
    if raw_type not in KIND_MAP:
        raise CatalogError(
            f"{ctx}: type must be one of {sorted(KIND_MAP)}, got {raw_type!r}"
        )
    kind = KIND_MAP[raw_type]
    clustered = require(d, "clustered", bool, ctx)

    treeh = None
    # ovde je bitno da mora postojati za btree i >=1 a ne sme da postoji
    # za hash index
    if kind == "btree":
        treeh = require(d, "treeHeight", int, ctx)
        positive(treeh, "treeHeight", ctx)
    else:
        if "treeHeight" in d:
            raise CatalogError(f"{ctx}: hash index must not have tree height")

    return Index(
        name=name,
        attributes=tuple(raw_attrs),  # redosled OCUVAN — bitno za prefix matching
        kind=kind,
        clustered=clustered,
        tree_height=treeh,
    )

def parse_table(d: dict, ctx: str) -> Table:
    check_unknown_keys(d, TABLE_KEYS, ctx)
    name = require(d, "name", str, ctx)
    ctx = f"{ctx}, table {name!r}"

    # dodavanje atributa
    raw_attrs = require(d, "attributes", list, ctx)
    if not raw_attrs:
        raise CatalogError(f"{ctx}: table has empty attributes list")
    attributes = tuple(parse_attribute(a, ctx) for a in raw_attrs)

    attr_names = [a.name for a in attributes]
    if len(attr_names) != len(set(attr_names)):  # duplirani nazivi atributa
        dupes = sorted({n for n in attr_names if attr_names.count(n) > 1})
        raise CatalogError(f"{ctx}: duplicate attribute name(s):{dupes}")

    # statistike
    n_rows = positive(require(d, "rowCount", int, ctx), "rowCount", ctx)
    n_blocks = positive(require(d, "blockCount", int, ctx), "blockCount", ctx)
    rows_per_block = positive(require(d, "rowsPerBlock", int, ctx), "rowsPerBlock", ctx)

    # validacija za blokove
    expected = math.ceil(n_rows / rows_per_block)
    if n_blocks != expected:
        raise CatalogError(f"{ctx}: blockCount = {n_blocks} inconsistent with {expected}")

    # validacija: distinct_values <= n_rows; unique => distinct_values == n_rows
    for a in attributes:
        if a.distinct_values > n_rows:
            raise CatalogError(f"{ctx}: attribute {a.name!r} distinctValues exceeds rowCount ({a.distinct_values} vs {n_rows})")
        if a.unique and a.distinct_values != n_rows:
            raise CatalogError(
                f"{ctx}, attribute {a.name!r}: unique attribute must have "
                f"distinctValues == rowCount ({n_rows}), got {a.distinct_values}"
            )

    raw_indexes = require(d, "indexes", list, ctx)
    indexes = tuple(parse_index(i, ctx) for i in raw_indexes)

    # validacija svaki atribut indeksa u tabeli
    attr_set = set(attr_names)
    for idx in indexes:
        for a in idx.attributes:
            if a not in attr_set:
                raise CatalogError(
                    f"{ctx}, index {idx.name!r}: references unknown attribute {a!r}"
                )

    clustered_cnt = sum(1 for idx in indexes if idx.clustered)
    if clustered_cnt > 1:
        raise CatalogError(
            f"{ctx}: at most one clustered index allowed, got {clustered_cnt}"
        )

    return Table(
        name=name,
        attributes=attributes,
        n_rows=n_rows,
        n_blocks=n_blocks,
        rows_per_block=rows_per_block,
        indexes=indexes,
    )


def load_catalog(path: str) -> Catalog:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise CatalogError(f"catalog file not found: {path!r}")
    except json.JSONDecodeError as e:
        raise CatalogError(f"invalid JSON in {path!r}: {e}")

    if not isinstance(data, dict):
        raise CatalogError("catalog: root must be a JSON object")
    check_unknown_keys(data, CATALOG_KEYS, "catalog")

    # validacija 2: bufferBlocks >= 3
    buffer_blocks = require(data, "bufferBlocks", int, "catalog")
    if buffer_blocks < 3:
        raise CatalogError(
            f"catalog: bufferBlocks must be >= 3 (in+out+work), got {buffer_blocks}"
        )

    # schema.tables
    schema = require(data, "schema", dict, "catalog")
    check_unknown_keys(schema, SCHEMA_KEYS, "catalog.schema")
    raw_tables = require(schema, "tables", list, "catalog.schema")
    if not raw_tables:
        raise CatalogError("catalog.schema: no tables defined")
    tables = tuple(parse_table(t, "catalog") for t in raw_tables)

    # validacija 3: duplirani nazivi tabela
    table_names = [t.name for t in tables]
    if len(table_names) != len(set(table_names)):
        dupes = sorted({n for n in table_names if table_names.count(n) > 1})
        raise CatalogError(f"catalog: duplicate table name(s): {dupes}")

    return Catalog(buffer_blocks=buffer_blocks, tables=tables)