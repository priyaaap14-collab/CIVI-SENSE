"""Errors the tool backend may raise. The MCP layer turns them into tool errors the model can read and react to."""


class BackendError(Exception):
    """A tool call that cannot proceed for a reason the *model and the user may be told about*.

    code: unauthenticated | forbidden | not_found | invalid_input | unavailable | rate_limited
    Anything else that goes wrong is an unexpected bug: the MCP SDK hides its details from the model.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
