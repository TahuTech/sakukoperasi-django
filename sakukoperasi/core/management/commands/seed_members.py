from django.core.management.base import BaseCommand

from core.models import Member


SAMPLE_MEMBERS = [
    {
        'id_member': 'M001',
        'name': 'Budi Santoso',
        'address': 'Jl. Merdeka No. 10, Jakarta',
        'phone_number': '081234567890',
    },
    {
        'id_member': 'M002',
        'name': 'Siti Nurhaliza',
        'address': 'Jl. Sudirman No. 25, Bandung',
        'phone_number': '082345678901',
    },
    {
        'id_member': 'M003',
        'name': 'Ahmad Wijaya',
        'address': 'Jl. Ahmad Yani No. 15, Surabaya',
        'phone_number': '083456789012',
    },
]


class Command(BaseCommand):
    help = 'Seed default sample members untuk testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Hapus existing members sebelum seed.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            Member.objects.filter(id_member__startswith='M').delete()
            self.stdout.write(self.style.WARNING('Sample members dihapus.'))

        for data in SAMPLE_MEMBERS:
            member, created = Member.objects.get_or_create(
                id_member=data['id_member'],
                defaults={
                    'name': data['name'],
                    'address': data['address'],
                    'phone_number': data['phone_number'],
                },
            )
            
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Member created: {member}')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Member exists: {member}')
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n{len(SAMPLE_MEMBERS)} sample members seeded successfully.'
            )
        )
