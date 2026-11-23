from datetime import datetime

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapper, mapped_column, Mapped

from config.database import Base


class User(Base):
    __tablename__ = 'user'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32))
    password: Mapped[str] = mapped_column(String(128))
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now())