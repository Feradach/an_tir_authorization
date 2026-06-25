from datetime import date, timedelta
from io import StringIO

from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from unittest.mock import patch
from django.test import RequestFactory, TestCase, override_settings

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
    is_kingdom_equestrian_authorization_officer,
    is_kingdom_marshal,
    is_kingdom_seneschal,
    is_regional_marshal,
    is_senior_marshal,
    membership_is_current,
)


class AuthorizationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Statuses
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Awaiting Second Marshal Concurrence')
        cls.status_regional = AuthorizationStatus.objects.create(name='Awaiting Regional Marshal Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Awaiting Kingdom Authorization Officer Review')
        cls.status_pending_background_check = AuthorizationStatus.objects.create(name='Awaiting Background Check')
        cls.status_pending_waiver = AuthorizationStatus.objects.create(name='Awaiting Waiver')
        cls.status_needs_concurrence = AuthorizationStatus.objects.create(name='Awaiting Fighter Concurrence')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_rejected = AuthorizationStatus.objects.create(name='Rejected')
        cls.status_inactive = AuthorizationStatus.objects.create(name='Inactive')

        # Branches
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.principality_summits = Branch.objects.create(name='Principality of the Summits', type='Principality', region=cls.branch_an_tir)
        cls.principality_tir_righ = Branch.objects.create(name='Principality of Tir Righ', type='Principality', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.branch_summits_shire = Branch.objects.create(name='Shire of Test Summits', type='Shire', region=cls.principality_summits)
        cls.branch_tir_righ_shire = Branch.objects.create(name='Shire of Test Tir Righ', type='Shire', region=cls.principality_tir_righ)
        cls.branch_inlands = Branch.objects.create(name='Inlands', type='Region', region=cls.branch_an_tir)
        cls.branch_other = Branch.objects.create(name='Special Other', type='Other', region=cls.branch_an_tir)

        # Disciplines
        cls.discipline_armored = Discipline.objects.create(name='Armored Combat')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier Combat')
        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.discipline_youth_rapier = Discipline.objects.create(name='Youth Rapier')
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.discipline_siege = Discipline.objects.create(name='Siege')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_equestrian_auth_officer = Discipline.objects.create(name='Equestrian Authorization Officer')
        cls.discipline_seneschal, _ = Discipline.objects.get_or_create(name='Seneschal')
        cls.discipline_earl_marshal = Discipline.objects.create(name='Earl Marshal')

        # Styles
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_weapon_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_jm_youth_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_sword_youth_armored = WeaponStyle.objects.create(name='Sword', discipline=cls.discipline_youth_armored)
        cls.style_lion_sword_youth_armored = WeaponStyle.objects.create(name='Lion - Sword', discipline=cls.discipline_youth_armored)
        cls.style_gryphon_sword_youth_armored = WeaponStyle.objects.create(name='Gryphon - Sword', discipline=cls.discipline_youth_armored)
        cls.style_dragon_sword_youth_armored = WeaponStyle.objects.create(name='Dragon - Sword', discipline=cls.discipline_youth_armored)
        cls.style_sword_youth_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_youth_rapier)
        cls.style_sm_youth_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_rapier)
        cls.style_gryphon_single_youth_rapier = WeaponStyle.objects.create(name='Gryphon - Single Sword', discipline=cls.discipline_youth_rapier)
        cls.style_gryphon_defensive_youth_rapier = WeaponStyle.objects.create(name='Gryphon - Sword w/Defensive Secondary', discipline=cls.discipline_youth_rapier)
        cls.style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_equestrian)
        cls.style_jm_equestrian = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_equestrian)
        cls.style_junior_ground_crew = WeaponStyle.objects.create(name='Ground Crew - Junior', discipline=cls.discipline_equestrian)
        cls.style_senior_ground_crew = WeaponStyle.objects.create(name='Ground Crew - Senior', discipline=cls.discipline_equestrian)
        cls.style_general_riding = WeaponStyle.objects.create(name='General Riding', discipline=cls.discipline_equestrian)
        cls.style_siege_engine = WeaponStyle.objects.create(name='Siege Engine', discipline=cls.discipline_siege)
        cls.style_mounted_gaming = WeaponStyle.objects.create(name='Mounted Gaming', discipline=cls.discipline_equestrian)
        cls.style_mounted_archery = WeaponStyle.objects.create(name='Mounted Archery', discipline=cls.discipline_equestrian)
        cls.style_crest_combat = WeaponStyle.objects.create(name='Crest Combat', discipline=cls.discipline_equestrian)
        cls.style_mounted_heavy_combat = WeaponStyle.objects.create(name='Mounted Heavy Combat', discipline=cls.discipline_equestrian)
        cls.style_driving = WeaponStyle.objects.create(name='Driving', discipline=cls.discipline_equestrian)
        cls.style_foam_tipped_jousting = WeaponStyle.objects.create(name='Jousting', discipline=cls.discipline_equestrian)

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
    def _date_value(self, value):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    def test_non_marshal_effective_expiration_equals_base_expiration(self):
        _, fighter = self.make_person('fighter_non_marshal', 'Fighter Non-Marshal')
        base_exp = date.today() + relativedelta(years=3)
        auth = self.grant_authorization(fighter, self.style_weapon_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, base_exp)

    def test_rapier_secondary_effective_expiration_is_limited_by_single_sword(self):
        _, fighter = self.make_person('fighter_rapier_secondary', 'Fighter Rapier Secondary')
        secondary_style = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_exp = date.today() + timedelta(days=60)
        secondary_exp = date.today() + relativedelta(years=2)
        self.grant_authorization(fighter, self.style_single_rapier, expiration=single_sword_exp)
        auth = self.grant_authorization(fighter, secondary_style, expiration=secondary_exp)

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, single_sword_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), single_sword_exp)

    def test_rapier_secondary_without_active_single_sword_is_effectively_expired(self):
        _, fighter = self.make_person('fighter_rapier_no_single', 'Fighter Rapier No Single')
        secondary_style = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        auth = self.grant_authorization(
            fighter,
            secondary_style,
            expiration=date.today() + relativedelta(years=2),
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))
        self.assertEqual(
            self._date_value(annotated_auth.effective_expiration_date),
            date.today() - relativedelta(years=1),
        )

    def test_youth_rapier_secondary_effective_expiration_is_limited_by_category_single_sword(self):
        _, fighter = self.make_person(
            'fighter_youth_rapier_secondary',
            'Fighter Youth Rapier Secondary',
            birthday=date.today() - relativedelta(years=12),
        )
        single_sword_exp = date.today() + timedelta(days=45)
        secondary_exp = date.today() + relativedelta(years=1)
        self.grant_authorization(
            fighter,
            self.style_gryphon_single_youth_rapier,
            expiration=single_sword_exp,
        )
        auth = self.grant_authorization(
            fighter,
            self.style_gryphon_defensive_youth_rapier,
            expiration=secondary_exp,
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, single_sword_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), single_sword_exp)

    def test_youth_rapier_secondary_without_active_single_sword_is_effectively_expired(self):
        _, fighter = self.make_person(
            'fighter_youth_rapier_no_single',
            'Fighter Youth Rapier No Single',
            birthday=date.today() - relativedelta(years=12),
        )
        auth = self.grant_authorization(
            fighter,
            self.style_gryphon_defensive_youth_rapier,
            expiration=date.today() + relativedelta(years=1),
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))
        self.assertEqual(
            self._date_value(annotated_auth.effective_expiration_date),
            date.today() - relativedelta(years=1),
        )

    def test_cut_and_thrust_spear_effective_expiration_is_limited_by_foundation_style(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_longsword = WeaponStyle.objects.create(name='Longsword', discipline=discipline_cut_and_thrust)
        style_spear = WeaponStyle.objects.create(name='Spear', discipline=discipline_cut_and_thrust)
        _, fighter = self.make_person('fighter_ct_spear', 'Fighter CT Spear')
        foundation_exp = date.today() + timedelta(days=75)
        spear_exp = date.today() + relativedelta(years=2)
        self.grant_authorization(fighter, style_longsword, expiration=foundation_exp)
        auth = self.grant_authorization(fighter, style_spear, expiration=spear_exp)

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, foundation_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), foundation_exp)

    def test_cut_and_thrust_spear_without_active_foundation_style_is_effectively_expired(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_spear = WeaponStyle.objects.create(name='Spear', discipline=discipline_cut_and_thrust)
        _, fighter = self.make_person('fighter_ct_spear_no_foundation', 'Fighter CT Spear No Foundation')
        auth = self.grant_authorization(
            fighter,
            style_spear,
            expiration=date.today() + relativedelta(years=2),
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))
        self.assertEqual(
            self._date_value(annotated_auth.effective_expiration_date),
            date.today() - relativedelta(years=1),
        )

    def test_mounted_gaming_effective_expiration_is_limited_by_general_riding(self):
        _, fighter = self.make_person('fighter_eq_mounted_gaming', 'Fighter EQ Mounted Gaming')
        general_riding_exp = date.today() + timedelta(days=40)
        mounted_gaming_exp = date.today() + relativedelta(years=2)
        self.grant_authorization(fighter, self.style_general_riding, expiration=general_riding_exp)
        auth = self.grant_authorization(fighter, self.style_mounted_gaming, expiration=mounted_gaming_exp)

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, general_riding_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), general_riding_exp)

    def test_mounted_weapon_game_effective_expiration_is_limited_by_mounted_gaming(self):
        _, fighter = self.make_person('fighter_eq_mounted_archery', 'Fighter EQ Mounted Archery')
        mounted_gaming_exp = date.today() + timedelta(days=50)
        mounted_archery_exp = date.today() + relativedelta(years=2)
        self.grant_authorization(fighter, self.style_mounted_gaming, expiration=mounted_gaming_exp)
        auth = self.grant_authorization(fighter, self.style_mounted_archery, expiration=mounted_archery_exp)

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, mounted_gaming_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), mounted_gaming_exp)

    def test_mounted_heavy_combat_effective_expiration_is_limited_by_mounted_gaming_and_general_riding(self):
        _, fighter = self.make_person('fighter_eq_mounted_heavy', 'Fighter EQ Mounted Heavy')
        general_riding_exp = date.today() + timedelta(days=80)
        mounted_gaming_exp = date.today() + timedelta(days=30)
        heavy_combat_exp = date.today() + relativedelta(years=2)
        self.grant_authorization(fighter, self.style_general_riding, expiration=general_riding_exp)
        self.grant_authorization(fighter, self.style_mounted_gaming, expiration=mounted_gaming_exp)
        auth = self.grant_authorization(fighter, self.style_mounted_heavy_combat, expiration=heavy_combat_exp)

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, mounted_gaming_exp)
        self.assertEqual(self._date_value(annotated_auth.effective_expiration_date), mounted_gaming_exp)

    def test_equestrian_dependent_authorization_without_active_prerequisite_is_effectively_expired(self):
        _, fighter = self.make_person('fighter_eq_no_prereq', 'Fighter EQ No Prereq')
        auth = self.grant_authorization(
            fighter,
            self.style_mounted_gaming,
            expiration=date.today() + relativedelta(years=2),
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))
        self.assertEqual(
            self._date_value(annotated_auth.effective_expiration_date),
            date.today() - relativedelta(years=1),
        )

    def test_marshal_effective_expiration_is_limited_by_membership(self):
        user, fighter = self.make_person(
            'fighter_marshal',
            'Fighter Marshal',
            membership_expiration=date.today() + timedelta(days=90),
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_sm_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, user.membership_expiration)

    def test_junior_marshal_effective_expiration_is_limited_by_membership(self):
        user, fighter = self.make_person(
            'fighter_junior_marshal',
            'Fighter Junior Marshal',
            membership_expiration=date.today() + timedelta(days=90),
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_jm_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, user.membership_expiration)

    def test_marshal_effective_expiration_without_membership_is_expired(self):
        user, fighter = self.make_person(
            'fighter_marshal_no_membership',
            'Fighter Marshal No Membership',
            membership='',
            membership_expiration=None,
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_jm_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))

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

    def test_youth_marshal_effective_expiration_without_background_check_is_expired(self):
        user, fighter = self.make_person(
            'fighter_youth_marshal_no_background',
            'Fighter Youth Marshal No Background',
            membership_expiration=date.today() + relativedelta(years=2),
            background_check_expiration=None,
        )
        base_exp = date.today() + relativedelta(years=2)
        auth = self.grant_authorization(fighter, self.style_sm_youth_armored, expiration=base_exp)

        self.assertEqual(auth.effective_expiration, date.today() - relativedelta(years=1))

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

    def test_queryset_annotation_expires_missing_membership_and_background_check(self):
        _, no_membership_fighter = self.make_person(
            'fighter_sort_no_membership',
            'Fighter Sort No Membership',
            membership='',
            membership_expiration=None,
        )
        _, no_background_fighter = self.make_person(
            'fighter_sort_no_background',
            'Fighter Sort No Background',
            membership_expiration=date.today() + relativedelta(years=2),
            background_check_expiration=None,
        )
        no_membership_auth = self.grant_authorization(
            no_membership_fighter,
            self.style_jm_armored,
            expiration=date.today() + relativedelta(years=2),
        )
        no_background_auth = self.grant_authorization(
            no_background_fighter,
            self.style_sm_youth_armored,
            expiration=date.today() + relativedelta(years=2),
        )

        expirations = {
            auth.id: self._date_value(auth.effective_expiration_date)
            for auth in Authorization.objects.with_effective_expiration().filter(
                id__in=[no_membership_auth.id, no_background_auth.id],
            )
        }

        self.assertEqual(expirations[no_membership_auth.id], date.today() - relativedelta(years=1))
        self.assertEqual(expirations[no_background_auth.id], date.today() - relativedelta(years=1))

    def test_annotated_effective_expiration_property_returns_date(self):
        _, fighter = self.make_person(
            'fighter_annotated_date',
            'Fighter Annotated Date',
            membership_expiration=date.today() + timedelta(days=90),
        )
        auth = self.grant_authorization(
            fighter,
            self.style_jm_armored,
            expiration=date.today() + relativedelta(years=2),
        )

        annotated_auth = Authorization.objects.with_effective_expiration().get(id=auth.id)

        self.assertIsInstance(annotated_auth.effective_expiration, date)
        self.assertLess(annotated_auth.effective_expiration, auth.expiration)


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
            is_minor=False,
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

    def test_youth_category_expiration_is_capped_at_age_out(self):
        birthday = date.today() - relativedelta(years=9, months=11)
        _, fighter = self.make_person(
            'lion_age_out',
            'Lion Age Out',
            birthday=birthday,
        )

        expected = birthday + relativedelta(years=10)
        self.assertEqual(calculate_authorization_expiration(fighter, self.style_lion_sword_youth_armored), expected)


class MarshalRoleCheckTests(AuthorizationTestBase):
    def test_is_senior_marshal_true_with_current_membership_and_active_authorization(self):
        user, marshal = self.make_person('sm_true', 'Senior Marshal True')
        self.grant_authorization(marshal, self.style_sm_armored)

        self.assertTrue(is_senior_marshal(user, 'Armored Combat'))
        self.assertFalse(is_senior_marshal(user, 'Rapier Combat'))

    def test_is_senior_marshal_false_when_membership_expired(self):
        user, marshal = self.make_person('sm_false_expired', 'Senior Marshal Expired')
        self.grant_authorization(marshal, self.style_sm_armored)
        user.membership_expiration = date.today() - timedelta(days=1)
        user.save()

        self.assertFalse(is_senior_marshal(user, 'Armored Combat'))

    def test_is_regional_marshal_checks_region_and_discipline(self):
        user, marshal = self.make_person('regional_user', 'Regional User')
        self.grant_authorization(marshal, self.style_sm_armored)
        self.appoint(marshal, self.region_summits, self.discipline_armored)

        self.assertTrue(is_regional_marshal(user, 'Armored Combat', 'Summits'))
        self.assertFalse(is_regional_marshal(user, 'Armored Combat', 'Tir Righ'))

    def test_is_kingdom_marshal_by_branch_assignment(self):
        user, marshal = self.make_person('kingdom_user', 'Kingdom User')
        self.grant_authorization(marshal, self.style_sm_armored)
        self.appoint(marshal, self.branch_an_tir, self.discipline_armored)

        self.assertTrue(is_kingdom_marshal(user, 'Armored Combat'))
        self.assertFalse(is_kingdom_marshal(user, 'Rapier Combat'))

    def test_authorization_officer_requires_current_membership(self):
        user, marshal = self.make_person('ao_user', 'AO User')
        self.appoint(marshal, self.branch_an_tir, self.discipline_auth_officer)

        self.assertTrue(is_kingdom_authorization_officer(user))

        user.membership_expiration = date.today() - timedelta(days=1)
        user.save()

        self.assertFalse(is_kingdom_authorization_officer(user))

    def test_staff_user_counts_as_kingdom_authorization_officer_without_appointment(self):
        user, _ = self.make_person('staff_ao', 'Staff AO')
        user.is_staff = True
        user.save()

        self.assertTrue(is_kingdom_authorization_officer(user))
        self.assertTrue(is_kingdom_equestrian_authorization_officer(user))

    def test_kingdom_equestrian_authorization_officer_is_separate_office(self):
        user, marshal = self.make_person('keao_user', 'KEAO User')
        self.appoint(marshal, self.branch_an_tir, self.discipline_equestrian_auth_officer)

        self.assertFalse(is_kingdom_authorization_officer(user))
        self.assertTrue(is_kingdom_equestrian_authorization_officer(user))

    def test_kingdom_seneschal_is_not_kingdom_marshal(self):
        user, person = self.make_person('kingdom_seneschal', 'Kingdom Seneschal')
        self.appoint(person, self.branch_an_tir, self.discipline_seneschal)

        self.assertTrue(is_kingdom_seneschal(user))
        self.assertFalse(is_kingdom_marshal(user))
        self.assertFalse(is_kingdom_authorization_officer(user))

    def test_earl_marshal_office_does_not_grant_senior_marshal_status(self):
        user, marshal = self.make_person('earl_no_sm', 'Earl No SM')
        self.appoint(marshal, self.branch_an_tir, self.discipline_earl_marshal)

        self.assertFalse(is_senior_marshal(user, 'Armored Combat'))


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
            is_minor=False,
            birthday=birthday,
        )
        ok, msg = authorization_follows_rules(marshal_user, minor, self.style_weapon_armored.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must be at least 16 years old to become authorized in Armored Combat.')

    def test_youth_marshal_no_longer_requires_background_check_at_proposal_time(self):
        marshal_user, marshal = self.make_person(
            'youth_marshal',
            'Youth Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(marshal, self.style_sm_youth_armored)

        _, fighter = self.make_person(
            'no_bg_target',
            'No BG Target',
            background_check_expiration=None,
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_jm_youth_armored.id)

        self.assertTrue(ok)

    def test_youth_authorization_must_match_age_category(self):
        marshal_user, marshal = self.make_person(
            'youth_category_marshal',
            'Youth Category Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(marshal, self.style_sm_youth_armored)
        _, fighter = self.make_person(
            'youth_category_target',
            'Youth Category Target',
            birthday=date.today() - relativedelta(years=12),
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_lion_sword_youth_armored.id)

        self.assertFalse(ok)
        self.assertIn('only for Lion youth', msg)

    def test_youth_rapier_category_secondary_accepts_matching_single_sword(self):
        marshal_user, marshal = self.make_person(
            'youth_rapier_marshal',
            'Youth Rapier Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(marshal, self.style_sm_youth_rapier)
        _, fighter = self.make_person(
            'youth_rapier_target',
            'Youth Rapier Target',
            birthday=date.today() - relativedelta(years=12),
        )
        self.grant_authorization(fighter, self.style_gryphon_single_youth_rapier)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_gryphon_defensive_youth_rapier.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_youth_rapier_secondary_accepts_pending_single_sword(self):
        marshal_user, marshal = self.make_person(
            'youth_rapier_no_single_marshal',
            'Youth Rapier No Single Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(marshal, self.style_sm_youth_rapier)
        _, fighter = self.make_person(
            'youth_rapier_no_single_target',
            'Youth Rapier No Single Target',
            birthday=date.today() - relativedelta(years=12),
        )
        self.grant_authorization(
            fighter,
            self.style_gryphon_single_youth_rapier,
            status=self.status_pending,
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_gryphon_defensive_youth_rapier.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_youth_rapier_secondary_requires_existing_single_sword(self):
        marshal_user, marshal = self.make_person(
            'youth_rapier_requires_single_marshal',
            'Youth Rapier Requires Single Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(marshal, self.style_sm_youth_rapier)
        _, fighter = self.make_person(
            'youth_rapier_requires_single_target',
            'Youth Rapier Requires Single Target',
            birthday=date.today() - relativedelta(years=12),
        )

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_gryphon_defensive_youth_rapier.id)

        self.assertFalse(ok)
        self.assertEqual(
            msg,
            'A fighter must have a single sword youth rapier authorization before adding other youth rapier authorizations.',
        )

    def test_adult_rapier_secondary_accepts_pending_single_sword(self):
        marshal_user, marshal = self.make_person('adult_rapier_marshal', 'Adult Rapier Marshal')
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        secondary_style = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.grant_authorization(marshal, style_sm_rapier)
        _, fighter = self.make_person('adult_rapier_target', 'Adult Rapier Target')
        self.grant_authorization(fighter, self.style_single_rapier, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, secondary_style.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_adult_rapier_secondary_requires_existing_single_sword(self):
        marshal_user, marshal = self.make_person('adult_rapier_requires_single_marshal', 'Adult Rapier Requires Single Marshal')
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        secondary_style = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.grant_authorization(marshal, style_sm_rapier)
        _, fighter = self.make_person('adult_rapier_requires_single_target', 'Adult Rapier Requires Single Target')

        ok, msg = authorization_follows_rules(marshal_user, fighter, secondary_style.id)

        self.assertFalse(ok)
        self.assertEqual(
            msg,
            'A fighter must have a single sword rapier authorization before adding other rapier authorizations.',
        )

    def test_cut_and_thrust_spear_accepts_pending_foundation_style(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_longsword = WeaponStyle.objects.create(name='Longsword', discipline=discipline_cut_and_thrust)
        style_spear = WeaponStyle.objects.create(name='Spear', discipline=discipline_cut_and_thrust)
        style_sm_cut_and_thrust = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_cut_and_thrust)
        marshal_user, marshal = self.make_person('ct_spear_marshal', 'CT Spear Marshal')
        self.grant_authorization(marshal, style_sm_cut_and_thrust)
        _, fighter = self.make_person('ct_spear_target', 'CT Spear Target')
        self.grant_authorization(fighter, style_longsword, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, style_spear.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_cut_and_thrust_spear_requires_existing_foundation_style(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_spear = WeaponStyle.objects.create(name='Spear', discipline=discipline_cut_and_thrust)
        style_sm_cut_and_thrust = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_cut_and_thrust)
        marshal_user, marshal = self.make_person('ct_spear_requires_foundation_marshal', 'CT Spear Requires Foundation Marshal')
        self.grant_authorization(marshal, style_sm_cut_and_thrust)
        _, fighter = self.make_person('ct_spear_requires_foundation_target', 'CT Spear Requires Foundation Target')

        ok, msg = authorization_follows_rules(marshal_user, fighter, style_spear.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'A fighter cannot be authorized with spear as their first cut and thrust authorization.')

    def test_mounted_gaming_accepts_pending_general_riding(self):
        marshal_user, marshal = self.make_person('eq_mg_pending_marshal', 'EQ MG Pending Marshal')
        _, fighter = self.make_person('eq_mg_pending_target', 'EQ MG Pending Target')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_general_riding, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_gaming.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_mounted_weapon_game_accepts_pending_mounted_gaming(self):
        marshal_user, marshal = self.make_person('eq_mounted_special_pending_marshal', 'EQ Mounted Special Pending Marshal')
        _, fighter = self.make_person('eq_mounted_special_pending_target', 'EQ Mounted Special Pending Target')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(marshal, self.style_general_riding)
        self.grant_authorization(marshal, self.style_mounted_gaming)
        self.grant_authorization(marshal, self.style_mounted_archery)
        self.grant_authorization(fighter, self.style_mounted_gaming, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_archery.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_mounted_heavy_combat_accepts_pending_mounted_gaming_and_general_riding(self):
        marshal_user, marshal = self.make_person('eq_mhc_pending_marshal', 'EQ MHC Pending Marshal')
        _, fighter = self.make_person('eq_mhc_pending_target', 'EQ MHC Pending Target')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(marshal, self.style_general_riding)
        self.grant_authorization(marshal, self.style_mounted_gaming)
        self.grant_authorization(marshal, self.style_mounted_heavy_combat)
        self.grant_authorization(fighter, self.style_general_riding, status=self.status_pending)
        self.grant_authorization(fighter, self.style_mounted_gaming, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_heavy_combat.id)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_blocks_pending_duplicate_authorization(self):
        marshal_user, marshal = self.make_person('pending_marshal', 'Awaiting Second Marshal Concurrence Marshal')
        _, fighter = self.make_person('pending_target', 'Awaiting Second Marshal Concurrence Target')
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
        self.assertEqual(msg, 'Ground Crew - Senior requires an active Ground Crew - Junior authorization.')

    def test_junior_ground_crew_blocked_when_senior_ground_crew_is_active(self):
        marshal_user, marshal = self.make_person('eq_sm_jgc_active', 'Eq SM JGC Active')
        _, fighter = self.make_person('eq_target_jgc_active', 'Eq Target JGC Active')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_senior_ground_crew)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_junior_ground_crew.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot make someone Ground Crew - Junior if they are already Ground Crew - Senior.')

    def test_junior_ground_crew_blocked_when_senior_ground_crew_is_pending(self):
        marshal_user, marshal = self.make_person('eq_sm_jgc_pending', 'Eq SM JGC Awaiting Second Marshal Concurrence')
        _, fighter = self.make_person('eq_target_jgc_pending', 'Eq Target JGC Awaiting Second Marshal Concurrence')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_senior_ground_crew, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_junior_ground_crew.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot have a new Ground Crew - Junior if Ground Crew - Senior is pending.')

    def test_senior_ground_crew_blocked_when_junior_ground_crew_is_pending(self):
        marshal_user, marshal = self.make_person('eq_sm_sgc_pending', 'Eq SM SGC Awaiting Second Marshal Concurrence')
        _, fighter = self.make_person('eq_target_sgc_pending', 'Eq Target SGC Awaiting Second Marshal Concurrence')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_junior_ground_crew, status=self.status_pending)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_senior_ground_crew.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Cannot have a new Ground Crew - Senior if Ground Crew - Junior is pending.')

    def test_senior_ground_crew_approval_marks_junior_ground_crew_inactive(self):
        kao_user, _ = self.make_person('eq_kao_sgc', 'Eq KAO SGC')
        kao_user.is_staff = True
        kao_user.save()
        _, fighter = self.make_person(
            'eq_target_sgc_approval',
            'Eq Target SGC Approval',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        junior_auth = self.grant_authorization(fighter, self.style_junior_ground_crew)
        review_status, _ = AuthorizationStatus.objects.get_or_create(name='Awaiting Equestrian Authorization Officer Review')
        senior_auth = self.grant_authorization(
            fighter,
            self.style_senior_ground_crew,
            status=review_status,
        )
        request = self.factory.post(
            '/authorizations/fighter/',
            {'authorization_id': str(senior_auth.id)},
        )
        request.user = kao_user

        ok, msg = approve_authorization(request)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Equestrian Ground Crew - Senior authorization approved!')
        junior_auth.refresh_from_db()
        self.assertEqual(junior_auth.status.name, 'Inactive')
        self.assertFalse(Authorization.objects.effectively_active().filter(id=junior_auth.id).exists())
        senior_auth.refresh_from_db()
        self.assertEqual(senior_auth.status.name, 'Active')

    def test_junior_equestrian_marshal_accepts_current_senior_ground_crew_name(self):
        current_senior_ground_crew = WeaponStyle.objects.create(
            name='Ground Crew - Senior',
            discipline=self.discipline_equestrian,
        )
        marshal_user, marshal = self.make_person('eq_sm_current_sgc', 'Eq SM Current SGC')
        _, fighter = self.make_person('eq_target_current_sgc', 'Eq Target Current SGC')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, current_senior_ground_crew)
        self.grant_authorization(fighter, self.style_general_riding)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_jm_equestrian.id)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_first_time_senior_equestrian_marshal_requires_active_junior_marshal(self):
        marshal_user, marshal = self.make_person('eq_sm_first_requires_jm', 'Eq SM First Requires JM')
        _, fighter = self.make_person('eq_target_first_requires_jm', 'Eq Target First Requires JM')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_mounted_gaming)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_sm_equestrian.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.')

    def test_senior_equestrian_marshal_renewal_does_not_require_active_junior_marshal(self):
        marshal_user, marshal = self.make_person('eq_sm_renew_marshal', 'Eq SM Renew Marshal')
        _, fighter = self.make_person('eq_target_renew_sm', 'Eq Target Renew SM')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_jm_equestrian, status=self.status_inactive)
        self.grant_authorization(fighter, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_general_riding)
        self.grant_authorization(fighter, self.style_mounted_gaming)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_sm_equestrian.id)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Authorization follows all rules.')

    def test_mounted_gaming_requires_general_riding(self):
        marshal_user, marshal = self.make_person('eq_sm_mg', 'Eq SM MG')
        _, fighter = self.make_person('eq_target_mg', 'Eq Target MG')
        self.grant_authorization(marshal, self.style_sm_equestrian)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_gaming.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Mounted Gaming requires a General Riding authorization.')

    def test_mounted_archery_requires_mounted_gaming(self):
        marshal_user, marshal = self.make_person('eq_sm_ma', 'Eq SM MA')
        _, fighter = self.make_person('eq_target_ma', 'Eq Target MA')
        self.grant_authorization(marshal, self.style_sm_equestrian)
        self.grant_authorization(fighter, self.style_general_riding)

        ok, msg = authorization_follows_rules(marshal_user, fighter, self.style_mounted_archery.id)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Mounted Archery requires a Mounted Gaming authorization.')

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
    def test_concurrence_requirement_is_disabled_by_default(self):
        _, fighter = self.make_person('concur_disabled', 'Concur Disabled')

        self.assertFalse(authorization_requires_concurrence(fighter, self.style_weapon_armored))

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
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

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
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
        _, fighter = self.make_person('pending_jm_target', 'Awaiting Second Marshal Concurrence JM Target')
        proposer_user, proposer = self.make_person('pending_jm_proposer', 'Awaiting Second Marshal Concurrence JM Proposer')
        approver_user, approver = self.make_person('pending_jm_approver', 'Awaiting Second Marshal Concurrence JM Approver')
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
        self.assertEqual(msg, 'Armored Combat Junior Marshal authorization approved!')
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
            'Awaiting Second Marshal Concurrence Youth JM Target',
            background_check_expiration=None,
        )
        proposer_user, proposer = self.make_person(
            'pending_youth_jm_proposer',
            'Awaiting Second Marshal Concurrence Youth JM Proposer',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        approver_user, approver = self.make_person(
            'pending_youth_jm_approver',
            'Awaiting Second Marshal Concurrence Youth JM Approver',
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
        _, fighter = self.make_person('pending_sm_target', 'Awaiting Second Marshal Concurrence SM Target')
        proposer_user, proposer = self.make_person('pending_sm_proposer', 'Awaiting Second Marshal Concurrence SM Proposer')
        approver_user, approver = self.make_person('pending_sm_approver', 'Awaiting Second Marshal Concurrence SM Approver')
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
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization ready for regional approval!')
        self.assertEqual(pending_auth.status, self.status_regional)

    def test_kingdom_earl_marshal_cannot_concur_pending_without_senior_marshal(self):
        proposer_user, proposer = self.make_person('earl_pending_prop', 'Earl Awaiting Second Marshal Concurrence Prop')
        earl_user, earl_person = self.make_person('earl_pending_actor', 'Earl Awaiting Second Marshal Concurrence Actor')
        _, fighter = self.make_person('earl_pending_target', 'Earl Awaiting Second Marshal Concurrence Target')
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

    def test_regional_approval_allows_same_discipline_regional_marshal_from_other_region_when_fighter_branch_is_region(self):
        proposer_user, proposer = self.make_person('regional_proposer', 'Regional Proposer')
        approver_user, approver = self.make_person('regional_wrong_approver', 'Regional Wrong Approver')
        _, fighter = self.make_person('regional_target', 'Regional Target', branch=self.region_summits)

        self.appoint(approver, self.region_tir_righ, self.discipline_armored)
        self.grant_authorization(approver, self.style_sm_armored)
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

        needs_regional.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
        self.assertEqual(needs_regional.status, self.status_active)

    def test_regional_approval_allows_same_discipline_regional_marshal_from_other_region_when_fighter_branch_is_local_branch(self):
        proposer_user, proposer = self.make_person('regional_local_proposer', 'Regional Local Proposer')
        approver_user, approver = self.make_person('regional_local_wrong', 'Regional Local Wrong')
        _, fighter = self.make_person('regional_local_target', 'Regional Local Target', branch=self.branch_gd)

        self.appoint(approver, self.region_tir_righ, self.discipline_armored)
        self.grant_authorization(approver, self.style_sm_armored)
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

        needs_regional.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
        self.assertEqual(needs_regional.status, self.status_active)

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
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
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
        self.assertEqual(
            msg,
            'You must be a regional marshal in this discipline, or a regional Earl Marshal in the fighter region, to approve this authorization.',
        )

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
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
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

    def test_authorization_officer_final_approval_sets_active_and_marks_junior_inactive(self):
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
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
        self.assertEqual(pending_senior.status, self.status_active)
        junior_auth.refresh_from_db()
        self.assertEqual(junior_auth.status.name, 'Inactive')
        self.assertFalse(Authorization.objects.effectively_active().filter(id=junior_auth.id).exists())
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
        self.assertEqual(msg, 'Armored Combat Senior Marshal authorization approved!')
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
    def test_appoint_branch_marshal_uses_selected_person_id_when_sca_name_is_duplicated(self):
        ao_user, ao_person = self.make_person('appoint_duplicate_ao', 'Appoint Duplicate AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        _, candidate = self.make_person('appoint_duplicate_candidate', 'Shared Marshal Name')
        self.make_person('appoint_duplicate_other', 'Shared Marshal Name')
        self.grant_authorization(candidate, self.style_sm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertTrue(ok, msg)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=candidate,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
            ).exists()
        )

    def test_non_authorization_officer_cannot_appoint_branch_marshal(self):
        normal_user, _ = self.make_person('appoint_normal_user', 'Appoint Normal User')
        _, candidate = self.make_person('appoint_candidate_user', 'Appoint Candidate User')
        self.grant_authorization(candidate, self.style_sm_armored)

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
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
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
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

    def test_authorization_officer_can_appoint_kingdom_seneschal_without_marshal_authorization(self):
        ao_user, ao_person = self.make_person('appoint_ao_seneschal', 'Appoint AO Seneschal')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        _, candidate = self.make_person('appoint_seneschal_candidate', 'Appoint Seneschal Candidate')

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_an_tir.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Kingdom Seneschal appointed.')
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=candidate,
                branch=self.branch_an_tir,
                discipline=self.discipline_seneschal,
            ).exists()
        )

    def test_admin_can_appoint_principality_seneschal_without_marshal_authorization(self):
        admin_user, _ = self.make_person('appoint_admin_seneschal', 'Appoint Admin Seneschal')
        admin_user.is_staff = True
        admin_user.save()
        _, candidate = self.make_person('appoint_principality_seneschal', 'Appoint Principality Seneschal')

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.principality_summits.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = admin_user

        ok, msg = appoint_branch_marshal(request)

        self.assertTrue(ok)
        self.assertEqual(msg, 'Principality of the Summits Seneschal appointed.')
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=candidate,
                branch=self.principality_summits,
                discipline=self.discipline_seneschal,
            ).exists()
        )

    def test_kingdom_seneschal_can_appoint_principality_and_local_seneschals(self):
        kingdom_user, kingdom_person = self.make_person('appoint_kingdom_seneschal', 'Appoint Kingdom Seneschal')
        self.appoint(kingdom_person, self.branch_an_tir, self.discipline_seneschal)
        _, principality_candidate = self.make_person('appoint_principality_by_ks', 'Appoint Principality By KS')
        _, local_candidate = self.make_person('appoint_local_by_ks', 'Appoint Local By KS')

        principality_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(principality_candidate.user_id),
                'branch_id': str(self.principality_tir_righ.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        principality_request.user = kingdom_user
        local_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(local_candidate.user_id),
                'branch_id': str(self.branch_summits_shire.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        local_request.user = kingdom_user

        principality_ok, _ = appoint_branch_marshal(principality_request)
        local_ok, _ = appoint_branch_marshal(local_request)

        self.assertTrue(principality_ok)
        self.assertTrue(local_ok)

    def test_principality_seneschal_can_appoint_local_seneschal_in_own_principality_only(self):
        principality_user, principality_person = self.make_person(
            'appoint_principality_seneschal_scope',
            'Appoint Principality Scope',
        )
        self.appoint(principality_person, self.principality_summits, self.discipline_seneschal)
        _, own_candidate = self.make_person('appoint_own_local_seneschal', 'Appoint Own Local Seneschal')
        _, other_candidate = self.make_person('appoint_other_local_seneschal', 'Appoint Other Local Seneschal')

        own_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(own_candidate.user_id),
                'branch_id': str(self.branch_summits_shire.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        own_request.user = principality_user
        other_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(other_candidate.user_id),
                'branch_id': str(self.branch_tir_righ_shire.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        other_request.user = principality_user

        own_ok, _ = appoint_branch_marshal(own_request)
        other_ok, other_msg = appoint_branch_marshal(other_request)

        self.assertTrue(own_ok)
        self.assertFalse(other_ok)
        self.assertEqual(other_msg, 'You do not have authority to appoint this marshal office.')

    def test_principality_seneschal_cannot_appoint_principality_seneschal(self):
        principality_user, principality_person = self.make_person(
            'appoint_principality_seneschal_peer',
            'Appoint Principality Peer',
        )
        self.appoint(principality_person, self.principality_summits, self.discipline_seneschal)
        _, candidate = self.make_person('appoint_principality_peer_candidate', 'Appoint Principality Peer Candidate')

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.principality_tir_righ.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = principality_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You do not have authority to appoint this marshal office.')

    def test_region_and_other_branches_cannot_have_seneschals(self):
        admin_user, _ = self.make_person('appoint_admin_invalid_seneschal', 'Appoint Admin Invalid')
        admin_user.is_staff = True
        admin_user.save()
        _, region_candidate = self.make_person('appoint_region_seneschal', 'Appoint Region Seneschal')
        _, other_candidate = self.make_person('appoint_other_seneschal', 'Appoint Other Seneschal')

        region_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(region_candidate.user_id),
                'branch_id': str(self.branch_inlands.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        region_request.user = admin_user
        other_request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(other_candidate.user_id),
                'branch_id': str(self.branch_other.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        other_request.user = admin_user

        region_ok, region_msg = appoint_branch_marshal(region_request)
        other_ok, other_msg = appoint_branch_marshal(other_request)

        self.assertFalse(region_ok)
        self.assertFalse(other_ok)
        self.assertEqual(
            region_msg,
            'Seneschal offices may only be appointed for kingdom, principality, or local branches.',
        )
        self.assertEqual(
            other_msg,
            'Seneschal offices may only be appointed for kingdom, principality, or local branches.',
        )

    def test_kingdom_earl_marshal_cannot_appoint_kingdom_seneschal(self):
        earl_user, earl_person = self.make_person('appoint_earl_seneschal', 'Appoint Earl Seneschal')
        self.grant_authorization(earl_person, self.style_sm_armored)
        self.appoint(earl_person, self.branch_an_tir, self.discipline_earl_marshal)
        _, candidate = self.make_person('appoint_seneschal_candidate_blocked', 'Appoint Seneschal Blocked')

        request = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_an_tir.id),
                'discipline_id': str(self.discipline_seneschal.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = earl_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'You do not have authority to appoint this marshal office.')
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=candidate,
                branch=self.branch_an_tir,
                discipline=self.discipline_seneschal,
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
                'person_id': str(candidate.user_id),
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
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
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
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
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
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
                'person_id': str(candidate_one.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
                'start_date': date.today().isoformat(),
            },
        )
        request_one.user = ao_user
        ok_one, msg_one = appoint_branch_marshal(request_one)

        request_two = self.factory.post(
            '/authorizations/branch_marshals/',
            {
                'person_id': str(candidate_two.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
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
                'person_id': str(candidate.user_id),
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
                'start_date': date.today().isoformat(),
            },
        )
        request.user = ao_user

        ok, msg = appoint_branch_marshal(request)

        self.assertFalse(ok)
        self.assertEqual(msg, 'Must be a current member to be a branch marshal.')


class DeactivateSupersededJuniorMarshalsCommandTests(AuthorizationTestBase):
    def test_dry_run_reports_superseded_junior_marshal_without_changing_status(self):
        _, fighter = self.make_person('superseded_dry_run', 'Superseded Dry Run')
        junior_auth = self.grant_authorization(fighter, self.style_jm_armored, status=self.status_active)
        self.grant_authorization(fighter, self.style_sm_armored, status=self.status_active)
        out = StringIO()

        call_command('deactivate_superseded_junior_marshals', stdout=out)

        junior_auth.refresh_from_db()
        self.assertEqual(junior_auth.status, self.status_active)
        self.assertIn('Found 1 active Junior Marshal authorization', out.getvalue())
        self.assertIn(f'authorization_id={junior_auth.id} user_id={fighter.user_id}', out.getvalue())
        self.assertIn('Dry run only.', out.getvalue())

    def test_apply_marks_only_superseded_junior_marshal_inactive(self):
        _, fighter = self.make_person('superseded_apply', 'Superseded Apply')
        _, other_fighter = self.make_person('not_superseded_apply', 'Not Superseded Apply')
        junior_auth = self.grant_authorization(fighter, self.style_jm_armored, status=self.status_active)
        senior_auth = self.grant_authorization(fighter, self.style_sm_armored, status=self.status_active)
        unrelated_junior = self.grant_authorization(
            other_fighter,
            self.style_jm_armored,
            status=self.status_active,
        )
        out = StringIO()

        call_command('deactivate_superseded_junior_marshals', '--apply', stdout=out)

        junior_auth.refresh_from_db()
        senior_auth.refresh_from_db()
        unrelated_junior.refresh_from_db()
        self.assertEqual(junior_auth.status.name, 'Inactive')
        self.assertEqual(senior_auth.status, self.status_active)
        self.assertEqual(unrelated_junior.status, self.status_active)
        self.assertIn('Marked 1 Junior Marshal authorization', out.getvalue())

    def test_apply_ignores_junior_when_senior_marshal_is_not_fully_active(self):
        pending_statuses = [
            self.status_pending,
            self.status_regional,
            self.status_kingdom,
        ]
        junior_authorizations = []
        for index, status in enumerate(pending_statuses, start=1):
            _, fighter = self.make_person(f'pending_senior_{index}', f'Pending Senior {index}')
            junior_authorizations.append(
                self.grant_authorization(fighter, self.style_jm_armored, status=self.status_active)
            )
            self.grant_authorization(fighter, self.style_sm_armored, status=status)
        out = StringIO()

        call_command('deactivate_superseded_junior_marshals', '--apply', stdout=out)

        for junior_auth in junior_authorizations:
            junior_auth.refresh_from_db()
            self.assertEqual(junior_auth.status, self.status_active)
        self.assertIn(
            'No active Junior Marshal authorizations are superseded by active Senior Marshal authorizations.',
            out.getvalue(),
        )
