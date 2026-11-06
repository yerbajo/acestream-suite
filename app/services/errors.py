"""The failures the service layer admits to, each one already a sentence for the user.

They share one base so a route can catch everything worth explaining with a single
clause, and so each subclass says who has to act: the value was wrong, the row is gone,
the name is taken, or the category still has work to do. None of them knows about HTTP,
which is why the CLI and the background checks report them the same way.
"""


class ServiceError(Exception):
    """A request the service refused, with a reason that can be shown as it is."""


class NotFoundError(ServiceError):
    """The row the caller named is not there: a stale link, or a deletion in another tab."""


class DuplicateError(ServiceError):
    """The value must be unique and another row already holds it."""


class CategoryInUseError(ServiceError):
    """The category still has channels; getting rid of them was never part of the request."""


class ValidationError(ServiceError, ValueError):
    """The value cannot be stored as it arrived. It is a `ValueError` too, so parsing code
    that does not know this hierarchy still catches it."""
