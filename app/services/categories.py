"""Categories: the user's own buckets for channels.

A name has to be free before a category is stored, and a commit can still fail because
another request took it first, so both the check and the commit are part of the same
answer: the caller gets the category or a `DuplicateError`, never a half-written row.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Category, Channel
from app.services.errors import CategoryInUseError, DuplicateError, NotFoundError, ValidationError


def _checked_name(name: str) -> str:
    """The name in the form it will be stored: no surrounding blanks, and never blank."""
    clean = name.strip()
    if not clean:
        raise ValidationError("category name must not be empty")
    return clean


def _stored(session: Session, category: Category, name: str) -> Category:
    """Commits a new or renamed category, or reports that the name was taken meanwhile.

    `name` arrives as an argument because a rollback expires the entity: reading
    `category.name` afterwards would return whatever the database still holds.
    """
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateError(f"category already exists: {name}") from exc
    return category


def create_category(session: Session, name: str) -> Category:
    """Stores a category with a name no other category has."""
    category = Category(name=_checked_name(name))
    session.add(category)
    return _stored(session, category, category.name)


def find_or_create(session: Session, name: str) -> Category:
    """The category called `name` in any case, created when the catalog has none."""
    clean = _checked_name(name)
    existing = session.scalars(
        select(Category).where(func.lower(Category.name) == clean.lower())
    ).first()
    return existing if existing is not None else create_category(session, clean)


def get_category(session: Session, category_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise NotFoundError(f"category not found: {category_id}")
    return category


def list_categories(session: Session) -> list[Category]:
    """Every category, by name, which is the order the form offers them in."""
    return list(session.scalars(select(Category).order_by(Category.name)))


def rename_category(session: Session, category_id: int, name: str) -> Category:
    category = get_category(session, category_id)
    category.name = _checked_name(name)
    return _stored(session, category, category.name)


def delete_category(session: Session, category_id: int) -> None:
    """Removes an empty category; a category with channels is refused, not emptied.

    The channels are the user's data, so deciding what happens to them is the user's
    job: reassign or delete them first, and the category goes on the second attempt.
    """
    category = get_category(session, category_id)
    in_use = session.scalar(
        select(func.count()).select_from(Channel).where(Channel.category_id == category_id)
    )
    if in_use:
        raise CategoryInUseError(
            f"category '{category.name}' is used by {in_use} channel(s); reassign them first"
        )
    session.delete(category)
    session.commit()
