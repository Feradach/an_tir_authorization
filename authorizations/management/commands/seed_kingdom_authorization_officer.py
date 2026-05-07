from datetime import date
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from authorizations.models import Branch, BranchMarshal, Discipline, Person, User, UserNote


DEFAULT_END_DATE = date(2100, 12, 31)
DEFAULT_ADMIN_EMAIL = 'antir.authorization.database@gmail.com'
DEFAULT_ADMIN_USER_ID = 15050


class Command(BaseCommand):
    help = 'Create or update the seed Kingdom Authorization Officer admin account.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default=DEFAULT_ADMIN_EMAIL, help='Username for the seed admin account.')
        parser.add_argument('--email', default=DEFAULT_ADMIN_EMAIL, help='Email address for the seed admin account.')
        parser.add_argument('--first-name', default='Database', help='Legal first name for the account.')
        parser.add_argument('--last-name', default='Administrator', help='Legal last name for the account.')
        parser.add_argument('--sca-name', default='Administrator', help='SCA name for the account profile.')
        parser.add_argument('--membership', default='KAO-SEED', help='Seed membership number/value.')
        parser.add_argument(
            '--user-id',
            type=int,
            default=DEFAULT_ADMIN_USER_ID,
            help='User ID to use when creating the seed account.',
        )
        parser.add_argument(
            '--expiration',
            default=DEFAULT_END_DATE.isoformat(),
            help='Membership and office expiration date in YYYY-MM-DD format.',
        )
        parser.add_argument('--password', help='Set the account password from this argument.')
        parser.add_argument(
            '--prompt-password',
            action='store_true',
            help='Prompt securely for the account password.',
        )

    def handle(self, *args, **options):
        username = options['username'].strip()
        email = options['email'].strip()
        first_name = options['first_name'].strip()
        last_name = options['last_name'].strip()
        sca_name = options['sca_name'].strip()
        membership = options['membership'].strip()
        user_id = options['user_id']
        expiration = self._parse_date(options['expiration'])

        if not username:
            raise CommandError('Username is required.')
        if not email:
            raise CommandError('Email is required.')
        if not first_name or not last_name:
            raise CommandError('First name and last name are required.')
        if not sca_name:
            raise CommandError('SCA name is required.')
        if not membership:
            raise CommandError('Membership is required because membership expiration must be retained.')
        if user_id < 1:
            raise CommandError('User ID must be a positive integer.')

        branch = Branch.objects.filter(name='An Tir').order_by('id').first()
        if not branch:
            raise CommandError('Canonical branch "An Tir" was not found.')

        discipline = Discipline.objects.filter(name='Authorization Officer').order_by('id').first()
        if not discipline:
            raise CommandError('Canonical discipline "Authorization Officer" was not found.')

        password = options.get('password')
        if options['prompt_password']:
            first_password = getpass('Password: ')
            second_password = getpass('Password again: ')
            if first_password != second_password:
                raise CommandError('Passwords do not match.')
            password = first_password

        membership_owner = User.objects.filter(membership=membership).exclude(username=username).first()
        if membership_owner:
            raise CommandError(
                f'Membership "{membership}" is already assigned to username "{membership_owner.username}".'
            )

        try:
            with transaction.atomic():
                user = User.objects.filter(username=username).first()
                user_created = user is None
                if user_created:
                    existing_id_user = User.objects.filter(pk=user_id).first()
                    if existing_id_user:
                        raise CommandError(
                            f'User ID {user_id} is already assigned to username "{existing_id_user.username}".'
                        )
                    user = User(id=user_id, username=username)

                user.email = email
                user.first_name = first_name
                user.last_name = last_name
                user.membership = membership
                user.membership_expiration = expiration
                user.waiver_expiration = expiration
                user.background_check_expiration = expiration
                user.address = '1'
                user.city = 'An Tir'
                user.state_province = 'OR'
                user.postal_code = '97000'
                user.country = 'USA'
                user.phone_number = '111-111-1111'
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                if password:
                    user.set_password(password)
                elif user_created:
                    user.set_unusable_password()
                user.save()

                person, person_created = Person.objects.get_or_create(
                    user=user,
                    defaults={
                        'sca_name': sca_name,
                        'branch': branch,
                        'is_minor': False,
                        'created_by': user,
                        'updated_by': user,
                    },
                )
                person.sca_name = sca_name
                person.branch = branch
                person.is_minor = False
                person.updated_by = user
                person.save()

                office, office_created = BranchMarshal.objects.update_or_create(
                    person=person,
                    branch=branch,
                    discipline=discipline,
                    defaults={
                        'start_date': date.today(),
                        'end_date': expiration,
                        'created_by': user,
                        'updated_by': user,
                    },
                )

                if user_created or person_created or office_created:
                    UserNote.objects.create(
                        person=person,
                        created_by=user,
                        note='Seed Kingdom Authorization Officer admin account created for launch migration.',
                    )
        except IntegrityError as exc:
            raise CommandError(f'Could not seed Kingdom Authorization Officer account: {exc}') from exc

        self.stdout.write(self.style.SUCCESS('Seed Kingdom Authorization Officer account is ready.'))
        self.stdout.write(f'Username: {user.username}')
        self.stdout.write(f'Person ID: {person.user_id}')
        self.stdout.write(f'Office ID: {office.id}')
        self.stdout.write(f'Office expiration: {office.end_date.isoformat()}')

    def _parse_date(self, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(f'Invalid expiration date "{value}". Use YYYY-MM-DD.') from exc
