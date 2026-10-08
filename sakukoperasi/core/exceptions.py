from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Ubah ValidationError dari level model menjadi respons HTTP 400, bukan 500."""
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, 'error_dict') else exc.messages
        exc = ValidationError(detail=detail)
    return drf_exception_handler(exc, context)
