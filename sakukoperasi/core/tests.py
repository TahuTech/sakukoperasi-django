import threading
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Jaminan, LoanRateTable, LoanRule, Member, MonthlyLoan, Savings, SavingsTransaction


def create_member(id_member='A001', **kwargs):
    return Member.objects.create(
        id_member=id_member,
        name=kwargs.pop('name', 'Anggota Uji'),
        address=kwargs.pop('address', 'Alamat'),
        phone_number=kwargs.pop('phone_number', '0800'),
        **kwargs,
    )


def add_transaction(savings, transaction_type, amount):
    return SavingsTransaction.objects.create(
        savings=savings,
        transaction_type=transaction_type,
        amount=Decimal(amount),
        transaction_date=date.today(),
    )


DEPOSIT = SavingsTransaction.TransactionType.DEPOSIT
WITHDRAWAL = SavingsTransaction.TransactionType.WITHDRAWAL


class SavingsTransactionTests(TestCase):
    def setUp(self):
        self.savings = create_member().tabungan

    def test_deposit_and_withdrawal_update_balance(self):
        tx1 = add_transaction(self.savings, DEPOSIT, '1000')
        tx2 = add_transaction(self.savings, WITHDRAWAL, '300')

        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('700'))
        self.assertEqual((tx1.balance_before, tx1.balance_after), (Decimal('0'), Decimal('1000')))
        self.assertEqual((tx2.balance_before, tx2.balance_after), (Decimal('1000'), Decimal('700')))

    def test_balance_before_always_taken_from_current_balance(self):
        add_transaction(self.savings, DEPOSIT, '500')
        tx = SavingsTransaction(
            savings=self.savings,
            transaction_type=DEPOSIT,
            amount=Decimal('100'),
            transaction_date=date.today(),
            balance_before=Decimal('999999'),  # nilai dari klien harus diabaikan
        )
        tx.save()
        self.assertEqual(tx.balance_before, Decimal('500'))
        self.assertEqual(tx.balance_after, Decimal('600'))

    def test_withdrawal_exceeding_balance_is_rejected(self):
        add_transaction(self.savings, DEPOSIT, '100')
        with self.assertRaises(ValidationError):
            add_transaction(self.savings, WITHDRAWAL, '101')
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('100'))
        self.assertEqual(self.savings.transactions.count(), 1)

    def test_non_positive_amount_is_rejected(self):
        for amount in ('0', '-50'):
            with self.assertRaises(ValidationError):
                add_transaction(self.savings, DEPOSIT, amount)
        self.assertEqual(self.savings.transactions.count(), 0)

    def test_transaction_is_immutable(self):
        tx = add_transaction(self.savings, DEPOSIT, '100')
        tx.amount = Decimal('5000')
        with self.assertRaises(ValidationError):
            tx.save()
        with self.assertRaises(ValidationError):
            tx.delete()
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('100'))


class MemberIdTests(TestCase):
    def test_ids_are_generated_sequentially(self):
        first = create_member('A001')
        second = create_member('A002')
        self.assertEqual((first.id_week, first.id_month), ('001', '0001'))
        self.assertEqual((second.id_week, second.id_month), ('002', '0002'))

    def test_manual_id_is_normalized(self):
        member = create_member('A001', id_week='7', id_month='12')
        self.assertEqual((member.id_week, member.id_month), ('007', '0012'))

    def test_generated_id_skips_ids_taken_meanwhile(self):
        create_member('A001')  # id_week 001
        create_member('A002', id_week='002', id_month='0002')
        third = create_member('A003')
        self.assertEqual(third.id_week, '003')


class MonthlyLoanTests(TestCase):
    def setUp(self):
        rule = LoanRule.objects.create(
            loan_type=LoanRule.LoanType.MONTHLY,
            max_loan_amount=5_000_000,
            max_installments=12,
            interest_rate=0,
        )
        self.rate = LoanRateTable.objects.create(
            loan_rule=rule, loan_amount=1_000_000, installment_count=10, installment_amount=120_000,
        )
        self.member = create_member()
        self.jaminan = Jaminan.objects.create(member=self.member, jenis_penjamin=Jaminan.JenisPenjamin.BPKB)

    def test_loan_number_and_values_are_generated(self):
        loans = [
            MonthlyLoan.objects.create(
                member=self.member, jaminan=self.jaminan, loan_rate_table=self.rate, loan_date=date.today(),
            )
            for _ in range(2)
        ]
        self.assertEqual([loan.loan_number for loan in loans], [1, 2])
        self.assertEqual(loans[0].installment_amount, Decimal('120000'))


class ApiValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        admin = get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123')
        self.client.force_authenticate(admin)

    def test_member_crud_returns_savings(self):
        response = self.client.post(
            '/api/members/',
            {'id_member': 'A001', 'name': 'Uji', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['tabungan']['balance'], '0.00')
        self.assertEqual(self.client.get('/api/members/').status_code, 200)

    def test_model_validation_error_returns_400(self):
        other = create_member('B001')
        member = create_member('A001')
        rule = LoanRule.objects.create(
            loan_type=LoanRule.LoanType.MONTHLY, max_loan_amount=1, max_installments=1, interest_rate=0,
        )
        rate = LoanRateTable.objects.create(
            loan_rule=rule, loan_amount=1, installment_count=1, installment_amount=1,
        )
        jaminan_other = Jaminan.objects.create(member=other, jenis_penjamin=Jaminan.JenisPenjamin.BPKB)

        response = self.client.post(
            '/api/pinjaman-bulanan/',
            {'member': member.pk, 'jaminan': jaminan_other.pk, 'loan_rate_table': rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class ConcurrencyTests(TransactionTestCase):
    """Menjalankan operasi paralel di thread terpisah (koneksi DB terpisah)."""

    def run_parallel(self, func, count):
        errors = []
        barrier = threading.Barrier(count)

        def worker(index):
            try:
                barrier.wait()
                func(index)
            except Exception as exc:  # noqa: BLE001 - dikumpulkan untuk assertion
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return errors

    def test_parallel_deposits_do_not_lose_updates(self):
        savings_id = create_member().tabungan.pk

        errors = self.run_parallel(
            lambda _: add_transaction(Savings.objects.get(pk=savings_id), DEPOSIT, '100'),
            count=10,
        )

        self.assertEqual(errors, [])
        self.assertEqual(Savings.objects.get(pk=savings_id).balance, Decimal('1000'))

    def test_parallel_member_creation_generates_unique_ids(self):
        errors = self.run_parallel(lambda i: create_member(f'P{i:03d}'), count=8)

        self.assertEqual(errors, [])
        week_ids = sorted(Member.objects.values_list('id_week', flat=True))
        self.assertEqual(week_ids, [f'{i:03d}' for i in range(1, 9)])


class ApiPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user('petugas', password='Rahasia-Kuat-123')
        create_member()

    def test_anonymous_request_is_rejected(self):
        for url in ('/api/members/', '/api/jaminan/', '/api/pinjaman-bulanan/'):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_user_without_model_permission_cannot_write(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/members/').status_code, 200)
        response = self.client.post(
            '/api/members/',
            {'id_member': 'X1', 'name': 'x', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_user_with_model_permission_can_write(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='add_member'))
        self.client.force_authenticate(get_user_model().objects.get(pk=self.staff.pk))
        response = self.client.post(
            '/api/members/',
            {'id_member': 'X1', 'name': 'x', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_token_authentication(self):
        response = self.client.post(
            '/api/auth/token/', {'username': 'petugas', 'password': 'Rahasia-Kuat-123'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()['token']
        self.assertEqual(token, Token.objects.get(user=self.staff).key)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(self.client.get('/api/members/').status_code, 200)


class SeedDefaultAdminTests(TestCase):
    def run_seed(self, *args):
        call_command('seed_default_admin', *args, stdout=StringIO())

    def test_requires_password(self):
        with self.assertRaises(CommandError):
            self.run_seed('--password', '')

    def test_auto_seed_without_password_is_skipped(self):
        self.run_seed('--if-not-exists', '--password', '')
        self.assertFalse(get_user_model().objects.exists())

    def test_weak_password_is_rejected(self):
        for weak in ('admin12345', '12345678'):
            with self.assertRaises(CommandError):
                self.run_seed('--password', weak)
        self.assertFalse(get_user_model().objects.exists())

    def test_creates_superuser_with_strong_password(self):
        self.run_seed('--username', 'boss', '--password', 'Koperasi-Aman-2026')
        user = get_user_model().objects.get(username='boss')
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password('Koperasi-Aman-2026'))
