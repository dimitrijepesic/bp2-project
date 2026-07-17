import json
import pytest

from src.catalog.loader import load_catalog
from src.catalog.model import CatalogError

FIXTURE = "tests/fixtures/primer_ulaza.json"


# ---------- happy path ----------
def test_load_primer():
    c = load_catalog(FIXTURE)
    assert c.buffer_blocks == 10
    assert len(c.tables) == 4
    assert c.attribute("Student", "smer").distinct_values == 5
    # kompozitni indeks: redosled atributa ocuvan
    idx = c.table("Student").indexes[1]
    assert idx.attributes == ("smer", "prosek")
    assert idx.kind == "btree"


def test_composite_index_order_preserved():
    c = load_catalog(FIXTURE)
    stip = c.table("Stipendija")
    composite = next(i for i in stip.indexes if len(i.attributes) == 2)
    assert composite.attributes == ("studentIndeks", "iznos")  # ne sortirano


# ---------- helper za pokvarene kataloge ----------
def _valid_catalog() -> dict:
    """minimalan ispravan katalog: jedna tabela, jedan atribut, bez indeksa."""
    return {
        "bufferBlocks": 10,
        "schema": {
            "tables": [
                {
                    "name": "T",
                    "rowCount": 100,
                    "blockCount": 10,
                    "rowsPerBlock": 10,
                    "attributes": [
                        {"name": "a", "type": "INT", "unique": False,
                         "distinctValues": 50},
                    ],
                    "indexes": [],
                }
            ]
        },
    }


def _write(tmp_path, data: dict) -> str:
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_valid_helper_loads(tmp_path):
    # sanity: helper sam po sebi mora da prodje, inace su ostali testovi lazni
    load_catalog(_write(tmp_path, _valid_catalog()))


# ---------- negativni testovi: po jedan za svaku validaciju ----------

def test_missing_required_field(tmp_path):
    data = _valid_catalog()
    del data["schema"]["tables"][0]["rowCount"]
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_wrong_type(tmp_path):
    data = _valid_catalog()
    data["schema"]["tables"][0]["rowCount"] = "sto"  # str umesto int
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_bool_not_accepted_as_int(tmp_path):
    data = _valid_catalog()
    data["schema"]["tables"][0]["rowCount"] = True  # bool nije validan int
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_buffer_blocks_too_small(tmp_path):  # validacija 2
    data = _valid_catalog()
    data["bufferBlocks"] = 2
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_duplicate_table_names(tmp_path):  # validacija 3 (tabele)
    data = _valid_catalog()
    data["schema"]["tables"].append(dict(data["schema"]["tables"][0]))
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_duplicate_attribute_names(tmp_path):  # validacija 3 (atributi)
    data = _valid_catalog()
    attrs = data["schema"]["tables"][0]["attributes"]
    attrs.append(dict(attrs[0]))  # isti 'a' dvaput
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_index_references_unknown_attribute(tmp_path):  # validacija 4
    data = _valid_catalog()
    data["schema"]["tables"][0]["indexes"].append({
        "name": "idx_bad",
        "attributes": ["nepostoji"],
        "type": "B_PLUS_TREE",
        "clustered": False,
        "treeHeight": 2,
    })
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_btree_missing_tree_height(tmp_path):  # validacija 5 (btree)
    data = _valid_catalog()
    data["schema"]["tables"][0]["indexes"].append({
        "name": "idx_a",
        "attributes": ["a"],
        "type": "B_PLUS_TREE",
        "clustered": False,
        # treeHeight namerno izostavljen
    })
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_hash_with_tree_height(tmp_path):  # validacija 5 (hash)
    data = _valid_catalog()
    data["schema"]["tables"][0]["indexes"].append({
        "name": "idx_a_hash",
        "attributes": ["a"],
        "type": "HASH",
        "clustered": False,
        "treeHeight": 3,  # hash ne sme imati
    })
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_block_count_inconsistent(tmp_path):  # validacija 6
    data = _valid_catalog()
    data["schema"]["tables"][0]["blockCount"] = 999  # ceil(100/10)=10
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_distinct_values_exceeds_rows(tmp_path):  # validacija 7 (gornja granica)
    data = _valid_catalog()
    data["schema"]["tables"][0]["attributes"][0]["distinctValues"] = 200  # > 100
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_unique_requires_distinct_equals_rows(tmp_path):  # validacija 7 (unique)
    data = _valid_catalog()
    a = data["schema"]["tables"][0]["attributes"][0]
    a["unique"] = True
    a["distinctValues"] = 50  # != rowCount 100
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_multiple_clustered_indexes(tmp_path):  # validacija 8
    data = _valid_catalog()
    data["schema"]["tables"][0]["indexes"] = [
        {"name": "i1", "attributes": ["a"], "type": "B_PLUS_TREE",
         "clustered": True, "treeHeight": 2},
        {"name": "i2", "attributes": ["a"], "type": "B_PLUS_TREE",
         "clustered": True, "treeHeight": 2},
    ]
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_unknown_field_rejected(tmp_path):  # detekcija typo-a
    data = _valid_catalog()
    data["schema"]["tables"][0]["clustred"] = True  # typo
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))

# ---------- minValue/maxValue (opcione statistike raspona) ----------
def test_min_max_loaded_when_given(tmp_path):
    data = _valid_catalog()
    a = data["schema"]["tables"][0]["attributes"][0]
    a["minValue"] = 0
    a["maxValue"] = 100
    c = load_catalog(_write(tmp_path, data))
    attr = c.attribute("T", "a")
    assert attr.min_value == 0
    assert attr.max_value == 100


def test_min_max_default_to_none(tmp_path):
    c = load_catalog(_write(tmp_path, _valid_catalog()))
    attr = c.attribute("T", "a")
    assert attr.min_value is None
    assert attr.max_value is None


def test_min_without_max_rejected(tmp_path):
    data = _valid_catalog()
    data["schema"]["tables"][0]["attributes"][0]["minValue"] = 0
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_min_max_on_string_attribute_rejected(tmp_path):
    data = _valid_catalog()
    a = data["schema"]["tables"][0]["attributes"][0]
    a["type"] = "STRING"
    a["minValue"] = 0
    a["maxValue"] = 100
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_min_not_below_max_rejected(tmp_path):
    data = _valid_catalog()
    a = data["schema"]["tables"][0]["attributes"][0]
    a["minValue"] = 100
    a["maxValue"] = 100
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


def test_non_numeric_min_max_rejected(tmp_path):
    data = _valid_catalog()
    a = data["schema"]["tables"][0]["attributes"][0]
    a["minValue"] = "0"
    a["maxValue"] = 100
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))


# ---------- elementi lista moraju biti JSON objekti (ne int/null/str/list/bool) ----------
# pytest.raises(CatalogError) usput garantuje da ne izleti sirovi TypeError
# (on bi propao kroz raises i oborio test)

BAD_JSON_ELEMENTS = [
    pytest.param(5, "int", id="int"),
    pytest.param(None, "null", id="null"),
    pytest.param("Student", "str", id="str"),
    pytest.param([], "list", id="list"),
    pytest.param(True, "bool", id="bool"),
]


@pytest.mark.parametrize("bad, type_name", BAD_JSON_ELEMENTS)
def test_table_element_not_object(tmp_path, bad, type_name):
    data = _valid_catalog()
    data["schema"]["tables"][0] = bad
    with pytest.raises(CatalogError) as ei:
        load_catalog(_write(tmp_path, data))
    assert f"schema.tables[0]: expected object, got {type_name}" in str(ei.value)


@pytest.mark.parametrize("bad, type_name", BAD_JSON_ELEMENTS)
def test_attribute_element_not_object(tmp_path, bad, type_name):
    data = _valid_catalog()
    data["schema"]["tables"][0]["attributes"].append(bad)  # drugi element -> [1]
    with pytest.raises(CatalogError) as ei:
        load_catalog(_write(tmp_path, data))
    assert f"table 'T'.attributes[1]: expected object, got {type_name}" in str(ei.value)


@pytest.mark.parametrize("bad, type_name", BAD_JSON_ELEMENTS)
def test_index_element_not_object(tmp_path, bad, type_name):
    data = _valid_catalog()
    data["schema"]["tables"][0]["indexes"] = [bad]
    with pytest.raises(CatalogError) as ei:
        load_catalog(_write(tmp_path, data))
    assert f"table 'T'.indexes[0]: expected object, got {type_name}" in str(ei.value)


def test_string_element_not_iterated_as_keys(tmp_path):
    # string ne sme da udje u check_unknown_keys kao skup karaktera
    data = _valid_catalog()
    data["schema"]["tables"][0] = "Student"
    with pytest.raises(CatalogError) as ei:
        load_catalog(_write(tmp_path, data))
    msg = str(ei.value)
    assert "unknown field" not in msg
    assert "'S'" not in msg  # nema pojedinacnih karaktera u poruci


# ---------- regresija: postojece ponasanje netaknuto ----------

def test_regression_minimal_catalog_still_loads(tmp_path):
    c = load_catalog(_write(tmp_path, _valid_catalog()))
    assert c.table("T").n_rows == 100


def test_regression_examples_input_loads():
    c = load_catalog("examples/input.json")
    assert len(c.tables) >= 1


def test_regression_buffer_blocks_3_accepted(tmp_path):
    data = _valid_catalog()
    data["bufferBlocks"] = 3
    assert load_catalog(_write(tmp_path, data)).buffer_blocks == 3


def test_regression_buffer_blocks_2_rejected(tmp_path):
    data = _valid_catalog()
    data["bufferBlocks"] = 2
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, data))
