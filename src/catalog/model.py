from dataclasses import dataclass
from typing import Literal

class CatalogError(Exception):
    pass

@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    unique: bool
    distinct_values: int

@dataclass(frozen=True)
class Index:
    name: str
    attributes: tuple[str,...]
    kind: Literal["btree", "hash"]
    clustered: bool
    tree_height: int | None

@dataclass(frozen=True)
class Table:
    name: str
    attributes: tuple[Attribute,...]
    n_rows: int
    n_blocks: int
    rows_per_block: int
    indexes: tuple[Index,...]

    def attribute(self, name: str) -> "Attribute":
        for at in self.attributes:
            if at.name == name:
                return at
        raise CatalogError(f"unknown attribute: {name!r} in table {self.name!r}")


@dataclass(frozen=True)
class Catalog:
    buffer_blocks: int
    tables: tuple[Table,...]

    def table(self, name: str) -> "Table":
        for table in self.tables:
            if table.name == name:
                return table
        raise CatalogError(f"unknown table: {name!r}")

    def attribute(self, table_name: str, attr_name: str) -> "Attribute":
        return self.table(table_name).attribute(attr_name)