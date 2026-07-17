from src.catalog.model import Attribute

# n_r/2 kada nema statistike o rasponu vrednosti (predavanje 6, slajd 25)
DEFAULT_RANGE_SELECTIVITY = 0.5


def equality_selectivity(attribute: Attribute) -> float:
    # unique => distinctValues == rowCount (garantovano validacijom kataloga),
    # pa formula automatski daje 1/n_r za unique atribute, bez posebnog slucaja
    return 1 / attribute.distinct_values


def range_selectivity(attribute: "Attribute | None" = None, op: "str | None" = None, value=None) -> float:
    # interpolaciona formula (predavanje 6, slajd 25 / silberschatz 16.3.2):
    # A <= v  =>  (v - min) / (max - min);  A > v  =>  (max - v) / (max - min),
    # odsecena na [0, 1]; > i >= (odnosno < i <=) se ne razlikuju, to je kontinualna
    # aproksimacija domena kakvu koristi i profesor u ispitnim resenjima
    # (npr. Procenat > 99 uz min=0, max=100 => 1/100). bez min/max statistika
    # (ili za attr op attr poredjenja) vraca se podrazumevano 1/2.
    if (
        attribute is None
        or attribute.min_value is None
        or attribute.max_value is None
        or op is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        return DEFAULT_RANGE_SELECTIVITY
    # granicni slucajevi pre deljenja: poredjenje int/float je u pythonu egzaktno
    # za svaku velicinu, a deljenje ogromnog int literala bi prelilo float
    # (sirovi OverflowError); vrednost na/van granice daje isto sto i formula
    # sa clamp-om, pa se rezultat za normalne vrednosti ne menja
    if value <= attribute.min_value:
        return 0.0 if op in ("<", "<=") else 1.0
    if value >= attribute.max_value:
        return 1.0 if op in ("<", "<=") else 0.0
    span = attribute.max_value - attribute.min_value
    if op in (">", ">="):
        fraction = (attribute.max_value - value) / span
    else:  # "<" ili "<="
        fraction = (value - attribute.min_value) / span
    return min(1.0, max(0.0, fraction))


def selectivity_for_op(op: str, attribute: Attribute, value=None) -> float:
    if op == "=":
        return equality_selectivity(attribute)
    return range_selectivity(attribute, op, value)


def conjunction_selectivity(selectivities: "list[float]") -> float:
    # pretpostavka nezavisnosti uslova (silberschatz 15.2.3)
    result = 1.0
    for s in selectivities:
        result *= s
    return result


def estimate_rows(n_rows: int, selectivity: float) -> float:
    return n_rows * selectivity
