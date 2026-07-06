

## Index matcher

**Modul:** `src/cost/index_matcher.py`

Odlučuje da li se indeks može iskoristiti za date selekcione uslove. Cost modeli selekcije ga zovu za svaki indeks tabele; vraća pokrivene uslove, preostale (za proveru nad dohvaćenim redovima) i da li je poslednji pokriveni uslov opseg ili `None` ako je indeks neupotrebljiv.

**B+ stablo: prefix pravilo:** uslovi moraju pokrivati neprekidan prefiks atributa indeksa; svi pre poslednjeg jednakošću, poslednji sme i poređenjem (poređenje prekida dalji match). Uslov van prefiksa ne kvari match, ide u preostale.

**Heš: potpuno poklapanje:** jednakost na svim atributima indeksa, poređenja se ne podržavaju.

**Pretpostavke:** više uslova na isti atribut -> jedan ulazi u match, ostali u preostale; prazna lista uslova → `None`; operatori: `=`, `<`, `>`, `<=`, `>=`.

Reference: Silberschatz pogl. 15.3 (A8), predavanja BP2 4 i 5.