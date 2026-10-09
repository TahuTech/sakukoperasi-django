from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join
from .member_savings import process_exit, summary
from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    LoanRateTable,
    LoanRule,
    Member,
    MemberSavingsAccount,
    Nasabah,
    Savings,
    SavingsDueRate,
    SavingsInterestRule,
    SavingsProduct,
    SavingsTransaction,
)


def format_rupiah(amount):
    """Format angka menjadi tampilan Rupiah."""
    if amount is None:
        return '-'
    return f"Rp {amount:,.0f}".replace(',', '.')

admin.site.site_header = 'SakuKoperasi Administration'
admin.site.site_title = 'SakuKoperasi Admin'
admin.site.index_title = 'Dashboard Admin'

class HasMemberSavingsAccountFilter(admin.SimpleListFilter):
    title = 'Akun simpanan pokok & wajib'
    parameter_name = 'akun_simpanan'

    def lookups(self, request, model_admin):
        return (('ada', 'Sudah ada'), ('belum', 'Belum ada'))

    def queryset(self, request, queryset):
        if self.value() == 'ada':
            return queryset.filter(akun_simpanan__isnull=False)
        if self.value() == 'belum':
            return queryset.filter(akun_simpanan__isnull=True)
        return queryset


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('id_member', 'name', 'id_week', 'id_month', 'phone_number', 'get_savings_account', 'is_active')
    list_filter = ('is_active', HasMemberSavingsAccountFilter)
    list_select_related = ('akun_simpanan',)
    search_fields = ('id_member', 'name', 'phone_number')
    readonly_fields = ('inactive_date',)
    actions = ('deactivate_members',)
    fieldsets = (
        ('Informasi Dasar', {
            'fields': ('id_member', 'name', 'phone_number', 'address'),
        }),
        ('Status Keanggotaan', {
            'fields': ('is_active', 'inactive_date'),
            'description': 'Anggota yang keluar dinonaktifkan; data tabungan & pinjaman tetap tersimpan.',
        }),
        ('ID Sistem', {
            'fields': ('id_week', 'id_month'),
            'description': 'Nomor ID otomatis dihasilkan sistem',
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='ID Akun Simpanan')
    def get_savings_account(self, obj):
        account = getattr(obj, 'akun_simpanan', None)
        return account.account_number if account else format_html('<span style="color:#b45309">{}</span>', 'belum ada')

    @admin.action(description='Nonaktifkan anggota terpilih')
    def deactivate_members(self, request, queryset):
        members = list(queryset.filter(is_active=True))
        for member in members:
            member.deactivate()
        self.message_user(request, f'{len(members)} anggota dinonaktifkan.')


@admin.register(LoanRule)
class LoanRuleAdmin(admin.ModelAdmin):
    list_display = ('loan_type', 'max_loan_amount', 'max_installments', 'interest_rate')
    list_filter = ('loan_type',)


@admin.register(LoanRateTable)
class LoanRateTableAdmin(admin.ModelAdmin):
    list_display = ('loan_rule', 'loan_amount', 'installment_count', 'installment_amount', 'admin_fee')
    list_filter = ('loan_rule__loan_type',)
    search_fields = ('loan_rule__loan_type',)


@admin.register(Jaminan)
class JaminanAdmin(admin.ModelAdmin):
    list_display = ('member', 'jenis_penjamin', 'created_at')
    list_filter = ('jenis_penjamin', 'created_at')
    search_fields = ('member__id_member', 'member__name', 'keterangan')


class LoanPaymentInline(admin.TabularInline):
    """Pembayaran append-only: baris lama hanya bisa dilihat."""
    model = LoanPayment
    extra = 1
    fields = ('amount', 'payment_date', 'notes', 'recorded_by', 'created_at')
    readonly_fields = ('recorded_by', 'created_at')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LoanPenaltyInline(admin.TabularInline):
    """Denda diinput manual; hapus denda belum dibayar lewat menu Denda Pinjaman."""
    model = LoanPenalty
    extra = 0
    fields = ('amount', 'penalty_date', 'reason', 'is_paid', 'paid_date', 'recorded_by')
    readonly_fields = ('recorded_by',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = (
        'number_label',
        'loan_type',
        'member',
        'loan_date',
        'get_loan_amount',
        'get_remaining',
        'status',
    )
    list_filter = ('loan_type', 'status', 'loan_date')
    search_fields = ('member__id_member', 'member__name', 'loan_number', 'jaminan__id')
    autocomplete_fields = ('member', 'loan_rate_table', 'jaminan')
    inlines = (LoanPaymentInline, LoanPenaltyInline)
    readonly_fields = (
        'loan_number', 'sequence', 'loan_type', 'status',
        'get_loan_amount', 'get_admin_fee', 'get_disbursed_amount', 'installment_duration',
        'get_installment_amount', 'get_total_due', 'get_total_paid', 'get_remaining',
        'get_unpaid_penalties', 'created_at', 'updated_at',
    )

    fieldsets = (
        ('Relasi Data', {
            'fields': ('member', 'loan_rate_table', 'jaminan'),
            'description': (
                'Jenis pinjaman mengikuti tarif yang dipilih. Jaminan wajib untuk pinjaman bulanan '
                'dan harus milik anggota yang sama.'
            ),
        }),
        ('Informasi Pinjaman', {
            'fields': ('loan_number', 'sequence', 'loan_type', 'loan_date', 'status'),
            'description': 'Nomor pinjaman otomatis: ID Mingguan anggota (mingguan) atau ID Bulanan anggota (bulanan).',
        }),
        ('Nilai Dari Tarif', {
            'fields': (
                'get_loan_amount', 'get_admin_fee', 'get_disbursed_amount',
                'get_installment_amount', 'installment_duration',
            ),
            'description': 'Disalin dari tarif saat pinjaman dibuat; tidak berubah walau tarif diubah.',
        }),
        ('Ringkasan Pembayaran', {
            'fields': ('get_total_due', 'get_total_paid', 'get_remaining', 'get_unpaid_penalties'),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Anggota & tarif dikunci setelah pinjaman dibuat.
        if obj is not None:
            return self.readonly_fields + ('member', 'loan_rate_table')
        return self.readonly_fields

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def save_formset(self, request, form, formset, change):
        for instance in formset.save(commit=False):
            if instance.pk is None:
                instance.recorded_by = request.user
            instance.save()
        formset.save_m2m()

    def get_queryset(self, request):
        qs = super().get_queryset(request).with_totals()
        return qs.select_related('member', 'loan_rate_table', 'jaminan')

    @admin.display(description='No. Pinjaman', ordering='loan_number')
    def number_label(self, obj):
        return obj.number_label

    @admin.display(description='Jumlah Pinjaman')
    def get_loan_amount(self, obj):
        return format_rupiah(obj.loan_amount)

    @admin.display(description='Biaya Admin')
    def get_admin_fee(self, obj):
        return format_rupiah(obj.admin_fee)

    @admin.display(description='Jumlah Dicairkan')
    def get_disbursed_amount(self, obj):
        return format_rupiah(obj.disbursed_amount)

    @admin.display(description='Angsuran per Periode')
    def get_installment_amount(self, obj):
        return format_rupiah(obj.installment_amount)

    @admin.display(description='Total Tagihan')
    def get_total_due(self, obj):
        return format_rupiah(obj.total_due)

    @admin.display(description='Total Dibayar')
    def get_total_paid(self, obj):
        return format_rupiah(obj.total_paid)

    @admin.display(description='Sisa Pinjaman')
    def get_remaining(self, obj):
        return format_rupiah(obj.remaining)

    @admin.display(description='Denda Belum Dibayar')
    def get_unpaid_penalties(self, obj):
        return format_rupiah(obj.unpaid_penalties)


@admin.register(LoanPayment)
class LoanPaymentAdmin(admin.ModelAdmin):
    list_display = ('loan', 'get_amount', 'payment_date', 'recorded_by')
    list_filter = ('payment_date',)
    search_fields = ('loan__loan_number', 'loan__member__id_member', 'loan__member__name')
    autocomplete_fields = ('loan',)
    readonly_fields = ('recorded_by', 'created_at')
    date_hierarchy = 'payment_date'

    @admin.display(description='Jumlah')
    def get_amount(self, obj):
        return format_rupiah(obj.amount)

    def save_model(self, request, obj, form, change):
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    # Pembayaran append-only.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoanPenalty)
class LoanPenaltyAdmin(admin.ModelAdmin):
    list_display = ('loan', 'get_amount', 'penalty_date', 'is_paid', 'paid_date', 'recorded_by')
    list_filter = ('is_paid', 'penalty_date')
    search_fields = ('loan__loan_number', 'loan__member__id_member', 'loan__member__name', 'reason')
    autocomplete_fields = ('loan',)
    readonly_fields = ('recorded_by', 'created_at')

    @admin.display(description='Jumlah')
    def get_amount(self, obj):
        return format_rupiah(obj.amount)

    def get_readonly_fields(self, request, obj=None):
        # Setelah dibuat, hanya status pembayaran yang bisa diubah.
        if obj is not None:
            return self.readonly_fields + ('loan', 'amount', 'penalty_date', 'reason')
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # Denda yang sudah dibayar tidak boleh dihapus.
        if obj is not None and obj.is_paid:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        # Hapus massal melewati validasi & update status pinjaman; hapus satu per satu saja.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions


@admin.register(SavingsProduct)
class SavingsProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'billing', 'get_min_balance', 'allow_withdrawal', 'withdraw_only_on_exit', 'is_active')
    list_filter = ('is_active',)

    @admin.display(description='Saldo Mengendap')
    def get_min_balance(self, obj):
        return format_rupiah(obj.min_balance)


@admin.register(SavingsInterestRule)
class SavingsInterestRuleAdmin(admin.ModelAdmin):
    list_display = ('product', 'effective_from', 'annual_rate', 'basis', 'get_min_balance', 'postings_count')
    list_filter = ('product', 'basis')
    readonly_fields = ('created_at',)

    @admin.display(description='Saldo Min. Dapat Bunga')
    def get_min_balance(self, obj):
        return format_rupiah(obj.min_balance_for_interest)

    @admin.display(description='Jumlah Posting')
    def postings_count(self, obj):
        return obj.postings.count()

    def has_delete_permission(self, request, obj=None):
        # Aturan yang sudah dipakai posting bunga harus tetap ada untuk audit.
        if obj is not None and obj.postings.exists():
            return False
        return super().has_delete_permission(request, obj)


class SavingsInline(admin.TabularInline):
    model = Savings
    extra = 0
    fields = ('account_number', 'product', 'get_balance', 'is_active', 'opened_date')
    readonly_fields = ('account_number', 'product', 'get_balance', 'is_active', 'opened_date')
    show_change_link = True

    @admin.display(description='Saldo')
    def get_balance(self, obj):
        return format_rupiah(obj.balance)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Nasabah)
class NasabahAdmin(admin.ModelAdmin):
    list_display = ('name', 'nik', 'phone_number', 'member')
    search_fields = ('name', 'nik', 'phone_number', 'member__id_member')
    autocomplete_fields = ('member',)
    readonly_fields = ('created_at',)
    inlines = (SavingsInline,)
    fieldsets = (
        (None, {
            'fields': ('member', 'name', 'nik', 'phone_number', 'address'),
            'description': 'Pilih anggota bila nasabah adalah anggota koperasi; data kosong akan disalin dari anggota.',
        }),
        ('Metadata', {'fields': ('created_at',), 'classes': ('collapse',)}),
    )


class ManualTransactionTypeMixin:
    """Bunga hanya diposting sistem (command apply_savings_interest), jadi tidak ditawarkan di form."""

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'transaction_type':
            kwargs['choices'] = [
                choice for choice in SavingsTransaction.TransactionType.choices
                if choice[0] != SavingsTransaction.TransactionType.INTEREST
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)


class SavingsTransactionInline(ManualTransactionTypeMixin, admin.TabularInline):
    """Inline transaksi di halaman rekening; baris lama hanya bisa dilihat."""
    model = SavingsTransaction
    extra = 1
    fields = ('transaction_type', 'amount', 'transaction_date', 'notes', 'balance_before', 'balance_after', 'recorded_by')
    readonly_fields = ('balance_before', 'balance_after', 'recorded_by')
    ordering = ('-transaction_date', '-created_at')

    # Transaksi append-only: koreksi lewat transaksi baru.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Savings)
class SavingsAdmin(admin.ModelAdmin):
    """Rekening simpanan; cari dengan nomor rekening atau nama nasabah."""
    list_display = ('account_number', 'nasabah', 'product', 'get_balance_rupiah', 'is_active', 'updated_at')
    list_filter = ('product', 'is_active')
    search_fields = ('account_number', 'nasabah__name', 'nasabah__phone_number', 'nasabah__member__id_member')
    autocomplete_fields = ('nasabah',)
    readonly_fields = ('get_balance_rupiah', 'is_active', 'closed_date', 'created_at', 'updated_at')
    inlines = (SavingsTransactionInline,)
    actions = ('close_accounts',)
    fieldsets = (
        ('Rekening', {
            'fields': ('account_number', 'nasabah', 'product', 'opened_date'),
            'description': 'Nomor rekening diisi manual sesuai buku tabungan.',
        }),
        ('Saldo & Status', {
            'fields': ('get_balance_rupiah', 'is_active', 'closed_date', 'updated_at', 'created_at'),
            'description': 'Saldo otomatis diperbarui saat ada transaksi.',
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Pemilik & jenis simpanan dikunci setelah rekening dibuka.
        if obj is not None:
            return self.readonly_fields + ('nasabah', 'product')
        return self.readonly_fields

    def get_inline_instances(self, request, obj=None):
        """Sembunyikan inline transaksi saat membuka rekening baru."""
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def save_formset(self, request, form, formset, change):
        for instance in formset.save(commit=False):
            if instance.pk is None:
                instance.recorded_by = request.user
            instance.save()
        formset.save_m2m()

    @admin.display(description='Saldo')
    def get_balance_rupiah(self, obj):
        return format_html('<strong>{}</strong>', format_rupiah(obj.balance))

    @admin.action(description='Tutup rekening terpilih (saldo harus 0)')
    def close_accounts(self, request, queryset):
        for account in queryset.filter(is_active=True):
            try:
                account.close()
            except ValidationError as exc:
                self.message_user(request, f'{account.account_number}: {" ".join(exc.messages)}', messages.ERROR)
            else:
                self.message_user(request, f'{account.account_number} ditutup.')

    def has_delete_permission(self, request, obj=None):
        """Rekening ditutup, bukan dihapus."""
        return False


@admin.register(SavingsTransaction)
class SavingsTransactionAdmin(ManualTransactionTypeMixin, admin.ModelAdmin):
    """Input & riwayat transaksi; rekening dicari dengan nomor rekening atau nama nasabah."""
    list_display = ('get_account', 'get_nasabah', 'transaction_type', 'get_amount_rupiah', 'get_balance_before_rupiah', 'get_balance_after_rupiah', 'transaction_date', 'recorded_by')
    list_filter = ('transaction_type', 'transaction_date', 'savings__product')
    search_fields = ('savings__account_number', 'savings__nasabah__name')
    autocomplete_fields = ('savings',)
    readonly_fields = ('get_balance_before_rupiah', 'get_balance_after_rupiah', 'interest_period', 'interest_rule', 'recorded_by', 'created_at')
    date_hierarchy = 'transaction_date'

    fieldsets = (
        ('Informasi Transaksi', {
            'fields': ('savings', 'transaction_type', 'transaction_date', 'amount'),
        }),
        ('Detail Saldo', {
            'fields': ('get_balance_before_rupiah', 'get_balance_after_rupiah'),
            'description': 'Otomatis dihitung saat transaksi disimpan.',
        }),
        ('Catatan', {
            'fields': ('notes',),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('interest_period', 'interest_rule', 'recorded_by', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='No. Rekening', ordering='savings__account_number')
    def get_account(self, obj):
        return obj.savings.account_number

    @admin.display(description='Nasabah', ordering='savings__nasabah__name')
    def get_nasabah(self, obj):
        return obj.savings.nasabah.name

    @admin.display(description='Jumlah')
    def get_amount_rupiah(self, obj):
        return format_rupiah(obj.amount)

    @admin.display(description='Saldo Sebelum')
    def get_balance_before_rupiah(self, obj):
        return format_rupiah(obj.balance_before)

    @admin.display(description='Saldo Sesudah')
    def get_balance_after_rupiah(self, obj):
        return format_rupiah(obj.balance_after)

    def save_model(self, request, obj, form, change):
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        """Optimize query dengan select_related."""
        qs = super().get_queryset(request)
        return qs.select_related('savings__nasabah', 'recorded_by')

    # Transaksi append-only: koreksi dilakukan dengan membuat transaksi pembalik.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SavingsDueRate)
class SavingsDueRateAdmin(admin.ModelAdmin):
    list_display = ('product', 'effective_from', 'get_amount')
    list_filter = ('product',)
    readonly_fields = ('created_at',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            kwargs['queryset'] = SavingsProduct.objects.exclude(billing=SavingsProduct.Billing.NONE)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='Nominal')
    def get_amount(self, obj):
        return format_rupiah(obj.amount)


class MemberSubAccountInline(admin.TabularInline):
    """Sub-rekening pokok & wajib; klik untuk melihat/menambah transaksi."""
    model = Savings
    fk_name = 'member_account'
    extra = 0
    fields = ('account_number', 'product', 'get_balance', 'is_active')
    readonly_fields = fields
    show_change_link = True
    verbose_name = 'Sub-rekening'
    verbose_name_plural = 'Sub-rekening (klik untuk transaksi)'

    @admin.display(description='Saldo')
    def get_balance(self, obj):
        return format_rupiah(obj.balance)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MemberSavingsAccount)
class MemberSavingsAccountAdmin(admin.ModelAdmin):
    """Akun simpanan pokok & wajib; cari dengan nama/ID anggota atau ID akun."""
    list_display = (
        'account_number', 'member', 'opened_date',
        'get_pokok_balance', 'get_pokok_arrears', 'get_wajib_balance', 'get_wajib_arrears', 'is_active',
    )
    list_filter = ('is_active',)
    search_fields = ('account_number', 'member__name', 'member__id_member')
    autocomplete_fields = ('member',)
    readonly_fields = ('is_active', 'closed_date', 'get_summary', 'created_at', 'updated_at')
    inlines = (MemberSubAccountInline,)
    actions = ('process_member_exit',)
    fieldsets = (
        ('Akun', {
            'fields': ('account_number', 'member', 'opened_date'),
            'description': 'ID akun diisi manual. Sub-rekening pokok & wajib dibuat otomatis; wajib ditagih mulai bulan buka.',
        }),
        ('Ringkasan', {'fields': ('get_summary', 'is_active', 'closed_date')}),
        ('Metadata', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return self.readonly_fields + ('member', 'opened_date')
        return self.readonly_fields

    def _info(self, obj, code, key):
        return format_rupiah(summary(obj).get(code, {}).get(key))

    @admin.display(description='Saldo Pokok')
    def get_pokok_balance(self, obj):
        return self._info(obj, SavingsProduct.PRINCIPAL, 'saldo')

    @admin.display(description='Tunggakan Pokok')
    def get_pokok_arrears(self, obj):
        return self._info(obj, SavingsProduct.PRINCIPAL, 'tunggakan')

    @admin.display(description='Saldo Wajib')
    def get_wajib_balance(self, obj):
        return self._info(obj, SavingsProduct.MANDATORY, 'saldo')

    @admin.display(description='Tunggakan Wajib')
    def get_wajib_arrears(self, obj):
        return self._info(obj, SavingsProduct.MANDATORY, 'tunggakan')

    @admin.display(description='Ringkasan Simpanan')
    def get_summary(self, obj):
        if not obj.pk:
            return '-'
        rows = format_html_join(
            '',
            '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>',
            (
                (
                    code.title(), format_rupiah(info['saldo']), format_rupiah(info['kewajiban']),
                    format_rupiah(info['tunggakan']), format_rupiah(info['tagihan_bulan_ini']),
                )
                for code, info in summary(obj).items()
            ),
        )
        return format_html(
            '<table><tr><th>Jenis</th><th>Saldo</th><th>Kewajiban</th><th>Tunggakan</th>'
            '<th>Tagihan Bulan Ini</th></tr>{}</table>',
            rows,
        )

    @admin.action(description='Proses keluar anggota (kembalikan pokok & wajib, tutup akun)')
    def process_member_exit(self, request, queryset):
        for account in queryset.filter(is_active=True):
            try:
                refunds = process_exit(account, user=request.user)
            except ValidationError as exc:
                self.message_user(request, f'{account.account_number}: {" ".join(exc.messages)}', messages.ERROR)
            else:
                total = sum(tx.amount for tx in refunds)
                self.message_user(request, f'{account.account_number}: dikembalikan {format_rupiah(total)}, akun ditutup.')

    def has_delete_permission(self, request, obj=None):
        return False
