from django.contrib import admin

from .dashboard import build_dashboard

# Pengelompokan menu admin per domain (urutan grup & model sesuai alur kerja petugas).
MENU_GROUPS = (
    ('Anggota', ('member', 'jaminan', 'nasabah')),
    ('Pinjaman', ('loan', 'loanpayment', 'loanpenalty')),
    ('Simpanan', ('membersavingsaccount', 'savings', 'savingstransaction')),
    ('Pengaturan', ('loanrule', 'loanratetable', 'savingsproduct', 'savingsduerate', 'savingsinterestrule')),
)
DOMAIN_APP_LABEL = 'member'


class SakuAdminSite(admin.AdminSite):
    site_header = 'SakuKoperasi'
    site_title = 'SakuKoperasi'
    index_title = 'Dasbor'

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)
        if app_label is not None:  # halaman indeks satu app tetap apa adanya
            return app_list

        domain_app = next((app for app in app_list if app['app_label'] == DOMAIN_APP_LABEL), None)
        if domain_app is None:
            return app_list

        models = {model['object_name'].lower(): model for model in domain_app['models']}
        groups = []
        for name, model_names in MENU_GROUPS:
            group_models = [models.pop(model_name) for model_name in model_names if model_name in models]
            if group_models:
                groups.append({
                    **domain_app,
                    'name': name,
                    # app_label unik per grup agar id/kelas HTML & sidebar tidak bentrok.
                    'app_label': f'{DOMAIN_APP_LABEL}-{name.lower()}',
                    'models': group_models,
                })
        if models:  # model baru yang belum dipetakan tetap tampil
            groups.append({**domain_app, 'name': 'Lainnya', 'models': list(models.values())})

        others = [app for app in app_list if app['app_label'] != DOMAIN_APP_LABEL]
        return groups + others

    def index(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), 'dashboard_cards': build_dashboard(request.user)}
        return super().index(request, extra_context)
