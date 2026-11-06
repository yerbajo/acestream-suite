"""The service layer: one public function is one unit of work, and it commits when it succeeds.

The session belongs to the caller and is passed in. A failure rolls it back and leaves a
`ServiceError` subclass to be raised, so a page and a command deal with errors the same way.
"""
