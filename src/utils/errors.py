"""
Custom exceptions. Each one maps to a clear HTTP status in src/api/main.py,
and each agent catches them so one failure does not crash the whole graph.
"""


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    user_message = "Something went wrong. Please try again."

    def __init__(self, message: str = "", **details):
        super().__init__(message or self.user_message)
        self.details = details


class InvalidRequestError(AppError):
    status_code = 400
    code = "invalid_request"
    user_message = "The request is not valid."


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"
    user_message = "Please log in again."


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    user_message = "Your role is not allowed to do that."


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    user_message = "Too many requests. Please wait a moment."


class PromptInjectionError(AppError):
    status_code = 400
    code = "blocked_by_guardrail"
    user_message = "I can't help with that request."


class LLMError(AppError):
    status_code = 503
    code = "llm_unavailable"
    user_message = "The language model is not available right now."


class VectorStoreError(AppError):
    status_code = 503
    code = "vector_db_unavailable"
    user_message = "The knowledge base is not available right now."


class MCPError(AppError):
    status_code = 503
    code = "mcp_unavailable"
    user_message = "The enterprise data service is not available right now."


class ToolTimeoutError(AppError):
    status_code = 504
    code = "tool_timeout"
    user_message = "A tool took too long to respond."
