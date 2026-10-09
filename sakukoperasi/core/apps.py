from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class CoreConfig(AppConfig):
    name = 'core'
    label = 'member'
    verbose_name = 'SakuKoperasi'


class SakuAdminConfig(AdminConfig):
    """Ganti AdminSite bawaan dengan SakuAdminSite (menu per domain + dasbor ringkasan)."""

    default_site = 'core.admin_site.SakuAdminSite'
