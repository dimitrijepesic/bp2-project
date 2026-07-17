from src.cost.sort import sort_cost


def test_fits_in_buffer_single_pass():
    # ceo ulaz staje u M blokova bafera -> 0 prolaza spajanja, samo citanje (izlaz ide dalje)
    assert sort_cost(5, 10) == 5
    assert sort_cost(10, 10) == 10


def test_one_merge_pass():
    # initial_runs = ceil(100/11) = 10, merge_factor = 10 -> log_10(10) = 1 prolaz
    # 100*(2*1+1) = 300
    assert sort_cost(100, 11) == 300


def test_two_merge_passes():
    # initial_runs = ceil(1000/11) = 91, merge_factor = 10 -> log_10(91) ~ 1.96 -> 2 prolaza
    # 1000*(2*2+1) = 5000
    assert sort_cost(1000, 11) == 5000


def test_small_merge_factor_needs_more_passes():
    # buffer=3 -> merge_factor=2, initial_runs = ceil(100/3) = 34 -> log2(34) ~ 5.09 -> 6 prolaza
    # 100*(2*6+1) = 1300
    assert sort_cost(100, 3) == 1300


def test_single_block_input():
    assert sort_cost(1, 5) == 1


def test_more_buffer_reduces_or_keeps_cost():
    small_buffer = sort_cost(1000, 5)
    large_buffer = sort_cost(1000, 50)
    assert large_buffer <= small_buffer


def test_zero_blocks_costs_nothing():
    assert sort_cost(0, 10) == 0


def test_buffer_boundary_b_equals_m_and_m_plus_one():
    assert sort_cost(10, 10) == 10  # b=M: jedan run, samo citanje (0 prolaza)
    assert sort_cost(11, 10) == 33  # b=M+1: 2 runa -> 1 prolaz -> 3b


def test_merge_pass_boundaries_small_fanin():
    # M=3 -> fan-in 2
    assert sort_cost(4, 3) == 4 * 3    # runs=2 -> 1 prolaz
    assert sort_cost(7, 3) == 7 * 5    # runs=3 -> 2 prolaza
    assert sort_cost(12, 3) == 12 * 5  # runs=4=2^2 -> tacno 2 prolaza (bez off-by-one)
    assert sort_cost(13, 3) == 13 * 7  # runs=5 -> 3 prolaza


def test_merge_pass_boundaries_runs_power_of_fanin():
    # M=10 -> fan-in 9: broj runova tacno stepen fan-ina ne sme dodati prolaz
    assert sort_cost(90, 10) == 90 * 3    # runs=9=9^1 -> 1 prolaz
    assert sort_cost(810, 10) == 810 * 5  # runs=81=9^2 -> 2 prolaza
    # neposredno iznad stepena: sledeci ceo prolaz
    assert sort_cost(91, 10) == 91 * 5    # runs=10 -> 2 prolaza
    assert sort_cost(901, 10) == 901 * 7  # runs=91 -> 3 prolaza
