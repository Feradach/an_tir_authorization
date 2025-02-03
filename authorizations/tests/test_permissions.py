from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from django.test import TestCase, RequestFactory
from authorizations.models import User, Region, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, Authorization, BranchMarshal
from authorizations.permissions import authorization_follows_rules, approve_authorization, calculate_age

"""
Tests:
authorization_follows_rules
	- Rule 1: A senior marshal in a discipline can authorize any person in a weapon style for that discipline
	- Rule 2: A junior marshal must be at least 16 years old
        - Rule 2a: Archery and Thrown junior marshals must be adults
    - Rule 3: A senior marshal must be an adult
    - Rule 4: A Rapier, Cut & Thrust or Youth Rapier fighter must have single sword as their first weapon authorization
    - Rule 5: Rapier fighters must be at lest 14 years old
    - Rule 6: Armored fighters, Cut & Thrust fighters, and Senior Equestrian Ground Crew must be at least 16 years old
    - Rule 7: Senior Equestrian Ground Crew must be at least 16 years old
    - Rule 8: Youth combatants must be at least 6 years old and minors.
        - Rule 8a: The exception is that marshals can be adults.
    - Rule 9: For equestrian, a person must be at least 5 years old to engage in general riding, mounted gaming, mounted archery, or junior ground crew.
    - Rule 10: For equestrian, a person must be an adult to participate in Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting.
    - Rule 11: Youth rapier marshals must already be Senior Rapier marshals
    - Rule 12: An Equestrian Junior marshal must already have Senior Ground Crew and General Riding Authorizations.
    - Rule 13: An Equestrian Senior marshal must already have Junior Marshal and Mounted Gaming Authorizations.
    - Rule 14: In order to authorize someone in Mounted Archery, Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting, the Senior Marshal must have the same Authorizations.
    - Rule 15: Junior and Senior marshals must be current members.
    - Rule 16: You cannot renew a revoked authorization.
    - Rule 17: Cannot duplicate/renew a pending authorization.
    - Rule 18: If someone has an active senior marshal, they cannot be made a junior marshal.
		- Rule 18a: If they have a pending senior marshal they cannot get a new junior marshal. They can renew an existing junior marshal.
    - Rule 19: Cannot add a new senior marshal if there is a pending junior marshal.
    - Rule 20: Cannot make an authorization for yourself.
    
approve_authorization
    - Rule 1: Kingdom authorization officer can approve any marshal by themselves.
        - Rule 1b: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
    - Rule 2: must be a senior marshal in the discipline to approve.
    - Rule 3: Cannot concur with an authorization you proposed.
    - Rule 4: If a pending authorization for Senior marshal is approved, it then goes to the region for approval.
        - Rule 4a: If a junior marshal is approved it becomes active.
        - Rule 4b: If a junior marshal for Youth combat is approved it goes to the Kingdom authorization officer for confirmation.
    - Rule 5: If the authorization is out for regional approval, you need to be a regional marshal to approve it.
        - Rule 5a: If the region approves a Youth Combat Senior marshal, it goes to the Kingdom authorization officer for confirmation.
        - Rule 5b: If the regional marshal approves a non-youth senior marshal, it becomes active.
        - Rule 5c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
"""
# Create your tests here.
class Rule1_20Test(TestCase):
    """
    Rule 1: A senior marshal in a discipline can authorize any person in a weapon style for that discipline.
    Rule 20: Cannot make an authorization for yourself.
    """

    @classmethod
    def setUpTestData(cls):
        # Setting up test data like this is going to be necessary as adding the database is an expensive operation.
        cls.user_marshal = User.objects.create_user(username='samanueke@hotmail.com', password='eGqNMC2D', membership='100',
                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.user_adult = User.objects.create_user(username='Frank_Smith@samplemail.com', password='eGqNMC2D',
                                          membership='4687335',
                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_rapier = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_rapier)
        cls.style_two_handed_armored = WeaponStyle.objects.create(name='Two-Handed', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Fargo the Bold', branch=cls.branch_tp, is_minor=False)
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Cedric the Bold', branch=cls.branch_tp, is_minor=False)
        cls.auth_1 = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_armored, status=cls.status_active,
                                          expiration=date.today() + relativedelta(years=1))
        cls.auth_2 = Authorization.objects.create(person=cls.person_marshal, style=cls.style_jm_rapier, status=cls.status_active,
                                          expiration=date.today() + relativedelta(years=1))


    def test_senior_marshal_can_authorize(self):
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_two_handed_armored.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


    def test_senior_marshal_cannot_authorize_different_discipline(self):
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_single_rapier.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must have a current Rapier senior marshal.')

    def test_senior_marshal_cannot_authorize_self(self):
        result, message = authorization_follows_rules(self.user_marshal, self.person_marshal, self.style_two_handed_armored.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot make an authorization for yourself.')

    def test_can_give_senior_if_have_junior(self):
        style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=self.discipline_armored)
        Authorization.objects.create(person=self.person_adult, style=style_jm_armored, status=self.status_active,
                                          expiration=date.today() + relativedelta(years=1))

        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_sm_armored.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


class AgeTest(TestCase):
    """Test age limitations on authorizations.
    This covers rules 2, 3, 5, 6, 7, 8, 9, and 10
    """
    @classmethod
    def setUpTestData(cls):
        # Shared Setup
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                membership='31913662',
                                                membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                               branch=cls.branch_tp,
                                               is_minor=False)

    def test_rule_2(self):
        """Rule 2: A junior marshal must be at least 16 years old"""
        # Data for the test
        discipline_armored = Discipline.objects.create(name='Armored')
        style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_armored)
        style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=discipline_armored)
        auth_sm_armor_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_armored,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1))

        user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))
        user_14yo = User.objects.create_user(username='edwin97@yahoo.com', password='eGqNMC2D', membership='51566283',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=14) - timedelta(days=10))

        person_adult = Person.objects.create(user=user_adult, sca_name='Ysabeau de la Mar', branch=self.branch_tp,
                                             is_minor=False)
        person_14yo = Person.objects.create(user=user_14yo, sca_name='Jocelyn the Bright', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_adult as a junior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_adult, 2)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_14yo as a junior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_14yo, 2)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 16 years old to become a junior marshal.')

    def test_rule_2a(self):
        """Rule 2a: Archery and Thrown junior marshals must be adults"""
        # Data for the test
        discipline_archery = Discipline.objects.create(name='Archery')
        style_jm_archery = WeaponStyle.objects.create(name='Junior Marshal', discipline=discipline_archery)
        style_sm_archery = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_archery)
        auth_jm_armor_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_archery,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1))

        user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))
        user_17yo = User.objects.create_user(username='leonardgarcia@galloway.com', password='eGqNMC2D',
                                             membership='17111275', membership_expiration=date.today() + relativedelta(years=1),
                                             birthday=date.today() - relativedelta(years=17) - timedelta(days=10))

        person_adult = Person.objects.create(user=user_adult, sca_name='Ysabeau de la Mar', branch=self.branch_tp,
                                             is_minor=False)
        person_17yo = Person.objects.create(user=user_17yo, sca_name='Torvald Shieldbreaker', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_adult as an archery junior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_adult, 1)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_17yo as an archery junior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_17yo, 1)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be an adult to become an archery or thrown weapon junior marshal.')

    def test_rule_3(self):
        """Rule 3: A senior marshal must be an adult"""
        # Data for the test
        discipline_armored = Discipline.objects.create(name='Armored')
        style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_armored)
        auth_sm_armor_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_armored,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1))

        user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))
        user_17yo = User.objects.create_user(username='leonardgarcia@galloway.com', password='eGqNMC2D',
                                             membership='17111275', membership_expiration=date.today() + relativedelta(years=1),
                                             birthday=date.today() - relativedelta(years=17) - timedelta(days=10))

        person_adult = Person.objects.create(user=user_adult, sca_name='Ysabeau de la Mar', branch=self.branch_tp,
                                             is_minor=False)
        person_17yo = Person.objects.create(user=user_17yo, sca_name='Torvald Shieldbreaker', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_adult as a senior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_adult, 1)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_17yo as a senior marshal
        result, message = authorization_follows_rules(self.user_marshal, person_17yo, 1)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be an adult to become a senior marshal.')

    def test_rule_5(self):
        """Rule 5: Rapier fighters must be at lest 14 years old"""
        # Data for the test
        discipline_rapier = Discipline.objects.create(name='Rapier')

        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_rapier)
        style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=discipline_rapier)

        user_12yo = User.objects.create_user(username='charleschase@parker.com', password='eGqNMC2D',
                                             membership='79100861',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=12) - timedelta(days=10))

        user_14yo = User.objects.create_user(username='edwin97@yahoo.com', password='eGqNMC2D', membership='51566283',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=14) - timedelta(days=10))

        person_12yo = Person.objects.create(user=user_12yo, sca_name='Avelina Greencloak', branch=self.branch_tp,
                                            is_minor=True)
        person_14yo = Person.objects.create(user=user_14yo, sca_name='Jocelyn the Bright', branch=self.branch_tp,
                                            is_minor=True)

        auth_sm_rapier_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_rapier,
                                                              status=self.status_active,
                                                              expiration=date.today() + relativedelta(years=1))
        # Do the test
        # POSITIVE: approve person_14yo as a rapier fighter
        result, message = authorization_follows_rules(self.user_marshal, person_14yo, 2)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_12yo as a rapier fighter
        result, message = authorization_follows_rules(self.user_marshal, person_12yo, 2)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 14 years old to become a rapier fighter.')


    def test_rule_6(self):
        """Rule 6: Armored and Cut & Thrust fighters must be at least 16 years old"""
        # Data for the test
        discipline_armored = Discipline.objects.create(name='Armored')
        style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_armored)
        auth_sm_armor_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_armored,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1))
        style_longsword_armored = WeaponStyle.objects.create(name='Two-Handed',
                                                             discipline=discipline_armored)

        user_14yo = User.objects.create_user(username='edwin97@yahoo.com', password='eGqNMC2D',
                                             membership='51566283',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=14) - timedelta(days=10))

        user_17yo = User.objects.create_user(username='leonardgarcia@galloway.com', password='eGqNMC2D',
                                             membership='17111275', membership_expiration=date.today() + relativedelta(years=1),
                                             birthday=date.today() - relativedelta(years=17) - timedelta(days=10))

        person_14yo = Person.objects.create(user=user_14yo, sca_name='Jocelyn the Bright', branch=self.branch_tp,
                                            is_minor=True)
        person_17yo = Person.objects.create(user=user_17yo, sca_name='Torvald Shieldbreaker', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_17yo as an armored fighter
        result, message = authorization_follows_rules(self.user_marshal, person_17yo, 2)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_14yo as an armored fighter
        result, message = authorization_follows_rules(self.user_marshal, person_14yo, 2)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 16 years old to become authorized in Armored combat.')


    def test_rule_7(self):
        """Rule 7: Senior Equestrian Ground Crew must be at least 16 years old"""
        # Data for the test
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal',
                                                         discipline=discipline_equestrian)
        style_snr_ground_equestrian = WeaponStyle.objects.create(name='Senior Ground Crew',
                                                                 discipline=discipline_equestrian)
        auth_sm_equestrian_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_equestrian,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

        user_14yo = User.objects.create_user(username='edwin97@yahoo.com', password='eGqNMC2D', membership='51566283',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=14) - timedelta(days=10))
        person_14yo = Person.objects.create(user=user_14yo, sca_name='Jocelyn the Bright', branch=self.branch_tp,
                                            is_minor=True)

        user_17yo = User.objects.create_user(username='leonardgarcia@galloway.com', password='eGqNMC2D',
                                             membership='17111275', membership_expiration=date.today() + relativedelta(years=1),
                                             birthday=date.today() - relativedelta(years=17) - timedelta(days=10))
        person_17yo = Person.objects.create(user=user_17yo, sca_name='Torvald Shieldbreaker', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_17yo as an equestrian ground crew
        style_id = WeaponStyle.objects.get(name='Senior Ground Crew').id
        result, message = authorization_follows_rules(self.user_marshal, person_17yo, style_id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_14yo as an equestrian ground crew
        self.assertEqual(calculate_age(person_14yo.user.birthday), 14)
        result, message = authorization_follows_rules(self.user_marshal, person_14yo, style_id)
        # new_authorization = Authorization.objects.get(person=person_14yo)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 16 years old to become authorized as Senior Ground Crew.')

    def test_rule_8(self):
        """Rule 8: Youth combatants must be at least 6 years old and minors."""
        # Data for the test
        discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal',
                                                            discipline=discipline_youth_armored)
        style_two_handed_youth_armored = WeaponStyle.objects.create(name='Two-Handed',
                                                            discipline=discipline_youth_armored)

        user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))

        auth_sm_youth_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_youth_armored,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

        user_3yo = User.objects.create_user(username='cwhite@yahoo.com', password='eGqNMC2D', membership='56149296',
                                            membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=3) - timedelta(days=10))

        user_12yo = User.objects.create_user(username='charleschase@parker.com', password='eGqNMC2D',
                                             membership='79100861',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=12) - timedelta(days=10))

        person_adult = Person.objects.create(user=user_adult, sca_name='Ysabeau de la Mar', branch=self.branch_tp,
                                             is_minor=False)
        person_3yo = Person.objects.create(user=user_3yo, sca_name='Tancred', branch=self.branch_tp, is_minor=True)
        person_12yo = Person.objects.create(user=user_12yo, sca_name='Avelina Greencloak', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_12yo as a youth armored fighter
        result, message = authorization_follows_rules(self.user_marshal, person_12yo, style_two_handed_youth_armored.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_3yo as a youth armored fighter
        result, message = authorization_follows_rules(self.user_marshal, person_3yo, style_two_handed_youth_armored.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 6 years old to become authorized in Youth Armored combat.')
        # NEGATIVE: do not approve person_adult as a youth armored fighter
        result, message = authorization_follows_rules(self.user_marshal, person_adult, style_two_handed_youth_armored.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a minor to become authorized in Youth Armored combat.')

    def test_rule_9(self):
        """Rule 9: For equestrian, a person must be at least 5 years old to engage in general riding, mounted gaming, mounted archery, or junior ground crew."""
        # Data for the test
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal',
                                                         discipline=discipline_equestrian)
        style_riding_equestrian = WeaponStyle.objects.create(name='General Riding',
                                                             discipline=discipline_equestrian)
        auth_sm_equestrian_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_equestrian,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

        user_3yo = User.objects.create_user(username='cwhite@yahoo.com', password='eGqNMC2D', membership='56149296',
                                            membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=3) - timedelta(days=10))

        user_12yo = User.objects.create_user(username='charleschase@parker.com', password='eGqNMC2D',
                                             membership='79100861',
                                             membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=12) - timedelta(days=10))

        person_3yo = Person.objects.create(user=user_3yo, sca_name='Tancred', branch=self.branch_tp, is_minor=True)
        person_12yo = Person.objects.create(user=user_12yo, sca_name='Avelina Greencloak', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_12yo as an equestrian general rider
        result, message = authorization_follows_rules(self.user_marshal, person_12yo, 2)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_3yo as an equestrian general rider
        result, message = authorization_follows_rules(self.user_marshal, person_3yo, 2)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be at least 5 years old to become authorized in General Riding.')

    def test_rule_10(self):
        """Rule 10: For equestrian, a person must be an adult to participate in Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting."""
        # Data for the test
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal',
                                                         discipline=discipline_equestrian)
        style_crest_combat_equestrian = WeaponStyle.objects.create(name='Crest Combat',
                                                                   discipline=discipline_equestrian)
        auth_sm_equestrian_marshal = Authorization.objects.create(person=self.person_marshal, style=style_sm_equestrian,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        auth_sm_equestrian_marshal = Authorization.objects.create(person=self.person_marshal, style=style_crest_combat_equestrian,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

        user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))
        user_17yo = User.objects.create_user(username='leonardgarcia@galloway.com', password='eGqNMC2D',
                                             membership='17111275', membership_expiration=date.today() + relativedelta(years=1),
                                             birthday=date.today() - relativedelta(years=17) - timedelta(days=10))

        person_adult = Person.objects.create(user=user_adult, sca_name='Ysabeau de la Mar', branch=self.branch_tp,
                                             is_minor=False)
        person_17yo = Person.objects.create(user=user_17yo, sca_name='Torvald Shieldbreaker', branch=self.branch_tp,
                                            is_minor=True)

        # Do the test
        # POSITIVE: approve person_adult as an equestrian crest combatant
        result, message = authorization_follows_rules(self.user_marshal, person_adult, 2)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')
        # NEGATIVE: do not approve person_17yo as an equestrian crest combatant
        result, message = authorization_follows_rules(self.user_marshal, person_17yo, 2)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be an adult to become authorized in Crest Combat.')


class Rule4Test(TestCase):
    """Rule 4: A Rapier, Cut & Thrust or Youth Rapier fighter must have single sword as their first weapon authorization"""

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                              membership='68907107',
                                              membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar', branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_two_handed_rapier = WeaponStyle.objects.create(name='Two-Handed Sword', discipline=cls.discipline_rapier)

        cls.auth_sm_rapier_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_rapier,
                                                                  status=cls.status_active, expiration=date.today() + relativedelta(years=1))

    def test_single_sword_first(self):
        # Do the test
        # NEGATIVE: do not approve person_adult for two-handed sword
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_two_handed_rapier.id)
        self.assertFalse(result)
        self.assertEqual(message, 'A fighter must be authorized with single sword as their first rapier authorization.')
        # POSITIVE: approve person_adult for single sword
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_single_rapier.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')

    def test_senior_marshal_exception(self):
        # Do the test
        # POSITIVE: approve person_adult for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult, self.style_sm_rapier.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


class Rule11Test(TestCase):
    """Rule 11: Youth rapier marshals must already be Senior Rapier marshals"""

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.discipline_youth_rapier = Discipline.objects.create(name='Youth Rapier')
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)
        cls.style_sm_youth_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_rapier)

        auth_sm_rapier_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_youth_rapier,
                                                              status=cls.status_active,
                                                              expiration=date.today() + relativedelta(years=1))

    def test_senior_marshal_first(self):
        # Do the test
        # NEGATIVE: do not approve person_adult for youth rapier marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_youth_rapier.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a senior rapier marshal to become a youth rapier marshal.')
        # MODIFICATION: add adult rapier marshal to person_adult
        auth_sm_rapier_adult = Authorization.objects.create(person=self.person_adult, style=self.style_sm_rapier,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1))
        # POSITIVE: approve person_adult for youth rapier marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_youth_rapier.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


class RuleEquestrianAuthsTest(TestCase):
    """
    Rule 12: An Equestrian Junior marshal must already have Senior Ground Crew and General Riding Authorizations.
    Rule 13: An Equestrian Senior marshal must already have Junior Marshal and Mounted Gaming Authorizations.
    Rule 14: In order to authorize someone in Mounted Archery, Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting, the Senior Marshal must have the same Authorizations.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')

        cls.style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_equestrian)
        cls.auth_sm_equestrian_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_equestrian,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

    def test_make_eq_junior(self):
        style_sr_ground_crew = WeaponStyle.objects.create(name='Senior Ground Crew', discipline=self.discipline_equestrian)
        style_riding = WeaponStyle.objects.create(name='General Riding', discipline=self.discipline_equestrian)
        style_jm_equestrian = WeaponStyle.objects.create(name='Junior Marshal', discipline=self.discipline_equestrian)


        # Do the test
        # NEGATIVE: do not approve person_adult for junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      style_jm_equestrian.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Junior Equestrian marshal must have Senior Ground Crew and General Riding authorization.')
        # MODIFICATION: add senior ground crew to person_adult
        auth_sr_ground_crew_adult = Authorization.objects.create(person=self.person_adult, style=style_sr_ground_crew,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        # NEGATIVE: do not approve person_adult for junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      style_jm_equestrian.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Junior Equestrian marshal must have Senior Ground Crew and General Riding authorization.')

        # MODIFICATION: add general riding to person_adult
        auth_riding_adult = Authorization.objects.create(person=self.person_adult, style=style_riding,
                                                         status=self.status_active,
                                                         expiration=date.today() + relativedelta(years=1))
        # POSITIVE: approve person_adult for junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      style_jm_equestrian.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')

    def test_make_eq_senior(self):
        style_riding = WeaponStyle.objects.create(name='Mounted Gaming', discipline=self.discipline_equestrian)
        style_jm_equestrian = WeaponStyle.objects.create(name='Junior Marshal', discipline=self.discipline_equestrian)

        # Do the test
        # NEGATIVE: do not approve person_adult for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_equestrian.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.')
        # MODIFICATION: add junior marshal to person_adult
        auth_jm_equestrian_adult = Authorization.objects.create(person=self.person_adult, style=style_jm_equestrian,
                                                                 status=self.status_active,
                                                                 expiration=date.today() + relativedelta(years=1))
        # NEGATIVE: do not approve person_adult for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_equestrian.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.')

        # MODIFICATION: add mounted gaming to person_adult
        auth_riding_adult = Authorization.objects.create(person=self.person_adult, style=style_riding,
                                                         status=self.status_active,
                                                         expiration=date.today() + relativedelta(years=1))
        # POSITIVE: approve person_adult for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_equestrian.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')

    def test_make_eq_mounted_heavy(self):
        style_mounted_heavy = WeaponStyle.objects.create(name='Mounted Heavy Combat', discipline=self.discipline_equestrian)

        # Do the test
        # NEGATIVE: do not approve person_adult for Mounted Heavy Combat
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      style_mounted_heavy.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be authorized in Mounted Heavy Combat to authorize other participants.')

        # MODIFICATION: add Mounted Heavy Combat to person_marshal
        auth_mounted_heavy_marshal = Authorization.objects.create(person=self.person_marshal, style=style_mounted_heavy,
                                                                  status=self.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        # POSITIVE: approve person_adult for Mounted Heavy Combat
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      style_mounted_heavy.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


class Rule15Test(TestCase):
    """Rule 15:Junior and Senior marshals must be current members."""

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_member = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='6890710',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_member = Person.objects.create(user=cls.user_member, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.user_non_member = User.objects.create_user(username='sandersdua@hotmail.com', password='eGqNMC2D')
        cls.person_non_member = Person.objects.create(user=cls.user_non_member, sca_name='Ysabeau de la Mar',
                                                  branch=cls.branch_tp,
                                                  is_minor=False)

        cls.user_old_member = User.objects.create_user(username='sandere@hotmail.com', password='eGqNMC2D',
                                                       membership='68907',
                                                       membership_expiration=date.today() - relativedelta(years=1))
        cls.person_old_member = Person.objects.create(user=cls.user_old_member, sca_name='Ysabeau de la Mar',
                                                      branch=cls.branch_tp,
                                                      is_minor=False)

        cls.discipline_archery = Discipline.objects.create(name='Archery')
        cls.style_sm_archery = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_archery)

        cls.auth_sm_archery_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_archery,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

    def test_must_be_member(self):
        # Do the test
        # NEGATIVE: do not approve person_non_member for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_non_member,
                                                      self.style_sm_archery.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a current member to be authorized as a marshal.')
        # NEGATIVE: do not approve person_old_member for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_old_member,
                                                      self.style_sm_archery.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a current member to be authorized as a marshal.')
        # POSITIVE: approve person_member for senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_member,
                                                      self.style_sm_archery.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')


class Rule16Test(TestCase):
    """
    Rule 16: You cannot renew a revoked authorization.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_member = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                   membership='68907107',
                                                   membership_expiration=date.today() + relativedelta(years=1))
        cls.person_member = Person.objects.create(user=cls.user_member, sca_name='Ysabeau de la Mar',
                                                  branch=cls.branch_tp,
                                                  is_minor=False)

        cls.discipline_archery = Discipline.objects.create(name='Archery')
        cls.style_sm_archery = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_archery)


        cls.auth_sm_archery_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_archery,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        cls.auth_sm_archery_marshal = Authorization.objects.create(person=cls.person_member,
                                                                   style=cls.style_sm_archery,
                                                                   status=cls.status_revoked,
                                                                   expiration=date.today() - timedelta(days=10))

    def test_revoked_auth(self):
        # Do the test
        # NEGATIVE: do not renew senior marshal authorization for person_member
        result, message = authorization_follows_rules(self.user_marshal, self.person_member,
                                                      self.style_sm_archery.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot renew a revoked authorization.')


class RulePendingTest(TestCase):
    """
    Rule 17: Cannot duplicate/renew a pending authorization.
    Rule 18: If someone has an active senior marshal, they cannot be made a junior marshal.
		Rule 18a: If they have a pending senior marshal they cannot get a new junior marshal. They can renew an existing junior marshal.
    Rule 19: Cannot add a new senior marshal if there is a pending junior marshal.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)
        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_missile = Discipline.objects.create(name='Missile')
        cls.style_sm_missile = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_missile)
        cls.style_jm_missile = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_missile)

        cls.auth_sm_missile_marshal = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_missile,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))

    def test_no_renew_pending(self):
        auth_sm_missile_marshal = Authorization.objects.create(person=self.person_adult,
                                                                   style=self.style_sm_missile,
                                                                   status=self.status_pending,
                                                                   expiration=date.today() + relativedelta(years=1))

        # Do the test
        # NEGATIVE: cannot renew pending senior authorization
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_sm_missile.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot renew a pending authorization.')
        # NEGATIVE: cannot add new junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_jm_missile.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot have a new junior marshal if a senior marshal is pending.')

    def test_no_junior_after_senior(self):
        auth_sm_missile_marshal = Authorization.objects.create(person=self.person_adult,
                                                               style=self.style_sm_missile,
                                                               status=self.status_active,
                                                               expiration=date.today() + relativedelta(years=1))

        # Do the test
        # NEGATIVE: cannot add junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_jm_missile.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot make someone a junior marshal if they are already a senior marshal.')

    def test_yes_renew_junior_with_pending_senior(self):
        auth_sm_missile_marshal = Authorization.objects.create(person=self.person_adult,
                                                               style=self.style_sm_missile,
                                                               status=self.status_pending,
                                                               expiration=date.today() + relativedelta(years=1))
        auth_jm_missile_marshal = Authorization.objects.create(person=self.person_adult,
                                                               style=self.style_jm_missile,
                                                               status=self.status_active,
                                                               expiration=date.today() + relativedelta(years=1))

        # Do the test
        # POSITIVE: can renew junior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.style_jm_missile.id)
        self.assertTrue(result)
        self.assertEqual(message, 'Authorization follows all rules.')

    def test_no_new_senior_with_pending_junior(self):
        auth_jm_missile_marshal = Authorization.objects.create(person=self.person_adult,
                                                               style=self.style_jm_missile,
                                                               status=self.status_pending,
                                                               expiration=date.today() + relativedelta(years=1))

        # Do the test
        # NEGATIVE: cannot add new senior marshal
        result, message = authorization_follows_rules(self.user_marshal, self.person_adult,
                                                      self.auth_sm_missile_marshal.id)
        self.assertFalse(result)
        self.assertEqual(message, 'Cannot have a new senior marshal if a junior marshal is pending.')

class ApprovalTestRule1(TestCase):
    """
    Rule 1: Kingdom authorization officer can approve any marshal by themselves.
    Rule 1a: If Youth Armored or Youth Rapier, set expiration to 2 years.
    Rule 1b: If not Youth Armored or Youth Rapier, set expiration to 4 years.
    Rule 1c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_auth_officer = WeaponStyle.objects.create(name='Authorization Officer', discipline=cls.discipline_auth_officer)

        cls.auth_marshal_auth_officer = Authorization.objects.create(person=cls.person_marshal, style=cls.style_auth_officer,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1), marshal=cls.person_marshal)
        cls.auth_adult_jm_armored = Authorization.objects.create(person=cls.person_adult,
                                                                 style=cls.style_jm_armored,
                                                                 status=cls.status_active,
                                                                 expiration=date.today() + relativedelta(years=1), marshal=cls.person_marshal)
        cls.auth_adult_sm_armored = Authorization.objects.create(person=cls.person_adult,
                                                                   style=cls.style_sm_armored,
                                                                   status=cls.status_pending,
                                                                   expiration=date.today() + relativedelta(years=1), marshal=cls.person_marshal)

    def setUp(self):
        self.factory = RequestFactory()

    def test_kingdom_marshal_approve_senior_expire_junior_delete(self):
        # Prepare request
        post_data = {'authorization_id': str(self.auth_adult_sm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # DATA CHECK: check that junior marshal is present.
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=self.style_jm_armored).exists())
        # POSITIVE: approve person_adult as a senior marshal.
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Armored Senior Marshal authorization approved!')
        # POSITIVE: confirm that senior marshal is approved for four years.
        self.assertEqual(Authorization.objects.get(person=self.person_adult, style=self.style_sm_armored).expiration,date.today() + relativedelta(years=4))
        # POSITIVE: confirm junior marshal is deleted.
        self.assertFalse(Authorization.objects.filter(person=self.person_adult, style=self.style_jm_armored).exists())


    def test_kingdom_marshal_approve_senior_expire_junior_delete(self):
        discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal',
                                                            discipline=discipline_youth_armored)
        auth_adult_jm_youth_armored = Authorization.objects.create(person=self.person_adult,
                                                                   style=style_sm_youth_armored,
                                                                   status=self.status_pending,
                                                                   expiration=date.today() + relativedelta(
                                                                       years=1), marshal=self.person_marshal)
        # Prepare request
        post_data = {'authorization_id': str(auth_adult_jm_youth_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # POSITIVE: check that youth marshal is approved for two years.
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Youth Armored Senior Marshal authorization approved!')
        self.assertEqual(Authorization.objects.get(person=self.person_adult, style=style_sm_youth_armored).expiration,
                         date.today() + relativedelta(years=2))


class ApprovalTestRule2(TestCase):
    """
    Rule 2: must be a senior marshal in the discipline to approve.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_other_marshal = User.objects.create_user(username='martindavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_other_marshal = Person.objects.create(user=cls.user_other_marshal, sca_name='Cedric Diggory',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)
        cls.style_jm_rapier = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_rapier)


        cls.auth_marshal_sm_armored = Authorization.objects.create(person=cls.person_marshal, style=cls.style_sm_armored,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        cls.auth_other_marshal_sm_rapier = Authorization.objects.create(person=cls.person_other_marshal, style=cls.style_sm_rapier,
                                                                  status=cls.status_active,
                                                                  expiration=date.today() + relativedelta(years=1))
        cls.auth_adult_jm_rapier = Authorization.objects.create(person=cls.person_adult,
                                                                 style=cls.style_jm_rapier,
                                                                 status=cls.status_pending,
                                                                 expiration=date.today() + relativedelta(years=1), marshal=cls.person_other_marshal)

    def setUp(self):
        self.factory = RequestFactory()

    def test_case(self):
        # Prepare request
        post_data = {'authorization_id': str(self.auth_adult_jm_rapier.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # NEGATIVE: person_marshal cannot approve person_adult as a rapier junior marshal.
        result, message = approve_authorization(request)
        self.assertFalse(result)
        self.assertEqual(message, 'You must be a senior marshal in this discipline to approve this authorization.')


class ApprovalTestRule3(TestCase):
    """
    Rule 3: Cannot concur with an authorization you proposed.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_other_marshal = User.objects.create_user(username='martindavis@gmail.com', password='eGqNMC2D',
                                                          membership='31913',
                                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.person_other_marshal = Person.objects.create(user=cls.user_other_marshal, sca_name='Cedric Diggory',
                                                         branch=cls.branch_tp,
                                                         is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)

        cls.auth_marshal_sm_armored = Authorization.objects.create(person=cls.person_marshal,
                                                                   style=cls.style_sm_armored,
                                                                   status=cls.status_active,
                                                                   expiration=date.today() + relativedelta(years=1))
        cls.auth_other_marshal_sm_armored = Authorization.objects.create(person=cls.person_other_marshal,
                                                                        style=cls.style_sm_armored,
                                                                        status=cls.status_active,
                                                                        expiration=date.today() + relativedelta(years=1))
        cls.auth_adult_jm_armored = Authorization.objects.create(person=cls.person_adult,
                                                                style=cls.style_jm_armored,
                                                                status=cls.status_pending,
                                                                expiration=date.today() + relativedelta(years=1), marshal=cls.person_marshal)

    def setUp(self):
        self.factory = RequestFactory()

    def test_case(self):
        # Prepare request
        post_data = {'authorization_id': str(self.auth_adult_jm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # NEGATIVE: person_marshal cannot confirm adult junior marshal
        result, message = approve_authorization(request)
        self.assertFalse(result)
        self.assertEqual(message, 'You cannot concur with your own authorization.')

        # Prepare request
        post_data = {'authorization_id': str(self.auth_adult_jm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_other_marshal

        # POSITIVE: other_marshal can confirm adult junior marshal
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Armored Junior Marshal authorization approved!')
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=self.style_jm_armored, status=self.status_active).exists())


class ApprovalTestRule4(TestCase):
    """
    Rule 4: If a pending authorization for Senior marshal is approved, it then goes to the region for approval.
    Rule 4a: If a junior marshal is approved it becomes active.
    Rule 4b: If a junior marshal for Youth combat is approved it goes to the Kingdom authorization officer for confirmation.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_other_marshal = User.objects.create_user(username='martindavis@gmail.com', password='eGqNMC2D',
                                                          membership='31913',
                                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.person_other_marshal = Person.objects.create(user=cls.user_other_marshal, sca_name='Cedric Diggory',
                                                         branch=cls.branch_tp,
                                                         is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_armored = Discipline.objects.create(name='Armored')

        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)

        cls.auth_marshal_sm_armored = Authorization.objects.create(person=cls.person_marshal,
                                                                   style=cls.style_sm_armored,
                                                                   status=cls.status_active,
                                                                   expiration=date.today() + relativedelta(years=1))

        cls.auth_other_marshal_sm_armored = Authorization.objects.create(person=cls.person_other_marshal,
                                                                   style=cls.style_sm_armored,
                                                                   status=cls.status_active,
                                                                   expiration=date.today() + relativedelta(years=1))

    def setUp(self):
        self.factory = RequestFactory()

    def test_pending_senior_to_region(self):
        auth_adult_sm_armored = Authorization.objects.create(person=self.person_adult,
                                                                               style=self.style_sm_armored,
                                                                               status=self.status_pending,
                                                                               expiration=date.today() + relativedelta(
                                                                                   years=1), marshal=self.person_other_marshal)

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_sm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # POSITIVE: Senior marshal becomes needs region approval
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Armored Senior Marshal authorization ready for regional approval!')
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=self.style_sm_armored, status=self.status_regional).exists())

    def test_pending_youth_junior_to_kingdom(self):
        discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal',
                                                                discipline=discipline_youth_armored)
        style_jm_youth_armored = WeaponStyle.objects.create(name='Junior Marshal',
                                                            discipline=discipline_youth_armored)
        auth_adult_jm_youth_armored = Authorization.objects.create(person=self.person_adult,
                                                             style=style_jm_youth_armored,
                                                             status=self.status_pending,
                                                             expiration=date.today() + relativedelta(
                                                                 years=1), marshal=self.person_other_marshal)
        auth_other_marshal_sm_youth_armored = Authorization.objects.create(person=self.person_other_marshal,
                                                                               style=style_sm_youth_armored,
                                                                               status=self.status_active,
                                                                               expiration=date.today() + relativedelta(
                                                                                   years=1))
        auth_marshal_sm_youth_armored = Authorization.objects.create(person=self.person_marshal,
                                                                         style=style_sm_youth_armored,
                                                                         status=self.status_active,
                                                                         expiration=date.today() + relativedelta(
                                                                             years=1))

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_jm_youth_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # POSITIVE: Junior marshal becomes needs kingdom approval
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Youth Armored Junior Marshal authorization ready for kingdom to confirm background check!')
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=style_jm_youth_armored,
                                                     status=self.status_kingdom).exists())


class ApprovalTestRule5(TestCase):
    """
    Rule 5: If the authorization is out for regional approval, you need to be the correct regional marshal to approve it (exception that Armored can approve Missile).
    Rule 5a: If the region approves a Youth Combat Senior marshal, it goes to the Kingdom authorization officer for confirmation.
    Rule 5b: If the regional marshal approves a non-youth senior marshal, it becomes active.
    Rule 5c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.branch_an_tir = Branch.objects.create(name='An Tir', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_other_marshal = User.objects.create_user(username='martindavis@gmail.com', password='eGqNMC2D',
                                                          membership='31913',
                                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.person_other_marshal = Person.objects.create(user=cls.user_other_marshal, sca_name='Cedric Diggory',
                                                         branch=cls.branch_tp,
                                                         is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

    def setUp(self):
        self.factory = RequestFactory()

    def test_must_be_correct_regional_marshal(self):
        discipline_armored = Discipline.objects.create(name='Armored')
        style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_armored)
        style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=discipline_armored)

        auth_marshal_sm_armored = Authorization.objects.create(person=self.person_marshal,
                                                                   style=style_sm_armored,
                                                                   status=self.status_active,
                                                                   expiration=date.today() + relativedelta(years=1))

        auth_other_marshal_sm_armored = Authorization.objects.create(person=self.person_other_marshal,
                                                                         style=style_sm_armored,
                                                                         status=self.status_active,
                                                                         expiration=date.today() + relativedelta(
                                                                             years=1))
        user_rapier_marshal = User.objects.create_user(username='kristinadav@gmail.com', password='eGqNMC2D',
                                                    membership='913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        person_rapier_marshal = Person.objects.create(user=user_rapier_marshal, sca_name='Theodric of the White Hart',
                                                   branch=self.branch_tp,
                                                   is_minor=False)
        discipline_rapier = Discipline.objects.create(name='Rapier')
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_rapier)

        auth_sm_rapier_marshal_armored = Authorization.objects.create(person=person_rapier_marshal, style=style_sm_armored,
                                                              status=self.status_active,
                                                              expiration=date.today() + relativedelta(
                                                                  years=1))
        auth_sm_rapier_marshal = Authorization.objects.create(person=person_rapier_marshal, style=style_sm_rapier,status=self.status_active,
                                                                               expiration=date.today() + relativedelta(
                                                                                   years=1))
        rapier_branch_marshal = BranchMarshal.objects.create(person=person_rapier_marshal, branch=self.branch_an_tir, discipline=discipline_rapier,start_date=date.today() - relativedelta(
                                                                                   years=1), end_date=date.today() + relativedelta(
        ))
        branch_armored_marshal = BranchMarshal.objects.create(person=self.person_marshal, branch=self.branch_an_tir,
                                                                  discipline=discipline_armored,
                                                                  start_date=date.today() - relativedelta(
                                                                      years=1), end_date=date.today() + relativedelta(years=1))
        auth_adult_sm_armored = Authorization.objects.create(person=self.person_adult,
                                                                     style=style_sm_armored,
                                                                     status=self.status_regional,
                                                                     expiration=date.today() + relativedelta(
                                                                         years=1), marshal=self.person_other_marshal)
        auth_adult_jm_armored = Authorization.objects.create(person=self.person_adult,
                                                             style=style_jm_armored,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(
                                                                 years=1), marshal=self.person_other_marshal)

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_sm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = user_rapier_marshal

        # Do the test
        # DATA CHECK: active adult_junior armored marshal exists
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=style_jm_armored, status=self.status_active).exists())
        # NEGATIVE: person_rapier_marshal cannot approve adult_armored
        result, message = approve_authorization(request)
        self.assertFalse(result)
        self.assertEqual(message,
                         'You must be a regional marshal in this discipline to approve this authorization.')

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_sm_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # POSITIVE: person_marshal can approve adult_armored
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message,
                         'Armored Senior Marshal authorization approved!')
        # POSITIVE: adult_armored becomes active
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=style_sm_armored,
                                                     status=self.status_active).exists())
        # POSITIVE: adult_armored expiration is four years from now.
        self.assertEqual(Authorization.objects.get(person=self.person_adult, style=style_sm_armored).expiration,
                         date.today() + relativedelta(years=4))
        # POSITIVE: adult_junior_armored is deleted
        self.assertFalse(Authorization.objects.filter(person=self.person_adult, style=style_jm_armored,
                                                     status=self.status_active).exists())

    def test_regional_armored_can_approve_missile_marshal(self):
        discipline_armored = Discipline.objects.create(name='Armored')
        style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_armored)

        discipline_missile = Discipline.objects.create(name='Missile')
        style_sm_missile = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_missile)

        auth_marshal_sm_armored = Authorization.objects.create(person=self.person_marshal,
                                                               style=style_sm_armored,
                                                               status=self.status_active,
                                                               expiration=date.today() + relativedelta(years=1))
        auth_other_marshal_sm_missile = Authorization.objects.create(person=self.person_other_marshal,
                                                                     style=style_sm_missile,
                                                                     status=self.status_active,
                                                                     expiration=date.today() + relativedelta(
                                                                         years=1))
        branch_armored_marshal = BranchMarshal.objects.create(person=self.person_marshal, branch=self.branch_an_tir,
                                                              discipline=discipline_armored,
                                                              start_date=date.today() - relativedelta(
                                                                  years=1),
                                                              end_date=date.today() + relativedelta(years=1))
        auth_adult_sm_missile = Authorization.objects.create(person=self.person_adult,
                                                                     style=style_sm_missile,
                                                                     status=self.status_regional,
                                                                     expiration=date.today() + relativedelta(
                                                                         years=1), marshal=self.person_other_marshal)

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_sm_missile.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # POSITIVE: regional armored marshal can approve missile marshal
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message,
                         'Missile Senior Marshal authorization approved!')


    def test_must_(self):
        discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal',
                                                                discipline=discipline_youth_armored)
        auth_marshal_sm_youth_armored = Authorization.objects.create(person=self.person_marshal,
                                                                         style=style_sm_youth_armored,
                                                                         status=self.status_active,
                                                                         expiration=date.today() + relativedelta(
                                                                             years=1))
        auth_other_marshal_sm_youth_armored = Authorization.objects.create(person=self.person_other_marshal,
                                                                               style=style_sm_youth_armored,
                                                                               status=self.status_active,
                                                                               expiration=date.today() + relativedelta(
                                                                                   years=1))
        auth_adult_sm_youth_armored = Authorization.objects.create(person=self.person_adult,
                                                                           style=style_sm_youth_armored,
                                                                           status=self.status_regional,
                                                                           expiration=date.today() + relativedelta(
                                                                               years=1), marshal=self.person_other_marshal)
        branch_youth_armored_marshal = BranchMarshal.objects.create(person=self.person_marshal, branch=self.branch_an_tir,
                                                              discipline=discipline_youth_armored,
                                                              start_date=date.today() - relativedelta(
                                                                  years=1),
                                                              end_date=date.today() + relativedelta(years=1))

        # Prepare request
        post_data = {'authorization_id': str(auth_adult_sm_youth_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # POSITIVE: adult_youth_marshal becomes needs kingdom approval
        result, message = approve_authorization(request)
        self.assertTrue(result)
        self.assertEqual(message,
                         'Youth Armored Senior Marshal authorization ready for kingdom to confirm background check!')
        self.assertTrue(Authorization.objects.filter(person=self.person_adult, style=style_sm_youth_armored,
                                                     status=self.status_kingdom).exists())


class ApprovalTestRule6(TestCase):
    """
    Rule 6: If the authorization is out for kingdom approval, you need to be a kingdom authorization officer to approve it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.region_an_tir = Region.objects.create(name='An Tir')
        cls.branch_tp = Branch.objects.create(name='Barony of Terra Pomaria', region=cls.region_an_tir)
        cls.branch_an_tir = Branch.objects.create(name='An Tir', region=cls.region_an_tir)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.user_marshal = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.person_marshal = Person.objects.create(user=cls.user_marshal, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_tp,
                                                   is_minor=False)

        cls.user_other_marshal = User.objects.create_user(username='martindavis@gmail.com', password='eGqNMC2D',
                                                          membership='31913',
                                                          membership_expiration=date.today() + relativedelta(years=1))
        cls.person_other_marshal = Person.objects.create(user=cls.user_other_marshal, sca_name='Cedric Diggory',
                                                         branch=cls.branch_tp,
                                                         is_minor=False)

        cls.user_adult = User.objects.create_user(username='sandersduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907107',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.person_adult = Person.objects.create(user=cls.user_adult, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_tp,
                                                 is_minor=False)

        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_armored)

        cls.auth_marshal_sm_youth_armored = Authorization.objects.create(person=cls.person_marshal,
                                                                     style=cls.style_sm_youth_armored,
                                                                     status=cls.status_active,
                                                                     expiration=date.today() + relativedelta(
                                                                         years=1))
        cls.auth_other_marshal_sm_youth_armored = Authorization.objects.create(person=cls.person_other_marshal,
                                                                           style=cls.style_sm_youth_armored,
                                                                           status=cls.status_active,
                                                                           expiration=date.today() + relativedelta(
                                                                               years=1))
        cls.auth_adult_sm_youth_armored = Authorization.objects.create(person=cls.person_adult,
                                                                   style=cls.style_sm_youth_armored,
                                                                   status=cls.status_kingdom,
                                                                   expiration=date.today() + relativedelta(
                                                                       years=1), marshal=cls.person_other_marshal)
        cls.branch_youth_armored_marshal = BranchMarshal.objects.create(person=cls.person_marshal,
                                                                    branch=cls.branch_an_tir,
                                                                    discipline=cls.discipline_youth_armored,
                                                                    start_date=date.today() - relativedelta(
                                                                        years=1),
                                                                    end_date=date.today() + relativedelta(years=1))

    def setUp(self):
        self.factory = RequestFactory()

    def test_case(self):
        # Prepare request
        post_data = {'authorization_id': str(self.auth_adult_sm_youth_armored.id)}
        request = self.factory.post('/dummy-url/', data=post_data)
        request.user = self.user_marshal

        # Do the test
        # NEGATIVE: person_marshal cannot approve person_adult due to needs kingdom approval.
        result, message = approve_authorization(request)
        self.assertFalse(result)
        self.assertEqual(message,
                         'You must be the kingdom authorization officer to approve this authorization.')

