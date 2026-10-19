import copy
import csv
import json
from pathlib import Path

import pytest

from metagen import ConfigError, load_config, write_files

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "retail_orders.json"


def write_config(tmp_path, mutate=None):
    data = json.loads(EXAMPLE.read_text())
    if mutate:
        data = copy.deepcopy(data)
        mutate(data)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def test_example_generates_all_three_files(tmp_path):
    cfg = load_config(EXAMPLE)
    written = write_files(cfg, tmp_path)
    assert [p.name for p in written] == [
        "COLUMN_DETAILS_DATA_1042.csv",
        "TABLE_DETAILS_DATA_1042.csv",
        "VIEW_DETAILS_DATA_1042.csv",
    ]


def test_every_environment_gets_every_column(tmp_path):
    cfg = load_config(EXAMPLE)
    rows = read_csv(write_files(cfg, tmp_path)[0])
    columns_per_env = sum(len(t.columns) for t in cfg.tables)
    assert len(rows) == columns_per_env * len(cfg.environments)
    assert {r["TGT_DB_NAME"] for r in rows} == {f"{e}_retail_cnsm" for e in cfg.environments}


def test_column_sequence_restarts_per_table(tmp_path):
    cfg = load_config(EXAMPLE)
    rows = read_csv(write_files(cfg, tmp_path)[0])
    orders = [r for r in rows if r["TGT_DB_NAME"] == "dev_retail_cnsm" and r["TGT_TABLE_NAME"] == "orders_daily"]
    assert [int(r["COL_SEQ_ID"]) for r in orders] == list(range(1, 8))
    assert orders[-1]["TGT_PARTITION_COL"] == "Y"


def test_table_locations_are_expanded_per_environment(tmp_path):
    cfg = load_config(EXAMPLE)
    rows = read_csv(write_files(cfg, tmp_path)[1])
    prd_orders = next(
        r for r in rows if r["TGT_DB_NAME"] == "prd_retail_cnsm" and r["TGT_TABLE_NAME"] == "orders_daily"
    )
    assert prd_orders["TGT_TABLE_LOCATION"] == "/data/prd/consumption/analytics/retail_orders/orders_daily"
    assert "{" not in "".join(r["TGT_TABLE_LOCATION"] for r in rows)


def test_views_point_at_the_same_environment(tmp_path):
    cfg = load_config(EXAMPLE)
    rows = read_csv(write_files(cfg, tmp_path)[2])
    for r in rows:
        env = r["TGT_DB_NAME"].split("_")[0]
        assert r["SRC_DB_NAME"].startswith(f"{env}_")


def test_pii_flag_is_written(tmp_path):
    cfg = load_config(EXAMPLE)
    rows = read_csv(write_files(cfg, tmp_path)[0])
    pii = {r["TGT_COL_NAME"] for r in rows if r["IS_COL_PII"] == "Y"}
    assert pii == {"email", "mobile_no"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["tables"]["orders_daily"]["columns"][0].update(type="Varchar"), "type 'Varchar'"),
        (lambda d: d["tables"]["orders_daily"]["columns"][2].pop("length"), "needs a positive integer length"),
        (lambda d: d["tables"]["orders_daily"]["columns"].append({"name": "order_id", "type": "BigInt"}), "duplicate"),
        (lambda d: d["tables"]["orders_daily"].update(primary_key="ord_id"), "primary_key 'ord_id'"),
        (lambda d: d["tables"]["orders_daily"].update(format="csv"), "format 'csv'"),
        (lambda d: d["views"]["v_orders_daily"].update(table="orders"), "table 'orders' is not defined"),
        (lambda d: d.update(location_template="/data/{env}/x"), "must contain {table}"),
        (
            lambda d: d["tables"]["orders_daily"]["columns"].append({"name": "extra", "type": "BigInt"}),
            "partition columns must come last",
        ),
    ],
)
def test_invalid_configs_are_rejected(tmp_path, mutate, message):
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(tmp_path, mutate))
    assert message in str(exc.value)


def test_all_problems_are_reported_together(tmp_path):
    def break_twice(d):
        d["tables"]["orders_daily"].update(format="csv")
        d["tables"]["customers_daily"]["columns"][0].update(type="Varchar")

    with pytest.raises(ConfigError) as exc:
        load_config(write_config(tmp_path, break_twice))
    assert len(exc.value.problems) == 2
