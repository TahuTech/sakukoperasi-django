from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    LoanRateTable,
    LoanRule,
    Member,
    Savings,
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

@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('id_member', 'name', 'id_week', 'id_month', 'phone_number', 'is_active')
    list_filter = ('is_active',)
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
        qs = super().get_queryset(request)
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


class SavingsAddForm(forms.ModelForm):
    """Form tambah Tabungan: hanya tampilkan anggota yang belum punya tabungan."""

    class Meta:
        model = Savings
        fields = ('member',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Hanya anggota yang belum memiliki tabungan
        anggota_dengan_tabungan = Savings.objects.values_list('member_id', flat=True)
        self.fields['member'].queryset = Member.objects.exclude(
            id__in=anggota_dengan_tabungan
        ).order_by('id_member')
        self.fields['member'].empty_label = '-- Pilih Anggota --'
        if not self.fields['member'].queryset.exists():
            self.fields['member'].help_text = (
                'Semua anggota sudah memiliki akun tabungan, '
                'atau belum ada anggota yang terdaftar.'
            )

    def clean_member(self):
        member = self.cleaned_data.get('member')
        if member and Savings.objects.filter(member=member).exists():
            raise ValidationError(
                f'Anggota {member.id_member} - {member.name} sudah memiliki akun tabungan.'
            )
        return member


class SavingsTransactionInline(admin.TabularInline):
    """Inline admin untuk SavingsTransaction di dalam Savings."""
    model = SavingsTransaction
    extra = 1
    fields = ('transaction_type', 'amount', 'transaction_date', 'notes', 'balance_before', 'balance_after', 'created_at')
    readonly_fields = ('balance_before', 'balance_after', 'created_at')
    ordering = ('-transaction_date', '-created_at')

    # Transaksi append-only: baris lama hanya bisa dilihat, koreksi lewat transaksi baru.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Savings)
class SavingsAdmin(admin.ModelAdmin):
    """Admin untuk menampilkan dan manage tabungan anggota."""
    list_display = ('member', 'get_member_id', 'get_balance_rupiah', 'updated_at')
    search_fields = ('member__id_member', 'member__name')
    readonly_fields = ('get_balance_rupiah', 'created_at', 'updated_at')
    inlines = [SavingsTransactionInline]
    add_form = SavingsAddForm

    def get_form(self, request, obj=None, **kwargs):
        """Gunakan SavingsAddForm saat menambah, form standar saat mengubah."""
        if obj is None:
            kwargs['form'] = SavingsAddForm
        return super().get_form(request, obj, **kwargs)

    def get_inline_instances(self, request, obj=None):
        """Sembunyikan inline transaksi saat membuat tabungan baru."""
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Form tambah: hanya pilih anggota
            return (
                ('Buat Akun Tabungan Baru', {
                    'fields': ('member',),
                    'description': (
                        'Pilih anggota yang belum memiliki akun tabungan. '
                        'Saldo awal dimulai dari Rp 0.'
                    ),
                }),
            )
        return (
            ('Informasi Anggota', {
                'fields': ('member',),
            }),
            ('Saldo Tabungan', {
                'fields': ('get_balance_rupiah', 'updated_at', 'created_at'),
                'description': 'Saldo otomatis diperbarui saat ada transaksi.',
            }),
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ('member', 'get_balance_rupiah', 'created_at', 'updated_at')

    def get_member_id(self, obj):
        return obj.member.id_member
    get_member_id.short_description = 'ID Anggota'

    def get_balance_rupiah(self, obj):
        return format_html('<strong>{}</strong>', format_rupiah(obj.balance))
    get_balance_rupiah.short_description = 'Saldo'

    def has_delete_permission(self, request, obj=None):
        """Hindari penghapusan akun tabungan."""
        return False


@admin.register(SavingsTransaction)
class SavingsTransactionAdmin(admin.ModelAdmin):
    """Admin untuk menampilkan dan membuat transaksi tabungan."""
    list_display = ('member_name', 'transaction_type', 'get_amount_rupiah', 'get_balance_before_rupiah', 'get_balance_after_rupiah', 'transaction_date')
    list_filter = ('transaction_type', 'transaction_date', 'created_at')
    search_fields = ('savings__member__id_member', 'savings__member__name')
    readonly_fields = ('get_balance_before_rupiah', 'get_balance_after_rupiah', 'created_at')
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
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )
    
    def member_name(self, obj):
        return f"{obj.savings.member.id_member} - {obj.savings.member.name}"
    member_name.short_description = 'Anggota'

    def get_amount_rupiah(self, obj):
        return format_rupiah(obj.amount)
    get_amount_rupiah.short_description = 'Jumlah'

    def get_balance_before_rupiah(self, obj):
        return format_rupiah(obj.balance_before)
    get_balance_before_rupiah.short_description = 'Saldo Sebelum'

    def get_balance_after_rupiah(self, obj):
        return format_rupiah(obj.balance_after)
    get_balance_after_rupiah.short_description = 'Saldo Sesudah'

    def get_queryset(self, request):
        """Optimize query dengan select_related."""
        qs = super().get_queryset(request)
        return qs.select_related('savings__member')

    # Transaksi append-only: koreksi dilakukan dengan membuat transaksi pembalik.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
