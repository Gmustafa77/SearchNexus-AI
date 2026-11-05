class AssistantError(Exception):
    """Domain error carrying an HTTP status and a safe, user-facing message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
