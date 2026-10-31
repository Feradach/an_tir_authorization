from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.test import override_settings
from django.urls import reverse, resolve, Resolver404, NoReverseMatch
from django.utils import timezone

from authorizations.authorization_history import was_authorized_during
from authorizations.models import Authorization, AuthorizationStatus, AuthorizationValidityInterval, Discipline, Sanction, WeaponStyle
from authorizations.permissions import authorization_requires_concurrence
from authorizations.views import _legacy_recovery_interval_exists, _legacy_recovery_requires_concurrence_on_date
from .test_additional import AdditionalCoverageBase


class SocietyConcurrenceHistoryTests(AdditionalCoverageBase):
    def setUp(self):
        super().setUp()
        self.user, self.person = self.make_person('history_fighter', 'History Fighter')

    def requires(self, style=None, on=None):
        style = style or self.style_weapon_armored
        on = on or date.today()
        electronic = authorization_requires_concurrence(self.person, style, today=on)
        self.assertEqual(electronic, _legacy_recovery_requires_concurrence_on_date(self.person, style, on))
        return electronic

    def test_first_authorization_cannot_be_disabled_by_stale_setting(self):
        with override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=False):
            self.assertTrue(self.requires())

    def test_exact_calendar_year_and_one_day_beyond(self):
        on = date(2028, 2, 29)
        cutoff = on - relativedelta(years=1)
        auth = self.grant_authorization(self.person, self.style_weapon_armored, expiration=cutoff)
        self.assertFalse(self.requires(on=on))
        auth.expiration = cutoff - timedelta(days=1)
        auth.save()
        self.assertTrue(self.requires(on=on))

    def test_recent_history_survives_inactive_current_row(self):
        auth = self.grant_authorization(self.person, self.style_weapon_armored, expiration=date.today()-timedelta(days=40))
        auth.status, _ = AuthorizationStatus.objects.get_or_create(name='Inactive')
        auth.save()
        self.assertFalse(self.requires())

    def test_future_history_cannot_exempt_backdated_first_authorization(self):
        self.grant_authorization(self.person, self.style_weapon_armored, expiration=date.today()+relativedelta(years=4))
        self.assertTrue(self.requires(on=date.today()-relativedelta(years=2)))

    def test_other_discipline_does_not_exempt(self):
        self.grant_authorization(self.person, self.style_weapon_armored)
        self.assertTrue(self.requires(self.style_single_rapier))

    def test_equestrian_is_covered_siege_and_both_youth_disciplines_exempt(self):
        for name, expected in [('Equestrian', True), ('Siege', False), ('Youth Armored', False), ('Youth Rapier', False)]:
            discipline, _ = Discipline.objects.get_or_create(name=name)
            style = WeaponStyle.objects.create(name='Test Fighter Style', discipline=discipline)
            self.assertEqual(self.requires(style), expected)
        self.assertFalse(self.requires(self.style_sm_armored))

    def test_missing_single_sword_does_not_manufacture_validity_history(self):
        case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, case, expiration=date.today()+relativedelta(years=2))
        self.assertFalse(AuthorizationValidityInterval.objects.filter(authorization=auth).exists())
        self.assertTrue(self.requires(self.style_single_rapier))

    def test_overbroad_secondary_history_still_requires_single_sword_history(self):
        case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, case, expiration=date.today()+relativedelta(years=2))
        AuthorizationValidityInterval.objects.create(authorization=auth, start_date=date.today()-relativedelta(years=3), end_date=auth.expiration)
        self.assertTrue(self.requires(self.style_single_rapier))
        self.assertFalse(_legacy_recovery_interval_exists(self.person, date.today(), style_names=['Case'], discipline=self.discipline_rapier))

    def test_membership_gap_remains_after_second_update(self):
        self.user.membership_expiration = date.today()-relativedelta(months=2)
        self.user.save()
        self.grant_authorization(self.person, self.style_sm_armored, expiration=date.today()+relativedelta(years=2))
        gap = date.today()-relativedelta(months=1)
        for years in [1, 2]:
            self.user.membership_expiration = date.today()+relativedelta(years=years)
            self.user.save()
            self.assertFalse(_legacy_recovery_interval_exists(self.person, gap, style_names=['Senior Marshal'], discipline=self.discipline_armored))
            self.assertTrue(_legacy_recovery_interval_exists(self.person, date.today(), style_names=['Senior Marshal'], discipline=self.discipline_armored))

    def test_single_sword_lapse_and_renewal_preserve_gap(self):
        sword = self.grant_authorization(self.person, self.style_single_rapier, expiration=date.today()-relativedelta(years=2))
        case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.grant_authorization(self.person, case, expiration=date.today()+relativedelta(years=1))
        self.assertTrue(self.requires(self.style_single_rapier))
        gap = date.today()-relativedelta(months=6)
        sword.expiration = date.today()+relativedelta(years=4)
        sword.save()
        self.assertFalse(_legacy_recovery_interval_exists(self.person, gap, style_names=['Case'], discipline=self.discipline_rapier))
        self.assertTrue(_legacy_recovery_interval_exists(self.person, date.today(), style_names=['Case'], discipline=self.discipline_rapier))

    def test_prerequisite_sanction_blocks_dependent_history(self):
        self.grant_authorization(self.person, self.style_single_rapier)
        case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.grant_authorization(self.person, case)
        Sanction.objects.create(person=self.person, discipline=self.discipline_rapier, style=self.style_single_rapier,
                                start_date=date.today()-timedelta(days=5), end_date=date.today()+timedelta(days=5), issue_note='Test')
        self.assertFalse(_legacy_recovery_interval_exists(self.person, date.today(), style_names=['Case'], discipline=self.discipline_rapier))

    def test_lifted_sanction_does_not_erase_past_ineligibility(self):
        self.grant_authorization(self.person, self.style_weapon_armored)
        Sanction.objects.create(person=self.person, discipline=self.discipline_armored,
                                start_date=date.today()-timedelta(days=10), end_date=date.today()+timedelta(days=5),
                                lifted_at=timezone.now(), issue_note='Test')
        self.assertFalse(_legacy_recovery_interval_exists(self.person, date.today()-timedelta(days=2), discipline=self.discipline_armored))
        self.assertTrue(_legacy_recovery_interval_exists(self.person, date.today(), discipline=self.discipline_armored))

    def test_missing_membership_does_not_manufacture_marshal_history(self):
        self.user.membership = None
        self.user.membership_expiration = None
        self.user.save()
        auth = self.grant_authorization(self.person, self.style_sm_armored, expiration=date.today()+relativedelta(years=2))
        self.assertFalse(AuthorizationValidityInterval.objects.filter(authorization=auth).exists())
        self.assertTrue(self.requires())

    def test_merged_history_counts_for_survivor_only(self):
        from authorizations.views import _reattach_authorization_history_to_winner
        survivor_user, survivor = self.make_person('survivor', self.person.sca_name)
        old = self.grant_authorization(self.person, self.style_weapon_armored, expiration=date.today()-timedelta(days=20))
        winner = self.grant_authorization(survivor, self.style_weapon_armored, status=self.status_needs_concurrence)
        _reattach_authorization_history_to_winner(old, winner)
        old.delete()
        self.user.merged_into = survivor_user
        self.user.save()
        self.assertFalse(authorization_requires_concurrence(survivor, self.style_weapon_armored))
        self.assertFalse(was_authorized_during(self.person, date.today()-relativedelta(years=1), date.today(), discipline=self.discipline_armored))


class SocietyConcurrenceWorkflowTests(AdditionalCoverageBase):
    def setUp(self):
        super().setUp()
        self.marshal_user, self.marshal = self.make_person('marshal', 'Marshal')
        self.target_user, self.target = self.make_person('target', 'Shared Name', waiver_expiration=date.today()+relativedelta(years=4))
        self.witness_user, self.witness = self.make_person('witness', 'Shared Name')
        self.grant_authorization(self.marshal, self.style_sm_armored)
        self.grant_authorization(self.witness, self.style_polearm_armored)

    def test_retired_csv_import_has_no_route_or_writer(self):
        from authorizations import views
        with self.assertRaises(Resolver404):
            resolve('/legacy_authorizations/upload')
        with self.assertRaises(NoReverseMatch):
            reverse('upload_legacy_authorizations')
        self.assertFalse(hasattr(views, '_apply_legacy_authorization_import'))
        self.assertEqual(resolve(reverse('paper_authorization_entry')).func, views.legacy_authorization_recovery)

    def test_first_authorization_can_be_concurred_by_different_style_with_duplicate_name(self):
        url = reverse('fighter', kwargs={'person_id': self.target_user.id})
        self.client.force_login(self.marshal_user)
        self.client.post(url, {'action': 'add_authorization', 'discipline': str(self.discipline_armored.id),
                               'weapon_styles': [str(self.style_weapon_armored.id)]})
        auth = Authorization.objects.get(person=self.target, style=self.style_weapon_armored)
        self.assertEqual(auth.status.name, 'Awaiting Fighter Concurrence')
        self.client.force_login(self.witness_user)
        self.client.post(url, {'action': 'concur_authorization', 'authorization_id': str(auth.id)})
        auth.refresh_from_db()
        self.assertEqual(auth.status.name, 'Active')
        self.assertEqual(auth.concurring_fighter_id, self.witness_user.id)

    def test_equestrian_concurrence_routes_to_waiver_review(self):
        eq = Discipline.objects.create(name='Equestrian')
        riding = WeaponStyle.objects.create(name='General Riding', discipline=eq)
        auth = self.grant_authorization(self.target, riding, marshal=self.marshal, status=self.status_needs_concurrence)
        self.grant_authorization(self.witness, riding)
        self.client.force_login(self.witness_user)
        self.client.post(reverse('fighter', kwargs={'person_id': self.target_user.id}),
                         {'action': 'concur_authorization', 'authorization_id': str(auth.id)})
        auth.refresh_from_db()
        self.assertEqual(auth.status.name, 'Awaiting Equestrian Authorization Officer Review')
        self.assertEqual(auth.concurring_fighter_id, self.witness_user.id)

    def equestrian_paper_setup(self):
        eq = Discipline.objects.create(name='Equestrian')
        riding = WeaponStyle.objects.create(name='General Riding', discipline=eq)
        senior = WeaponStyle.objects.create(name='Senior Marshal', discipline=eq)
        officer, _ = Discipline.objects.get_or_create(name='Equestrian Authorization Officer')
        self.appoint(self.marshal, self.branch_an_tir, officer)
        self.grant_authorization(self.marshal, senior)
        self.grant_authorization(self.witness, riding)
        self.client.force_login(self.marshal_user)
        data = {'person_id': [str(self.target_user.id)], 'person_sca_name': [self.target.sca_name],
                'person_first_name': [self.target_user.first_name], 'person_last_name': [self.target_user.last_name],
                'marshal_id': [str(self.marshal_user.id)], 'marshal_sca_name': [self.marshal.sca_name],
                'marshal_first_name': [self.marshal_user.first_name], 'marshal_last_name': [self.marshal_user.last_name],
                'weapon_style': ['Equestrian - General Riding'], 'auth_date': [date.today().isoformat()]}
        return riding, data

    def test_paper_equestrian_requires_witness_even_if_checkbox_omitted(self):
        riding, data = self.equestrian_paper_setup()
        response = self.client.post(reverse('paper_authorization_entry'), data, follow=True)
        self.assertContains(response, 'Concurring fighter is required for this paper authorization.')
        self.assertFalse(Authorization.objects.filter(person=self.target, style=riding).exists())

    def test_paper_equestrian_accepts_distinct_qualified_witness_with_same_name(self):
        riding, data = self.equestrian_paper_setup()
        data.update({'concurring_officer_id': [str(self.witness_user.id)],
                     'concurring_officer_sca_name': [self.witness.sca_name],
                     'concurring_officer_first_name': [self.witness_user.first_name],
                     'concurring_officer_last_name': [self.witness_user.last_name]})
        response = self.client.post(reverse('paper_authorization_entry'), data, follow=True)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        auth = Authorization.objects.get(person=self.target, style=riding)
        self.assertEqual(auth.concurring_fighter_id, self.witness_user.id)

    def test_sanctioned_single_sword_cannot_be_bypassed_by_secondary_signer(self):
        self.grant_authorization(self.witness, self.style_single_rapier)
        case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.grant_authorization(self.witness, case)
        Sanction.objects.create(person=self.witness, discipline=self.discipline_rapier, style=self.style_single_rapier,
                                start_date=date.today()-timedelta(days=1), end_date=date.today()+timedelta(days=5), issue_note='Test')
        auth = self.grant_authorization(self.target, self.style_single_rapier, marshal=self.marshal, status=self.status_needs_concurrence)
        self.client.force_login(self.witness_user)
        self.client.post(reverse('fighter', kwargs={'person_id': self.target_user.id}),
                         {'action': 'concur_authorization', 'authorization_id': str(auth.id)})
        auth.refresh_from_db()
        self.assertEqual(auth.status.name, 'Awaiting Fighter Concurrence')
        self.assertIsNone(auth.concurring_fighter_id)

    def test_backdated_electronic_entry_rejects_witness_membership_gap(self):
        self.appoint(self.marshal, self.branch_an_tir, self.discipline_auth_officer)
        Authorization.objects.filter(person=self.witness).delete()
        self.witness_user.membership_expiration = date.today()-timedelta(days=60)
        self.witness_user.save()
        self.grant_authorization(self.witness, self.style_sm_armored, expiration=date.today()+relativedelta(years=3))
        self.witness_user.membership_expiration = date.today()+relativedelta(years=1)
        self.witness_user.save()
        self.client.force_login(self.marshal_user)
        response = self.client.post(reverse('fighter', kwargs={'person_id': self.target_user.id}),
                                    {'action': 'add_authorization', 'discipline': str(self.discipline_armored.id),
                                     'weapon_styles': [str(self.style_weapon_armored.id)],
                                     'authorization_date': (date.today()-timedelta(days=30)).isoformat(),
                                     'concurring_fighter_id': str(self.witness_user.id)}, follow=True)
        self.assertContains(response, 'cannot concur')
        self.assertFalse(Authorization.objects.filter(person=self.target, style=self.style_weapon_armored).exists())

    def test_backdated_electronic_entry_rejects_proposing_marshal_membership_gap(self):
        self.appoint(self.marshal, self.branch_an_tir, self.discipline_auth_officer)
        self.marshal_user.membership_expiration = date.today()-timedelta(days=60)
        self.marshal_user.save()
        self.marshal_user.membership_expiration = date.today()+relativedelta(years=1)
        self.marshal_user.save()
        self.client.force_login(self.marshal_user)
        response = self.client.post(reverse('fighter', kwargs={'person_id': self.target_user.id}),
                                    {'action': 'add_authorization', 'discipline': str(self.discipline_armored.id),
                                     'weapon_styles': [str(self.style_weapon_armored.id)],
                                     'authorization_date': (date.today()-timedelta(days=30)).isoformat()}, follow=True)
        self.assertContains(response, 'was not a Senior Marshal')
        self.assertFalse(Authorization.objects.filter(person=self.target, style=self.style_weapon_armored).exists())
