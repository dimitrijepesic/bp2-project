# BP2 — Mini optimizator SQL upita

Projekat za predmet Baze podataka 2. Ulaz: SQL upit + JSON katalog šeme sa
statistikama. Izlaz: najbolji plan evaluacije (materijalizacijom) sa cenom u
blok transferima po operaciji. Formule cene: Silberschatz pogl. 15-16,
predavanja BP2 5 i 6.

Python, bez zavisnosti — samo pytest za testove.

## Pokretanje testova

```
pytest tests/ -v
```

Trenutno: 72 testa.

## Urađeno do sad

- **Katalog** (`src/catalog/`) — model šeme (frozen dataclass) i JSON loader sa
  8 validacija (konzistentnost broja blokova, distinct/unique, indeksi...).
- **Index matcher** (`src/cost/index_matcher.py`) — odlučuje da li indeks
  pokriva date selekcione uslove: B+ stablo po prefix pravilu (jednakosti,
  poslednji uslov sme biti opseg), heš samo potpuno poklapanje jednakostima.
  Vraća pokrivene i preostale uslove.
- **SQL parser** (`src/sqlparser/`) — tokenizer (pozicije u porukama grešaka),
  frozen AST i recursive descent parser za podskup SQL-a:
  `SELECT ... FROM ... [WHERE ...] [ORDER BY ...]`, do 4 tabele i 6 uslova u
  WHERE, disjunkcija samo u zagradama kao član konjunkcije.

## Sledeće

- Semantička analiza (razrešavanje imena, selekcija vs join, provera tipova)
- Cost modeli (selekcija, join, sortiranje)
- Optimizator (nabrajanje planova) i ispis plana

## Primer ulaza

Katalog: `tests/fixtures/primer_ulaza.json` (4 tabele, bufferBlocks=10).
Upiti: `tests/fixtures/upiti/*.sql` (validni `q*.sql`, nevalidni `bad_*.sql`).
