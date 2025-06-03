from datetime import date
from django.contrib import messages
from django.contrib.messages.storage.cookie import CookieStorage
from dateutil.relativedelta import relativedelta
from django.core.exceptions import PermissionDenied
from django.test import TestCase, RequestFactory
from django.urls import reverse

from authorizations.models import User, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, Authorization, BranchMarshal
from authorizations.permissions import is_kingdom_authorization_officer
from authorizations.views import add_authorization, appoint_branch_marshal

"""
Tests:
Index:
Which pending authorizations are returned based on the user role?

fighter:
Can view a fighter
only see current authorizations
see current and old pending authorizations
see current and old need regional approval
see current and old need kingdom approval
see current and old sanctions

add_fighter:
Can add a fighter that follows the rules
- Add maximum user
- Add minimum user
- Minor must have a birthday
- Membership number must be unique
- Membership expiration and number must be both present or both absent
- User creation rejected if authorization violates rules
- User creation approved if authorization follows rules

add_authorization:
- marshal authorizations are set to pending
- new authorization can be added if it follows the rules
- existing authorization can be updated if it follows the rules

my_account:
- Can see self
- Can't see others
- Can see children
- auth officer can see all
- Can update user information

branch_marshal:
- Can add a branch marshal
- Cannot add a junior marshal as a regional marshal
- Can add a junior marshal as a local branch marshal
- Cannot add someone who doesn't have a marshal in the same discipline
- Cannot add someone who is already a branch marshal
"""
# Create your tests here.
class IndexViewTest(TestCase):
    """
    Test the Index page
    Which pending authorizations are returned based on the user role?
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.branch_sm = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.branch_tr = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.branch_sm)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.branch_tr)
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.discipline_archery = Discipline.objects.create(name='Archery')
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)
        cls.style_sm_archery = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_archery)
        cls.style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_equestrian)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D', membership='68907108', membership_expiration=date.today() + relativedelta(years=1))
        cls.north_user = User.objects.create_user(username='northernduane@hotmail.com', password='eGqNMC2D', membership='68907106', membership_expiration=date.today() + relativedelta(years=1))
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar', branch=cls.branch_gd, is_minor=False)
        cls.north_person = Person.objects.create(user=cls.north_user, sca_name='Francis du Pont', branch=cls.branch_lg, is_minor=False)

        cls.marshal_user = User.objects.create_user(username='targetmarshal@gmail.com', password='eGqNMC2D', membership='68920107', membership_expiration=date.today() + relativedelta(years=1))
        cls.marshal_person = Person.objects.create(user=cls.marshal_user, sca_name='Theodric of the White Hart', branch=cls.branch_gd, is_minor=False)
        cls.auth_sm_armored_target = Authorization.objects.create(person=cls.marshal_person,
                                                              style=cls.style_sm_armored,
                                                              status=cls.status_active,
                                                              expiration=date.today() + relativedelta(years=1))


    def test_anonymous_view(self):

        # POSITIVE: See all people
        # NEGATIVE: Don't see authorizations
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertIn('all_people', response.context)
        self.assertNotIn('pending_authorizations', response.context)


    def test_marshal_view(self):
        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                             status=self.status_pending,
                                                             expiration=date.today() + relativedelta(years=1))

        # NEGATIVE: Don't see authorizations
        self.client.login(username='targetmarshal@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertNotIn(auth_sm_armored_south, response.context['pending_authorizations'])


    def test_branch_marshal_view(self):
        branch_marshal_gd = BranchMarshal.objects.create(person=self.marshal_person, branch=self.branch_gd, discipline=self.discipline_armored, start_date=date.today() - relativedelta(years=1), end_date=date.today() + relativedelta(years=1))

        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                       status=self.status_pending,
                                                       expiration=date.today() + relativedelta(years=1))
        auth_sm_rapier_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_rapier,
                                                      status=self.status_pending,
                                                      expiration=date.today() + relativedelta(years=1))
        auth_sm_armored_north = Authorization.objects.create(person=self.north_person, style=self.style_sm_armored,
                                                       status=self.status_pending,
                                                       expiration=date.today() + relativedelta(years=1))

        user_frank = User.objects.create_user(username='frankduane@hotmail.com', password='eGqNMC2D', membership='68908809', membership_expiration=date.today() + relativedelta(years=1))
        person_frank = Person.objects.create(user=user_frank, sca_name='Francis du Pont', branch=self.branch_gd, is_minor=False)

        auth_sm_armored_frank = Authorization.objects.create(person=person_frank, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))

        user_claudia = User.objects.create_user(username='claudia@hotmail.com', password='eGqNMC2D',
                                                membership='68544809',
                                                membership_expiration=date.today() + relativedelta(years=1))
        person_claudia = Person.objects.create(user=user_claudia, sca_name='Claudia', branch=self.branch_sm,
                                               is_minor=False)

        auth_sm_armored_claudia = Authorization.objects.create(person=person_claudia, style=self.style_sm_armored,
                                                               status=self.status_kingdom,
                                                               expiration=date.today() + relativedelta(years=1))

        self.client.login(username='targetmarshal@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')

        # POSITIVE: See pending in your branch
        self.assertIn(auth_sm_armored_south, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending in other branches
        self.assertNotIn(auth_sm_armored_north, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending in other disciplines
        self.assertNotIn(auth_sm_rapier_south, response.context['pending_authorizations'])

        # NEGATIVE: Don't see regional approvals
        self.assertNotIn(auth_sm_armored_frank, response.context['pending_authorizations'])

        # NEGATIVE: Don't see kingdom approvals
        self.assertNotIn(auth_sm_armored_claudia, response.context['pending_authorizations'])



    def test_regional_marshal_view(self):
        branch_mh = Branch.objects.create(name='Shire of MyrtleHolt', region=self.region_summits)

        branch_marshal_sm = BranchMarshal.objects.create(person=self.marshal_person, branch=self.branch_sm, discipline=self.discipline_armored, start_date=date.today() - relativedelta(years=1), end_date=date.today() + relativedelta(years=1))


        user_sara = User.objects.create_user(username='sara@hotmail.com', password='eGqNMC2D', membership='68908809', membership_expiration=date.today() + relativedelta(years=1))
        person_sara = Person.objects.create(user=user_sara, sca_name='Sara', branch=branch_mh, is_minor=False)
        auth_sm_armored_sara = Authorization.objects.create(person=person_sara, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))
        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))
        auth_sm_rapier_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_rapier,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))

        auth_sm_armored_north = Authorization.objects.create(person=self.north_person, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))

        user_frank = User.objects.create_user(username='frankduane@hotmail.com', password='eGqNMC2D',
                                              membership='6890912121',
                                              membership_expiration=date.today() + relativedelta(years=1))
        person_frank = Person.objects.create(user=user_frank, sca_name='Francis du Pont', branch=self.branch_gd,
                                             is_minor=False)

        auth_sm_armored_frank = Authorization.objects.create(person=person_frank, style=self.style_sm_armored,
                                                             status=self.status_pending,
                                                             expiration=date.today() + relativedelta(years=1))

        user_claudia = User.objects.create_user(username='claudia@hotmail.com', password='eGqNMC2D',
                                              membership='68544809',
                                              membership_expiration=date.today() + relativedelta(years=1))
        person_claudia = Person.objects.create(user=user_claudia, sca_name='Claudia', branch=self.branch_sm, is_minor=False)

        auth_sm_armored_claudia = Authorization.objects.create(person=person_claudia, style=self.style_sm_armored,
                                                             status=self.status_kingdom,
                                                             expiration=date.today() + relativedelta(years=1))


        # POSITIVE: See regional approvals in your region.
        # NEGATIVE: Don't see pending or kingdom approvals.
        # NEGATIVE: Don't see regional outside your region.
        self.client.login(username='targetmarshal@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')

        # POSITIVE: See pending in your region
        self.assertIn(auth_sm_armored_south, response.context['pending_authorizations'])
        self.assertIn(auth_sm_armored_sara, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending in other disciplines
        self.assertNotIn(auth_sm_rapier_south, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending in other region
        self.assertNotIn(auth_sm_armored_north, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending approvals
        self.assertNotIn(auth_sm_armored_frank, response.context['pending_authorizations'])

        # NEGATIVE: Don't see kingdom approvals
        self.assertNotIn(auth_sm_armored_claudia, response.context['pending_authorizations'])

    def test_kingdom_marshal_view(self):
        branch_mh = Branch.objects.create(name='Shire of MyrtleHolt', type='Shire', region=self.branch_sm)

        branch_marshal_an_tir = BranchMarshal.objects.create(person=self.marshal_person, branch=self.branch_an_tir,
                                                         discipline=self.discipline_armored,
                                                         start_date=date.today() - relativedelta(years=1),
                                                         end_date=date.today() + relativedelta(years=1))



        user_sara = User.objects.create_user(username='sara@hotmail.com', password='eGqNMC2D', membership='68908809',
                                             membership_expiration=date.today() + relativedelta(years=1))
        person_sara = Person.objects.create(user=user_sara, sca_name='Sara', branch=branch_mh, is_minor=False)
        auth_sm_armored_sara = Authorization.objects.create(person=person_sara, style=self.style_sm_armored,
                                                            status=self.status_regional,
                                                            expiration=date.today() + relativedelta(years=1))
        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))

        auth_sm_armored_north = Authorization.objects.create(person=self.north_person, style=self.style_sm_armored,
                                                             status=self.status_regional,
                                                             expiration=date.today() + relativedelta(years=1))

        user_frank = User.objects.create_user(username='frankduane@hotmail.com', password='eGqNMC2D',
                                              membership='6890912121',
                                              membership_expiration=date.today() + relativedelta(years=1))
        person_frank = Person.objects.create(user=user_frank, sca_name='Francis du Pont', branch=self.branch_gd,
                                             is_minor=False)

        auth_sm_armored_frank = Authorization.objects.create(person=person_frank, style=self.style_sm_armored,
                                                             status=self.status_pending,
                                                             expiration=date.today() + relativedelta(years=1))

        user_claudia = User.objects.create_user(username='claudia@hotmail.com', password='eGqNMC2D',
                                                membership='68544809',
                                                membership_expiration=date.today() + relativedelta(years=1))
        person_claudia = Person.objects.create(user=user_claudia, sca_name='Claudia', branch=self.branch_sm,
                                               is_minor=False)

        auth_sm_armored_claudia = Authorization.objects.create(person=person_claudia, style=self.style_sm_armored,
                                                               status=self.status_kingdom,
                                                               expiration=date.today() + relativedelta(years=1))

        auth_sm_rapier_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_rapier,
                                                            status=self.status_regional,
                                                            expiration=date.today() + relativedelta(years=1))

        # POSITIVE: See regional approvals in your discipline.
        # NEGATIVE: Don't see pending or kingdom approvals
        self.client.login(username='targetmarshal@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')

        # POSITIVE: See pending in your discipline
        self.assertIn(auth_sm_armored_south, response.context['pending_authorizations'])
        self.assertIn(auth_sm_armored_sara, response.context['pending_authorizations'])
        self.assertIn(auth_sm_armored_north, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending in other disciplines
        self.assertNotIn(auth_sm_rapier_south, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending approvals
        self.assertNotIn(auth_sm_armored_frank, response.context['pending_authorizations'])

        # NEGATIVE: Don't see kingdom approvals
        self.assertNotIn(auth_sm_armored_claudia, response.context['pending_authorizations'])

    def test_auth_officer_view(self):
        branch_mh = Branch.objects.create(name='Shire of MyrtleHolt', type='Shire', region=self.branch_sm)
        discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')

        branch_marshal_an_tir = BranchMarshal.objects.create(person=self.marshal_person, branch=self.branch_an_tir,
                                                             discipline=discipline_auth_officer,
                                                             start_date=date.today() - relativedelta(years=1),
                                                             end_date=date.today() + relativedelta(years=1))

        user_sara = User.objects.create_user(username='sara@hotmail.com', password='eGqNMC2D', membership='68908809',
                                             membership_expiration=date.today() + relativedelta(years=1))
        person_sara = Person.objects.create(user=user_sara, sca_name='Sara', branch=branch_mh, is_minor=False)
        auth_sm_armored_sara = Authorization.objects.create(person=person_sara, style=self.style_sm_armored,
                                                            status=self.status_kingdom,
                                                            expiration=date.today() + relativedelta(years=1))
        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                             status=self.status_kingdom,
                                                             expiration=date.today() + relativedelta(years=1))

        auth_sm_armored_north = Authorization.objects.create(person=self.north_person, style=self.style_sm_armored,
                                                             status=self.status_kingdom,
                                                             expiration=date.today() + relativedelta(years=1))

        user_frank = User.objects.create_user(username='frankduane@hotmail.com', password='eGqNMC2D',
                                              membership='6890912121',
                                              membership_expiration=date.today() + relativedelta(years=1))
        person_frank = Person.objects.create(user=user_frank, sca_name='Francis du Pont', branch=self.branch_gd,
                                             is_minor=False)

        auth_sm_armored_frank = Authorization.objects.create(person=person_frank, style=self.style_sm_armored,
                                                             status=self.status_pending,
                                                             expiration=date.today() + relativedelta(years=1))

        user_claudia = User.objects.create_user(username='claudia@hotmail.com', password='eGqNMC2D',
                                                membership='68544809',
                                                membership_expiration=date.today() + relativedelta(years=1))
        person_claudia = Person.objects.create(user=user_claudia, sca_name='Claudia', branch=self.branch_sm,
                                               is_minor=False)

        auth_sm_armored_claudia = Authorization.objects.create(person=person_claudia, style=self.style_sm_armored,
                                                               status=self.status_regional,
                                                               expiration=date.today() + relativedelta(years=1))

        auth_sm_rapier_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_rapier,
                                                            status=self.status_kingdom,
                                                            expiration=date.today() + relativedelta(years=1))


        # POSITIVE: See kingdom approvals
        # NEGATIVE: Don't see pending or regional approvals
        self.assertTrue(is_kingdom_authorization_officer(self.marshal_person.user))



        self.client.login(username='targetmarshal@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')

        # POSITIVE: See kingdom approvals from all disciplines
        self.assertIn(auth_sm_armored_south, response.context['pending_authorizations'])
        self.assertIn(auth_sm_armored_sara, response.context['pending_authorizations'])
        self.assertIn(auth_sm_rapier_south, response.context['pending_authorizations'])
        self.assertIn(auth_sm_armored_north, response.context['pending_authorizations'])

        # NEGATIVE: Don't see pending approvals
        self.assertNotIn(auth_sm_armored_frank, response.context['pending_authorizations'])

        # NEGATIVE: Don't see regional approvals
        self.assertNotIn(auth_sm_armored_claudia, response.context['pending_authorizations'])


class FighterViewTest(TestCase):
    """
    Test the Fighter page
    Can view a fighter
    only see current authorizations
    see current and old pending authorizations
    see current and old need regional approval
    see current and old need kingdom approval
    see current and old sanctions
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier')
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_shield_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_two_armored = WeaponStyle.objects.create(name='Two-Handed', discipline=cls.discipline_armored)
        cls.style_spear_armored = WeaponStyle.objects.create(name='Spear', discipline=cls.discipline_armored)
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_offensive_rapier = WeaponStyle.objects.create(name='Sword & Offensive Secondary', discipline=cls.discipline_rapier)
        cls.style_defensive_rapier = WeaponStyle.objects.create(name='Sword & Defensive Secondary', discipline=cls.discipline_rapier)
        cls.style_two_rapier = WeaponStyle.objects.create(name='Two-Handed', discipline=cls.discipline_rapier)
        cls.style_spear_rapier = WeaponStyle.objects.create(name='Spear', discipline=cls.discipline_rapier)
        cls.style_sm_equestrian = WeaponStyle.objects.create(name='Senior Marshal',
                                                             discipline=cls.discipline_equestrian)
        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907108',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.north_user = User.objects.create_user(username='northernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907106',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_gd, is_minor=False)
        cls.north_person = Person.objects.create(user=cls.north_user, sca_name='Francis du Pont', branch=cls.branch_lg,
                                                 is_minor=False)

        # current auths
        cls.auth_sm_armored_south = Authorization.objects.create(person=cls.south_person, style=cls.style_sm_armored,
                                                             status=cls.status_active,
                                                             expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)
        cls.auth_shield_armored_south = Authorization.objects.create(person=cls.south_person, style=cls.style_shield_armored,
                                                                 status=cls.status_active,
                                                                 expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)

        #expired auths
        cls.auth_two_armored_south = Authorization.objects.create(person=cls.south_person, style=cls.style_two_armored,
                                                             status=cls.status_active,
                                                             expiration=date.today() - relativedelta(years=1), marshal=cls.north_person)


        # pending auths
        cls.auth_spear_armored_south = Authorization.objects.create(person=cls.south_person, style=cls.style_spear_armored,
                                                                status=cls.status_pending,
                                                                expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)
        cls.auth_sm_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_sm_rapier,
                                                            status=cls.status_pending,
                                                            expiration=date.today() - relativedelta(years=1), marshal=cls.north_person)


        # regional auths
        cls.auth_single_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_single_rapier,
                                                                status=cls.status_regional,
                                                                expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)
        cls.auth_offensive_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_offensive_rapier,
                                                                  status=cls.status_regional,
                                                                  expiration=date.today() - relativedelta(years=1), marshal=cls.north_person)


        # kingdom auths
        cls.auth_defensive_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_defensive_rapier,
                                                                   status=cls.status_kingdom,
                                                                   expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)
        cls.auth_two_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_two_rapier,
                                                            status=cls.status_kingdom,
                                                            expiration=date.today() - relativedelta(years=1), marshal=cls.north_person)


        # revoked auths
        cls.auth_spear_rapier_south = Authorization.objects.create(person=cls.south_person, style=cls.style_spear_rapier,
                                                               status=cls.status_revoked,
                                                               expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)
        cls.auth_sm_equestrian_south = Authorization.objects.create(person=cls.south_person, style=cls.style_sm_equestrian,
                                                                status=cls.status_revoked,
                                                                expiration=date.today() - relativedelta(years=1), marshal=cls.north_person)

        # other person auths
        cls.auth_sm_armored_north = Authorization.objects.create(person=cls.north_person, style=cls.style_sm_equestrian,
                                                             status=cls.status_active,
                                                             expiration=date.today() + relativedelta(years=1), marshal=cls.north_person)


    def test_view_fighter_page(self):
        fighter_id = self.south_person.user_id
        response = self.client.get(reverse('fighter', args=[fighter_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/fighter.html')

        active_armored_styles = response.context['authorization_list'].get('Armored', {}).get('styles', [])
        pending_armored_styles = response.context['pending_authorization_list'].get('Armored', {}).get('styles', [])
        pending_rapier_styles = response.context['pending_authorization_list'].get('Rapier', {}).get('styles', [])
        active_equestrian_styles = response.context['authorization_list'].get('Equestrian', {}).get('styles', [])
        revoked_rapier_styles = response.context['sanctions'].get('Rapier', {}).get('styles', [])
        revoked_equestrian_styles = response.context['sanctions'].get('Equestrian', {}).get('styles', [])

        # current auths
        self.assertIn('Senior Marshal', active_armored_styles)
        self.assertIn('Weapon & Shield', active_armored_styles)

        # expired auths
        self.assertNotIn('Two-Handed', active_armored_styles)

        # pending auths
        self.assertIn('Spear', pending_armored_styles)
        self.assertIn('Senior Marshal', pending_rapier_styles)


        # regional auths
        self.assertIn('Single Sword', pending_rapier_styles)
        self.assertIn('Sword & Offensive Secondary', pending_rapier_styles)

        # kingdom auths
        self.assertIn('Sword & Defensive Secondary', pending_rapier_styles)
        self.assertIn('Two-Handed', pending_rapier_styles)

        # revoked auths
        self.assertIn('Spear', revoked_rapier_styles)
        self.assertIn('Senior Marshal', revoked_equestrian_styles)

        # other person auths
        self.assertNotIn('Senior Marshal', active_equestrian_styles)

class AddFighterViewTest(TestCase):
    """
    Test the Add Fighter page
    Can add a fighter that follows the rules
        - Add maximum user
        - Add minimum user
        - Minor must have a birthday
        - Membership number must be unique
        - Membership expiration and number must be both present or both absent
        - User creation rejected if authorization violates rules
        - User creation approved if authorization follows rules
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_shield_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)

        cls.marshal_user = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                            membership='31913662',
                                            membership_expiration=date.today() + relativedelta(years=1))
        cls.marshal_person = Person.objects.create(user=cls.marshal_user, sca_name='Theodric of the White Hart',
                                           branch=cls.branch_gd,
                                           is_minor=False)

        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907108',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_gd, is_minor=False)

        cls.auth_sm_armored_south = Authorization.objects.create(person=cls.marshal_person, style=cls.style_sm_armored,
                                                             status=cls.status_active,
                                                             expiration=date.today() + relativedelta(years=1), marshal=cls.south_person)

    def test_view_add_fighter_page(self):
        # NEGATIVE: Non-marshal cannot view
        self.client.login(username='southernduane@hotmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('add_fighter'))
        self.assertEqual(response.status_code, 403)

        # POSITIVE: Marshal can view
        self.client.login(username='kristinadavis@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('add_fighter'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/new_fighter.html')

        # POST data
        post_data = {
            'email': 'newfighter@example.com',
            'username': 'newfighter',
            'password': '654654ERERE',
            'first_name': 'John',
            'last_name': 'Doe',
            'membership': '123456',
            'membership_expiration': '2100-01-01',
            'address': '123 Main St',
            'city': 'Testville',
            'state_province': 'Oregon',
            'postal_code': '12345',
            'country': 'United States',
            'birthday': '2000-01-01',
            'sca_name': 'Fighter Test',
            'branch': self.branch_gd.id,
            'is_minor': False,
            'weapon_styles': [self.style_shield_armored.id],
        }

        # Send the POST request
        response = self.client.post(reverse('add_fighter'), post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # POSITIVE: User, person, and authorization were created
        self.assertTrue(User.objects.filter(username='newfighter').exists())
        self.assertTrue(Person.objects.filter(sca_name='Fighter Test').exists())
        self.assertTrue(
            Authorization.objects.filter(person__sca_name='Fighter Test', style=self.style_shield_armored).exists())
        self.assertContains(response, 'User and person created successfully')

        # NEGATIVE: Authorization breaks rules, no user created
        post_data = {
            'email': 'badfighter@example.com',
            'username': 'badfighter',
            'password': '654654ERERE',
            'first_name': 'Jane',
            'last_name': 'Doe',
            'address': '123 Main St',
            'city': 'Testville',
            'state_province': 'Oregon',
            'postal_code': '12345',
            'country': 'United States',
            'branch': self.branch_gd.id,
            'is_minor': False,
            'weapon_styles': [self.style_sm_armored.id],
        }

        response = self.client.post(reverse('add_fighter'), post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Error during creation: Must be a current member to be authorized as a marshal.')
        self.assertFalse(User.objects.filter(username='badfighter').exists())


class AddAuthorizationViewTest(TestCase):
    """
    Test the Add Authorization page
    - marshal authorizations are set to pending
    - new authorization can be added if it follows the rules
    - existing authorization can be updated if it follows the rules
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Pending')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_regional = AuthorizationStatus.objects.create(name='Needs Regional Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Needs Kingdom Approval')

        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_shield_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)

        cls.marshal_user = User.objects.create_user(username='targetmarshal@gmail.com', password='eGqNMC2D',
                                                    membership='68920107',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.marshal_person = Person.objects.create(user=cls.marshal_user, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_gd, is_minor=False)

        cls.auth_sm_armored_marshal = Authorization.objects.create(person=cls.marshal_person, style=cls.style_sm_armored,
                                                                   status=cls.status_active,
                                                                   expiration=date.today() + relativedelta(years=1),
                                                                   marshal=cls.marshal_person)

        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D')
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_gd, is_minor=False)

    def test_add_authorization(self):
        factory = RequestFactory()

        # NEGATIVE: Non-marshal cannot view
        # POST data:
        post_data = {'weapon_styles': [self.style_shield_armored.id, self.style_sm_armored.id]}
        request = factory.post('/fake-path/', post_data)
        request.user = self.south_user
        setattr(request, '_messages', CookieStorage(request))
        with self.assertRaises(PermissionDenied):
            add_authorization(request, person_id=self.marshal_person.user_id)


        # POSITIVE: Marshal can view
        request.user = self.marshal_user
        setattr(request, '_messages', CookieStorage(request))
        response = add_authorization(request, person_id=self.south_person.user_id)

        # NEGATIVE: Authorization breaks rules, no user created
        self.assertFalse(response)
        message_list = [msg.message for msg in messages.get_messages(request)]
        self.assertIn('Error during creation: Must be a current member to be authorized as a marshal.', message_list)
        self.assertFalse(Authorization.objects.filter(person=self.south_person, style=self.style_shield_armored).exists())


        # POSITIVE: Authorization created
        #POST data:
        post_data = {'weapon_styles': [self.style_shield_armored.id]}
        request = factory.post('/fake-path/', post_data)
        request.user = self.marshal_user
        setattr(request, '_messages', CookieStorage(request))
        response = add_authorization(request, person_id=self.south_person.user_id)
        self.assertTrue(response)
        message_list = [msg.message for msg in messages.get_messages(request)]
        self.assertIn('Authorization for Weapon & Shield created successfully!', message_list)
        self.assertTrue(
            Authorization.objects.filter(person=self.south_person, style=self.style_shield_armored).exists())



class MyAccountViewTest(TestCase):
    """
    Test the My Account page
    - Can see self
    - Can't see others
    - Can see children
    - auth officer can see all
    - Can update user information
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.discipline_auth = Discipline.objects.create(name='Authorization Officer')

        cls.marshal_user = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.marshal_person = Person.objects.create(user=cls.marshal_user, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_gd,
                                                   is_minor=False)

        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907108',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_gd, is_minor=False)

        cls.north_user = User.objects.create_user(username='northernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907106',
                                                  membership_expiration=date.today() + relativedelta(years=1), birthday=date.today() - relativedelta(years=15))
        cls.north_person = Person.objects.create(user=cls.north_user, sca_name='Francis du Pont',
                                                 branch=cls.branch_gd, is_minor=True, parent=cls.south_person)

        cls.branch_auth_officer = BranchMarshal.objects.create(branch=cls.branch_an_tir, person=cls.marshal_person,
                                                               discipline=cls.discipline_auth,
                                                               start_date=date.today() - relativedelta(years=1),
                                                               end_date=date.today() + relativedelta(years=1))

        cls.branch_auth_officer_marshal = BranchMarshal.objects.create(person=cls.marshal_person, branch=cls.branch_an_tir,
                                                                   start_date=date.today() - relativedelta(years=1),
                                                                   end_date=date.today() + relativedelta(years=1))


    def test_view_my_account(self):
        # NEGATIVE: Anonymous can't see
        response = self.client.get(reverse('user_account', args=[self.south_user.id]))
        self.assertEqual(response.status_code, 302)

        # POSITIVE: Can see self
        self.client.login(username='southernduane@hotmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('user_account', args=[self.south_user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

        # NEGATIVE: Can't see others
        self.client.login(username='southernduane@hotmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('user_account', args=[self.marshal_user.id]))
        self.assertEqual(response.status_code, 403)

        # POSITIVE: Can see children
        self.client.login(username='southernduane@hotmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('user_account', args=[self.north_user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

        # POSITIVE: Auth Officer can see all
        self.client.login(username='kristinadavis@gmail.com', password='eGqNMC2D')
        response = self.client.get(reverse('user_account', args=[self.south_user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')


class BranchMarshalViewTest(TestCase):
    """
    Test the Appoint Branch Marshal function
    - Can add a branch marshal
    - Cannot add a junior marshal as a regional marshal
    - Can add a junior marshal as a local branch marshal
    - Cannot add someone who doesn't have a marshal in the same discipline
    - Cannot add someone who is already a branch marshal
    """

    @classmethod
    def setUpTestData(cls):
        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.branch_sm = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.branch_tr = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.branch_sm)
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.discipline_auth = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_armored = Discipline.objects.create(name='Armored')
        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)

        cls.marshal_user = User.objects.create_user(username='kristinadavis@gmail.com', password='eGqNMC2D',
                                                    membership='31913662',
                                                    membership_expiration=date.today() + relativedelta(years=1))
        cls.marshal_person = Person.objects.create(user=cls.marshal_user, sca_name='Theodric of the White Hart',
                                                   branch=cls.branch_gd,
                                                   is_minor=False)

        cls.south_user = User.objects.create_user(username='southernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907108',
                                                  membership_expiration=date.today() + relativedelta(years=1))
        cls.south_person = Person.objects.create(user=cls.south_user, sca_name='Ysabeau de la Mar',
                                                 branch=cls.branch_gd, is_minor=False)

        cls.north_user = User.objects.create_user(username='northernduane@hotmail.com', password='eGqNMC2D',
                                                  membership='68907106',
                                                  membership_expiration=date.today() + relativedelta(years=1),
                                                  birthday=date.today() - relativedelta(years=15))
        cls.north_person = Person.objects.create(user=cls.north_user, sca_name='Francis du Pont',
                                                 branch=cls.branch_gd, is_minor=True, parent=cls.south_person)

        cls.branch_auth_officer = BranchMarshal.objects.create(branch=cls.branch_an_tir, person=cls.marshal_person,
                                                               discipline=cls.discipline_auth,
                                                               start_date=date.today() - relativedelta(years=1),
                                                               end_date=date.today() + relativedelta(years=1))

        cls.branch_auth_officer_marshal = BranchMarshal.objects.create(person=cls.marshal_person,
                                                                       branch=cls.branch_an_tir,
                                                                       start_date=date.today() - relativedelta(years=1),
                                                                       end_date=date.today() + relativedelta(years=1))

    def test_view_branch_marshal(self):
        factory = RequestFactory()

        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                                     status=self.status_active,
                                                                     expiration=date.today() + relativedelta(years=1),
                                                                     marshal=self.marshal_person)

        auth_sm_armored_north = Authorization.objects.create(person=self.north_person, style=self.style_sm_armored,
                                                               status=self.status_active,
                                                               expiration=date.today() + relativedelta(years=1),
                                                               marshal=self.marshal_person)

        branch_summits_armored_marshal = BranchMarshal.objects.create(person=self.south_person,
                                                                       branch=self.branch_summits,
                                                                       discipline=self.discipline_armored,
                                                                       start_date=date.today() - relativedelta(years=1),
                                                                       end_date=date.today() + relativedelta(years=1))

        # NEGATIVE: Non-marshal auth officer cannot view
        # POST data:
        post_data = {
            'person': self.north_person.sca_name,
            'branch': self.branch_gd.name,
            'discipline': self.discipline_armored.name,
            'start_date': date.today(),
        }
        request = factory.post('/fake-path/', post_data)
        request.user = self.south_user
        result, message = appoint_branch_marshal(request)
        self.assertFalse(result)
        self.assertEqual(message, 'Only the authorization officer can appoint branch marshals.')
        self.assertFalse(BranchMarshal.objects.filter(person=self.north_person, branch=self.branch_gd).exists())

        # POSITIVE: Authorization officer can view and add a branch marshal
        request.user = self.marshal_user
        result, message = appoint_branch_marshal(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Branch marshal appointed.')
        self.assertTrue(BranchMarshal.objects.filter(person=self.north_person, branch=self.branch_gd).exists())

    def test_junior_branch_marshal(self):
        factory = RequestFactory()

        style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=self.discipline_armored)

        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=style_jm_armored,
                                                                     status=self.status_active,
                                                                     expiration=date.today() + relativedelta(years=1),
                                                                     marshal=self.marshal_person)


        # NEGATIVE: Junior marshal cannot be a regional marshal
        post_data = {
            'person': self.south_person.sca_name,
            'branch': self.branch_summits.name,
            'discipline': self.discipline_armored.name,
            'start_date': date.today(),
        }
        request = factory.post('/fake-path/', post_data)
        request.user = self.marshal_user
        result, message = appoint_branch_marshal(request)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a senior marshal to be a regional marshal.')
        self.assertFalse(BranchMarshal.objects.filter(person=self.south_person, branch=self.branch_summits).exists())

        # POSITIVE: Junior marshal can be a local branch marshal
        post_data = {
            'person': self.south_person.sca_name,
            'branch': self.branch_gd.name,
            'discipline': self.discipline_armored.name,
            'start_date': date.today(),
        }
        request = factory.post('/fake-path/', post_data)
        request.user = self.marshal_user
        result, message = appoint_branch_marshal(request)
        self.assertTrue(result)
        self.assertEqual(message, 'Branch marshal appointed.')
        self.assertTrue(BranchMarshal.objects.filter(person=self.south_person, branch=self.branch_gd).exists())

    def test_right_discipline_branch_marshal(self):
        factory = RequestFactory()

        discipline_rapier = Discipline.objects.create(name='Rapier')
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=discipline_rapier)

        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=style_sm_rapier,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1),
                                                             marshal=self.marshal_person)

        # NEGATIVE: Can't add someone who doesn't have a marshal in the same discipline
        post_data = {
            'person': self.south_person.sca_name,
            'branch': self.branch_summits.name,
            'discipline': self.discipline_armored,
            'start_date': date.today(),
        }
        request = factory.post('/fake-path/', post_data)
        request.user = self.marshal_user
        result, message = appoint_branch_marshal(request)
        self.assertFalse(result)
        self.assertEqual(message, 'Must be a marshal in the discipline to be a branch marshal.')
        self.assertFalse(BranchMarshal.objects.filter(person=self.south_person, branch=self.branch_summits).exists())

    def test_no_double_branch_marshal(self):
        factory = RequestFactory()

        auth_sm_armored_south = Authorization.objects.create(person=self.south_person, style=self.style_sm_armored,
                                                             status=self.status_active,
                                                             expiration=date.today() + relativedelta(years=1),
                                                             marshal=self.marshal_person)

        branch_summits_armored_marshal = BranchMarshal.objects.create(person=self.south_person,
                                                                       branch=self.branch_summits,
                                                                       discipline=self.discipline_armored,
                                                                       start_date=date.today() - relativedelta(years=1),
                                                                       end_date=date.today() + relativedelta(years=1))

        # NEGATIVE: Can't add someone who doesn't have a marshal in the same discipline
        post_data = {
            'person': self.south_person.sca_name,
            'branch': self.branch_gd.name,
            'discipline': self.discipline_armored,
            'start_date': date.today(),
        }
        request = factory.post('/fake-path/', post_data)
        request.user = self.marshal_user
        result, message = appoint_branch_marshal(request)
        self.assertFalse(result)
        self.assertEqual(message, 'Can only serve as one branch marshal position at a time.')
        self.assertFalse(BranchMarshal.objects.filter(person=self.south_person, branch=self.branch_gd).exists())