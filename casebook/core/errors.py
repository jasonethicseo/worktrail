"""Xano 에러 의미의 로컬 짝.

`.xs` 의 precondition error_type → HTTP 상태는 어댑터가 매핑한다.
error_type 이 없는 precondition 은 Xano 기본(standard)과 같게 둔다.
"""
from __future__ import annotations


class ApiError(Exception):
    error_type = "standard"
    http_status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InputError(ApiError):
    error_type = "inputerror"
    http_status = 400


class NotFoundError(ApiError):
    error_type = "notfound"
    http_status = 404


class AccessDeniedError(ApiError):
    error_type = "accessdenied"
    http_status = 403


class UnauthorizedError(ApiError):
    error_type = "unauthorized"
    http_status = 401
