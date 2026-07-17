import math

# eksterno obedinjeno sortiranje (predavanje bp2 5, slajd 15): br blokova ulaza, M bafera
# faza 0 (kreiranje nizova): 2*br (citanje + pisanje pocetnih sortiranih nizova)
# svaki prolaz spajanja: 2*br (citanje + pisanje), osim poslednjeg: njegov izlaz se
# ne pise na disk nego prosledjuje sledecoj operaciji (isto vazi za sve operacije)
# ukupno = br*(2*broj_prolaza + 1)


def sort_cost(n_blocks: int, buffer_blocks: int) -> int:
    initial_runs = math.ceil(n_blocks / buffer_blocks)
    merge_factor = buffer_blocks - 1
    passes = math.ceil(round(math.log(initial_runs, merge_factor), 9)) if initial_runs > 1 else 0
    return n_blocks * (2 * passes + 1)
