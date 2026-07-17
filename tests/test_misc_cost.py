from src.cost.misc import materialization_cost, output_blocks, projection_cost, stable_ceil


def test_projection_cost_is_single_pass_over_input():
    assert projection_cost(100) == 100


def test_output_blocks_rounds_up():
    assert output_blocks(95, 10) == 10
    assert output_blocks(100, 10) == 10
    assert output_blocks(1, 10) == 1


def test_output_blocks_handles_fractional_row_estimate():
    assert output_blocks(16.6667, 10) == 2


def test_materialization_cost_is_pass_through():
    assert materialization_cost(42) == 42


def test_stable_ceil_boundaries():
    assert stable_ceil(0.0) == 0                    # nula ostaje nula
    assert stable_ceil(10.0) == 10                  # tacno ceo broj
    assert stable_ceil(10.000000000000002) == 10    # binarni epsilon, ne 11
    assert stable_ceil(9.9999999999) == 10          # tik ispod celog
    assert stable_ceil(10.0001) == 11               # stvarno naceti blok se ne krije
    assert stable_ceil(10.5) == 11


def test_output_blocks_epsilon_not_overcounted():
    # 10.000000000000002 reda u blokovima od 10: matematicki tacno 1 blok, ne 2
    assert output_blocks(10.000000000000002, 10) == 1
    assert output_blocks(0.0, 10) == 0
    assert output_blocks(101.0, 10) == 11
