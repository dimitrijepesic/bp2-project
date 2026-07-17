# BP2 - mini optimizator SQL upita

Projekat za predmet Baze podataka 2. Ulaz: SQL upit + JSON katalog šeme sa
statistikama. Izlaz: najbolji plan evaluacije sa cenom u blok transferima po
operaciji. Model evaluacije: pipelining sa materijalizacijom po potrebi -
rezultat operacije koji staje u bafer (M) ostaje u memoriji i sledeća operacija
ga čita besplatno; upis i ponovno čitanje se plaćaju samo kad rezultat ne staje
u M (ista metodologija kao u profesorovim rešenjima ispitnih rokova). Formule
cene: Silberschatz pogl. 15-16, predavanja BP2 5 i 6.

Python, bez zavisnosti (samo pytest za testove).

## Obim prema postavci

Originalna postavka zadatka je autoritativna. Obavezno: do 4 tabele bez
podupita, SELECT lista samo od atributa, FROM kroz zareze (bez JOIN...ON),
WHERE kao konjunkcija do 6 uslova, ORDER BY sa najviše jednim atributom, bez
GROUP BY; algoritmi selekcije (jednakost/poređenje/složeni uslovi), B+ i heš
indeksi, spoljno sortiranje, spajanja (ugnežđena/blok-ugnežđena/indeks-ugnežđena
petlja, merge, heš), projekcija i evaluacija materijalizacijom, sa cenom u blok
transferima po operaciji. Precrtano u postavci (nije zahtev): provera
sintakse/semantike upita, spajanja sa konjunkcijom i disjunkcijom, agregacije i
skupovne operacije.

Dodato preko postavke (ne menja obavezne rezultate): parser i semantički
analizator sa preciznim porukama grešaka, validacije kataloga u loader-u,
`SELECT *`, alijasi tabela, više predikata u dozvoljenoj WHERE konjunkciji i
nejednakosni attr-op-attr join uslov sa konvencionalnom selektivnošću 0.5
(za taj slučaj u materijalima nema formule).

Van obima: OR/disjunkcija (WHERE je po postavci isključivo konjunkcija, OR se
odbija jasnom porukom), agregacije, GROUP BY/HAVING, DISTINCT, skupovne
operacije, podupiti, JOIN ... ON, izrazi u SELECT listi, operatori <> i !=,
"interesting orders" (klaster indeks koji daje već sortiran izlaz) i
peak-memory/pipeline optimizacije.

## Pokretanje testova

```
pytest tests/ -v
```

Trenutno: 317 testova.

## Pokretanje programa

```
python main.py <katalog.json> "<SQL upit>"
python main.py <katalog.json> <upit.sql>
```

Ispisuje odabrani plan evaluacije (stablo operacija sa algoritmom i cenom po
operaciji) i ukupnu cenu u blok transferima. Primer:

```
python main.py tests/fixtures/primer_ulaza.json "SELECT ime FROM Student WHERE indeks = '2020/1234'"
```

## Delovi sistema

- **Katalog** (`src/catalog/`) - model šeme (frozen dataclass) i JSON loader sa
  8 validacija (konzistentnost broja blokova, distinct/unique, indeksi...).
- **Index matcher** (`src/cost/index_matcher.py`) - odlučuje da li indeks
  pokriva date selekcione uslove: B+ stablo po prefix pravilu (jednakosti,
  poslednji uslov sme biti opseg) uz dozvoljeno ne-prefiks poklapanje jednog
  atributa kad prefiks ništa ne pokrije (kao u profesorovom rešenju roka 2025);
  heš samo potpuno poklapanje jednakostima. Vraća pokrivene i preostale uslove.
- **SQL parser** (`src/sqlparser/`) - tokenizer (pozicije u porukama grešaka),
  frozen AST i recursive descent parser za podskup SQL-a:
  `SELECT ... FROM ... [WHERE ...] [ORDER BY ...]`, do 4 tabele i 6
  konjunktivnih (AND) uslova u WHERE. `SELECT *` je podržan, semantika ga širi
  u sve atribute svih FROM tabela.
- **Semantika** (`src/semantic/analyzer.py`) - razrešava kvalifikovane i
  nekvalifikovane atribute iz AST-a u konkretnu (tabela, atribut) referencu
  prema katalogu (greška na nepoznat/dvosmislen atribut ili tabelu van FROM),
  klasifikuje svaki WHERE uslov kao `SelectionPredicate` (jedna tabela) ili
  `JoinPredicate` (attr op attr različitih tabela), i proverava
  kompatibilnost tipova (broj/tekst) između literala i atributa.
- **Cost modeli** (`src/cost/`):
  - `selectivity.py` - jednakost = 1/V(A,r); opseg = interpolaciona formula
    `(max-v)/(max-min)` odnosno `(v-min)/(max-min)` kada atribut u katalogu ima
    opcione `minValue`/`maxValue` statistike (samo numerički tipovi), inače
    podrazumevanih 0.5; konjunkcija uz pretpostavku nezavisnosti uslova.
  - `selection.py` - bira najjeftiniji pristup tabeli između algoritama
    A1-A9 sa predavanja ("Obrada upita", slajd 5-9): A1 pun scan, A2/A3
    klaster indeks jednakost (key/nonkey), A4 sekundarni indeks jednakost,
    A5/A6 klaster/sekundarni poređenje (opseg), A8 kompozitni indeks, A9
    presek preko 2+ nezavisnih indeksa. A7 nema svoju formulu, ostvaruje se
    automatski kad se poklopi samo jedan uslov, a ostatak se besplatno
    filtrira u memoriji. Vraća algoritam, imena korišćenih indeksa, cenu u
    blokovima i procenu veličine rezultata.
  - `sort.py` - cena eksternog obedinjenog sortiranja, br*(2*broj_prolaza+1).
  - `join.py` - 5 algoritama (ugnježđena petlja, blok ugnježđena petlja,
    indeks ugnježđena petlja, obedinjeno spajanje, heš spajanje) plus
    `estimate_join_rows` - procena veličine izlaza jednakosnog spajanja.
  - `misc.py` - cena projekcije (čitanje ulaza samo ako nije u memoriji; bag
    semantika bez dedup-a) i materijalizacije rezultata koji ne staje u bafer.
- **Optimizator** (`src/optimizer/`, plan u `src/plan/nodes.py`):
  - `src/plan/nodes.py` - stablo plana (`SelectionNode`, `JoinNode`,
    `ProjectionNode`, `SortNode`); svaki čvor nosi `in_memory` (rezultat staje
    u bafer i ostaje u memoriji) i `materialize` (mora na disk). `total_cost()`
    hoda po stablu dodajući cenu upisa samo za decu koja se materijalizuju
    (koren se nikad ne materijalizuje, izlaz ide direktno pozivaocu).
  - `enumerator.py` - `find_best_join_plan`: dinamičko programiranje po
    podskupovima FROM tabela (Optimizacija upita, slajd 17-19), iscrpna
    pretraga svih podela podskupa na dve strane (FROM ima najviše 4 tabele,
    pa je to jeftino). Za svaki par isprobava sve dostupne join algoritme
    (ugnježđena/blok ugnježđena petlja uvek; indeks ugnježđena petlja samo
    kad je unutrašnja strana jedna bazna tabela sa indeksom, njena eventualna
    selekcija se tada apsorbuje i filtrira besplatno u memoriji;
    sort-merge/heš samo uz jednakosni predikat; spajanje u memoriji za 0 kad
    oba ulaza staju u bafer) i bira najjeftiniji, uz `estimate_join_rows` za
    procenu veličine rezultata. Tabela bez WHERE uslova se ne skenira
    unapred, čita je direktno operacija koja je konzumira.
  - `planner.py` - `build_plan`: poziva enumerator za stablo spajanja, pa
    dodaje `ProjectionNode` (SELECT lista) i `SortNode` (ako postoji
    ORDER BY).
- **Printer** (`src/plan/printer.py`):
  - `format_plan(plan) -> str` - uvučeno stablo, jedna linija po operaciji sa
    čitljivim nazivom algoritma (npr. "A2 grupišući indeks, jednakost po
    ključu", "heš spajanje"), cenom te operacije i procenom veličine izlaza;
    na kraju ukupna cena celog plana (`total_cost`).
  - `print_plan(plan) -> None` - direktno printa `format_plan`.

Svih 6 celina je povezano preko `main.py` u pokretljiv program.

## Provera protiv primera sa predavanja

`tests/test_slide_example.py` - rađeni primer sa predavanja "Optimizacija
upita" (slajd 32-35, Profesor/Predaje spajanje) prepisan u naš katalog/upit
format. Optimizator bira isti plan kao Plan 2 sa slajda: selekcija
Profesor = 6 (identično), Predaje se ne skenira već joj se pristupa kroz
Hash(SifPro) u indeks ugnježđenoj petlji, a filter `Semestar` se primenjuje
besplatno u memoriji posle spajanja. Spajanje 20*(1,2+10) = 224 i ukupno
230, identično slajdu: prosečan pristup heš korpi računa se kao 1,2
(konstanta `HASH_ACCESS_COST` u `src/cost/selection.py`, ista konvencija
kao slajdovo "1,2*20").

## Provera protiv ispitnih rokova

Tri roka rešena na času rekonstruisana su kao fixture + test:
`tests/test_zadatak_radi.py` (~2023, Ivan/Procenat>99),
`tests/test_zadatak_radi_2024.py` (2024, Nenad/IDPro=5001) i
`tests/test_zadatak_osoba_automobil.py` (2025 sept, Osoba/Koristi/Automobil),
plus puna "siwiki" postavka roka 2024 sa kompozitnim klaster indeksom
(IDPro, IDZap): `tests/test_zadatak_radi_petar.py`.

Rok 2024 se poklapa identično: 19 (7 + 12 + 0, i Petar varijanta 19). Rok
~2023 i rok 2025 poklapaju se do heš konvencije: profesor u rokovima pristup
heš korpi računa kao 1, mi kao 1,2 (`HASH_ACCESS_COST`, konvencija sa
slajda). Rok ~2023 = 18.2 kod nas naspram rokovih 18 (razlika je tačno 0,2
heš korpe); rok 2025 = 264 identično (selekcija je pun scan za 2 umesto
rokovog heša za 2 - ista cena, drugačiji algoritam, jer heš sa 1,2 košta
2,2). Procene broja redova i redosled spajanja koji optimizator bira
odgovaraju rešenjima sa časa korak po korak.

## Provera protiv examples/ foldera

Sva 22 upita iz `examples/queries/` se izvršavaju nad `examples/input.json`.
Optimizator prati formule i metodologiju profesorovih rešenja rokova (gore),
pa se od referentnog `examples/results.txt` razlikuje na četiri
dokumentovana načina (razlike u konvencijama, ne greške):

1. referenca naplaćuje upis konačnog rezultata na disk; kod nas se koren
   plana nikad ne materijalizuje (kao u rokovima), pa smo tipično jeftiniji
   za tačno `output_blocks`;
2. referenca računa pristup heš korpi kao 1, mi kao 1,2 (konvencija sa
   slajda, vidi rokove gore);
3. referenca koristi "interesting orders" (klaster indeks daje sortiran
   izlaz, npr. simple_08: 200 naspram naših 700 eksternim sortiranjem), što
   je van obima ovog projekta;
4. referenca ne naplaćuje ponovno čitanje materijalizovanog ulaza projekcije
   (simple_07: 934 naspram naših 1068).

Seek-cost kolonu reference ne računamo (cena je isključivo broj blok
transfera).

## Primer ulaza

Katalog: `tests/fixtures/primer_ulaza.json` (4 tabele, bufferBlocks=10).
Upiti: `tests/fixtures/upiti/*.sql` - `q*.sql` i `bad_*.sql` su interni test
fixture-i; `slajd_profesor_*.sql` i `zadatak_*.sql` su rekonstrukcije
profesorskih primera (slajd/rokovi); zvanični primeri profesora su u
`examples/` i ne menjaju se.
