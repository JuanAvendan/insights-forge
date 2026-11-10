"""MongoDB connector tests.

Pure functions are tested directly; the integration tests use the ``mongo``
service from ``compose.yaml`` when ``INSIGHTS_FORGE_TEST_MONGO_URL`` is set
(``mongodb://analyst:analyst@localhost:57017/shop``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal

import duckdb
import pytest
from bson import Decimal128, ObjectId

from insights_forge.connectors import ConnectorError, MongoConnector, list_collections
from insights_forge.connectors.mongo import flatten, parse_filter, safe_uri
from insights_forge.pipeline import analyze

MONGO_URL = os.environ.get("INSIGHTS_FORGE_TEST_MONGO_URL")
needs_mongo = pytest.mark.skipif(not MONGO_URL, reason="INSIGHTS_FORGE_TEST_MONGO_URL not set")


@pytest.mark.parametrize(
    ("uri", "masked"),
    [
        ("mongodb://bob:s3cret@db:27017/shop", "mongodb://bob:***@db:27017/shop"),
        ("mongodb+srv://bob:p%40ss@cluster.x.net/shop", "mongodb+srv://bob:***@cluster.x.net/shop"),
        ("mongodb://db:27017/shop", "mongodb://db:27017/shop"),
    ],
)
def test_safe_uri(uri: str, masked: str) -> None:
    assert safe_uri(uri) == masked


def test_parse_filter() -> None:
    assert parse_filter(None) == {}
    assert parse_filter("  ") == {}
    assert parse_filter('{"region": "East", "total": {"$gt": 100}}') == {
        "region": "East",
        "total": {"$gt": 100},
    }
    assert parse_filter({"a": 1}) == {"a": 1}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"$where": "sleep(1000)"}', r"\$where operator runs JavaScript"),
        ('{"$or": [{"a": 1}, {"$where": "true"}]}', r"\$where operator"),
        ('{"$expr": {"$function": {"body": "x"}}}', r"\$function operator"),
        ("[1, 2]", "must be a JSON object"),
        ("{not json", "must be valid JSON"),
    ],
)
def test_parse_filter_rejects(raw: str, message: str) -> None:
    with pytest.raises(ConnectorError, match=message):
        parse_filter(raw)


def test_flatten_nested_and_bson_types() -> None:
    oid = ObjectId()
    when = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
    doc = {
        "_id": oid,
        "total": Decimal128(Decimal("12.50")),
        "when": when,
        "customer": {"email": "a@b.c", "address": {"city": "Cali"}},
        "tags": ["online", "promo"],
        "blob": b"\x00\x01",
        "flag": True,
    }
    assert flatten(doc) == {
        "_id": str(oid),
        "total": 12.5,
        "when": when,
        "customer.email": "a@b.c",
        "customer.address.city": "Cali",
        "tags": json.dumps(["online", "promo"]),
        "blob": None,
        "flag": True,
    }


def test_name_masks_password() -> None:
    connector = MongoConnector("mongodb://bob:s3cret@db/shop", "sales")
    assert connector.name == "sales (mongodb://bob:***@db/shop)"


def test_invalid_uri_is_a_connector_error() -> None:
    with pytest.raises(ConnectorError):
        list_collections("mongodb://bob:s3cret@:bad:port")


# --------------------------------------------------------------------------- server


@pytest.mark.integration
@needs_mongo
def test_collections() -> None:
    assert MONGO_URL is not None
    assert list_collections(MONGO_URL) == ["sales"]


@pytest.mark.integration
@needs_mongo
def test_nested_documents_profile_like_the_csv() -> None:
    assert MONGO_URL is not None
    analysis = analyze(MongoConnector(MONGO_URL, "sales"), "sales")
    p = analysis.profile
    assert p.rows == 1012
    assert "_id" not in analysis.df.columns
    assert p.duplicate_rows == 12  # visible because _id is left out
    assert p.column("customer.email").nulls == 50  # missing sub-document field
    assert p.column("region").nulls == 20  # missing top-level field
    assert analysis.insights.roles.measure == "total"
    assert analysis.insights.roles.date == "order_date"


@pytest.mark.integration
@needs_mongo
def test_filter_limit_and_include_id() -> None:
    assert MONGO_URL is not None
    con = duckdb.connect()
    east = MongoConnector(MONGO_URL, "sales", filter='{"region": "East"}').load(con)
    assert east.shape[0] == 312
    few = MongoConnector(MONGO_URL, "sales", limit=5, include_id=True).load(con)
    assert few.shape[0] == 5
    assert "_id" in few.columns


@pytest.mark.integration
@needs_mongo
def test_unknown_collection_and_empty_result() -> None:
    assert MONGO_URL is not None
    with pytest.raises(ConnectorError, match=r"'nope' not found\. Available: sales"):
        MongoConnector(MONGO_URL, "nope").load(duckdb.connect())
    with pytest.raises(ConnectorError, match="No documents"):
        MongoConnector(MONGO_URL, "sales", filter='{"region": "Mars"}').load(duckdb.connect())


@pytest.mark.integration
@needs_mongo
def test_wrong_password_is_masked() -> None:
    assert MONGO_URL is not None
    bad = MONGO_URL.replace("analyst:analyst@", "analyst:WrongPass9@")
    with pytest.raises(ConnectorError) as exc:
        list_collections(bad)
    assert "WrongPass9" not in str(exc.value)
    assert "Authentication failed" in str(exc.value)
