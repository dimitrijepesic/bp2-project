from dataclasses import dataclass
from typing import Literal

from src.catalog.model import Index

Op = Literal["=", "<", ">", "<=", ">="]
VALID_OPS = {"=", "<", ">", "<=", ">="}
RANGE_OPERATORS = VALID_OPS - {"="}

@dataclass(frozen=True)
class SimpleCondition:
    attribute: str
    op: Op
    value: object=None
    
    def __post_init__(self):
        if self.op not in VALID_OPS:
            raise ValueError(f"unsupported operator: {self.op!r}")
@dataclass(frozen=True)
class IndexMatch:
    index:Index
    matched: tuple[SimpleCondition,...]
    remaining: tuple[SimpleCondition,...]
    last_is_range: bool

def match_index(index: Index, conditions: list[SimpleCondition]) -> IndexMatch | None:
    conditions = tuple(conditions)
    if not conditions:
        return None
    
    if index.kind == "btree":
        return match_btree(index,conditions)
    
    if index.kind == "hash":
        return match_hash(index,conditions)
    
    raise ValueError(f"unsupported index kind: {index.kind!r}")

def match_btree(index: Index, conditions: tuple[SimpleCondition,...]) -> IndexMatch | None:
    used_indexes: set[int] = set()
    covered: list[SimpleCondition] = []
    last_is_range = False

    for index_attribute in index.attributes:
        equality = find_condition(
            conditions,
            index_attribute,
            operator="=",
            excluded = used_indexes,
        )

        if equality is not None:
            pos, condition = equality
            used_indexes.add(pos)
            covered.append(condition)
            continue

        range_condition = find_range_condition(
            conditions,
            index_attribute,
            excluded = used_indexes,
        )

        if range_condition is not None:
            pos, condition = range_condition
            used_indexes.add(pos)
            covered.append(condition)
            last_is_range = True
            break

        break

    if not covered:
        return None
    
    residual = tuple(
        condition
        for pos, condition in enumerate(conditions)
        if pos not in used_indexes
    )

    return IndexMatch(
        index = index,
        matched = tuple(covered),
        remaining = residual,
        last_is_range = last_is_range,
    )

def match_hash(index: Index, conditions: tuple[SimpleCondition,...]) -> IndexMatch | None:
    used_indexes: set[int] = set()
    covered: list[SimpleCondition] = []

    for index_attribute in index.attributes:
        equality = find_condition(
            conditions,
            index_attribute,
            operator="=",
            excluded = used_indexes,
        )

        if equality is None:
            return None
        
        pos, condition = equality
        used_indexes.add(pos)
        covered.append(condition)

    residual = tuple(
        condition
        for pos, condition in enumerate(conditions)
        if pos not in used_indexes
    )

    return IndexMatch(
        index = index,
        matched = tuple(covered),
        remaining = residual,
        last_is_range = False,
    )
    

def find_condition(conditions:tuple[SimpleCondition,...], attribute: str, operator: str, excluded: set[int]) -> tuple[int, SimpleCondition] | None:
    for pos, condition in enumerate(conditions):
        if pos in excluded:
            continue
        if condition.attribute == attribute and condition.op == operator:
            return pos, condition
        
    return None


def find_range_condition(conditions: tuple[SimpleCondition, ...], attribute: str, excluded: set[int]) -> tuple[int, SimpleCondition] | None:
    for pos, condition in enumerate(conditions):
        if pos in excluded:
            continue
        if condition.attribute == attribute and condition.op in RANGE_OPERATORS:
            return pos, condition
        
    return None