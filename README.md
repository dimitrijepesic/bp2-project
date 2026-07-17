# Mini SQL query optimizer

Input: a SQL query and a JSON catalog with
schema statistics. Output: the cheapest evaluation plan, printed as an
operation tree with the chosen algorithm and cost (in block transfers) per
operation.

Supported SQL subset: `SELECT` attributes (or `*`) from up to 4 tables,
`WHERE` as a conjunction of up to 6 conditions, `ORDER BY` one attribute.

Python, no dependencies (pytest only for tests).

## Usage

```
python main.py <catalog.json> "<SQL query>"
python main.py <catalog.json> <query.sql>
```

Example:

```
python main.py tests/fixtures/primer_ulaza.json "SELECT ime FROM Student WHERE indeks = '2020/1234'"
```

## Tests

```
pytest tests/ -v
```

## Layout

- `src/catalog/` - schema model and JSON catalog loader
- `src/sqlparser/` - tokenizer, AST and recursive descent parser
- `src/semantic/` - name resolution, selection vs join classification, type checks
- `src/cost/` - cost formulas: selectivity, selection algorithms A1-A9, external sort, joins
- `src/optimizer/` - dynamic programming join enumeration and planner
- `src/plan/` - plan tree and printer
- `main.py` - CLI entry point

Notes on scope, conventions and verification against course materials (lecture
slide example, exam solutions, official examples folder): [NOTES.md](NOTES.md).
