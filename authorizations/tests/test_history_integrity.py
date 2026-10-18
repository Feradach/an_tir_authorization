from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from django.utils import timezone

from authorizations.models import AuthorizationValidityInterval, Discipline, Sanction, WeaponStyle
from authorizations.views import (
    _activate_pending_background_check_authorizations, _activate_pending_waiver_authorizations,
    _legacy_recovery_interval_exists, _reattach_authorization_history_to_winner,
)
from .test_additional import AdditionalCoverageBase


class HistoryIntegrityTests(AdditionalCoverageBase):
    def setUp(self):
        super().setUp()
        self.user, self.person = self.make_person('history', 'History Fighter')
        self.today = date.today()

    def valid(self, auth, on):
        return _legacy_recovery_interval_exists(
            self.person, on, discipline=auth.style.discipline, style_names=[auth.style.name],
        )

    def marshal_after_membership_gap(self):
        self.user.membership_expiration = self.today - timedelta(days=60)
        self.user.save()
        auth = self.grant_authorization(self.person, self.style_sm_armored,
                                        expiration=self.today + relativedelta(years=2))
        self.user.membership_expiration = self.today + relativedelta(years=1)
        self.user.save()
        return auth

    def test_second_membership_update_preserves_gap(self):
        auth = self.marshal_after_membership_gap()
        self.user.membership_expiration += timedelta(days=60)
        self.user.save()
        self.assertFalse(self.valid(auth, self.today - timedelta(days=30)))
        self.assertTrue(self.valid(auth, self.today))

    def test_ordinary_authorization_save_preserves_membership_gap(self):
        auth = self.marshal_after_membership_gap()
        auth.save()
        self.assertFalse(self.valid(auth, self.today - timedelta(days=30)))
        self.assertTrue(self.valid(auth, self.today))

    def test_renewal_preserves_earlier_membership_gap(self):
        auth = self.marshal_after_membership_gap()
        auth.expiration += timedelta(days=10)
        auth.save()
        self.assertFalse(self.valid(auth, self.today - timedelta(days=30)))

    def test_missing_membership_does_not_create_history(self):
        self.user.membership = None
        self.user.membership_expiration = None
        self.user.save()
        auth = self.grant_authorization(self.person, self.style_sm_armored,
                                        expiration=self.today + relativedelta(years=2))
        self.assertFalse(auth.validity_intervals.exists())

    def test_removing_membership_closes_current_history(self):
        auth = self.grant_authorization(self.person, self.style_sm_armored)
        self.user.membership = None
        self.user.membership_expiration = None
        self.user.save()
        self.assertFalse(self.valid(auth, self.today))
        self.assertTrue(self.valid(auth, self.today - timedelta(days=1)))

    def test_missing_single_sword_does_not_create_history(self):
        style = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, style, expiration=self.today + relativedelta(years=2))
        self.assertFalse(auth.validity_intervals.exists())

    def test_prerequisite_corrected_to_past_closes_dependent_history(self):
        sword = self.grant_authorization(self.person, self.style_single_rapier)
        style = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, style)
        sword.expiration = self.today - timedelta(days=15)
        sword.save()
        self.assertFalse(auth.validity_intervals.filter(end_date__gte=self.today).exists())
        self.assertFalse(self.valid(auth, self.today - timedelta(days=5)))

    def test_status_change_closes_history_and_restoration_preserves_gap(self):
        auth = self.grant_authorization(self.person, self.style_weapon_armored)
        auth.status = self.status_revoked
        auth.save()
        self.assertFalse(self.valid(auth, self.today))
        with patch('authorizations.models.date') as clock:
            clock.today.return_value = self.today + timedelta(days=30)
            auth.status = self.status_active
            auth.save()
        self.assertFalse(self.valid(auth, self.today + timedelta(days=15)))
        self.assertTrue(self.valid(auth, self.today + timedelta(days=30)))

    def test_partial_save_uses_persisted_values_for_history(self):
        auth = self.grant_authorization(self.person, self.style_weapon_armored)
        original = list(auth.validity_intervals.values_list('start_date', 'end_date'))
        auth.expiration += relativedelta(years=4)
        auth.save(update_fields=['updated_at'])
        self.assertEqual(list(auth.validity_intervals.values_list('start_date', 'end_date')), original)

    def test_missing_history_not_recreated_by_unrelated_authorization(self):
        auth = self.grant_authorization(self.person, self.style_weapon_armored)
        auth.validity_intervals.all().delete()
        self.grant_authorization(self.person, self.style_single_rapier)
        self.assertFalse(auth.validity_intervals.exists())

    def test_closed_history_survives_duplicate_name_and_other_account_updates(self):
        auth = self.marshal_after_membership_gap()
        other_user, other = self.make_person('other', self.person.sca_name)
        self.grant_authorization(other, self.style_sm_armored)
        original = list(auth.validity_intervals.values_list('start_date', 'end_date'))
        other_user.membership_expiration += relativedelta(years=1)
        other_user.save()
        self.assertEqual(list(auth.validity_intervals.values_list('start_date', 'end_date')), original)

    def test_background_check_activation_records_history(self):
        self.user.background_check_expiration = self.today + relativedelta(years=1)
        self.user.save()
        style = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_youth_armored)
        auth = self.grant_authorization(self.person, style, status=self.status_pending_background_check)
        with patch('authorizations.views.authorization_officer_sign_off_enabled', return_value=False):
            self.assertEqual(_activate_pending_background_check_authorizations(self.user), 1)
        self.assertTrue(self.valid(auth, self.today))
        self.assertFalse(self.valid(auth, self.today - timedelta(days=1)))

    def test_background_check_gap_and_removal_preserve_actual_history(self):
        self.user.background_check_expiration = self.today - timedelta(days=60)
        self.user.save()
        style = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_youth_armored)
        auth = self.grant_authorization(self.person, style)
        for days in [180, 250]:
            self.user.background_check_expiration = self.today + timedelta(days=days)
            self.user.save()
            self.assertFalse(self.valid(auth, self.today - timedelta(days=30)))
            self.assertTrue(self.valid(auth, self.today))
        self.user.background_check_expiration = None
        self.user.save()
        self.assertFalse(self.valid(auth, self.today))
        self.assertTrue(self.valid(auth, self.today - timedelta(days=90)))

    def test_waiver_activation_starts_today_and_closes_replaced_ground_crew(self):
        eq = Discipline.objects.create(name='Equestrian')
        junior_style = WeaponStyle.objects.create(name='Junior Ground Crew', discipline=eq)
        senior_style = WeaponStyle.objects.create(name='Senior Ground Crew', discipline=eq)
        junior = self.grant_authorization(self.person, junior_style)
        senior = self.grant_authorization(self.person, senior_style, status=self.status_pending_waiver)
        _activate_pending_waiver_authorizations(self.user)
        self.assertTrue(self.valid(senior, self.today))
        self.assertFalse(self.valid(senior, self.today - timedelta(days=1)))
        self.assertFalse(self.valid(junior, self.today))
        self.assertTrue(self.valid(junior, self.today - timedelta(days=1)))

    def test_waiver_repair_dry_run_does_not_write_and_apply_records_validity(self):
        self.user.waiver_expiration = self.today + relativedelta(years=1)
        self.user.save()
        auth = self.grant_authorization(self.person, self.style_weapon_armored, status=self.status_pending_waiver)
        call_command('activate_pending_waiver_authorizations', stdout=StringIO())
        self.assertFalse(auth.validity_intervals.exists())
        call_command('activate_pending_waiver_authorizations', '--apply', stdout=StringIO())
        self.assertTrue(self.valid(auth, self.today))
        self.assertFalse(self.valid(auth, self.today - timedelta(days=1)))

    def test_prerequisite_sanction_is_respected_by_backdated_checks(self):
        self.grant_authorization(self.person, self.style_single_rapier)
        style = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, style)
        Sanction.objects.create(person=self.person, discipline=self.discipline_rapier,
                                style=self.style_single_rapier, start_date=self.today - timedelta(days=10),
                                end_date=self.today + timedelta(days=10), lifted_at=timezone.now(), issue_note='Test')
        self.assertFalse(self.valid(auth, self.today - timedelta(days=5)))
        self.assertTrue(self.valid(auth, self.today))

    def test_overbroad_dependent_history_requires_prerequisite_history(self):
        style = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, style)
        AuthorizationValidityInterval.objects.create(authorization=auth, start_date=self.today - timedelta(days=30),
                                                     end_date=self.today + timedelta(days=30))
        self.assertFalse(self.valid(auth, self.today))

    def test_merged_history_belongs_only_to_survivor(self):
        survivor_user, survivor = self.make_person('survivor', self.person.sca_name)
        old = self.grant_authorization(self.person, self.style_weapon_armored, expiration=self.today - timedelta(days=20))
        winner = self.grant_authorization(survivor, self.style_weapon_armored, status=self.status_pending_waiver)
        _reattach_authorization_history_to_winner(old, winner)
        old.delete()
        self.user.merged_into = survivor_user
        self.user.save()
        on = self.today - timedelta(days=30)
        self.assertTrue(_legacy_recovery_interval_exists(survivor, on, discipline=self.discipline_armored))
        self.assertFalse(_legacy_recovery_interval_exists(self.person, on, discipline=self.discipline_armored))

    def test_waiver_activation_resumes_existing_dependent(self):
        self.grant_authorization(self.person, self.style_single_rapier, status=self.status_pending_waiver)
        style = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        auth = self.grant_authorization(self.person, style)
        self.assertFalse(auth.validity_intervals.exists())
        _activate_pending_waiver_authorizations(self.user)
        self.assertTrue(self.valid(auth, self.today))
        self.assertFalse(self.valid(auth, self.today - timedelta(days=1)))

    def test_equestrian_prerequisite_removal_closes_transitive_dependents(self):
        eq = Discipline.objects.create(name='Equestrian')
        riding = self.grant_authorization(self.person, WeaponStyle.objects.create(name='General Riding', discipline=eq))
        gaming = self.grant_authorization(self.person, WeaponStyle.objects.create(name='Mounted Gaming', discipline=eq))
        archery = self.grant_authorization(self.person, WeaponStyle.objects.create(name='Mounted Archery', discipline=eq))
        riding.status = self.status_revoked
        riding.save()
        for auth in [gaming, archery]:
            self.assertFalse(auth.validity_intervals.filter(end_date__gte=self.today).exists())
            self.assertTrue(self.valid(auth, self.today - timedelta(days=1)))

    def test_cut_and_thrust_keeps_valid_alternative_prerequisite(self):
        ct = Discipline.objects.create(name='Cut & Thrust')
        first = self.grant_authorization(self.person, WeaponStyle.objects.create(name='Single Sword', discipline=ct))
        self.grant_authorization(self.person, WeaponStyle.objects.create(name='Two-Handed', discipline=ct))
        spear = self.grant_authorization(self.person, WeaponStyle.objects.create(name='Spear', discipline=ct))
        first.status = self.status_revoked
        first.save()
        self.assertTrue(self.valid(spear, self.today))
