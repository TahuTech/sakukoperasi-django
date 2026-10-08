import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

# Password bawaan versi lama yang pernah dipublikasikan di README; selalu ditolak.
KNOWN_DEFAULT_PASSWORDS = {'admin12345'}


class Command(BaseCommand):
    help = 'Create or update a default admin user (superuser).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default=os.getenv('DEFAULT_ADMIN_USERNAME', 'admin'),
            help='Admin username. Default from DEFAULT_ADMIN_USERNAME or "admin".',
        )
        parser.add_argument(
            '--password',
            default=os.getenv('DEFAULT_ADMIN_PASSWORD', ''),
            help='Admin password. Default from DEFAULT_ADMIN_PASSWORD (tidak ada password bawaan).',
        )
        parser.add_argument(
            '--email',
            default=os.getenv('DEFAULT_ADMIN_EMAIL', 'admin@sakukoperasi.local'),
            help='Admin email. Default from DEFAULT_ADMIN_EMAIL or "admin@sakukoperasi.local".',
        )
        parser.add_argument(
            '--if-not-exists',
            action='store_true',
            help='Only create user when missing. If user exists, skip update.',
        )

    def handle(self, *args, **options):
        username = options['username'].strip()
        password = options['password']
        email = options['email'].strip()

        if not username:
            raise CommandError('username cannot be empty.')

        User = get_user_model()
        existing = User.objects.filter(username=username).first()

        if options['if_not_exists'] and existing:
            self.stdout.write(self.style.WARNING(f'Admin user already exists, skipped: {username}'))
            return

        if not password:
            if options['if_not_exists']:
                # Mode auto-seed (entrypoint): jangan gagalkan startup, cukup beri peringatan.
                self.stdout.write(self.style.WARNING(
                    'DEFAULT_ADMIN_PASSWORD kosong, admin tidak dibuat. '
                    'Isi di .env atau jalankan: ./tahu superuser'
                ))
                return
            raise CommandError('password cannot be empty. Gunakan --password atau DEFAULT_ADMIN_PASSWORD.')

        if password in KNOWN_DEFAULT_PASSWORDS:
            raise CommandError('Password admin bawaan lama tidak boleh dipakai. Gunakan password lain.')

        user = existing or User(username=username)
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError('Password admin terlalu lemah: ' + ' '.join(exc.messages)) from exc

        created = existing is None
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'Admin user created: {username}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Admin user updated: {username}'))
