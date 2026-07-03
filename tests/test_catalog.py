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
    # kompozitni indeks: redosled atributa OCUVAN
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
    """Minimalan ispravan katalog — jedna tabela, jedan atribut, bez indeksa."""
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