import math

# projekcija: select je bag semantika, bez eliminacije duplikata, pa je cena samo
# jedno citanje ulaza (nema dodatnog sortiranja/hesiranja radi dedup-a)


def stable_ceil(value: float) -> int:
    # round(9) pre ceil, isto kao u join.py i sort.py: binarna greska proizvoda
    # selektivnosti (npr. 0.1*0.1 = 0.010000000000000002) gurne matematicki ceo
    # rezultat tik iznad celog broja pa bi go ceil pogresno dodao 1 blok;
    # 9 decimala proguta epsilon a ne sakrije stvarno naceti blok
    return math.ceil(round(value, 9))


def projection_cost(input_blocks: int, input_in_memory: bool = False) -> int:
    # ulaz koji je vec u memoriji (pipelining) se ne cita ponovo -> cena 0
    return 0 if input_in_memory else input_blocks


def output_blocks(n_rows: float, rows_per_block: int) -> int:
    # broj redova (moze biti procena, necelobrojna) -> broj blokova za smestaj rezultata
    return stable_ceil(n_rows / rows_per_block)


def materialization_cost(blocks: int) -> int:
    # evaluacija materijalizacijom: rezultat svake operacije se pise na disk
    # da bi ga sledeca operacija u planu mogla procitati
    return blocks
