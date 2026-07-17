from src.catalog.model import Catalog
from src.cost.misc import projection_cost
from src.cost.sort import sort_cost
from src.optimizer.enumerator import find_best_join_plan
from src.plan.nodes import ProjectionNode, SortNode
from src.semantic.analyzer import ResolvedQuery


def build_plan(resolved_query: ResolvedQuery, catalog: Catalog) -> "ProjectionNode | SortNode":
    buffer_blocks = catalog.buffer_blocks
    join_tree = find_best_join_plan(resolved_query.tables, resolved_query.where, catalog, buffer_blocks)

    select_attributes = tuple(f"{a.table}.{a.attribute.name}" for a in resolved_query.select)
    order_by = resolved_query.order_by

    # order by kljuc van select liste (poredi se semanticki identitet
    # ResolvedAttr-a, tabela + atribut, ne tekst/alias): sortira se pre
    # zavrsne projekcije, inace bi se sortiralo po atributu koji je
    # projekcija vec izbacila (fizicki neizvrsivo). ukupna cena je ista u
    # oba redosleda jer projekcija ne menja broj blokova (nema modela
    # sirine sloga), samo se write+read par premesta sa izlaza projekcije
    # na izlaz sortiranja
    if order_by is not None and order_by not in resolved_query.select:
        sort_in_memory = join_tree.output_blocks <= buffer_blocks
        sorted_tree = SortNode(
            input=join_tree,
            attribute=f"{order_by.table}.{order_by.attribute.name}",
            cost_blocks=0 if join_tree.in_memory else sort_cost(join_tree.output_blocks, buffer_blocks),
            output_rows=join_tree.output_rows,
            output_blocks=join_tree.output_blocks,
            rows_per_block=join_tree.rows_per_block,
            in_memory=sort_in_memory,
            materialize=not sort_in_memory,
        )
        projection_in_memory = sorted_tree.output_blocks <= buffer_blocks
        return ProjectionNode(
            input=sorted_tree,
            attributes=select_attributes,
            cost_blocks=projection_cost(sorted_tree.output_blocks, sorted_tree.in_memory),
            output_rows=sorted_tree.output_rows,
            output_blocks=sorted_tree.output_blocks,
            rows_per_block=sorted_tree.rows_per_block,
            in_memory=projection_in_memory,
            materialize=not projection_in_memory,
        )

    # projekcija ne menja broj redova (bag semantika, bez dedup-a) pa izlaz staje u
    # memoriju tacno kad staje i ulaz; ulaz koji je vec u baferu se ne cita ponovo
    projection_in_memory = join_tree.output_blocks <= buffer_blocks
    plan: "ProjectionNode | SortNode" = ProjectionNode(
        input=join_tree,
        attributes=select_attributes,
        cost_blocks=projection_cost(join_tree.output_blocks, join_tree.in_memory),
        output_rows=join_tree.output_rows,
        output_blocks=join_tree.output_blocks,
        rows_per_block=join_tree.rows_per_block,
        in_memory=projection_in_memory,
        materialize=not projection_in_memory,
    )

    if order_by is not None:
        # kljuc sortiranja je u select listi pa je sort koren plana: ulaz
        # koji je vec u memoriji sortira se u memoriji (0 blok transfera),
        # inace eksterno sortiranje koje ukljucuje i citanje
        # materijalizovanog ulaza
        sort_blocks = 0 if plan.in_memory else sort_cost(plan.output_blocks, buffer_blocks)
        plan = SortNode(
            input=plan,
            attribute=f"{order_by.table}.{order_by.attribute.name}",
            cost_blocks=sort_blocks,
            output_rows=plan.output_rows,
            output_blocks=plan.output_blocks,
            rows_per_block=plan.rows_per_block,
            in_memory=plan.in_memory,
            materialize=not plan.in_memory,
        )

    return plan
