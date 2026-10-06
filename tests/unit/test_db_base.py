from sqlalchemy import Column, ForeignKey, Integer, Table
from sqlalchemy.schema import CreateTable

from app.db.base import NAMING_CONVENTION, Base
from app.db.models import Order, OrderItem


def test_base_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_constraints_get_deterministic_names() -> None:
    parent = Table("parent", Base.metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id")),
    )
    try:
        ddl = str(CreateTable(child))
        assert "CONSTRAINT pk_child PRIMARY KEY (id)" in ddl
        assert "CONSTRAINT fk_child_parent_id_parent FOREIGN KEY(parent_id)" in ddl
    finally:
        Base.metadata.remove(child)
        Base.metadata.remove(parent)


def test_order_models_have_public_ids_versions_and_items() -> None:
    order_columns = Order.__table__.c
    item_columns = OrderItem.__table__.c

    assert order_columns.id.type.python_type is int
    assert order_columns.public_id.type.python_type.__name__ == "UUID"
    assert order_columns.version.type.python_type is int
    assert item_columns.unit_price.type.precision == 12
    assert item_columns.unit_price.type.scale == 2
    assert OrderItem.__table__.c.order_id.foreign_keys
