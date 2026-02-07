
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from authorizations.models import (
    Authorization,
    AuthorizationNote,
    AuthorizationStatus,
    Branch,
    BranchMarshal,
    Discipline,
    Person,
    Title,
    User,
    WeaponStyle,
)
from authorizations.permissions import validate_reject_authorization
from authorizations.views import CreatePersonForm


class AdditionalCoverageBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status_active, _ = AuthorizationStatus.objects.get_or_create(name='Active')
        cls.status_pending, _ = AuthorizationStatus.objects.get_or_create(name='Pending')
        cls.status_regional, _ = AuthorizationStatus.objects.get_or_create(name='Needs Regional Approval')
        cls.status_kingdom, _ = AuthorizationStatus.objects.get_or_create(name='Needs Kingdom Approval')
        cls.status_pending_waiver, _ = AuthorizationStatus.objects.get_or_create(name='Pending Waiver')
        cls.status_needs_concurrence, _ = AuthorizationStatus.objects.get_or_create(name='Needs Concurrence')
        cls.status_revoked, _ = AuthorizationStatus.objects.get_or_create(name='Revoked')
        cls.status_rejected, _ = AuthorizationStatus.objects.get_or_create(name='Rejected')

        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.branch_other = Branch.objects.create(name='Nowhere Other', type='Other', region=cls.region_summits)

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier Combat')
        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_earl_marshal = Discipline.objects.create(name='Earl Marshal')

        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_weapon_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_polearm_armored = WeaponStyle.objects.create(name='Polearm', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)

    def setUp(self):
        self._membership_seed = 300000

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
        waiver_expiration=None,
        background_check_expiration=None,
    ):
        if membership == 'auto':
            membership = self._next_membership()
        if membership_expiration == 'auto':
            membership_expiration = date.today() + relativedelta(years=1)

        user = User.objects.create_user(
            username=username,
            password='StrongPass!123',
            email=email or f'{username}@example.com',
            first_name=sca_name.split()[0],
            last_name='Tester',
            membership=membership,
            membership_expiration=membership_expiration,
            birthday=birthday,
            state_province='Oregon',
            country='United States',
            waiver_expiration=waiver_expiration,
            background_check_expiration=background_check_expiration,
            address='123 Main St',
            city='Portland',
            postal_code='97201',
            phone_number='(503) 555-1212',
        )
        person = Person.objects.create(
            user=user,
            sca_name=sca_name,
            branch=branch or self.branch_gd,
            is_minor=is_minor,
            parent=parent,
        )
        return user, person

    def grant_authorization(self, person, style, *, status=None, marshal=None, expiration=None, concurring_fighter=None):
        return Authorization.objects.create(
            person=person,
            style=style,
            status=status or self.status_active,
            marshal=marshal or person,
            expiration=expiration or (date.today() + relativedelta(years=1)),
            concurring_fighter=concurring_fighter,
        )

    def appoint(self, person, branch, discipline):
        return BranchMarshal.objects.create(
            person=person,
            branch=branch,
            discipline=discipline,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def login(self, user):
        self.client.login(username=user.username, password='StrongPass!123')

    def messages_for(self, response):
        return [m.message for m in response.context['messages']]

class AddAuthorizationSecurityTests(AdditionalCoverageBase):
    def test_non_authorization_officer_cannot_spoof_marshal_id(self):
        acting_user, acting_person = self.make_person('addauth_actor', 'AddAuth Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)

        selected_user, selected_person = self.make_person('addauth_selected', 'AddAuth Selected')
        target_user, target_person = self.make_person('addauth_target', 'AddAuth Target')

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'marshal_id': str(selected_user.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('You are not allowed to specify an authorizing marshal.', messages)
        self.assertFalse(
            Authorization.objects.filter(person=target_person, style=self.style_weapon_armored).exists()
        )

    def test_authorization_officer_selected_marshal_must_be_senior_in_discipline(self):
        ao_user, ao_person = self.make_person('addauth_ao', 'AddAuth AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        selected_user, selected_person = self.make_person('addauth_not_senior', 'Not Senior')
        target_user, target_person = self.make_person('addauth_target2', 'AddAuth Target2')

        self.login(ao_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'marshal_id': str(selected_user.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertTrue(any('is not a senior marshal in Armored' in m for m in messages))
        self.assertFalse(
            Authorization.objects.filter(person=target_person, style=self.style_weapon_armored).exists()
        )

    def test_authorization_officer_can_set_marshal_id_when_selected_is_senior(self):
        ao_user, ao_person = self.make_person('addauth_ao_ok', 'AddAuth AO OK')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        selected_user, selected_person = self.make_person('addauth_senior', 'Selected Senior')
        self.grant_authorization(selected_person, self.style_sm_armored)

        target_user, target_person = self.make_person('addauth_target3', 'AddAuth Target3', waiver_expiration=date.today() + relativedelta(years=1))
        # Keep concurrence from kicking in by giving target an existing active auth in the same discipline.
        self.grant_authorization(target_person, self.style_polearm_armored, status=self.status_active, marshal=selected_person)

        self.login(ao_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'marshal_id': str(selected_user.id),
            },
            follow=True,
        )

        created = Authorization.objects.get(person=target_person, style=self.style_weapon_armored)
        self.assertEqual(created.marshal, selected_person)
        self.assertEqual(created.status, self.status_active)
        self.assertEqual(response.status_code, 200)

    def test_marshal_authorization_requires_current_membership(self):
        acting_user, acting_person = self.make_person('addauth_membership_actor', 'Membership Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)

        target_user, target_person = self.make_person(
            'addauth_membership_target',
            'Membership Target',
            membership=None,
            membership_expiration=None,
        )

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_jm_armored.id)],
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('Must be a current member to be authorized as a marshal.', messages)
        self.assertFalse(
            Authorization.objects.filter(person=target_person, style=self.style_jm_armored).exists()
        )

    def test_non_marshal_authorization_goes_pending_waiver_when_no_waiver(self):
        acting_user, acting_person = self.make_person('addauth_waiver_actor', 'Waiver Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)

        target_user, target_person = self.make_person(
            'addauth_waiver_target',
            'Waiver Target',
            membership=None,
            membership_expiration=None,
            waiver_expiration=None,
        )
        self.grant_authorization(target_person, self.style_polearm_armored, status=self.status_active, marshal=acting_person)

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
            },
            follow=True,
        )

        created = Authorization.objects.get(person=target_person, style=self.style_weapon_armored)
        self.assertEqual(created.status, self.status_pending_waiver)
        self.assertEqual(response.status_code, 200)

    def test_existing_non_marshal_update_clears_concurring_fighter_when_concurrence_not_required(self):
        acting_user, acting_person = self.make_person('addauth_clear_actor', 'Clear Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)

        target_user, target_person = self.make_person(
            'addauth_clear_target',
            'Clear Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        _, concurring_person = self.make_person('addauth_clear_concur', 'Clear Concur')
        existing = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_active,
            marshal=acting_person,
            concurring_fighter=concurring_person,
        )

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
            },
            follow=True,
        )

        existing.refresh_from_db()
        self.assertIsNone(existing.concurring_fighter)
        self.assertEqual(existing.status, self.status_active)
        self.assertEqual(response.status_code, 200)

    def test_pending_duplicate_is_rejected_in_add_authorization(self):
        acting_user, acting_person = self.make_person('addauth_pending_actor', 'Pending Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)
        target_user, target_person = self.make_person('addauth_pending_target', 'Pending Target')
        self.grant_authorization(target_person, self.style_weapon_armored, status=self.status_pending, marshal=acting_person)

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('Cannot renew a pending authorization.', messages)

    def test_revoked_duplicate_is_rejected_in_add_authorization(self):
        acting_user, acting_person = self.make_person('addauth_revoked_actor', 'Revoked Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)
        target_user, target_person = self.make_person('addauth_revoked_target', 'Revoked Target')
        self.grant_authorization(target_person, self.style_weapon_armored, status=self.status_revoked, marshal=acting_person)

        self.login(acting_user)
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('Cannot renew a revoked authorization.', messages)

class PendingNoteFlowTests(AdditionalCoverageBase):
    def test_marshal_proposal_two_step_flow_sets_and_clears_pending_session(self):
        acting_user, acting_person = self.make_person('note_propose_actor', 'Note Propose Actor')
        self.grant_authorization(acting_person, self.style_sm_armored)
        target_user, target_person = self.make_person('note_propose_target', 'Note Propose Target')

        self.login(acting_user)

        first = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_jm_armored.id)],
            },
            follow=True,
        )

        pending_key = f'pending_authorization_{target_user.id}'
        self.assertIn(pending_key, self.client.session)
        self.assertFalse(Authorization.objects.filter(person=target_person, style=self.style_jm_armored).exists())
        self.assertIn('Eligibility verified. Please add a note to finalize the marshal promotion.', self.messages_for(first))

        second = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'pending_authorization': '1',
                'action_note': 'Marshal proposal note',
            },
            follow=True,
        )

        created = Authorization.objects.get(person=target_person, style=self.style_jm_armored)
        self.assertEqual(created.status, self.status_pending)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=created,
                action='marshal_proposed',
                created_by=acting_user,
            ).exists()
        )
        self.assertNotIn(pending_key, self.client.session)
        self.assertEqual(second.status_code, 200)

    def test_approve_two_step_flow_requires_note_and_clears_pending_session(self):
        proposer_user, proposer_person = self.make_person('note_approve_proposer', 'Note Approve Proposer')
        approver_user, approver_person = self.make_person('note_approve_approver', 'Note Approve Approver')
        target_user, target_person = self.make_person('note_approve_target', 'Note Approve Target')

        self.grant_authorization(proposer_person, self.style_sm_armored)
        self.grant_authorization(approver_person, self.style_sm_armored)

        pending_auth = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer_person,
        )

        self.login(approver_user)

        first = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn('Eligibility verified. Please add a note to finalize the marshal promotion.', self.messages_for(first))

        second = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'action_note': 'Approval note',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=pending_auth,
                action='marshal_concurred',
                created_by=approver_user,
            ).exists()
        )
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertEqual(second.status_code, 200)

    def test_reject_two_step_flow_requires_note_and_clears_pending_session(self):
        proposer_user, proposer_person = self.make_person('note_reject_proposer', 'Note Reject Proposer')
        regional_user, regional_person = self.make_person('note_reject_regional', 'Note Reject Regional')
        target_user, target_person = self.make_person('note_reject_target', 'Note Reject Target', branch=self.branch_gd)

        self.grant_authorization(proposer_person, self.style_sm_armored)
        self.appoint(regional_person, self.region_summits, self.discipline_armored)

        pending_auth = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer_person,
        )

        self.login(regional_user)

        first = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn('Eligibility verified. Please add a note to finalize the marshal promotion.', self.messages_for(first))

        second = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_auth.id),
                'action_note': 'Reject note',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_rejected)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=pending_auth,
                action='marshal_rejected',
                created_by=regional_user,
            ).exists()
        )
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertEqual(second.status_code, 200)

    def test_issue_sanction_two_step_flow_requires_note_and_clears_pending_session(self):
        ao_user, ao_person = self.make_person('note_sanction_ao', 'Note Sanction AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        target_user, target_person = self.make_person('note_sanction_target', 'Note Sanction Target')

        self.login(ao_user)

        first = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
            },
            follow=True,
        )

        pending_key = f'pending_sanction_issue_{target_user.id}'
        self.assertIn(pending_key, self.client.session)
        self.assertIn('Eligibility verified. Please add a note to finalize the sanction.', self.messages_for(first))

        second = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': target_user.id}),
            {
                'pending_sanction_issue': '1',
                'action_note': 'Issue sanction note',
            },
            follow=True,
        )

        revoked = Authorization.objects.get(person=target_person, style=self.style_weapon_armored)
        self.assertEqual(revoked.status, self.status_revoked)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=revoked,
                action='sanction_issued',
                created_by=ao_user,
            ).exists()
        )
        self.assertNotIn(pending_key, self.client.session)
        self.assertEqual(second.status_code, 200)


class RejectValidationTests(AdditionalCoverageBase):
    def test_validate_reject_authorization_false_for_non_regional_user(self):
        requester_user, requester_person = self.make_person('reject_validate_plain', 'Reject Validate Plain')
        proposer_user, proposer_person = self.make_person('reject_validate_proposer', 'Reject Validate Proposer')
        target_user, target_person = self.make_person('reject_validate_target', 'Reject Validate Target')
        self.grant_authorization(proposer_person, self.style_sm_armored)
        pending_auth = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer_person,
        )

        ok, msg = validate_reject_authorization(requester_user, pending_auth)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You do not have authority to reject this authorization.')

    def test_validate_reject_authorization_true_for_regional_marshal(self):
        regional_user, regional_person = self.make_person('reject_validate_regional', 'Reject Validate Regional')
        proposer_user, proposer_person = self.make_person('reject_validate_prop2', 'Reject Validate Prop2')
        target_user, target_person = self.make_person('reject_validate_tgt2', 'Reject Validate Tgt2')

        self.appoint(regional_person, self.region_summits, self.discipline_armored)
        self.grant_authorization(proposer_person, self.style_sm_armored)
        pending_auth = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer_person,
        )

        ok, msg = validate_reject_authorization(regional_user, pending_auth)

        self.assertTrue(ok)
        self.assertEqual(msg, 'OK')

class ConcurrenceFlowTests(AdditionalCoverageBase):
    def setUp(self):
        super().setUp()
        self.proposer_user, self.proposer_person = self.make_person('concur_proposer', 'Concur Proposer')
        self.target_user, self.target_person = self.make_person(
            'concur_target',
            'Concur Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        self.concurring_user, self.concurring_person = self.make_person('concur_helper', 'Concur Helper')
        self.unqualified_user, self.unqualified_person = self.make_person('concur_unqualified', 'Concur Unqualified')

        # Proposer can propose by virtue of senior marshal.
        self.grant_authorization(self.proposer_person, self.style_sm_armored)
        # Concurring user must be authorized in discipline.
        self.grant_authorization(self.concurring_person, self.style_weapon_armored)

        self.pending_a = self.grant_authorization(
            self.target_person,
            self.style_weapon_armored,
            status=self.status_needs_concurrence,
            marshal=self.proposer_person,
        )
        self.pending_b = self.grant_authorization(
            self.target_person,
            self.style_polearm_armored,
            status=self.status_needs_concurrence,
            marshal=self.proposer_person,
        )

    def test_concurrence_records_fighter_and_approves_all_pending_in_discipline(self):
        self.login(self.concurring_user)

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.target_user.id}),
            {
                'action': 'concur_authorization',
                'authorization_id': str(self.pending_a.id),
            },
            follow=True,
        )

        self.pending_a.refresh_from_db()
        self.pending_b.refresh_from_db()

        self.assertEqual(self.pending_a.status, self.status_active)
        self.assertEqual(self.pending_b.status, self.status_active)
        self.assertEqual(self.pending_a.concurring_fighter, self.concurring_person)
        self.assertEqual(self.pending_b.concurring_fighter, self.concurring_person)
        self.assertEqual(response.status_code, 200)

    def test_fighter_cannot_concur_for_self(self):
        self.login(self.target_user)

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.target_user.id}),
            {
                'action': 'concur_authorization',
                'authorization_id': str(self.pending_a.id),
            },
            follow=True,
        )

        self.assertIn('You do not have permission to concur with this authorization.', self.messages_for(response))

    def test_proposing_marshal_cannot_concur(self):
        self.login(self.proposer_user)

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.target_user.id}),
            {
                'action': 'concur_authorization',
                'authorization_id': str(self.pending_a.id),
            },
            follow=True,
        )

        self.assertIn('You do not have permission to concur with this authorization.', self.messages_for(response))

    def test_user_without_authorization_in_discipline_cannot_concur(self):
        self.login(self.unqualified_user)

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.target_user.id}),
            {
                'action': 'concur_authorization',
                'authorization_id': str(self.pending_a.id),
            },
            follow=True,
        )

        self.assertIn('You do not have permission to concur with this authorization.', self.messages_for(response))

    def test_concurring_fighter_visible_only_to_authorization_officer(self):
        self.pending_a.status = self.status_active
        self.pending_a.concurring_fighter = self.concurring_person
        self.pending_a.save()

        ao_user, ao_person = self.make_person('concur_visibility_ao', 'Concur Visibility AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        self.login(ao_user)
        ao_response = self.client.get(reverse('fighter', kwargs={'person_id': self.target_user.id}))
        self.assertContains(ao_response, 'Concurring Fighter:')
        self.assertContains(ao_response, self.concurring_person.sca_name)

        self.login(self.unqualified_user)
        non_ao_response = self.client.get(reverse('fighter', kwargs={'person_id': self.target_user.id}))
        self.assertNotContains(non_ao_response, 'Concurring Fighter:')


class ModelValidationAndConstraintTests(AdditionalCoverageBase):
    def test_person_clean_raises_for_minor_without_birthday(self):
        user = User.objects.create_user(
            username='person_minor_no_bday',
            password='StrongPass!123',
            email='person_minor_no_bday@example.com',
            first_name='Minor',
            last_name='NoBday',
            membership=self._next_membership(),
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
            address='123 Main St',
            city='Portland',
            postal_code='97201',
            phone_number='(503) 555-1212',
            birthday=None,
        )

        with self.assertRaises(ValidationError):
            Person.objects.create(user=user, sca_name='Minor No Birthday', branch=self.branch_gd, is_minor=True)

    def test_person_save_clears_parent_when_not_minor(self):
        parent_user, parent_person = self.make_person('person_parent', 'Person Parent')
        child_user, child_person = self.make_person(
            'person_child',
            'Person Child',
            is_minor=True,
            birthday=date.today() - relativedelta(years=15),
            parent=parent_person,
        )

        child_person.is_minor = False
        child_person.save()
        child_person.refresh_from_db()

        self.assertIsNone(child_person.parent)

    def test_user_save_normalizes_partial_membership_fields_to_none(self):
        user = User.objects.create_user(
            username='user_partial_membership',
            password='StrongPass!123',
            email='user_partial_membership@example.com',
            first_name='Partial',
            last_name='Membership',
            membership='123456',
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
            address='123 Main St',
            city='Portland',
            postal_code='97201',
            phone_number='(503) 555-1212',
        )
        user.refresh_from_db()

        self.assertIsNone(user.membership)
        self.assertIsNone(user.membership_expiration)

    def test_user_save_sets_waiver_to_at_least_membership_expiration(self):
        exp = date.today() + relativedelta(years=1)
        user = User.objects.create_user(
            username='user_waiver_from_membership',
            password='StrongPass!123',
            email='user_waiver_from_membership@example.com',
            first_name='Waiver',
            last_name='FromMembership',
            membership='123999',
            membership_expiration=exp,
            waiver_expiration=None,
            state_province='Oregon',
            country='United States',
            address='123 Main St',
            city='Portland',
            postal_code='97201',
            phone_number='(503) 555-1212',
        )
        user.refresh_from_db()
        self.assertEqual(user.waiver_expiration, exp)

    def test_authorization_note_is_immutable(self):
        user, person = self.make_person('note_immutable_user', 'Note Immutable User')
        auth = self.grant_authorization(person, self.style_weapon_armored)
        note = AuthorizationNote.objects.create(
            authorization=auth,
            created_by=user,
            action='marshal_proposed',
            note='Original note',
        )

        note.note = 'Changed note'
        with self.assertRaises(ValidationError):
            note.save()

        with self.assertRaises(ValidationError):
            note.delete()

    def test_authorization_unique_constraint_prevents_duplicate_person_style(self):
        user, person = self.make_person('unique_auth_user', 'Unique Auth User')
        self.grant_authorization(person, self.style_weapon_armored)

        with self.assertRaises(IntegrityError):
            self.grant_authorization(person, self.style_weapon_armored)


class SearchFilteringAndPaginationTests(AdditionalCoverageBase):
    def test_table_view_paginates_by_authorization_rows(self):
        _, alpha = self.make_person('search_page_alpha', 'Alpha Search')
        _, bravo = self.make_person('search_page_bravo', 'Bravo Search')
        _, charlie = self.make_person('search_page_charlie', 'Charlie Search')

        self.grant_authorization(alpha, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(bravo, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(charlie, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'items_per_page': '1', 'page': '2'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['view_mode'], 'table')
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.paginator.count, 3)
        self.assertEqual(len(page_obj.object_list), 1)
        self.assertEqual(page_obj.object_list[0].person.sca_name, 'Bravo Search')

    def test_card_view_paginates_by_person_not_authorization(self):
        _, alpha = self.make_person('search_card_alpha', 'Alpha Card')
        _, zulu = self.make_person('search_card_zulu', 'Zulu Card')

        self.grant_authorization(alpha, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(alpha, self.style_polearm_armored, status=self.status_active)
        self.grant_authorization(zulu, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(
            reverse('search'),
            {'view': 'card', 'items_per_page': '1', 'page': '2'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['view_mode'], 'card')
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.paginator.count, 2)
        self.assertEqual(len(page_obj.object_list), 1)
        person = page_obj.object_list[0]
        self.assertEqual(person.sca_name, 'Zulu Card')
        self.assertTrue(hasattr(person, 'filtered_authorizations'))
        self.assertEqual(len(person.filtered_authorizations), 1)

    def test_email_filter_is_case_insensitive(self):
        _, target = self.make_person(
            'search_email_target',
            'Search Email Target',
            email='Mixed.Case@TestMail.org',
        )
        _, other = self.make_person(
            'search_email_other',
            'Search Email Other',
            email='other@testmail.org',
        )
        target_auth = self.grant_authorization(target, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(other, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'email': 'mixed.case@testmail.org'})

        self.assertEqual(response.status_code, 200)
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertEqual(page_ids, [target_auth.id])

    def test_minor_filter_limits_results(self):
        _, minor = self.make_person(
            'search_minor_yes',
            'Search Minor Yes',
            is_minor=True,
            birthday=date.today() - relativedelta(years=13),
        )
        _, adult = self.make_person('search_minor_no', 'Search Minor No')
        minor_auth = self.grant_authorization(minor, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(adult, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'is_minor': 'True'})

        self.assertEqual(response.status_code, 200)
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertEqual(page_ids, [minor_auth.id])


class UserSelfAppointmentEdgeTests(AdditionalCoverageBase):
    def setUp(self):
        super().setUp()
        self.owner_user, self.owner_person = self.make_person('self_owner_user', 'Self Owner')
        self.other_user, self.other_person = self.make_person('self_other_user', 'Self Other')
        self.ao_user, self.ao_person = self.make_person('self_ao_user', 'Self AO')
        self.appoint(self.ao_person, self.branch_an_tir, self.discipline_auth_officer)

    def test_owner_only_rule_applies_even_to_authorization_officer(self):
        self.login(self.ao_user)

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        self.assertIn('You can only change your own marshal appointment.', self.messages_for(response))
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_self_set_regional_rejects_other_branch_type(self):
        self.login(self.owner_user)

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.branch_other.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        self.assertIn(
            'Selected branch type is not eligible for marshal appointments.',
            self.messages_for(response),
        )
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.branch_other,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_self_set_regional_refreshes_existing_same_office(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)
        existing = BranchMarshal.objects.create(
            person=self.owner_person,
            branch=self.branch_gd,
            discipline=self.discipline_armored,
            start_date=date.today() - timedelta(days=200),
            end_date=date.today() + timedelta(days=5),
        )

        self.login(self.owner_user)
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        existing.refresh_from_db()
        self.assertEqual(existing.end_date, date.today() + relativedelta(years=1))
        self.assertTrue(any('has been refreshed.' in m for m in self.messages_for(response)))
        self.assertEqual(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).count(),
            1,
        )

    def test_self_set_regional_blocks_when_other_active_office_exists(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)
        BranchMarshal.objects.create(
            person=self.owner_person,
            branch=self.branch_lg,
            discipline=self.discipline_armored,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() + relativedelta(months=6),
        )

        self.login(self.owner_user)
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        self.assertIn(
            'You already hold an active officer position. Please end it before setting a new one.',
            self.messages_for(response),
        )
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_authorization_officer_discipline_must_use_an_tir_branch(self):
        self.login(self.owner_user)

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_auth_officer.id),
            },
            follow=True,
        )

        self.assertIn(
            'Authorization Officers must be appointed at the Kingdom level (An Tir).',
            self.messages_for(response),
        )
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                discipline=self.discipline_auth_officer,
                end_date__gte=date.today(),
            ).exists()
        )


class CreatePersonFormEdgeTests(AdditionalCoverageBase):
    def form_payload(self, **overrides):
        payload = {
            'honeypot': '',
            'email': 'form.edge@example.com',
            'username': 'form_edge_user',
            'first_name': 'Form',
            'last_name': 'Edge',
            'membership': '',
            'membership_expiration': '',
            'address': '123 Main St',
            'address2': '',
            'city': 'Portland',
            'state_province': 'Oregon',
            'postal_code': '97201',
            'country': 'United States',
            'phone_number': '5035551212',
            'birthday': '',
            'sca_name': 'Form Edge',
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

    def test_new_title_without_rank_is_rejected(self):
        form = CreatePersonForm(data=self.form_payload(new_title='Custom Herald'))

        self.assertFalse(form.is_valid())
        self.assertIn('Creating a new title requires choosing a rank.', form.errors['__all__'])
        self.assertFalse(Title.objects.filter(name='Custom Herald').exists())

    def test_new_title_with_rank_creates_title(self):
        form = CreatePersonForm(
            data=self.form_payload(
                username='form_edge_user_with_title',
                email='form.edge.title@example.com',
                new_title='Custom Herald',
                new_title_rank='Grant of Arms',
            )
        )

        self.assertTrue(form.is_valid(), msg=form.errors.as_text())
        created_title = form.cleaned_data['title']
        self.assertEqual(created_title.name, 'Custom Herald')
        self.assertEqual(created_title.rank, 'Grant of Arms')
        self.assertTrue(Title.objects.filter(name='Custom Herald', rank='Grant of Arms').exists())

    def test_blank_membership_normalizes_to_none(self):
        form = CreatePersonForm(
            data=self.form_payload(
                username='form_edge_blank_member',
                email='form.edge.blank@example.com',
                membership='   ',
            )
        )

        self.assertTrue(form.is_valid(), msg=form.errors.as_text())
        self.assertIsNone(form.cleaned_data['membership'])

    def test_membership_rejects_non_digits(self):
        form = CreatePersonForm(
            data=self.form_payload(
                username='form_edge_bad_member',
                email='form.edge.bad@example.com',
                membership='12AB34',
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Enter 1-20 digits.', form.errors['membership'])

    def test_membership_rejects_more_than_twenty_digits(self):
        form = CreatePersonForm(
            data=self.form_payload(
                username='form_edge_long_member',
                email='form.edge.long@example.com',
                membership='123456789012345678901',
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Ensure this value has at most 20 characters (it has 21).', form.errors['membership'])
