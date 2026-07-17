import sys
from pathlib import Path

from src.catalog.loader import load_catalog
from src.catalog.model import CatalogError
from src.optimizer.planner import build_plan
from src.plan.printer import print_plan
from src.semantic.analyzer import SemanticError, analyze
from src.sqlparser.parser import parse
from src.sqlparser.tokenizer import ParseError


def _read_query(arg: str) -> str:
    path = Path(arg)
    if path.suffix.lower() in (".sql", ".txt") and path.is_file():
        return path.read_text(encoding="utf-8")
    return arg


def main(argv: "list[str]") -> int:
    if len(argv) != 2:
        print('Upotreba: python main.py <katalog.json> <upit.sql|.txt | "SQL upit">', file=sys.stderr)
        return 2

    catalog_path, query_arg = argv
    sql = _read_query(query_arg)

    try:
        catalog = load_catalog(catalog_path)
        query = parse(sql)
        resolved = analyze(query, catalog)
        plan = build_plan(resolved, catalog)
    except (CatalogError, ParseError, SemanticError) as e:
        print(f"Greška: {e}", file=sys.stderr)
        return 1

    print_plan(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
