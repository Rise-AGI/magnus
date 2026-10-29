"""Administrative mappings for colliding IDs imported from another endpoint."""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class ResourceAlias(Base):
    __tablename__ = "resource_aliases"

    source_target: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    destination_id: Mapped[str] = mapped_column(String, nullable=False)
