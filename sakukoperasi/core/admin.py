from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from .models import Jaminan, LoanRateTable, LoanRule, Member, MonthlyLoan, Savings, SavingsTransaction


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
    list_display = ('id_member', 'name', 'id_week', 'id_month', 'phone_number')
    search_fields = ('id_member', 'name', 'phone_number')
    fieldsets = (
        ('Informasi Dasar', {
            'fields': ('id_member', 'name', 'phone_number', 'address'),
        }),
        ('ID Sistem', {
            'fields': ('id_week', 'id_month'),
            'description': 'Nomor ID otomatis dihasilkan sistem',
            'classes': ('collapse',),
        }),
    )


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


@admin.register(MonthlyLoan)
class MonthlyLoanAdmin(admin.ModelAdmin):
    list_display = (
        'formatted_loan_number',
        'member',
        'loan_date',
        'loan_amount',
        'installment_amount',
        'installment_duration',
        'status',
    )
    list_filter = ('status', 'loan_date', 'created_at')
    search_fields = ('member__id_member', 'member__name', 'loan_number', 'jaminan__id')
    readonly_fields = ('loan_number', 'loan_amount', 'installment_amount', 'installment_duration', 'created_at', 'updated_at')
    autocomplete_fields = ('member', 'loan_rate_table', 'jaminan')

    fieldsets = (
        ('Relasi Data', {
            'fields': ('member', 'jaminan', 'loan_rate_table'),
            'description': 'Nomor anggota tetap menjadi relasi utama, dan jaminan harus milik anggota yang sama.',
        }),
        ('Informasi Pinjaman', {
            'fields': ('loan_number', 'loan_date', 'status'),
        }),
        ('Nilai Turunan Dari Daftar Pinjaman', {
            'fields': ('loan_amount', 'installment_amount', 'installment_duration'),
            'description': 'Field ini otomatis mengikuti pilihan daftar pinjaman bulanan.',
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def formatted_loan_number(self, obj):
        return f"PB-{obj.loan_number:06d}"
    formatted_loan_number.short_description = 'No. Pinjaman'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('member', 'loan_rate_table', 'jaminan')


@admin.register(Jaminan)
class JaminanAdmin(admin.ModelAdmin):
    list_display = ('member', 'jenis_penjamin', 'created_at')
    list_filter = ('jenis_penjamin', 'created_at')
    search_fields = ('member__id_member', 'member__name', 'keterangan')


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
