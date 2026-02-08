
from datetime import date, timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse

from authorizations.models import (
    Authorization,
    AuthorizationNote,
    AuthorizationStatus,
    Branch,
    BranchMarshal,
    Discipline,
    Person,
    User,
    WeaponStyle,
)


class ViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')
        cls.status_pending_waiver = AuthorizationStatus.objects.create(name='Pending Waiver')
        cls.status_needs_concurrence = AuthorizationStatus.objects.create(name='Needs Concurrence')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_rejected = AuthorizationStatus.objects.create(name='Rejected')

        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.branch_other = Branch.objects.create(name='Special Other', type='Other', region=cls.region_summits)

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier Combat')
        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_earl_marshal = Discipline.objects.create(name='Earl Marshal')

        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_weapon_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_jm_youth_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_youth_armored)

    def setUp(self):
        cache.clear()
        self._membership_seed = 200000

    def _next_membership(self):
        self._membership_seed += 1
        return str(self._membership_seed)

    def make_person(
        self,
        username,
        sca_name,
        *,
        branch=None,
        membership='auto',
        membership_expiration='auto',
        is_minor=False,
        birthday=None,
        parent=None,
        email=None,
        background_check_expiration=None,
        waiver_expiration=None,
        password='StrongPass!123',
    ):
        if membership == 'auto':
            membership = self._next_membership()
        if membership_expiration == 'auto':
            membership_expiration = date.today() + relativedelta(years=1)

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email or f'{username}@example.com',
            first_name=sca_name.split()[0],
            last_name='Tester',
            membership=membership,
            membership_expiration=membership_expiration,
            birthday=birthday,
            state_province='Oregon',
            country='United States',
            background_check_expiration=background_check_expiration,
            waiver_expiration=waiver_expiration,
        )
        person = Person.objects.create(
            user=user,
            sca_name=sca_name,
            branch=branch or self.branch_gd,
            is_minor=is_minor,
            parent=parent,
        )
        return user, person

    def grant_authorization(self, person, style, *, status=None, expiration=None, marshal=None):
        return Authorization.objects.create(
            person=person,
            style=style,
            status=status or self.status_active,
            expiration=expiration or (date.today() + relativedelta(years=1)),
            marshal=marshal or person,
        )

    def appoint(self, person, branch, discipline, *, end_date=None):
        return BranchMarshal.objects.create(
            person=person,
            branch=branch,
            discipline=discipline,
            start_date=date.today() - timedelta(days=1),
            end_date=end_date or (date.today() + relativedelta(years=1)),
        )

    def messages_for(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def date_value(self, maybe_date):
        return maybe_date.isoformat() if maybe_date else ''

    def registration_payload(self, username='new_user', email='new_user@example.com', **overrides):
        payload = {
            'honeypot': '',
            'email': email,
            'username': username,
            'first_name': 'New',
            'last_name': 'User',
            'membership': self._next_membership(),
            'membership_expiration': self.date_value(date.today() + relativedelta(years=1)),
            'address': '123 Main St',
            'address2': '',
            'city': 'Portland',
            'state_province': 'Oregon',
            'postal_code': '97201',
            'country': 'United States',
            'phone_number': '5035551212',
            'birthday': '',
            'sca_name': 'New User of An Tir',
            'title': '',
            'new_title': '',
            'new_title_rank': '',
            'branch': str(self.branch_gd.id),
            'is_minor': '',
            'parent_id': '',
            'background_check_expiration': '',
        }
        payload.update(overrides)
        return payload

    def account_update_payload(self, user, person, **overrides):
        payload = {
            'honeypot': '',
            'email': user.email,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'membership': user.membership or '',
            'membership_expiration': self.date_value(user.membership_expiration),
            'address': user.address or '123 Main St',
            'address2': user.address2 or '',
            'city': user.city or 'Portland',
            'state_province': user.state_province or 'Oregon',
            'postal_code': user.postal_code or '97201',
            'country': user.country or 'United States',
            'phone_number': user.phone_number or '5035551212',
            'birthday': self.date_value(user.birthday),
            'sca_name': person.sca_name,
            'title': str(person.title_id) if person.title_id else '',
            'new_title': '',
            'new_title_rank': '',
            'branch': str(person.branch_id),
            'is_minor': 'on' if person.is_minor else '',
            'parent_id': str(person.parent_id) if person.parent_id else '',
            'background_check_expiration': self.date_value(user.background_check_expiration),
        }
        payload.update(overrides)
        return payload


class IndexViewTests(ViewTestBase):
    def test_unique_name_redirects_to_fighter_page(self):
        unique_user, _ = self.make_person('unique_index_user', 'Unique Fighter')

        response = self.client.get(reverse('index'), {'sca_name': 'Unique Fighter'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': unique_user.id}))

    def test_duplicate_name_renders_match_table_for_anonymous_user(self):
        self.make_person('dup_index_1', 'Duplicate Fighter')
        self.make_person('dup_index_2', 'Duplicate Fighter')

        response = self.client.get(reverse('index'), {'sca_name': 'Duplicate Fighter'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertEqual(response.context['name_matches'].count(), 2)

    def test_duplicate_name_renders_match_table_for_authenticated_user(self):
        viewer_user, _ = self.make_person('index_viewer', 'Index Viewer')
        self.make_person('dup_index_auth_1', 'Duplicate Auth Fighter')
        self.make_person('dup_index_auth_2', 'Duplicate Auth Fighter')

        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'), {'sca_name': 'Duplicate Auth Fighter'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertEqual(response.context['name_matches'].count(), 2)


class SearchViewTests(ViewTestBase):
    def test_goal_search_renders_search_form(self):
        response = self.client.get(reverse('search'), {'goal': 'search'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/search_form.html')

    def test_membership_filter_in_table_view(self):
        _, fighter_a = self.make_person('search_table_a', 'Search Table A', membership='1111111111')
        _, fighter_b = self.make_person('search_table_b', 'Search Table B', membership='2222222222')
        auth_a = self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'membership': '1111111111'})

        self.assertEqual(response.status_code, 200)
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertEqual(page_ids, [auth_a.id])

    def test_membership_filter_in_card_view(self):
        _, fighter_a = self.make_person('search_card_a', 'Search Card A', membership='3333333333')
        _, fighter_b = self.make_person('search_card_b', 'Search Card B', membership='4444444444')
        self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'membership': '3333333333', 'view': 'card'})

        self.assertEqual(response.status_code, 200)
        people = list(response.context['page_obj'].object_list)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].sca_name, 'Search Card A')
        self.assertTrue(hasattr(people[0], 'filtered_authorizations'))
        self.assertEqual(len(people[0].filtered_authorizations), 1)

    def test_start_date_filter_uses_effective_expiration_for_youth_marshal(self):
        _, marshal_person = self.make_person('search_date_marshal', 'Search Date Marshal')
        _, youth_person = self.make_person(
            'search_date_youth',
            'Search Date Youth',
            background_check_expiration=date.today() + timedelta(days=15),
            membership_expiration=date.today() + relativedelta(years=2),
        )
        _, normal_person = self.make_person('search_date_normal', 'Search Date Normal')

        youth_auth = self.grant_authorization(
            youth_person,
            self.style_sm_youth_armored,
            status=self.status_active,
            expiration=date.today() + relativedelta(years=2),
            marshal=marshal_person,
        )
        normal_auth = self.grant_authorization(
            normal_person,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() + timedelta(days=120),
            marshal=marshal_person,
        )

        response = self.client.get(
            reverse('search'),
            {'start_date': (date.today() + timedelta(days=60)).isoformat()},
        )

        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertIn(normal_auth.id, page_ids)
        self.assertNotIn(youth_auth.id, page_ids)

    def test_sort_expiration_uses_effective_expiration_annotation(self):
        _, marshal_person = self.make_person('search_sort_marshal', 'Search Sort Marshal')
        _, youth_person = self.make_person(
            'search_sort_youth',
            'Search Sort Youth',
            background_check_expiration=date.today() + timedelta(days=10),
            membership_expiration=date.today() + relativedelta(years=2),
        )
        _, normal_person = self.make_person('search_sort_normal', 'Search Sort Normal')

        youth_auth = self.grant_authorization(
            youth_person,
            self.style_sm_youth_armored,
            status=self.status_active,
            expiration=date.today() + relativedelta(years=2),
            marshal=marshal_person,
        )
        self.grant_authorization(
            normal_person,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() + timedelta(days=20),
            marshal=marshal_person,
        )

        response = self.client.get(reverse('search'), {'sort': 'expiration'})

        first_auth = response.context['page_obj'].object_list[0]
        self.assertEqual(first_auth.id, youth_auth.id)

    def test_invalid_start_date_reports_error_message(self):
        response = self.client.get(reverse('search'), {'start_date': 'not-a-date'}, follow=True)

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('Start date must be in YYYY-MM-DD format.', messages)

    def test_search_form_membership_input_attributes(self):
        response = self.client.get(reverse('search'), {'goal': 'search'})
        content = response.content.decode('utf-8')

        self.assertIn('id="membership"', content)
        self.assertIn('type="text"', content)
        self.assertIn('maxlength="20"', content)

class RegisterViewTests(ViewTestBase):
    def test_register_get_renders_template(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/register.html')

    @patch('authorizations.views.send_mail')
    def test_register_post_creates_user_and_person(self, mock_send_mail):
        payload = self.registration_payload(username='register_ok', email='register_ok@example.com')

        response = self.client.post(reverse('register'), payload)

        created_user = User.objects.get(username='register_ok')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': created_user.id}))
        self.assertFalse(created_user.is_active)
        self.assertTrue(Person.objects.filter(user=created_user).exists())
        mock_send_mail.assert_called_once()

    def test_register_minor_requires_birthday(self):
        payload = self.registration_payload(
            username='register_minor',
            email='register_minor@example.com',
            is_minor='on',
            birthday='',
            membership='',
            membership_expiration='',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A birthday must be provided for minors.')
        self.assertFalse(User.objects.filter(username='register_minor').exists())

    def test_register_requires_membership_and_expiration_together(self):
        payload = self.registration_payload(
            username='register_membership_pair',
            email='register_membership_pair@example.com',
            membership='1234567',
            membership_expiration='',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Must have both a membership number and expiration or neither.')

    def test_register_rejects_duplicate_membership(self):
        self.make_person('register_existing_member', 'Register Existing Member', membership='9999999999')
        payload = self.registration_payload(
            username='register_duplicate_member',
            email='register_duplicate_member@example.com',
            membership='9999999999',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A user with this membership number already exists.')
        self.assertContains(response, 'account recovery options on the login page')
        self.assertContains(response, 'Kingdom Authorization Officer')

    def test_register_rejects_duplicate_first_last_email(self):
        User.objects.create_user(
            username='register_existing_identity',
            password='StrongPass!123',
            email='dup.identity@example.com',
            first_name='Dup',
            last_name='Identity',
        )
        payload = self.registration_payload(
            username='register_duplicate_identity',
            email='dup.identity@example.com',
            first_name='Dup',
            last_name='Identity',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this first name, last name, and email already exists.')
        self.assertContains(response, 'recover your credentials from the login page')
        self.assertContains(response, 'merge duplicate accounts')

    def test_register_rejects_duplicate_first_last_email_case_insensitive(self):
        User.objects.create_user(
            username='register_existing_identity_case',
            password='StrongPass!123',
            email='Case.Match@Example.com',
            first_name='Case',
            last_name='Match',
        )
        payload = self.registration_payload(
            username='register_duplicate_identity_case',
            email='case.match@example.com',
            first_name='case',
            last_name='match',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this first name, last name, and email already exists.')

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_background_check_field_renders_when_feature_enabled(self):
        response = self.client.get(reverse('register'))

        self.assertContains(response, 'name="background_check_expiration"')

    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_background_check_field_hidden_when_feature_disabled(self):
        response = self.client.get(reverse('register'))

        self.assertNotContains(response, 'name="background_check_expiration"')


class TombstoneBehaviorTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ao_user = User.objects.create_user(
            username='merge_ao',
            password='StrongPass!123',
            email='merge_ao@example.com',
            first_name='Merge',
            last_name='AO',
            membership='9191919191',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Merge AO',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def _create_merged_user(self, username, sca_name, *, merged_into, email):
        user = User.objects.create_user(
            username=username,
            password='StrongPass!123',
            email=email,
            first_name=sca_name.split()[0],
            last_name='Merged',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        person = Person.objects.create(
            user=user,
            sca_name=sca_name,
            branch=self.branch_gd,
            is_minor=False,
        )
        user.merged_into = merged_into
        user.merged_at = timezone.now()
        user.is_active = False
        user.save()
        return user, person

    def test_fighter_redirects_merged_person_to_survivor(self):
        survivor_user, _ = self.make_person('fighter_survivor', 'Fighter Survivor')
        source_user, _ = self._create_merged_user(
            'fighter_source_merged',
            'Fighter Source',
            merged_into=survivor_user,
            email='fighter_source_merged@example.com',
        )

        response = self.client.get(reverse('fighter', kwargs={'person_id': source_user.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': survivor_user.id}))

    @patch('authorizations.views.send_mail')
    def test_username_recovery_excludes_merged_accounts(self, mock_send_mail):
        survivor_user, _ = self.make_person('recover_survivor', 'Recover Survivor', email='survivor-recover@example.com')
        active_user, _ = self.make_person('recover_active', 'Recover Active', email='shared-recover@example.com')
        merged_user, _ = self._create_merged_user(
            'recover_merged',
            'Recover Merged',
            merged_into=survivor_user,
            email='shared-recover@example.com',
        )

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'get_username',
                'email': 'shared-recover@example.com',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        email_body = mock_send_mail.call_args[0][1]
        self.assertIn(active_user.username, email_body)
        self.assertNotIn(merged_user.username, email_body)

    @patch('authorizations.views.send_mail')
    def test_password_reset_by_username_ignores_merged_accounts(self, mock_send_mail):
        survivor_user, _ = self.make_person('reset_survivor', 'Reset Survivor')
        merged_user, _ = self._create_merged_user(
            'reset_merged',
            'Reset Merged',
            merged_into=survivor_user,
            email='reset_merged@example.com',
        )

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'reset_password',
                'username': merged_user.username,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 0)
        self.assertIn(
            'If an account exists for that username, a password reset link has been sent to the email on file.',
            self.messages_for(response),
        )

    def test_password_reset_token_rejects_merged_account(self):
        survivor_user, _ = self.make_person('token_survivor', 'Token Survivor')
        merged_user, _ = self._create_merged_user(
            'token_merged',
            'Token Merged',
            merged_into=survivor_user,
            email='token_merged@example.com',
        )
        uidb64 = urlsafe_base64_encode(force_bytes(merged_user.pk))
        token = PasswordResetTokenGenerator().make_token(merged_user)

        response = self.client.get(reverse('password_reset_token', kwargs={'uidb64': uidb64, 'token': token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or has expired')

    def test_merge_page_search_excludes_already_merged_identities(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        survivor_user, _ = self.make_person('merge_page_survivor', 'Merge Page Survivor')
        active_user, _ = self.make_person('merge_page_active', 'Duplicate Merge Name')
        merged_user, _ = self._create_merged_user(
            'merge_page_merged',
            'Duplicate Merge Name',
            merged_into=survivor_user,
            email='merge_page_merged@example.com',
        )

        response = self.client.get(
            reverse('merge_accounts'),
            {
                'action': 'search_old',
                'old_sca_name': 'Duplicate Merge Name',
            },
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = [person.user_id for person in response.context['old_matches']]
        self.assertIn(active_user.id, returned_ids)
        self.assertNotIn(merged_user.id, returned_ids)


class UserAccountViewTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.owner_user = User.objects.create_user(
            username='account_owner',
            password='StrongPass!123',
            email='owner@example.com',
            first_name='Owner',
            last_name='User',
            membership='5555555555',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.owner_person = Person.objects.create(
            user=cls.owner_user,
            sca_name='Owner of Account',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.child_user = User.objects.create_user(
            username='account_child',
            password='StrongPass!123',
            email='child@example.com',
            first_name='Child',
            last_name='User',
            membership='6666666666',
            membership_expiration=date.today() + relativedelta(years=1),
            birthday=date.today() - relativedelta(years=15),
            state_province='Oregon',
            country='United States',
        )
        cls.child_person = Person.objects.create(
            user=cls.child_user,
            sca_name='Child of Owner',
            branch=cls.branch_gd,
            is_minor=True,
            parent=cls.owner_person,
        )

        cls.other_user = User.objects.create_user(
            username='account_other',
            password='StrongPass!123',
            email='other@example.com',
            first_name='Other',
            last_name='User',
            membership='7777777777',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.other_person = Person.objects.create(
            user=cls.other_user,
            sca_name='Other User',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.ao_user = User.objects.create_user(
            username='account_ao',
            password='StrongPass!123',
            email='ao@example.com',
            first_name='Auth',
            last_name='Officer',
            membership='8888888888',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Authorization Officer',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def test_owner_can_view_own_account(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_parent_can_view_minor_child_account(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.child_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_non_owner_non_parent_non_ao_is_forbidden(self):
        self.client.login(username=self.other_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 403)

    def test_authorization_officer_can_view_any_account(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_non_ao_cannot_modify_background_check_expiration(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=(date.today() + relativedelta(years=2)).isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.owner_user.background_check_expiration)

    def test_ao_can_modify_background_check_expiration(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        bg_date = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=bg_date.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.background_check_expiration, bg_date)

    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_self_set_regional_is_blocked_when_testing_disabled(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('Testing is not enabled; cannot set self as marshal officer.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_self_set_regional_requires_current_membership(self):
        self.owner_user.membership = None
        self.owner_user.membership_expiration = None
        self.owner_user.save()
        self.grant_authorization(self.owner_person, self.style_sm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('A current SCA membership (with valid expiration) is required.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_self_set_regional_local_branch_allows_junior_or_senior(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_self_set_regional_region_branch_requires_senior(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('You must hold an active Senior Marshal in Armored.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

class WaiverWorkflowTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.owner_user = User.objects.create_user(
            username='waiver_owner',
            password='StrongPass!123',
            email='waiver_owner@example.com',
            first_name='Waiver',
            last_name='Owner',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        cls.owner_person = Person.objects.create(
            user=cls.owner_user,
            sca_name='Waiver Owner',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.other_user = User.objects.create_user(
            username='waiver_other',
            password='StrongPass!123',
            email='waiver_other@example.com',
            first_name='Waiver',
            last_name='Other',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        cls.other_person = Person.objects.create(
            user=cls.other_user,
            sca_name='Waiver Other',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.ao_user = User.objects.create_user(
            username='waiver_ao',
            password='StrongPass!123',
            email='waiver_ao@example.com',
            first_name='Waiver',
            last_name='AO',
            membership='9090909090',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Waiver AO',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def test_owner_can_view_waiver_page(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/waiver.html')

    def test_authorization_officer_cannot_view_other_users_waiver_page(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.get(
            reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('You can only sign a waiver for your own account.', messages)

    def test_non_owner_non_ao_cannot_sign_waiver_for_other_user(self):
        self.client.login(username=self.other_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('You can only sign a waiver for your own account.', messages)

    def test_ao_signing_pending_waiver_activates_authorizations_and_sets_latest_expiration(self):
        exp_one = date.today() + timedelta(days=30)
        exp_two = date.today() + timedelta(days=90)
        auth_one = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_pending_waiver,
            expiration=exp_one,
            marshal=self.ao_person,
        )
        auth_two = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_single_rapier,
            status=self.status_pending_waiver,
            expiration=exp_two,
            marshal=self.ao_person,
        )

        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        response = self.client.post(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}), follow=True)

        self.owner_user.refresh_from_db()
        auth_one.refresh_from_db()
        auth_two.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(auth_one.status, self.status_active)
        self.assertEqual(auth_two.status, self.status_active)
        self.assertEqual(self.owner_user.waiver_expiration, exp_two)

    def test_owner_signing_without_pending_sets_one_year_waiver(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}), follow=True)

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.waiver_expiration, date.today() + relativedelta(years=1))


class SanctionsWorkflowTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target_user = User.objects.create_user(
            username='sanction_target',
            password='StrongPass!123',
            email='sanction_target@example.com',
            first_name='Sanction',
            last_name='Target',
            membership='5151515151',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.target_person = Person.objects.create(
            user=cls.target_user,
            sca_name='Sanction Target',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.normal_user = User.objects.create_user(
            username='sanction_normal',
            password='StrongPass!123',
            email='sanction_normal@example.com',
            first_name='Normal',
            last_name='User',
            membership='5252525252',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.normal_person = Person.objects.create(
            user=cls.normal_user,
            sca_name='Sanction Normal',
            branch=cls.branch_gd,
            is_minor=False,
        )

        cls.ao_user = User.objects.create_user(
            username='sanction_ao',
            password='StrongPass!123',
            email='sanction_ao@example.com',
            first_name='AO',
            last_name='User',
            membership='5353535353',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Sanction AO',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def test_issue_sanctions_requires_authorization_officer_or_earl_marshal(self):
        self.client.login(username=self.normal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}))

        self.assertEqual(response.status_code, 403)

    def test_issue_style_sanction_creates_revoked_authorization_and_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'action_note': 'Issued at kingdom event',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        auth = Authorization.objects.get(person=self.target_person, style=self.style_weapon_armored)
        self.assertEqual(auth.status, self.status_revoked)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=auth,
                action='sanction_issued',
                created_by=self.ao_user,
            ).exists()
        )

    def test_manage_sanctions_lift_two_step_flow_requires_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        revoked_auth = Authorization.objects.create(
            person=self.target_person,
            style=self.style_weapon_armored,
            status=self.status_revoked,
            expiration=date.today(),
            marshal=self.ao_person,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'authorization_id': str(revoked_auth.id),
            },
            follow=True,
        )

        self.assertIn('pending_sanction_lift', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize lifting the sanction.',
            self.messages_for(first_response),
        )

        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'authorization_id': str(revoked_auth.id),
                'action_note': 'Sanction lifted after review',
            },
            follow=True,
        )

        revoked_auth.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(revoked_auth.status, self.status_active)
        self.assertNotIn('pending_sanction_lift', self.client.session)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=revoked_auth,
                action='sanction_lifted',
                created_by=self.ao_user,
            ).exists()
        )


class ApiStylesViewTests(ViewTestBase):
    def test_get_weapon_styles_returns_only_matching_discipline(self):
        extra_discipline = Discipline.objects.create(name='Extra Discipline')
        extra_style = WeaponStyle.objects.create(name='Extra Style', discipline=extra_discipline)

        response = self.client.get(reverse('get_weapon_styles', kwargs={'discipline_id': self.discipline_armored.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        style_names = {item['name'] for item in payload['styles']}
        self.assertIn(self.style_weapon_armored.name, style_names)
        self.assertNotIn(extra_style.name, style_names)

    def test_get_weapon_styles_with_invalid_discipline_returns_empty_list(self):
        response = self.client.get(reverse('get_weapon_styles', kwargs={'discipline_id': 999999}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'styles': []})
