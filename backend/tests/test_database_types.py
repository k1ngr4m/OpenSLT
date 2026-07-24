from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.types import JSONText
from app.models import ContractDataFile


def test_json_text_compiles_as_longtext_for_mysql() -> None:
    table = Table(
        "example",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("payload", JSONText(), nullable=False),
    )

    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))

    assert "payload LONGTEXT NOT NULL" in ddl
    assert "payload JSON" not in ddl


def test_json_text_round_trip_preserves_structures_and_unicode() -> None:
    engine = create_engine("sqlite:///:memory:")
    table = Table(
        "example",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("payload", JSONText(), nullable=True),
    )
    table.metadata.create_all(engine)
    payload = {"名称": "测试", "items": [1, True, None, {"key": "value"}]}

    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, payload=payload))
        connection.execute(table.insert().values(id=2, payload=None))
        values = connection.execute(select(table.c.payload).order_by(table.c.id)).scalars().all()

    assert values == [payload, None]


def test_contract_file_unique_index_fits_legacy_innodb_limit() -> None:
    index = next(
        item
        for item in ContractDataFile.__table__.indexes
        if item.name == "uq_t_contract_data_files_node_name_checksum"
    )

    ddl = str(CreateIndex(index).compile(dialect=mysql.dialect()))

    assert "CREATE UNIQUE INDEX uq_t_contract_data_files_node_name_checksum" in ddl
    assert "(workflow_node_id, filename(120), checksum(64))" in ddl
