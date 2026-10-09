from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """`?page=<n>` dan `?page_size=<n>` (maks 200); respons: count, next, previous, results."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200
