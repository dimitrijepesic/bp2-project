import dataclasses
import itertools

from src.catalog.model import Catalog
from src.cost.join import (
    ALGO_BLOCK_NESTED_LOOP,
    ALGO_HASH_JOIN,
    ALGO_IN_MEMORY_JOIN,
    ALGO_INDEX_NESTED_LOOP,
    ALGO_NESTED_LOOP,
    ALGO_SORT_MERGE,
    block_nested_loop_cost,
    estimate_join_rows_conjunction,
    hash_join_cost,
    index_nested_loop_cost,
    nested_loop_cost,
    sort_merge_join_cost,
)
from src.cost.misc import materialization_cost, output_blocks
from src.cost.selection import ALGO_JOIN_INDEX_ACCESS, ALGO_NO_SELECTION, select_best
from src.plan.nodes import JoinNode, SelectionNode, total_cost
from src.semantic.analyzer import JoinPredicate, ResolvedAttr

# nabrajanje planova spajanja dinamickim programiranjem (optimizacija upita, slajd 17-19):
# findbestplan(S) proba sve S1 x (S-S1) podele, rekurzivno uzme najbolji plan za svaku
# stranu i najjeftiniji dostupan algoritam spajanja medju njima. from ima <= 4 tabele,
# pa je iscrpna pretraga svih 2^n-2 podela sasvim prihvatljiva.
#
# pipelining (kao u profesorovim resenjima rokova): rezultat operacije koji staje u
# bafer (output_blocks <= M) ostaje u memoriji, operacija iznad ga ne cita ponovo
# i nista se ne pise na disk; materijalizacija se placa samo kad rezultat ne staje u M.


def _selection_node(table_name: str, catalog: Catalog, where: tuple, buffer_blocks: int) -> SelectionNode:
    table = catalog.table(table_name)
    conditions = [c for c in where if getattr(c, "table", None) == table_name]
    if not conditions:
        # bez where uslova selekcija je identitet: nema posla (cena 0), tabela je vec
        # na disku (nema materijalizacije) i cita je direktno operacija iznad
        return SelectionNode(
            table=table_name,
            algorithm=ALGO_NO_SELECTION,
            index_names=(),
            cost_blocks=0,
            output_rows=float(table.n_rows),
            output_blocks=table.n_blocks,
            rows_per_block=table.rows_per_block,
            in_memory=False,
            materialize=False,
        )
    result = select_best(table, conditions)
    blocks = output_blocks(result.output_rows, table.rows_per_block)
    in_memory = blocks <= buffer_blocks
    return SelectionNode(
        table=table_name,
        algorithm=result.algorithm,
        index_names=result.index_names,
        cost_blocks=result.cost_blocks,
        output_rows=result.output_rows,
        output_blocks=blocks,
        rows_per_block=table.rows_per_block,
        in_memory=in_memory,
        materialize=not in_memory,
    )


def _connecting_predicates(where: tuple, tables1: "set[str]", tables2: "set[str]") -> "list[JoinPredicate]":
    result = []
    for c in where:
        if not isinstance(c, JoinPredicate):
            continue
        if (c.left.table in tables1 and c.right.table in tables2) or (
            c.left.table in tables2 and c.right.table in tables1
        ):
            result.append(c)
    return result


# kad se predikatu zamene strane mora se okrenuti i operator: nejednakost, za
# razliku od jednakosti, nije simetricna (B.y>A.x posle zamene postaje A.x<B.y)
_FLIP_OP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "="}


def _oriented_predicate_pairs(
    predicates: "list[JoinPredicate]", tables1: "set[str]"
) -> "list[tuple[ResolvedAttr, str, ResolvedAttr]]":
    # okrene svaki predikat izmedju dve strane tako da je levi atribut sa tables1
    # strane, a desni sa druge; pri zameni strana okrece se i operator (_FLIP_OP),
    # inace bi se A.x<B.y i B.y>A.x (isti predikat, obrnut zapis) racunali kao dva
    # razlicita. dedup ide tek posle orijentacije, po celom (levi, op, desni);
    # ResolvedAttr nosi i ime tabele pa nema mesanja izmedju tabela sa isto
    # nazvanim atributima. redosled prvog pojavljivanja je ocuvan.
    pairs = []
    for pred in predicates:
        if pred.left.table in tables1:
            pairs.append((pred.left, pred.op, pred.right))
        else:
            pairs.append((pred.right, _FLIP_OP[pred.op], pred.left))
    return list(dict.fromkeys(pairs))


def _index_nested_loop_candidates(
    catalog: Catalog,
    outer: "SelectionNode | JoinNode",
    inner_tables: "tuple[str, ...]",
    equality_preds: "list[JoinPredicate]",
) -> "list[tuple[int, str]]":
    # indeks ugnjezdena petlja zahteva da unutrasnja strana bude jedna bazna tabela:
    # indeksi postoje samo nad fizickim tabelama, ne nad medjurezultatima spajanja;
    # vraca listu (cena, ime indeksa)
    if len(inner_tables) != 1:
        return []
    inner_table_name = inner_tables[0]
    inner_table = catalog.table(inner_table_name)
    results = []
    for pred in equality_preds:
        if pred.left.table == inner_table_name:
            join_attr = pred.left.attribute
        elif pred.right.table == inner_table_name:
            join_attr = pred.right.attribute
        else:
            continue
        candidate = index_nested_loop_cost(
            outer.output_rows, outer.output_blocks, inner_table, join_attr, outer.in_memory
        )
        if candidate is not None:
            results.append(candidate)
    return results


def _charged_cost(node: "SelectionNode | JoinNode") -> int:
    # ukupna cena deteta onako kako je total_cost naplacuje roditelju:
    # njegovo podstablo + eventualni upis njegovog rezultata na disk
    return total_cost(node) + (materialization_cost(node.output_blocks) if node.materialize else 0)


def _absorb_inner(inner: SelectionNode, index_name: str) -> SelectionNode:
    # indeks ugnjezdena petlja pristupa unutrasnjoj tabeli direktno kroz indeks
    # spajanja (ta cena je u ceni spajanja), a njeni where uslovi se filtriraju
    # besplatno u memoriji nad dovucenim redovima, pa cvor gubi svoju cenu
    return dataclasses.replace(
        inner,
        algorithm=ALGO_JOIN_INDEX_ACCESS,
        index_names=(index_name,),
        cost_blocks=0,
        in_memory=True,
        materialize=False,
    )


def _best_join_node(
    catalog: Catalog,
    buffer_blocks: int,
    p1: "SelectionNode | JoinNode",
    tables1: "tuple[str, ...]",
    p2: "SelectionNode | JoinNode",
    tables2: "tuple[str, ...]",
    predicates: "list[JoinPredicate]",
) -> JoinNode:
    # kandidat = (cena za poredjenje, cena spajanja, algoritam, apsorbovana strana,
    # indeks, levo dete, desno dete). cena za poredjenje je ukupna cena plana kandidata
    # umanjena za istu konstantu (naplacene cene originalne dece), pa su kandidati
    # uporedivi i kad im se deca razlikuju: kod indeks petlje odbija naplacenu cenu
    # apsorbovanog deteta (njegova selekcija se ne izvrsava, tabeli se pristupa kroz
    # indeks), kod kandidata sa prinudnom materijalizacijom jedne strane dodaje upis
    # te strane (videti configs dole); kod ostalih je jednaka ceni spajanja
    candidates: "list[tuple[float, float, str, SelectionNode | None, str | None, SelectionNode | JoinNode, SelectionNode | JoinNode]]" = []

    both_resident = p1.in_memory and p2.in_memory
    fit_together = p1.output_blocks + p2.output_blocks <= buffer_blocks

    if both_resident and fit_together:
        # oba ulaza vec u baferu i zajedno staju u M -> spajanje u memoriji
        # bez ijednog blok transfera
        candidates.append((0, 0, ALGO_IN_MEMORY_JOIN, None, None, p1, p2))

    # konfiguracije dece za nl/bnl/sort-merge/hash kandidate: po pravilu jedna,
    # sa originalnim cvorovima. izuzetak: oba ulaza pojedinacno staju u bafer ali
    # ne mogu istovremeno biti u njemu (zbir > M), pa jedna strana mora na disk.
    # tada se prave dve konfiguracije (materijalizuj levu ili desnu stranu) sa
    # kopijama dece (dataclasses.replace, memoizovani planovi se ne diraju, kopija
    # zivi samo u ovom kandidatu) i formule dobijaju zastavice koje odgovaraju
    # kopijama; treci clan je upis koji kopija dodaje u total_cost u odnosu na
    # original (za cenu poredjenja). konfiguracija sa obe strane na disku se ne
    # pravi: placala bi dva upisa a nista ne donosi, dominirana je
    if both_resident and not fit_together:
        configs = [
            (dataclasses.replace(p1, in_memory=False, materialize=True), p2, materialization_cost(p1.output_blocks)),
            (p1, dataclasses.replace(p2, in_memory=False, materialize=True), materialization_cost(p2.output_blocks)),
        ]
    else:
        configs = [(p1, p2, 0)]

    for left, right, extra in configs:
        # ugnjezdena i blok ugnjezdena petlja rade i bez uslova spajanja (dekartov
        # proizvod); probaju se oba redosleda spoljasnja/unutrasnja, a posto oba
        # dele istu konfiguraciju dece min je bezbedan
        nl = min(
            nested_loop_cost(left.output_rows, left.output_blocks, right.output_blocks, left.in_memory, right.in_memory),
            nested_loop_cost(right.output_rows, right.output_blocks, left.output_blocks, right.in_memory, left.in_memory),
        )
        candidates.append((nl + extra, nl, ALGO_NESTED_LOOP, None, None, left, right))
        bnl = min(
            block_nested_loop_cost(left.output_blocks, right.output_blocks, buffer_blocks, left.in_memory, right.in_memory),
            block_nested_loop_cost(right.output_blocks, left.output_blocks, buffer_blocks, right.in_memory, left.in_memory),
        )
        candidates.append((bnl + extra, bnl, ALGO_BLOCK_NESTED_LOOP, None, None, left, right))

    equality_preds = [p for p in predicates if p.op == "="]
    if equality_preds:
        # indeks petlja radi nad originalnim cvorovima i u prelivnom slucaju:
        # unutrasnja strana se apsorbuje (pristup kroz indeks, ne zauzima bafer
        # kao celina), pa je bitno samo da li je spoljasnja strana u memoriji
        for cost, index_name in _index_nested_loop_candidates(catalog, p1, tables2, equality_preds):
            candidates.append((cost - _charged_cost(p2), cost, ALGO_INDEX_NESTED_LOOP, p2, index_name, p1, p2))
        for cost, index_name in _index_nested_loop_candidates(catalog, p2, tables1, equality_preds):
            candidates.append((cost - _charged_cost(p1), cost, ALGO_INDEX_NESTED_LOOP, p1, index_name, p1, p2))
        for left, right, extra in configs:
            sm = sort_merge_join_cost(
                left.output_blocks,
                right.output_blocks,
                buffer_blocks,
                r_in_memory=left.in_memory,
                s_in_memory=right.in_memory,
            )
            candidates.append((sm + extra, sm, ALGO_SORT_MERGE, None, None, left, right))
            hj = hash_join_cost(left.output_blocks, right.output_blocks, left.in_memory, right.in_memory)
            candidates.append((hj + extra, hj, ALGO_HASH_JOIN, None, None, left, right))

    _, cost, algorithm, absorbed, index_name, left, right = min(candidates, key=lambda item: item[0])

    attr_pairs = _oriented_predicate_pairs(predicates, set(tables1))
    if attr_pairs:
        output_rows = estimate_join_rows_conjunction(p1.output_rows, p2.output_rows, attr_pairs)
    else:
        output_rows = p1.output_rows * p2.output_rows  # dekartov proizvod

    if absorbed is p1:
        left = _absorb_inner(p1, index_name)
    elif absorbed is p2:
        right = _absorb_inner(p2, index_name)

    combined_tables = tuple(sorted(set(tables1) | set(tables2)))
    rows_per_block = min(p1.rows_per_block, p2.rows_per_block)
    blocks = output_blocks(output_rows, rows_per_block)
    in_memory = blocks <= buffer_blocks

    return JoinNode(
        left=left,
        right=right,
        algorithm=algorithm,
        cost_blocks=cost,
        output_rows=output_rows,
        output_blocks=blocks,
        rows_per_block=rows_per_block,
        tables=combined_tables,
        in_memory=in_memory,
        materialize=not in_memory,
    )


def find_best_join_plan(
    tables: "tuple[str, ...]", where: tuple, catalog: Catalog, buffer_blocks: int
) -> "SelectionNode | JoinNode":
    memo: "dict[tuple[str, ...], SelectionNode | JoinNode]" = {}

    def best(subset: "tuple[str, ...]") -> "SelectionNode | JoinNode":
        subset = tuple(sorted(subset))
        if subset in memo:
            return memo[subset]

        if len(subset) == 1:
            plan = _selection_node(subset[0], catalog, where, buffer_blocks)
            memo[subset] = plan
            return plan

        best_plan = None
        for r in range(1, len(subset)):
            for s1 in itertools.combinations(subset, r):
                s2 = tuple(t for t in subset if t not in s1)
                if s1 > s2:
                    continue  # (s1,s2) i (s2,s1) daju iste podskupove, racunaj jednom
                p1 = best(s1)
                p2 = best(s2)
                predicates = _connecting_predicates(where, set(s1), set(s2))
                candidate = _best_join_node(catalog, buffer_blocks, p1, s1, p2, s2, predicates)
                if best_plan is None or total_cost(candidate) < total_cost(best_plan):
                    best_plan = candidate

        memo[subset] = best_plan
        return best_plan

    return best(tuple(tables))
