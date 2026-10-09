from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Ubah ValidationError model menjadi HTTP 400 dan ProtectedError menjadi 409, bukan 500."""
    if isinstance(exc, ProtectedError):
        return Response(
            {'detail': 'Data tidak dapat dihapus karena masih terkait data keuangan. '
                       'Untuk anggota, gunakan nonaktifkan.'},
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, 'error_dict') else exc.messages
        exc = ValidationError(detail=detail)
    return drf_exception_handler(exc, context)
