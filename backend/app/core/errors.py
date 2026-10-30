"""Application errors mapped to HTTP responses with safe messages."""


class AppError(Exception):
    status_code = 400

    def __init__(self, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class ValidationFailed(AppError):
    status_code = 422


class PermissionDenied(AppError):
    status_code = 403
