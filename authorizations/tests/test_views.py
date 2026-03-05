
from datetime import date, timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse

from authorizations.models import (
    Authorization,
    AuthorizationNote,
    AuthorizationStatus,
    AuthorizationPortalSetting,
    Branch,
    BranchMarshal,
    Discipline,
    Person,
    ReportValue,
    ReportingPeriod,
    Sanction,
    MembershipRosterEntry,
    MembershipRosterImport,
    UserNote,
    User,
    WeaponStyle,
)
from authorizations.reporting import EQUESTRIAN_TYPE_ORDER, QUARTERLY_DISCIPLINE_MAP, REGION_ORDER


class ViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')
        cls.status_pending_background_check = AuthorizationStatus.objects.create(name='Pending Background Check')
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

    def seed_membership_roster(self, membership_number, first_name, last_name, expiration, *, has_society_waiver=False):
        return MembershipRosterEntry.objects.create(
            membership_number=membership_number,
            first_name=first_name,
            last_name=last_name,
            membership_expiration=expiration,
            has_society_waiver=has_society_waiver,
        )

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
    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_header_uses_standard_logo_when_test_features_disabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/AnTirWebLogo.png')
        self.assertNotContains(response, '/static/AnTirWebLogo_Proto.png')

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_header_uses_proto_logo_when_test_features_enabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/AnTirWebLogo_Proto.png')
        self.assertNotContains(response, '/static/AnTirWebLogo.png')

    def test_index_hides_authorization_officer_sign_off_for_non_kao_when_disabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Require Kingdom Authorization Officer Verification')
        self.assertNotContains(response, 'Kingdom Authorization Officer Verification Is Enabled')

    def test_index_shows_enabled_notice_for_non_kao_when_enabled(self):
        AuthorizationPortalSetting.objects.create(require_kao_verification=True)
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kingdom Authorization Officer Verification Is Enabled')
        self.assertNotContains(response, 'name="authorization_officer_sign_off"')

    def test_index_kao_sees_authorization_officer_sign_off_dropdown_with_current_state(self):
        kao_user, kao_person = self.make_person('index_setting_kao_view', 'Index Setting KAO View')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Require Kingdom Authorization Officer Verification')
        self.assertContains(response, 'name="authorization_officer_sign_off"')
        self.assertContains(response, '<option value="off" selected>Off</option>', html=True)

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        response_on = self.client.get(reverse('index'))
        self.assertContains(response_on, '<option value="on" selected>On</option>', html=True)

    def test_index_kao_sees_membership_upload_controls(self):
        kao_user, kao_person = self.make_person('index_membership_kao_view', 'Index Membership KAO View')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Society Membership CSV')
        self.assertContains(response, 'name="membership_csv"')
        self.assertContains(response, 'Last upload:')

    def test_index_non_kao_does_not_see_membership_upload_controls(self):
        user, _ = self.make_person('index_membership_non_kao', 'Index Membership Non KAO')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Upload Society Membership CSV')
        self.assertNotContains(response, 'name="membership_csv"')

    def test_non_kao_cannot_change_authorization_officer_sign_off_setting(self):
        user, _ = self.make_person('index_setting_non_kao', 'Index Setting Non KAO')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Authorization Officer can change this setting.')
        self.assertFalse(AuthorizationPortalSetting.objects.exists())

    def test_kao_can_change_authorization_officer_sign_off_setting(self):
        kao_user, kao_person = self.make_person('index_setting_kao', 'Index Setting KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        on_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'on',
            },
            follow=True,
        )

        self.assertEqual(on_response.status_code, 200)
        self.assertContains(on_response, 'Require Kingdom Authorization Officer Verification is now On.')
        setting = AuthorizationPortalSetting.objects.get(pk=1)
        self.assertTrue(setting.require_kao_verification)
        self.assertEqual(setting.updated_by, kao_user)

        off_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'off',
            },
            follow=True,
        )

        self.assertEqual(off_response.status_code, 200)
        self.assertContains(off_response, 'Require Kingdom Authorization Officer Verification is now Off.')
        setting.refresh_from_db()
        self.assertFalse(setting.require_kao_verification)

    def test_bulk_approve_button_only_shows_for_kao_when_sign_off_enabled(self):
        kao_user, kao_person = self.make_person('index_bulk_btn_kao', 'Index Bulk Button KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_btn_prop', 'Index Bulk Button Prop')
        target_user, target_person = self.make_person(
            'index_bulk_btn_target',
            'Index Bulk Button Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        enabled_response = self.client.get(reverse('index'))
        self.assertContains(enabled_response, 'Approve All Needs Kingdom Approval')

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        disabled_response = self.client.get(reverse('index'))
        self.assertNotContains(disabled_response, 'Approve All Needs Kingdom Approval')

    def test_kao_can_bulk_approve_needs_kingdom_with_marshal_note_flow(self):
        kao_user, kao_person = self.make_person('index_bulk_flow_kao', 'Index Bulk Flow KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_flow_prop', 'Index Bulk Flow Prop')
        target_user, target_person = self.make_person(
            'index_bulk_flow_target',
            'Index Bulk Flow Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )

        pending_jm = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        pending_weapon = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        first = self.client.post(
            reverse('index'),
            {'action': 'approve_all_kingdom_authorizations'},
            follow=True,
        )

        self.assertContains(first, 'Eligibility verified. Please add a note to finalize the marshal promotion approvals.')
        self.assertIn('pending_authorization_action', self.client.session)
        pending_jm.refresh_from_db()
        pending_weapon.refresh_from_db()
        self.assertEqual(pending_jm.status, self.status_kingdom)
        self.assertEqual(pending_weapon.status, self.status_kingdom)

        second = self.client.post(
            reverse('index'),
            {
                'action': 'approve_all_kingdom_authorizations',
                'action_note': 'Bulk kingdom approval note',
                'pending_authorization_action': '1',
            },
            follow=True,
        )

        pending_jm.refresh_from_db()
        pending_weapon.refresh_from_db()
        self.assertEqual(pending_jm.status, self.status_active)
        self.assertEqual(pending_weapon.status, self.status_active)
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertContains(second, 'Approved all 2 authorizations waiting for Kingdom approval.')

    def test_turning_sign_off_off_auto_approves_needs_kingdom(self):
        kao_user, kao_person = self.make_person('index_bulk_auto_kao', 'Index Bulk Auto KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_auto_prop', 'Index Bulk Auto Prop')
        target_user, target_person = self.make_person(
            'index_bulk_auto_target',
            'Index Bulk Auto Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )

        pending_jm = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        pending_weapon = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'off',
            },
            follow=True,
        )

        pending_jm.refresh_from_db()
        pending_weapon.refresh_from_db()
        self.assertEqual(pending_jm.status, self.status_active)
        self.assertEqual(pending_weapon.status, self.status_active)
        self.assertContains(response, 'Require Kingdom Authorization Officer Verification is now Off.')
        self.assertContains(response, 'Automatically approved all 2 authorizations waiting for Kingdom approval.')

    def test_authorization_officer_queue_shows_only_kingdom_and_pending_background_check(self):
        ao_user, ao_person = self.make_person('index_queue_ao', 'Index Queue AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_queue_prop', 'Index Queue Prop')
        target_user, target_person = self.make_person(
            'index_queue_target',
            'Index Queue Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )

        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_single_rapier,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer_person,
        )

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Needs Kingdom Approval')
        self.assertContains(response, 'Pending Background Check')
        self.assertNotContains(response, 'Needs Regional Approval')
        self.assertNotContains(response, 'Approve As (optional):')
        self.assertNotContains(response, 'btn btn-danger">Reject')

    def test_pending_background_check_row_uses_go_to_page_action(self):
        ao_user, ao_person = self.make_person('index_bg_ao', 'Index BG AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_prop', 'Index BG Prop')
        target_user, target_person = self.make_person('index_bg_target', 'Index BG Target')

        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_single_rapier,
            status=self.status_kingdom,
            marshal=proposer_person,
        )

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Pending Background Check')
        self.assertContains(response, 'Needs Kingdom Approval')
        self.assertContains(response, 'Go To Page')
        self.assertContains(response, f'href="{reverse("user_account", kwargs={"user_id": target_user.id})}"')
        self.assertContains(response, 'class="btn btn-primary">Go To Page')
        self.assertNotContains(response, 'btn btn-danger">Reject')

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

    def test_table_view_download_csv_respects_filters_and_includes_all_pages(self):
        _, fighter_a = self.make_person('search_csv_a', 'Search CSV A', branch=self.branch_lg)
        _, fighter_b = self.make_person('search_csv_b', 'Search CSV B', branch=self.branch_lg)
        _, fighter_c = self.make_person('search_csv_c', 'Search CSV C', branch=self.branch_gd)
        self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_c, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(
            reverse('search'),
            {
                'branch': self.branch_lg.name,
                'items_per_page': '1',
                'download': 'csv',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename=\"authorizations_search.csv\"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig')
        self.assertIn('SCA Name,Region,Branch,Discipline,Weapon Style,Marshal,Expiration,Minor', content)
        self.assertIn('Search CSV A', content)
        self.assertIn('Search CSV B', content)
        self.assertNotIn('Search CSV C', content)
        self.assertNotIn('<a ', content)

@override_settings(AUTHZ_TEST_FEATURES=False)
class RegisterViewTests(ViewTestBase):
    def test_register_get_renders_template(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/register.html')
        self.assertContains(
            response,
            'letters, digits, and the symbols @, ., +, -, and _.'
        )

    @patch('authorizations.views.send_mail')
    def test_register_post_creates_user_and_person(self, mock_send_mail):
        payload = self.registration_payload(username='register_ok', email='register_ok@example.com')
        self.seed_membership_roster(
            payload['membership'],
            payload['first_name'],
            payload['last_name'],
            date.fromisoformat(payload['membership_expiration']),
        )

        response = self.client.post(reverse('register'), payload)

        created_user = User.objects.get(username='register_ok')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': created_user.id}))
        self.assertFalse(created_user.is_active)
        self.assertTrue(Person.objects.filter(user=created_user).exists())
        mock_send_mail.assert_called_once()

    def test_register_rejects_membership_not_found_in_roster(self):
        payload = self.registration_payload(
            username='register_membership_not_found',
            email='register_membership_not_found@example.com',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Invalid membership number or expiration date. If you believe this is an error, please contact the Kingdom Authorization Officer.',
        )

    @override_settings(AUTHZ_TEST_FEATURES=True)
    @patch('authorizations.views.send_mail')
    def test_register_skips_membership_roster_validation_in_test_mode(self, mock_send_mail):
        payload = self.registration_payload(
            username='register_test_mode_skip',
            email='register_test_mode_skip@example.com',
        )

        response = self.client.post(reverse('register'), payload)

        created_user = User.objects.get(username='register_test_mode_skip')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            created_user.waiver_expiration,
            date.fromisoformat(payload['membership_expiration']),
        )
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
        self.assertContains(response, 'Please correct the errors below.')
        self.assertContains(response, 'Please correct the following errors:')
        self.assertContains(response, 'Must have both a membership number and expiration or neither.')

    def test_register_shows_explicit_state_and_postal_errors(self):
        payload = self.registration_payload(
            username='register_bad_location',
            email='register_bad_location@example.com',
            state_province='California',
            postal_code='12345',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
        )
        self.assertContains(
            response,
            'Postal code must be within An Tir (Canada: starts with V; US: starts with 97, 98, 991-994, 838, or 835).',
        )

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
        self.assertContains(response, 'Membership number must be between 1 and 20 digits.')
        self.assertContains(
            response,
            'Postal code must be within An Tir:'
        )
        self.assertContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver. After that you will be able to add authorizations to yourself.'
        )

    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_background_check_field_hidden_when_feature_disabled(self):
        response = self.client.get(reverse('register'))

        self.assertNotContains(response, 'name="background_check_expiration"')
        self.assertContains(
            response,
            'If entering a membership number, your First and Last name must match what is listed in your SCA membership account'
        )
        self.assertContains(response, 'Postal code must be within An Tir.')
        self.assertContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver.'
        )
        self.assertNotContains(response, 'Membership number must be between 1 and 20 digits.')
        self.assertNotContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver. After that you will be able to add authorizations to yourself.'
        )


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
    def test_fighter_login_instructions_can_be_requested_anonymously(self, mock_send_mail):
        target_user, _ = self.make_person('fighter_login_target', 'Fighter Login Target')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'send_login_instructions',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        email_body = mock_send_mail.call_args[0][1]
        self.assertIn(target_user.username, email_body)
        response_messages = self.messages_for(response)
        self.assertTrue(any('Login instructions have been sent to the email on file.' in message for message in response_messages))
        self.assertTrue(any(settings.DEFAULT_FROM_EMAIL in message for message in response_messages))

    @patch('authorizations.views.send_mail')
    def test_fighter_login_instructions_does_not_send_for_merged_record(self, mock_send_mail):
        survivor_user, _ = self.make_person('fighter_login_survivor', 'Fighter Login Survivor')
        source_user, _ = self._create_merged_user(
            'fighter_login_source_merged',
            'Fighter Login Source',
            merged_into=survivor_user,
            email='fighter_login_source_merged@example.com',
        )

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': source_user.id}),
            {
                'action': 'send_login_instructions',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': survivor_user.id}))
        self.assertEqual(mock_send_mail.call_count, 0)

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


@override_settings(AUTHZ_TEST_FEATURES=False)
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

    def test_parent_account_lists_child_account_links(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Child Accounts')
        self.assertContains(response, 'Child of Owner (account_child)')
        self.assertContains(
            response,
            reverse('user_account', kwargs={'user_id': self.child_user.id}),
        )

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

    def test_account_update_skips_roster_check_when_membership_unchanged(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            city='Seattle',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.city, 'Seattle')

    def test_account_update_rejects_changed_membership_not_in_roster(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership='1231231231',
            membership_expiration=new_expiration.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        messages = self.messages_for(response)
        self.assertIn(
            'Invalid membership number or expiration date. If you believe this is an error, please contact the Kingdom Authorization Officer.',
            messages,
        )
        self.assertNotEqual(self.owner_user.membership, '1231231231')

    def test_account_update_accepts_changed_membership_when_roster_matches(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '9239239239'
        self.seed_membership_roster(new_membership, 'Owner', 'User', new_expiration)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)

    def test_account_update_membership_extends_waiver_when_roster_waiver_is_yes(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        self.owner_user.waiver_expiration = None
        self.owner_user.save(update_fields=['waiver_expiration'])
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '7337337337'
        self.seed_membership_roster(
            new_membership,
            'Owner',
            'User',
            new_expiration,
            has_society_waiver=True,
        )
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.waiver_expiration, new_expiration)

    def test_account_update_membership_does_not_extend_waiver_when_roster_waiver_not_yes(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        self.owner_user.waiver_expiration = None
        self.owner_user.save(update_fields=['waiver_expiration'])
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '7447447447'
        self.seed_membership_roster(
            new_membership,
            'Owner',
            'User',
            new_expiration,
            has_society_waiver=False,
        )
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.owner_user.waiver_expiration)

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_account_update_skips_roster_validation_in_test_mode(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_membership = '4242424242'
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)
        self.assertEqual(self.owner_user.waiver_expiration, new_expiration)

    def test_ao_bypass_requires_note_for_unmatched_membership_change(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership='2232232232',
            membership_expiration=(date.today() + relativedelta(years=2)).isoformat(),
            membership_validation_bypass='on',
            membership_validation_note='',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        messages = self.messages_for(response)
        self.assertIn('A bypass note is required when overriding membership validation.', messages)
        self.assertNotEqual(self.owner_user.membership, '2232232232')

    def test_ao_bypass_with_note_saves_and_records_officer_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        new_membership = '3233233233'
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            membership_validation_bypass='on',
            membership_validation_note='Manual verification from Society spreadsheet correction.',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)
        bypass_note = UserNote.objects.filter(person=self.owner_person, note__icontains='Membership validation bypass applied').first()
        self.assertIsNotNone(bypass_note)

    def test_account_update_surfaces_explicit_state_and_postal_errors(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            state_province='California',
            postal_code='12345',
            city='Seattle',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('Please correct the errors with the form.', messages)
        self.assertIn(
            'State/Province: State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
            messages,
        )
        self.assertIn(
            'Postal Code: Postal code must be within An Tir (Canada: starts with V; US: starts with 97, 98, 991-994, 838, or 835).',
            messages,
        )
        self.assertContains(
            response,
            'State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
        )
        self.assertContains(
            response,
            'Postal code must be within An Tir (Canada: starts with V; US: starts with 97, 98, 991-994, 838, or 835).',
        )

    def test_non_ao_cannot_upload_membership_roster(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members.csv',
            b'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n12345,Test,User,1/1/2030\n',
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
        )

        self.assertEqual(response.status_code, 403)

    def test_ao_upload_membership_roster_replaces_rows(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        MembershipRosterEntry.objects.create(
            membership_number='111111',
            first_name='Old',
            last_name='Member',
            membership_expiration=date(2030, 1, 1),
        )
        upload = SimpleUploadedFile(
            'members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Fresh,Member,2/2/2031\n'
                '333333,New,Person,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MembershipRosterEntry.objects.filter(membership_number='111111').exists())
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='222222').exists())
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='333333').exists())
        metadata = MembershipRosterImport.objects.get(pk=1)
        self.assertEqual(metadata.row_count, 2)
        self.assertEqual(metadata.source_filename, 'members.csv')

    def test_ao_upload_membership_roster_skips_rows_with_blank_membership_number(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_with_blank_row.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '444444,Valid,Member,2/2/2031\n'
                ',Blank,LegacyId,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='444444').exists())
        self.assertFalse(MembershipRosterEntry.objects.filter(first_name='Blank', last_name='LegacyId').exists())
        messages = self.messages_for(response)
        self.assertTrue(any('row(s) were skipped' in message for message in messages))

    def test_ao_upload_membership_roster_parses_waiver_yes_flag(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_waiver.csv',
            (
                'Legacy ID (C),Waiver (C),First Name,Last Name,Membership Expiration Date\n'
                '555555,Yes,Waiver,Yes,2/2/2031\n'
                '666666,,Waiver,No,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        yes_entry = MembershipRosterEntry.objects.get(membership_number='555555')
        no_entry = MembershipRosterEntry.objects.get(membership_number='666666')
        self.assertTrue(yes_entry.has_society_waiver)
        self.assertFalse(no_entry.has_society_waiver)

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

    @override_settings(AUTHZ_TEST_FEATURES=True)
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

    @override_settings(AUTHZ_TEST_FEATURES=True)
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

    @override_settings(AUTHZ_TEST_FEATURES=True)
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

class MarshalOfficerAppointmentPermissionTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)

        cls.kao_user = User.objects.create_user(
            username='office_kao',
            password='StrongPass!123',
            email='office_kao@example.com',
            first_name='Office',
            last_name='KAO',
            membership='5656565656',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kao_person = Person.objects.create(
            user=cls.kao_user,
            sca_name='Office KAO',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.kao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        cls.kem_user = User.objects.create_user(
            username='office_kem',
            password='StrongPass!123',
            email='office_kem@example.com',
            first_name='Office',
            last_name='KEM',
            membership='5757575757',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kem_person = Person.objects.create(
            user=cls.kem_user,
            sca_name='Office KEM',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.kem_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.kem_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.kem_person,
        )

        cls.krapier_user = User.objects.create_user(
            username='office_krapier',
            password='StrongPass!123',
            email='office_krapier@example.com',
            first_name='Office',
            last_name='KRapier',
            membership='5858585858',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.krapier_person = Person.objects.create(
            user=cls.krapier_user,
            sca_name='Office Kingdom Rapier',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.krapier_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.krapier_person,
            style=cls.style_sm_rapier,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_rapier_user = User.objects.create_user(
            username='office_candidate_rapier',
            password='StrongPass!123',
            email='office_candidate_rapier@example.com',
            first_name='Candidate',
            last_name='Rapier',
            membership='5959595959',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_rapier_person = Person.objects.create(
            user=cls.candidate_rapier_user,
            sca_name='Office Candidate Rapier',
            branch=cls.branch_gd,
            is_minor=False,
        )
        Authorization.objects.create(
            person=cls.candidate_rapier_person,
            style=cls.style_sm_rapier,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_armored_user = User.objects.create_user(
            username='office_candidate_armored',
            password='StrongPass!123',
            email='office_candidate_armored@example.com',
            first_name='Candidate',
            last_name='Armored',
            membership='6060606060',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_armored_person = Person.objects.create(
            user=cls.candidate_armored_user,
            sca_name='Office Candidate Armored',
            branch=cls.branch_gd,
            is_minor=False,
        )
        Authorization.objects.create(
            person=cls.candidate_armored_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_earl_user = User.objects.create_user(
            username='office_candidate_earl',
            password='StrongPass!123',
            email='office_candidate_earl@example.com',
            first_name='Candidate',
            last_name='Earl',
            membership='6161616161',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_earl_person = Person.objects.create(
            user=cls.candidate_earl_user,
            sca_name='Office Candidate Earl',
            branch=cls.branch_gd,
            is_minor=False,
        )
        Authorization.objects.create(
            person=cls.candidate_earl_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_ao_user = User.objects.create_user(
            username='office_candidate_ao',
            password='StrongPass!123',
            email='office_candidate_ao@example.com',
            first_name='Candidate',
            last_name='AO',
            membership='6262626262',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_ao_person = Person.objects.create(
            user=cls.candidate_ao_user,
            sca_name='Office Candidate AO',
            branch=cls.branch_gd,
            is_minor=False,
        )

    def _appointment_payload(self, person, branch, discipline):
        return {
            'action': 'appoint_branch_marshal',
            'person': person.sca_name,
            'branch': branch.name,
            'discipline': discipline.name,
            'start_date': date.today().isoformat(),
        }

    def test_kingdom_discipline_marshal_can_appoint_regional_same_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.region_summits, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.region_summits,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_discipline_marshal_can_appoint_branch_same_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_lg, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_lg,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_discipline_marshal_cannot_appoint_other_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            self._appointment_payload(self.candidate_armored_person, self.branch_lg, self.discipline_armored),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_armored_person,
                branch=self.branch_lg,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )
        self.assertIn('You do not have authority to appoint this marshal office.', self.messages_for(response))

    def test_kingdom_discipline_marshal_cannot_appoint_kingdom_level_office(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_an_tir, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_earl_marshal_can_appoint_kingdom_discipline_marshal(self):
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_an_tir, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_earl_marshal_cannot_appoint_kingdom_earl_marshal(self):
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_an_tir, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_authorization_officer_can_appoint_kingdom_earl_marshal(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_an_tir, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_authorization_officer_can_appoint_second_authorization_officer(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            self._appointment_payload(self.candidate_ao_person, self.branch_an_tir, self.discipline_auth_officer),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_ao_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_auth_officer,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_discipline_marshal_can_end_lower_same_discipline_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.region_summits,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertLess(appointment.end_date, date.today())

    def test_kingdom_discipline_marshal_cannot_end_kingdom_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertGreaterEqual(appointment.end_date, date.today())

    def test_kingdom_earl_marshal_cannot_end_kingdom_earl_marshal_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_earl_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertGreaterEqual(appointment.end_date, date.today())

    def test_kingdom_authorization_officer_can_end_kingdom_earl_marshal_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_earl_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertLess(appointment.end_date, date.today())

    def test_cannot_appoint_branch_level_earl_marshal(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_lg, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_lg,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )
        self.assertIn(
            'Earl Marshal offices may only be appointed at regional or kingdom level.',
            self.messages_for(response),
        )

    def test_fighter_shows_limited_office_expiration_for_officer_self(self):
        limited_date = date.today() + timedelta(days=20)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.candidate_rapier_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertContains(response, formatted_limited_date)

    def test_fighter_shows_limited_office_expiration_for_superior(self):
        limited_date = date.today() + timedelta(days=25)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertContains(response, formatted_limited_date)

    def test_fighter_hides_limited_office_expiration_for_non_superior(self):
        limited_date = date.today() + timedelta(days=30)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        viewer_user, _ = self.make_person('office_unrelated_viewer', 'Office Unrelated Viewer')
        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertNotContains(response, formatted_limited_date)


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
        cls.kingdom_armored_user = User.objects.create_user(
            username='sanction_kingdom_armored',
            password='StrongPass!123',
            email='sanction_kingdom_armored@example.com',
            first_name='Kingdom',
            last_name='Armored',
            membership='5454545454',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kingdom_armored_person = Person.objects.create(
            user=cls.kingdom_armored_user,
            sca_name='Sanction Kingdom Armored',
            branch=cls.branch_gd,
            is_minor=False,
        )
        BranchMarshal.objects.create(
            person=cls.kingdom_armored_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_armored,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.kingdom_armored_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.ao_person,
        )

    def test_issue_sanctions_requires_kingdom_sanctions_role(self):
        self.client.login(username=self.normal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}))

        self.assertEqual(response.status_code, 403)

    def test_kingdom_marshal_can_issue_sanction_in_own_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() + relativedelta(days=30)).isoformat(),
                'action_note': 'Issued by kingdom armored marshal',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, date.today() + relativedelta(days=30))
        self.assertIn('Sanction end date: ', sanction.issue_note)

    def test_kingdom_marshal_cannot_issue_sanction_outside_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_single_rapier.id),
                'sanction_end_date': (date.today() + relativedelta(days=30)).isoformat(),
                'action_note': 'Attempted cross-discipline sanction',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_single_rapier,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn(
            'You do not have permission to sanction this discipline.',
            self.messages_for(response),
        )

    def test_issue_style_sanction_creates_revoked_authorization_and_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() + relativedelta(days=60)).isoformat(),
                'action_note': 'Issued at kingdom event',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, date.today() + relativedelta(days=60))
        self.assertIn((date.today() + relativedelta(days=60)).isoformat(), sanction.issue_note)

    def test_issue_sanction_requires_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'action_note': 'Missing end date',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_weapon_armored,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn('Please select a sanction end date.', self.messages_for(response))

    def test_issue_sanction_rejects_past_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() - relativedelta(days=1)).isoformat(),
                'action_note': 'Past end date',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_weapon_armored,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn('Sanction end date cannot be in the past.', self.messages_for(response))

    def test_issue_sanction_caps_end_date_at_office_term_and_warns(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        requested_end_date = date.today() + relativedelta(years=2)
        expected_end_date = date.today() + relativedelta(years=1)

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': requested_end_date.isoformat(),
                'action_note': 'Too long',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, expected_end_date)
        self.assertIn(
            f'Sanction end date cannot exceed the marshal office expiration date. It was set to {expected_end_date.isoformat()}.',
            self.messages_for(response),
        )

    def test_resanctioned_style_overwrites_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        existing = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today() - relativedelta(days=10),
            end_date=date.today() + relativedelta(days=10),
            issue_note='Original note',
            issued_by=self.ao_user,
        )

        new_end_date = date.today() + relativedelta(days=90)
        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': new_end_date.isoformat(),
                'action_note': 'Extended sanction',
            },
            follow=True,
        )

        existing.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(existing.end_date, new_end_date)
        self.assertEqual(existing.start_date, date.today() - relativedelta(days=10))

    def test_manage_sanctions_lift_two_step_flow_requires_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
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
                'sanction_id': str(sanction.id),
                'action_note': 'Sanction lifted after review',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertIsNotNone(sanction.lifted_at)
        self.assertNotIn('pending_sanction_lift', self.client.session)
        self.assertEqual(sanction.lift_note, 'Sanction lifted after review')

    def test_manage_sanctions_extend_two_step_flow_requires_note_and_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
            },
            follow=True,
        )

        self.assertIn('pending_sanction_extend', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note and end date to finalize extending the sanction.',
            self.messages_for(first_response),
        )

        new_end_date = date.today() + relativedelta(days=90)
        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': new_end_date.isoformat(),
                'action_note': 'Extension approved after follow-up review',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(sanction.end_date, new_end_date)
        self.assertNotIn('pending_sanction_extend', self.client.session)
        self.assertIn('Extension approved after follow-up review', sanction.issue_note)
        self.assertIn(f'Sanction end date: {new_end_date.isoformat()}', sanction.issue_note)

    def test_manage_sanctions_extend_rejects_past_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        original_end_date = date.today() + relativedelta(days=30)
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=original_end_date,
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': (date.today() - relativedelta(days=1)).isoformat(),
                'action_note': 'Invalid extension attempt',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sanction.end_date, original_end_date)
        self.assertIn('Sanction end date cannot be in the past.', self.messages_for(response))

    def test_manage_sanctions_extend_caps_end_date_at_office_term_and_warns(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        original_end_date = date.today() + relativedelta(days=30)
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=original_end_date,
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )
        requested_end_date = date.today() + relativedelta(years=2)
        expected_end_date = date.today() + relativedelta(years=1)

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': requested_end_date.isoformat(),
                'action_note': 'Requested long extension',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sanction.end_date, expected_end_date)
        self.assertIn(
            f'Sanction end date cannot exceed the marshal office expiration date. It was set to {expected_end_date.isoformat()}.',
            self.messages_for(response),
        )

    def test_kingdom_marshal_can_lift_sanction_in_own_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
            },
            follow=True,
        )
        self.assertIn(
            'Eligibility verified. Please add a note to finalize lifting the sanction.',
            self.messages_for(first_response),
        )

        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
                'action_note': 'Lifted by kingdom armored marshal',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertIsNotNone(sanction.lifted_at)
        self.assertEqual(sanction.lift_note, 'Lifted by kingdom armored marshal')

    def test_kingdom_marshal_cannot_lift_sanction_outside_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_rapier,
            style=self.style_single_rapier,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing rapier sanction',
            issued_by=self.ao_user,
        )

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
                'action_note': 'Attempted out-of-discipline lift',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(sanction.lifted_at)
        self.assertIn(
            'You do not have permission to manage this sanction.',
            self.messages_for(response),
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


class ReportsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.q2_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=2,
            authorization_officer_name='Officer Q2',
        )
        cls.q3_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=3,
            authorization_officer_name='Officer Q3',
        )
        cls.q4_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=4,
            authorization_officer_name='Officer Q4',
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=595,
            display_order=1,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=608,
            display_order=1,
        )
        ReportValue.objects.create(
            reporting_period=cls.q2_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=643,
            display_order=1,
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
            region_name='Central',
            subject_name='Armored Combat',
            metric_name='Combatants',
            value=220,
            display_order=10,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
            region_name='Central',
            subject_name='Armored Combat',
            metric_name='Combatants',
            value=230,
            display_order=10,
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.EQUESTRIAN,
            region_name='An Tir',
            subject_name='General Riding',
            metric_name='Reporting Quarter',
            value=45,
            display_order=10,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.EQUESTRIAN,
            region_name='An Tir',
            subject_name='General Riding',
            metric_name='Reporting Quarter',
            value=49,
            display_order=10,
        )

    def test_reports_defaults_to_latest_and_previous_quarters(self):
        response = self.client.get(reverse('reports'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q4_2025)
        self.assertEqual(response.context['selected_compare_period'], self.q3_2025)
        self.assertContains(response, 'Q4 2025')
        self.assertContains(response, 'Q3 2025')
        self.assertContains(response, 'Armored Combat')
        self.assertContains(response, '-13')

    def test_reports_allows_explicit_quarter_selection(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': str(self.q3_2025.id), 'compare_period': str(self.q2_2025.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q3_2025)
        self.assertEqual(response.context['selected_compare_period'], self.q2_2025)
        self.assertContains(response, 'Q3 2025')
        self.assertContains(response, 'Q2 2025')
        self.assertContains(response, '-35')

    def test_reports_compare_none_disables_compare_columns(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': str(self.q4_2025.id), 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q4_2025)
        self.assertIsNone(response.context['selected_compare_period'])
        self.assertFalse(response.context['show_compare_columns'])
        self.assertNotContains(response, '<th>Change</th>', html=True)

    def test_reports_supports_current_dynamic_period_without_persisting_rows(self):
        status_active, _ = AuthorizationStatus.objects.get_or_create(name='Active')
        branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        for region_name in REGION_ORDER:
            Branch.objects.create(name=region_name, type='Region', region=branch_an_tir)
        region_central = Branch.objects.get(name='Central')
        local_branch = Branch.objects.create(name='Central Local', type='Barony', region=region_central)

        discipline_map = {}
        for discipline_name, _ in QUARTERLY_DISCIPLINE_MAP:
            discipline_map[discipline_name] = Discipline.objects.create(name=discipline_name)
        equestrian = discipline_map['Equestrian']
        for style_name in EQUESTRIAN_TYPE_ORDER:
            WeaponStyle.objects.create(name=style_name, discipline=equestrian)

        discipline = discipline_map['Armored Combat']
        style = WeaponStyle.objects.create(name='Weapon & Shield', discipline=discipline)
        marshal_style = WeaponStyle.objects.create(name='Junior Marshal', discipline=discipline)
        user = User.objects.create_user(
            username='current_report_user',
            password='StrongPass!123',
            email='current_report_user@example.com',
            first_name='Current',
            last_name='Report',
            membership='999000111',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        person = Person.objects.create(user=user, sca_name='Current Report User', branch=local_branch, is_minor=False)
        Authorization.objects.create(
            person=person,
            style=style,
            status=status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=person,
        )
        Authorization.objects.create(
            person=person,
            style=marshal_style,
            status=status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=person,
        )

        response = self.client.get(
            reverse('reports'),
            {'current_period': 'current', 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['current_is_dynamic'])
        self.assertIsNone(response.context['selected_compare_period'])
        self.assertFalse(response.context['show_compare_columns'])
        self.assertContains(response, 'Current')
        participants_row = next(
            row for row in response.context['marshal_rows']
            if row['subject_name'] == 'Armored Combat' and row['metric_name'] == 'Total Participants'
        )
        self.assertEqual(participants_row['current_value'], 1)

    def test_reports_current_mode_handles_configuration_mismatch_without_crash(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': 'current', 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['current_is_dynamic'])
        self.assertContains(response, 'Current report could not be generated safely')

    def test_reports_download_quarterly_csv_includes_compare_columns(self):
        response = self.client.get(
            reverse('reports'),
            {'download': 'quarterly_marshal'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename="quarterly_marshal_report.csv"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig')
        self.assertIn('Discipline,Authorization Detail,Q4 2025,Q3 2025,Change', content)
        self.assertIn('Armored Combat,Total Participants,595,608,-13', content)

    def test_reports_download_quarterly_csv_omits_compare_columns_when_none_selected(self):
        response = self.client.get(
            reverse('reports'),
            {
                'current_period': str(self.q4_2025.id),
                'compare_period': '',
                'download': 'quarterly_marshal',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig').splitlines()
        self.assertEqual(content[0], 'Discipline,Authorization Detail,Q4 2025')
        self.assertIn('Armored Combat,Total Participants,595', content)
