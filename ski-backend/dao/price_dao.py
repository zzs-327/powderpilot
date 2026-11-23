"""价格数据访问层。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.price import Price


class PriceDao:
    def __init__(self, session: Session):
        self.session = session

    def select_prices(self, resort_id: int, item: str = '') -> list[Price]:
        statement = select(Price).where(Price.resort_id == resort_id)
        if item:
            statement = statement.where(Price.item.contains(item))
        return list(self.session.execute(statement.order_by(Price.id)).scalars())

    def select_by_item(self, resort_id: int, item: str) -> Price | None:
        return self.session.execute(
            select(Price).where(Price.resort_id == resort_id, Price.item == item)
        ).scalar_one_or_none()
