"""What can go wrong while talking to the Acestream engine.

They all derive from `EngineError`, so a caller that only needs to know whether the
engine answered can catch that one class and let the rest through.
"""


class EngineError(Exception):
    """Anything that stopped a request to the engine from being useful."""


class EngineUnavailableError(EngineError):
    """Nothing is listening at the configured address, or the connection broke."""


class EngineTimeoutError(EngineError):
    """The engine took longer than the configured timeout to answer."""


class EngineProtocolError(EngineError):
    """The engine replied, but not with the API described in docs/engine-api.md."""


class ContentNotFoundError(EngineError):
    """The engine could not resolve the Content ID it was given into playable content."""

    def __init__(self, content_id: str):
        super().__init__(f"engine could not load content: {content_id}")
        self.content_id = content_id
