"""Resolve old endpoint IDs before the usual resource authorization checks."""
from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from .models import ResourceAlias


def resolve_resource_id(
    db: Session,
    kind: str,
    resource_id: str,
    source_target: str | None,
) -> str:
    if source_target is None:
        return resource_id
    alias = db.get(
        ResourceAlias,
        (source_target, kind, resource_id),
    )
    return alias.destination_id if alias is not None else resource_id


def exclude_shadowed_resources(
    query: Query,
    model,
    kind: str,
    source_target: str | None,
) -> Query:
    if source_target is None:
        return query
    # On a legacy endpoint a colliding old ID denotes its imported resource.
    # Listing the primary resource under that same ID would make a subsequent
    # update/delete act on a different object. The canonical endpoint shows both.
    return query.filter(model.id.not_in(select(ResourceAlias.source_id).where(
        ResourceAlias.source_target == source_target,
        ResourceAlias.kind == kind,
        ResourceAlias.source_id != ResourceAlias.destination_id,
    )))
