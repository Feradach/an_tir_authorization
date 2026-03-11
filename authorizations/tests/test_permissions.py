from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from unittest.mock import patch
from django.test import RequestFactory, TestCase

from authorizations.models import (
    Authorization,
    AuthorizationNote,
    AuthorizationStatus,
    Branch,
    BranchMarshal,
    Discipline,
    Person,
    Sanction,
    User,
    WeaponStyle,
)
from authorizations.permissions import (
    appoint_branch_marshal,
    approve_authorization,
    authorization_note_office_label,
    authorization_follows_rules,
    authorization_requires_concurrence,
    calculate_authorization_expiration,
    create_authorization_note,
    is_kingdom_authorization_officer,
    is_kingdom_marshal,
    is_regional_marshal,
    is_senior_marshal,
    membership_is_current,
)


class AuthorizationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Statuses
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')
        cls.status_pending_background_check = AuthorizationStatus.objects.create(name='Pending Background Check')
        cls.status_pending_waiver = AuthorizationStatus.objects.create(name='Pending Waiver')
        cls.status_needs_concurrence = AuthorizationStatus.objects.create(name='Needs Concurrence')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_rejected = AuthorizationStatus.objects.create(name='Rejected')

        # Branches
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)

        # Disciplines
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier Combat')
        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.discipline_youth_rapier = Discipline.objects.create(name='Youth Rapier')
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.discipline_siege = Discipline.objects.create(name='Siege')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_earl_marshal = Discipline.objects.create(name='Earl Marshal')

        # Styles
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_weapon_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_jm_youth_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_sword_youth_armored = WeaponStyle.objects.create(name='Sword', discipline=cls.discipline_youth_armored)
        cls.style_sword_youth_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_youth_rapier)
        cls.style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_equestrian)
        cls.style_junior_ground_crew = WeaponStyle.objects.create(name='Junior Ground Crew', discipline=cls.discipline_equestrian)
        cls.style_senior_ground_crew = WeaponStyle.objects.create(name='Senior Ground Crew', discipline=cls.discipline_equestrian)
        cls.style_general_riding = WeaponStyle.objects.create(name='General Riding', discipline=cls.discipline_equestrian)
        cls.style_siege_engine = WeaponStyle.objects.create(name='Siege Engine', discipline=cls.discipline_siege)
        cls.style_mounted_gaming = WeaponStyle.objects.create(name='Mounted Gaming', discipline=cls.discipline_equestrian)
        cls.style_mounted_archery = WeaponStyle.objects.create(name='Mounted Archery', discipline=cls.discipline_equestrian)
        cls.style_crest_combat = WeaponStyle.objects.create(name='Crest Combat', discipline=cls.discipline_equestrian)
        cls.style_mounted_heavy_combat = WeaponStyle.objects.create(name='Mounted Heavy Combat', discipline=cls.discipline_equestrian)
        cls.style_driving = WeaponStyle.objects.create(name='Driving', discipline=cls.discipline_equestrian)
        cls.style_foam_tipped_jousting = WeaponStyle.objects.create(name='Foam-Tipped Jousting', discipline=cls.discipline_equestrian)

    def setUp(self):
        self.factory = RequestFactory()
        self._membership_seed = 100000

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
        country='United States',
        state_province='Oregon',
        background_check_expiration=None,
        waiver_expiration=None,
    ):
        if membership == 'auto':
            membership = self._next_membership()
        if membership_expiration == 'auto':
            membership_expiration = date.today() + relativedelta(years=1)

        user = User.objects.create_user(
            username=username,
            password='StrongPass!123',
            email=f'{username}@example.com',
            first_name=sca_name.split()[0],
            last_name='Tester',
            membership=membership,
            membership_expiration=membership_expiration,
            birthday=birthday,
            country=country,
            state_province=state_province,
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


class MembershipCurrentTests(AuthorizationTestBase):
    def test_returns_false_without_membership(self):
        user, _ = self.make_person('no_membership', 'No Membership', membership=None, membership_expiration=None)
        self.assertFalse(membership_is_current(user))

    def test_returns_false_without_membership_expiration(self):
        user, _ = self.make_person(
            'no_membership_exp',
            'No Membership Exp',
            membership=self._next_membership(),
            membership_expiration=None,
        )
        self.assertFalse(membership_is_current(user))

    def test_returns_false_when_membership_expired(self):
        user, _ = self.make_person(
            'expired_membership',
            'Expired Membership',
            membership_expiration=date.today() - timedelta(days=1),
        )
        self.assertFalse(membership_is_current(user))

    def test_returns_true_when_membership_is_current(self):
        user, _ = self.make_person('current_member', 'Current Member')
        self.assertTrue(membership_is_current(user))


class EffectiveExpirationTests(AuthorizationTestBase):
    def test_non_marshal_effective_expiration_equals_base_expiration(self):
        _, fighter = self.make_person('fighter_non_marshal', 'Fighter Non-Marshal')
        base_exp = date.today() + relativedelta(years=3)
        auth = self.grant_authorization(fighter, self.style_weapon_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, base_exp)

    def test_marshal_effective_expiration_is_limited_by_membership(self):
        user, fighter = self.make_person(
            'fighter_marshal',
            'Fighter Marshal',
            membership_expiration=date.today() + timedelta(days=90),
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_sm_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, user.membership_expiration)

    def test_youth_marshal_effective_expiration_is_limited_by_background_check(self):
        user, fighter = self.make_person(
            'fighter_youth_marshal',
            'Fighter Youth Marshal',
            membership_expiration=date.today() + relativedelta(years=2),
            background_check_expiration=date.today() + timedelta(days=60),
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_sm_youth_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, user.background_check_expiration)

    def test_queryset_annotation_supports_filter_and_sort(self):
        user_a, fighter_a = self.make_person(
            'fighter_sort_a',
            'Fighter Sort A',
            membership_expiration=date.today() + relativedelta(years=2),
            background_check_expiration=date.today() + timedelta(days=45),
        )
        user_b, fighter_b = self.make_person(
            'fighter_sort_b',
            'Fighter Sort B',
            membership_expiration=date.today() + relativedelta(years=2),
            background_check_expiration=date.today() + timedelta(days=120),
        )
        auth_a = self.grant_authorization(
            fighter_a,
            self.style_sm_youth_armored,
            expiration=date.today() + relativedelta(years=2),
        )
        auth_b = self.grant_authorization(
            fighter_b,
            self.style_sm_youth_armored,
            expiration=date.today() + relativedelta(years=2),
        )

        sorted_ids = list(
            Authorization.objects.with_effective_expiration()
            .filter(style=self.style_sm_youth_armored)
            .order_by('effective_expiration_date')
            .values_list('id', flat=True)
        )
        self.assertEqual(sorted_ids, [auth_a.id, auth_b.id])

        filtered_ids = list(
            Authorization.objects.with_effective_expiration()
            .filter(
                style=self.style_sm_youth_armored,
                effective_expiration_date__gte=date.today() + timedelta(days=90),
            )
            .values_list('id', flat=True)
        )
        self.assertEqual(filtered_ids, [auth_b.id])


class AuthorizationExpirationCalculationTests(AuthorizationTestBase):
    def test_adult_non_youth_defaults_to_four_years(self):
        _, fighter = self.make_person('adult_non_youth', 'Adult Non Youth', is_minor=False)

        expected = date.today() + relativedelta(years=4)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_weapon_armored), expected)

    def test_adult_youth_defaults_to_two_years(self):
        _, fighter = self.make_person('adult_youth', 'Adult Youth', is_minor=False)

        expected = date.today() + relativedelta(years=2)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_sword_youth_armored), expected)

    def test_minor_us_expiration_is_capped_at_age_18(self):
        birthday = date.today() - relativedelta(years=17, months=11)
        _, fighter = self.make_person(
            'minor_us',
            'Minor US',
            is_minor=True,
            birthday=birthday,
            country='United States',
            state_province='Oregon',
        )

        expected = birthday + relativedelta(years=18)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_weapon_armored), expected)

    def test_minor_canada_expiration_is_capped_at_age_19_by_country(self):
        birthday = date.today() - relativedelta(years=18, months=11)
        _, fighter = self.make_person(
            'minor_ca_country',
            'Minor CA Country',
            is_minor=True,
            birthday=birthday,
            country='Canada',
            state_province='British Columbia',
        )

        expected = birthday + relativedelta(years=19)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_weapon_armored), expected)

    def test_minor_canada_expiration_is_capped_at_age_19_by_province(self):
        birthday = date.today() - relativedelta(years=18, months=11)
        _, fighter = self.make_person(
            'minor_ca_province',
            'Minor CA Province',
            is_minor=True,
            birthday=birthday,
            country='',
            state_province='BC',
        )

        expected = birthday + relativedelta(years=19)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_weapon_armored), expected)


class MarshalRoleCheckTests(AuthorizationTestBase):
    def test_is_senior_marshal_true_with_current_membership_and_active_authorization(self):
        user, marshal = self.make_person('sm_true', 'Senior Marshal True')
        self.grant_authorization(marshal, self.style_sm_armored)

        self.assertTrue(is_senior_marshal(user, 'Armored'))
        self.assertFalse(is_senior_marshal(user, 'Rapier Combat'))

    def test_is_senior_marshal_false_when_membership_expired(self):
        user, marshal = self.make_person('sm_false_expired', 'Senior Marshal Expired')
        self.grant_authorization(marshal, self.style_sm_armored)
        user.membership_expiration = date.today() - timedelta(days=1)
        user.save()

        self.assertFalse(is_senior_marshal(user, 'Armored'))

    def test_is_regional_marshal_checks_region_and_discipline(self):
        user, marshal = self.make_person('regional_user', 'Regional User')
        self.grant_authorization(marshal, self.style_sm_armored)
        self.appoint(marshal, self.region_summits, self.discipline_armored)

        self.assertTrue(is_regional_marshal(user, 'Armored', 'Summits'))
        self.assertFalse(is_regional_marshal(user, 'Armored', 'Tir Righ'))

    def test_is_kingdom_marshal_by_branch_assignment(self):
        user, marshal = self.make_person('kingdom_user', 'Kingdom User')
        self.grant_authorization(marshal, self.style_sm_armored)
        self.appoint(marshal, self.branch_an_tir, self.discipline_armored)

        self.assertTrue(is_kingdom_marshal(user, 'Armored'))
        self.assertFalse(is_kingdom_marshal(user, 'Rapier Combat'))

    def test_authorization_officer_requires_current_membership(self):
        user, marshal = self.make_person('ao_user', 'AO User')
        self.appoint(marshal, self.branch_an_tir, self.discipline_auth_officer)

        self.assertTrue(is_kingdom_authorization_officer(user))

        user.membership_expiration = date.today() - timedelta(days=1)
        user.save()

        self.assertFalse(is_kingdom_authorization_officer(user))

    def test_earl_marshal_office_does_not_grant_senior_marshal_status(self):
        user, marshal = self.make_person('earl_no_sm', 'Earl No SM')
        self.appoint(marshal, self.branch_an_tir, self.discipline_earl_marshal)

        self.assertFalse(is_senior_marshal(user, 'Armored'))


class AuthorizationRuleTests(AuthorizationTestBase):
    def test_blocks_self_authorization(self):
        user, marshal = self.make_person('self_auth_user', 'Self Auth User')
        self.grant_authorization(marshal, self.style_sm_armored)

        ok, msg = authorization_follows_rules(user, marshal, self.style_weapon_armored.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot make an authorization for yourself.')

    def test_requires_senior_marshal_in_matching_discipline(self):
        marshal_user, marshal = self.make_person('discipline_marshal', 'Discipline Marshal')
        _, fighter = self.make_person('discipline_target', 'Discipline Target')
        self.grant_authorization(marshal, self.style_sm_armored)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_single_rapier.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must have a current Rapier Combat senior marshal.')

    def test_blocks_armored_authorization_for_minor_under_16(self):
        marshal_user, marshal = self.make_person('age_marshal', 'Age Marshal')
        self.grant_authorization(marshal, self.style_sm_armored)

        birthday = date.today() - relativedelta(years=15)
        _, minor = self.make_person(
            'age_minor',
            'Age Minor',
            is_minor=True,
            birthday=birthday,
        )

        ok, msg = authorization_follows_rules(marshal_user, minor, self.style_weapon_armored.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must be at least 16 years old to become authorized in Armored combat.')

    def test_youth_marshal_no_longer_requires_background_check_at_proposal_time(self):
        marshal_user, marshal = self.make_person('youth_marshal', 'Youth Marshal')
        self.grant_authorization(marshal, self.style_sm_youth_armored)

        _, fighter = self.make_person(
            'no_bg_target',
            'No BG Target',
            background_check_expiration=None,
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_jm_youth_armored.id)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_blocks_pending_duplicate_authorization(self):
        marshal_user, marshal = self.make_person('pending_marshal', 'Pending Marshal')
        _, fighter = self.make_person('pending_target', 'Pending Target')
        self.grant_authorization(marshal, self.style_sm_armored)
        self.grant_authorization(fighter, self.style_weapon_armored, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_weapon_armored.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot renew a pending authorization.')

    def test_blocks_sanctioned_authorization(self):
        marshal_user, marshal = self.make_person('sanctioned_marshal', 'Sanctioned Marshal')
        _, fighter = self.make_person('sanctioned_target', 'Sanctioned Target')
        self.grant_authorization(marshal, self.style_sm_armored)
        Sanction.objects.create(
            person=fighter,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Sanctioned for testing.',
            issued_by=marshal_user,
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_weapon_armored.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot issue an authorization while a sanction is active for this style or discipline.')

    def test_valid_authorization_path(self):
        marshal_user, marshal = self.make_person('valid_marshal', 'Valid Marshal')
        _, fighter = self.make_person('valid_target', 'Valid Target')
        self.grant_authorization(marshal, self.style_sm_armored)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_weapon_armored.id)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_senior_ground_crew_requires_junior_ground_crew(self):
        marshal_user, marshal = self.make_person('eq_sm_sgc', 'Eq SM SGC')
        _, fighter = self.make_person('eq_target_sgc', 'Eq Target SGC')
        self.grant_authorization(marshal, self.style_sm_equestrian)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_senior_ground_crew.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Senior Ground Crew requires an active Junior Ground Crew authorization.')

    def test_mounted_gaming_requires_general_riding(self):
        marshal_user, marshal = self.make_person('eq_sm_mg', 'Eq SM MG')
        _, fighter = self.make_person('eq_target_mg', 'Eq Target MG')
        self.grant_authorization(marshal, self.style_sm_equestrian)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_gaming.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Mounted Gaming requires an active General Riding authorization.')

    def test_mounted_archery_requires_mounted_gaming(self):
        marshal_user, marshal = self.make_person('eq_sm_ma', 'Eq SM MA')
        _, fighter = self.make_person('eq_target_ma', 'Eq Target MA')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_general_riding)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_archery.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Mounted Archery requires an active Mounted Gaming authorization.')

    def test_first_time_special_requires_same_skill_marshal_but_renewal_does_not(self):
        marshal_user, marshal = self.make_person('eq_sm_special', 'Eq SM Special')
        _, first_timer = self.make_person('eq_first_special', 'Eq First Special')
        _, returning = self.make_person('eq_return_special', 'Eq Return Special')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(first_timer, self.style_general_riding)
        self.grant_authorization(first_timer, self.style_mounted_gaming)
        self.grant_authorization(returning, self.style_general_riding)
        self.grant_authorization(returning, self.style_mounted_gaming)
        self.grant_authorization(
            returning,
            self.style_mounted_archery,
            expiration=date.today() - relativedelta(days=30),
        )

        first_ok, first_msg = authorization_follows_rules(marshal_user, first_timer, self.style_mounted_archery.id)
        return_ok, return_msg = authorization_follows_rules(marshal_user, returning, self.style_mounted_archery.id)

        self.assertFalse(first_ok)
        self.assertEqual(
            first_msg,
            'Must be authorized in Mounted Archery to authorize a first-time participant in this skill.',
        )
        self.assertTrue(return_ok)
        self.assertEqual(return_msg, 'Authorization follows all rules.')


class ConcurrenceRequirementTests(AuthorizationTestBase):
    def test_requires_concurrence_when_no_prior_authorization_in_discipline(self):
        _, fighter = self.make_person('concur_none', 'Concur None')

        self.assertTrue(authorization_requires_concurrence(fighter, self.style_weapon_armored))

    def test_does_not_require_concurrence_for_recently_expired_authorization(self):
        _, fighter = self.make_person('concur_recent', 'Concur Recent')
        self.grant_authorization(
            fighter,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() - timedelta(days=200),
        )

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_weapon_armored))

    def test_requires_concurrence_for_lapsed_authorization_older_than_one_year(self):
        _, fighter = self.make_person('concur_old', 'Concur Old')
        self.grant_authorization(
            fighter,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() - timedelta(days=400),
        )

        self.assertTrue(authorization_requires_concurrence(fighter, self.style_weapon_armored))

    def test_marshal_styles_never_require_concurrence(self):
        _, fighter = self.make_person('concur_marshal', 'Concur Marshal')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_sm_armored))

    def test_equestrian_non_marshal_never_requires_concurrence(self):
        _, fighter = self.make_person('concur_eq_exempt', 'Concur EQ Exempt')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_general_riding))

    def test_siege_non_marshal_never_requires_concurrence(self):
        _, fighter = self.make_person('concur_siege_exempt', 'Concur Siege Exempt')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_siege_engine))

    def test_youth_armored_non_marshal_never_requires_concurrence(self):
        _, fighter = self.make_person('concur_ya_exempt', 'Concur YA Exempt')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_sword_youth_armored))

    def test_youth_rapier_non_marshal_never_requires_concurrence(self):
        _, fighter = self.make_person('concur_yr_exempt', 'Concur YR Exempt')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_sword_youth_rapier))


class ApproveAuthorizationTests(AuthorizationTestBase):
    def test_pending_junior_marshal_is_approved_by_different_senior_marshal(self):
        _, fighter = self.make_person('pending_jm_target', 'Pending JM Target')
        proposer_user, proposer = self.make_person('pending_jm_proposer', 'Pending JM Proposer')
        approver_user, approver = self.make_person('pending_jm_approver', 'Pending JM Approver')
        self.grant_authorization(proposer, self.style_sm_armored)
        self.grant_authorization(approver, self.style_sm_armored)

        pending_auth = self.grant_authorization(
            fighter,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_auth.id), 'action_note': 'Concurred at event'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        pending_auth.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Junior Marshal authorization approved!')
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=pending_auth,
                action='marshal_concurred',
                created_by=approver_user,
            ).exists()
        )

    def test_pending_youth_junior_marshal_moves_to_pending_background_check(self):
        _, fighter = self.make_person(
            'pending_youth_jm_target',
            'Pending Youth JM Target',
            background_check_expiration=None,
        )
        proposer_user, proposer = self.make_person(
            'pending_youth_jm_proposer',
            'Pending Youth JM Proposer',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        approver_user, approver = self.make_person(
            'pending_youth_jm_approver',
            'Pending Youth JM Approver',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(proposer, self.style_sm_youth_armored)
        self.grant_authorization(approver, self.style_sm_youth_armored)

        pending_auth = self.grant_authorization(
            fighter,
            self.style_jm_youth_armored,
            status=self.status_pending,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_auth.id), 'action_note': 'Youth JM concurrence'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        pending_auth.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Youth Armored Junior Marshal authorization pending background check.')
        self.assertEqual(pending_auth.status, self.status_pending_background_check)

    def test_needs_regional_youth_senior_moves_to_pending_background_check(self):
        proposer_user, proposer = self.make_person('regional_youth_sm_prop', 'Regional Youth SM Prop')
        approver_user, approver = self.make_person(
            'regional_youth_sm_approver',
            'Regional Youth SM Approver',
            branch=self.branch_gd,
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        _, fighter = self.make_person(
            'regional_youth_sm_target',
            'Regional Youth SM Target',
            branch=self.branch_gd,
            background_check_expiration=None,
        )
        self.grant_authorization(approver, self.style_sm_youth_armored)
        self.appoint(approver, self.region_summits, self.discipline_youth_armored)

        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_youth_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Youth SM regional confirmation'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        needs_regional.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Youth Armored Senior Marshal authorization pending background check.')
        self.assertEqual(needs_regional.status, self.status_pending_background_check)

    def test_needs_kingdom_youth_senior_moves_to_pending_background_check(self):
        ao_user, ao_person = self.make_person('kingdom_youth_sm_ao', 'Kingdom Youth SM AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer = self.make_person('kingdom_youth_sm_prop', 'Kingdom Youth SM Prop')
        _, fighter = self.make_person(
            'kingdom_youth_sm_target',
            'Kingdom Youth SM Target',
            background_check_expiration=None,
        )

        needs_kingdom = self.grant_authorization(
            fighter,
            self.style_sm_youth_armored,
            status=self.status_kingdom,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_kingdom.id)},
        )
        request.user = ao_user

        ok, msg = approve_authorization(request)

        needs_kingdom.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Youth Armored Senior Marshal authorization pending background check.')
        self.assertEqual(needs_kingdom.status, self.status_pending_background_check)

    def test_pending_senior_marshal_moves_to_regional_approval(self):
        _, fighter = self.make_person('pending_sm_target', 'Pending SM Target')
        proposer_user, proposer = self.make_person('pending_sm_proposer', 'Pending SM Proposer')
        approver_user, approver = self.make_person('pending_sm_approver', 'Pending SM Approver')
        self.grant_authorization(proposer, self.style_sm_armored)
        self.grant_authorization(approver, self.style_sm_armored)

        pending_auth = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_pending,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_auth.id), 'action_note': 'Eligible for regional review'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        pending_auth.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Senior Marshal authorization ready for regional approval!')
        self.assertEqual(pending_auth.status, self.status_regional)

    def test_kingdom_earl_marshal_cannot_concur_pending_without_senior_marshal(self):
        proposer_user, proposer = self.make_person('earl_pending_prop', 'Earl Pending Prop')
        earl_user, earl_person = self.make_person('earl_pending_actor', 'Earl Pending Actor')
        _, fighter = self.make_person('earl_pending_target', 'Earl Pending Target')
        self.grant_authorization(proposer, self.style_sm_armored)
        self.appoint(earl_person, self.branch_an_tir, self.discipline_earl_marshal)

        pending_auth = self.grant_authorization(
            fighter,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_auth.id), 'action_note': 'Attempting concurrence as Earl Marshal'},
        )
        request.user = earl_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You must be a senior marshal in this discipline to approve this authorization.')

    def test_cannot_concur_with_own_pending_authorization(self):
        _, fighter = self.make_person('self_concur_target', 'Self Concur Target')
        proposer_user, proposer = self.make_person('self_concur_proposer', 'Self Concur Proposer')
        self.grant_authorization(proposer, self.style_sm_armored)

        pending_auth = self.grant_authorization(
            fighter,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_auth.id), 'action_note': 'Attempting own concurrence'},
        )
        request.user = proposer_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You cannot concur with your own authorization.')

    def test_regional_approval_requires_same_region_when_fighter_branch_is_region(self):
        proposer_user, proposer = self.make_person('regional_proposer', 'Regional Proposer')
        approver_user, approver = self.make_person('regional_wrong_approver', 'Regional Wrong Approver')
        _, fighter = self.make_person('regional_target', 'Regional Target', branch=self.region_summits)

        self.appoint(approver, self.region_tir_righ, self.discipline_armored)
        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Attempting out-of-region approval'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You must be a regional marshal in the same region as the fighter to approve this authorization.')

    def test_regional_approval_requires_same_region_when_fighter_branch_is_local_branch(self):
        proposer_user, proposer = self.make_person('regional_local_proposer', 'Regional Local Proposer')
        approver_user, approver = self.make_person('regional_local_wrong', 'Regional Local Wrong')
        _, fighter = self.make_person('regional_local_target', 'Regional Local Target', branch=self.branch_gd)

        self.appoint(approver, self.region_tir_righ, self.discipline_armored)
        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Attempting out-of-region approval'},
        )
        request.user = approver_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You must be a regional marshal in the same region as the fighter to approve this authorization.')

    def test_regional_earl_marshal_can_do_regional_confirmation_any_discipline_in_region(self):
        proposer_user, proposer = self.make_person('regional_earl_prop', 'Regional Earl Prop')
        earl_user, earl_person = self.make_person('regional_earl_actor', 'Regional Earl Actor')
        _, fighter = self.make_person('regional_earl_target', 'Regional Earl Target', branch=self.branch_gd)
        self.grant_authorization(earl_person, self.style_sm_armored)
        self.appoint(earl_person, self.region_summits, self.discipline_earl_marshal)

        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Regional Earl confirmation'},
        )
        request.user = earl_user

        ok, msg = approve_authorization(request)

        needs_regional.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Senior Marshal authorization approved!')
        self.assertEqual(needs_regional.status, self.status_active)

    def test_regional_earl_marshal_cannot_do_regional_confirmation_outside_region(self):
        proposer_user, proposer = self.make_person('regional_earl_oor_prop', 'Regional Earl OOR Prop')
        earl_user, earl_person = self.make_person('regional_earl_oor_actor', 'Regional Earl OOR Actor')
        _, fighter = self.make_person('regional_earl_oor_target', 'Regional Earl OOR Target', branch=self.branch_lg)
        self.appoint(earl_person, self.region_summits, self.discipline_earl_marshal)

        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Regional Earl out-of-region attempt'},
        )
        request.user = earl_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You must be a regional marshal in the same region as the fighter to approve this authorization.')

    def test_kingdom_earl_marshal_can_do_regional_confirmation_any_region(self):
        proposer_user, proposer = self.make_person('kingdom_earl_prop', 'Kingdom Earl Prop')
        earl_user, earl_person = self.make_person('kingdom_earl_actor', 'Kingdom Earl Actor')
        _, fighter = self.make_person('kingdom_earl_target', 'Kingdom Earl Target', branch=self.branch_lg)
        self.grant_authorization(earl_person, self.style_sm_armored)
        self.appoint(earl_person, self.branch_an_tir, self.discipline_earl_marshal)

        needs_regional = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_regional.id), 'action_note': 'Kingdom Earl regional confirmation'},
        )
        request.user = earl_user

        ok, msg = approve_authorization(request)

        needs_regional.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Senior Marshal authorization approved!')
        self.assertEqual(needs_regional.status, self.status_active)

    def test_kingdom_earl_marshal_cannot_do_kingdom_confirmation(self):
        proposer_user, proposer = self.make_person('kingdom_earl_kingdom_prop', 'Kingdom Earl Kingdom Prop')
        earl_user, earl_person = self.make_person('kingdom_earl_kingdom_actor', 'Kingdom Earl Kingdom Actor')
        _, fighter = self.make_person('kingdom_earl_kingdom_target', 'Kingdom Earl Kingdom Target')
        self.appoint(earl_person, self.branch_an_tir, self.discipline_earl_marshal)

        needs_kingdom = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_kingdom,
            marshal=proposer,
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(needs_kingdom.id), 'action_note': 'Kingdom Earl kingdom confirmation attempt'},
        )
        request.user = earl_user

        ok, msg = approve_authorization(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Only the Kingdom Authorization Officer can approve this authorization.')

    def test_authorization_officer_final_approval_sets_active_and_removes_junior(self):
        ao_user, ao_person = self.make_person('ao_approver', 'AO Approver')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        proposer_user, proposer = self.make_person('ao_proposer', 'AO Proposer')
        _, fighter = self.make_person('ao_target', 'AO Target')

        junior_auth = self.grant_authorization(fighter, self.style_jm_armored, status=self.status_active, marshal=proposer)
        pending_senior = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_kingdom,
            marshal=proposer,
            expiration=date.today() + timedelta(days=20),
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(pending_senior.id), 'action_note': 'AO final approval'},
        )
        request.user = ao_user

        ok, msg = approve_authorization(request)

        pending_senior.refresh_from_db()
        fighter.user.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Senior Marshal authorization approved!')
        self.assertEqual(pending_senior.status, self.status_active)
        self.assertFalse(Authorization.objects.filter(id=junior_auth.id).exists())
        self.assertGreaterEqual(fighter.user.waiver_expiration, pending_senior.expiration)
        self.assertFalse(
            AuthorizationNote.objects.filter(
                authorization=pending_senior,
                action='marshal_approved',
            ).exists()
        )

    def test_authorization_officer_submit_as_kingdom_final_approval_still_succeeds_without_note(self):
        ao_user, ao_person = self.make_person('ao_submit_as', 'AO Submit As')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        submit_as_user, _ = self.make_person('ao_submit_as_target', 'Bob Marshal')
        _, proposer = self.make_person('ao_submit_as_prop', 'SubmitAs Proposer')
        _, fighter = self.make_person('ao_submit_as_fighter', 'SubmitAs Fighter')

        pending_senior = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_kingdom,
            marshal=proposer,
            expiration=date.today() + timedelta(days=20),
        )

        request = self.factory.post(
            '/authorizations/fighter/',
            {
                'authorization_id': str(pending_senior.id),
                'action_note': 'AO confirmation note',
                'submit_as_user_id': str(submit_as_user.id),
            },
        )
        request.user = ao_user

        ok, msg = approve_authorization(request)

        pending_senior.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Senior Marshal authorization approved!')
        self.assertEqual(pending_senior.status, self.status_active)
        self.assertFalse(
            AuthorizationNote.objects.filter(
                authorization=pending_senior,
                action='marshal_approved',
            ).exists()
        )


class AuthorizationNoteOfficeTests(AuthorizationTestBase):
    def test_uses_relevant_current_office_for_matching_discipline(self):
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        user, person = self.make_person('office_rapier_user', 'Office Rapier User')
        _, fighter = self.make_person('office_rapier_target', 'Office Rapier Target')
        self.grant_authorization(person, self.style_sm_armored)
        self.grant_authorization(person, style_sm_rapier)
        self.appoint(person, self.branch_an_tir, self.discipline_rapier)
        authorization = self.grant_authorization(
            fighter,
            style_sm_rapier,
            status=self.status_pending,
            marshal=person,
        )

        label = authorization_note_office_label(user, authorization, 'marshal_proposed')

        self.assertEqual(label, 'Kingdom Rapier Marshal')

    def test_falls_back_to_marshal_status_when_office_is_not_relevant(self):
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        user, person = self.make_person('office_fallback_user', 'Office Fallback User')
        _, fighter = self.make_person('office_fallback_target', 'Office Fallback Target')
        self.grant_authorization(person, self.style_sm_armored)
        self.grant_authorization(person, style_sm_rapier)
        self.appoint(person, self.branch_an_tir, self.discipline_rapier)
        authorization = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_pending,
            marshal=person,
        )

        label = authorization_note_office_label(user, authorization, 'marshal_proposed')

        self.assertEqual(label, 'Senior Marshal')

    def test_logs_and_uses_best_match_when_multiple_active_offices_exist(self):
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        user, person = self.make_person('office_multi_user', 'Office Multi User')
        _, fighter = self.make_person('office_multi_target', 'Office Multi Target')
        self.grant_authorization(person, self.style_sm_armored)
        self.grant_authorization(person, style_sm_rapier)
        armored_office = self.appoint(person, self.branch_an_tir, self.discipline_armored)
        rapier_office = self.appoint(person, self.branch_an_tir, self.discipline_rapier)
        authorization = self.grant_authorization(
            fighter,
            self.style_sm_armored,
            status=self.status_pending,
            marshal=person,
        )

        with patch('authorizations.permissions.logger.error') as mocked_error:
            note = create_authorization_note(
                authorization=authorization,
                created_by=user,
                action='marshal_proposed',
                note='Testing office selection',
            )

        self.assertEqual(note.office, 'Kingdom Armored Marshal')
        mocked_error.assert_called()
        logged_ids = mocked_error.call_args_list[0].args[-1]
        self.assertIn(armored_office.id, logged_ids)
        self.assertIn(rapier_office.id, logged_ids)


class AppointBranchMarshalTests(AuthorizationTestBase):
    def test_non_authorization_officer_cannot_appoint_branch_marshal(self):
        normal_user, _ = self.make_person('appoint_normal_user', 'Appoint Normal User')
        _, candidate = self.make_person('appoint_candidate_user', 'Appoint Candidate User')
        self.grant_authorization(candidate, self.style_sm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = normal_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You do not have authority to appoint this marshal office.')

    def test_authorization_officer_can_appoint_local_branch_marshal_with_junior(self):
        ao_user, ao_person = self.make_person('appoint_ao_user', 'Appoint AO User')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate = self.make_person('appoint_local_candidate', 'Appoint Local Candidate')
        self.grant_authorization(candidate, self.style_jm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Branch marshal appointed.')
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=candidate,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
            ).exists()
        )

    def test_regional_branch_marshal_requires_senior_marshal(self):
        ao_user, ao_person = self.make_person('appoint_ao_user_regional', 'Appoint AO User Regional')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate = self.make_person('appoint_regional_candidate', 'Appoint Regional Candidate')
        self.grant_authorization(candidate, self.style_jm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.region_summits.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must be a senior marshal to be a regional marshal.')

    def test_only_one_active_branch_marshal_position_allowed(self):
        ao_user, ao_person = self.make_person('appoint_ao_user_single', 'Appoint AO User Single')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate = self.make_person('appoint_single_candidate', 'Appoint Single Candidate')
        self.grant_authorization(candidate, self.style_sm_armored)
        self.appoint(candidate, self.branch_lg, self.discipline_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Can only serve as one branch marshal position at a time.')

    def test_duplicate_active_branch_marshal_position_is_blocked(self):
        ao_user, ao_person = self.make_person('appoint_ao_user_duplicate', 'Appoint AO User Duplicate')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate = self.make_person('appoint_duplicate_candidate', 'Appoint Duplicate Candidate')
        self.grant_authorization(candidate, self.style_sm_armored)
        self.appoint(candidate, self.branch_gd, self.discipline_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'This fighter already holds this active marshal office.')

    def test_two_people_can_hold_same_active_branch_marshal_office(self):
        ao_user, ao_person = self.make_person('appoint_ao_user_shared', 'Appoint AO User Shared')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate_one = self.make_person('appoint_shared_candidate_one', 'Appoint Shared Candidate One')
        _, candidate_two = self.make_person('appoint_shared_candidate_two', 'Appoint Shared Candidate Two')
        self.grant_authorization(candidate_one, self.style_sm_armored)
        self.grant_authorization(candidate_two, self.style_sm_armored)

        request_one = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate_one.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request_one.user = ao_user
        ok_one, msg_one = appoint_branch_marshal(request_one)

        request_two = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate_two.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request_two.user = ao_user
        ok_two, msg_two = appoint_branch_marshal(request_two)

        self.assertTrue(ok_one)
        self.assertEqual(msg_one, 'Branch marshal appointed.')
        self.assertTrue(ok_two)
        self.assertEqual(msg_two, 'Branch marshal appointed.')

    def test_candidate_must_have_current_membership(self):
        ao_user, ao_person = self.make_person('appoint_ao_user_membership', 'Appoint AO User Membership')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)

        _, candidate = self.make_person(
            'appoint_expired_candidate',
            'Appoint Expired Candidate',
            membership_expiration=date.today() - timedelta(days=1),
        )
        self.grant_authorization(candidate, self.style_sm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person': candidate.sca_name,
                'branch': self.branch_gd.name,
                'discipline': self.discipline_armored.name,
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must be a current member to be a branch marshal.')
