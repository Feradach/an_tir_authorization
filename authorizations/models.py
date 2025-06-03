from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

BRANCH_TYPE_CHOICES = [
    ('Kingdom', 'Kingdom'),
    ('Principality', 'Principality'),
    ('Region', 'Region'),
    ('Barony', 'Barony'),
    ('Province', 'Province'),
    ('Shire', 'Shire'),
    ('Riding', 'Riding'),
    ('Canton', 'Canton'),
    ('College', 'College'),
    ('Stronghold', 'Stronghold'),
    ('Port', 'Port'),
    ('Incipient Shire', 'Incipient Shire'),
    ('Other', 'Other'),
]

TITLE_RANK_CHOICES = [
    ('Ducal', 'Ducal'),
    ('County', 'County'),
    ('Viscounty', 'Viscounty'),
    ('Peerage', 'Peerage'),
    ('Baronial', 'Baronial'),
    ('Grant of Arms', 'Grant of Arms'),
    ('Award of Arms', 'Award of Arms'),
    ('Non-Armigerous', 'Non-Armigerous'),
]

# Create your models here.
class User(AbstractUser):
    """User model. It is extended to include the membership information and their address."""
    membership = models.IntegerField(null=True, blank=True, unique=True)
    membership_expiration = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255,null=True, blank=True)
    address2 = models.CharField(max_length=255,null=True, blank=True)
    city = models.CharField(max_length=100,null=True, blank=True)
    state_province = models.CharField(max_length=100,null=True, blank=True)
    postal_code = models.CharField(null=True, blank=True, max_length=10,
        validators=[
            RegexValidator(
                regex=r'"""(\d{5}(-\d{4})?|[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d)$',
                message='Enter a valid postal code (e.g., 12345, 12345-6789, or A1A 1A1).'
            )
        ],
        help_text='Enter a valid postal code: 12345, 12345-6789, or A1A 1A1.'
    )
    country = models.CharField(max_length=100,null=True, blank=True)
    phone_number = models.CharField(max_length=20,null=True, blank=True)
    birthday = models.DateField(null=True, blank=True)
    has_logged_in = models.BooleanField(default=False)
    waiver_expiration = models.DateField(null=True, blank=True)
    background_check_expiration = models.DateField(null=True, blank=True)
    pass

    def save(self, *args, **kwargs):
        # Automatically set sca_name to user first name if not provided
        if not self.membership or not self.membership_expiration:
            self.membership = None
            self.membership_expiration = None

        super().save(*args, **kwargs)

class BranchManager(models.Manager):
    def regions(self):
        """Return all region branches (Kingdom, Principality, Region)"""
        return self.filter(type__in=['Kingdom', 'Principality', 'Region'])

    def non_regions(self):
        """Return all non-region branches"""
        return self.exclude(type__in=['Kingdom', 'Principality', 'Region'])

    def get_all_sub_branches(self, region):
        """Get all branches that are under the given region"""
        if region.type in ['Kingdom', 'Principality', 'Region']:
            return region.sub_branches.all()
        return self.none()

class Branch(models.Model):
    """This is the inidividual branch of An Tir the person is in.
    Include all regions as branches so that we can assign them regional marshals."""
    objects = BranchManager()
    name = models.CharField(max_length=150)
    region = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='sub_branches')
    type = models.CharField(max_length=50, choices=BRANCH_TYPE_CHOICES, default='Other')

    def __str__(self):
        return self.name

    def is_region(self):
        """Return True if this branch is a region (Kingdom, Principality, or Region type)"""
        return self.type in ['Kingdom', 'Principality', 'Region']

    def get_all_sub_branches(self):
        """Get all branches that are under this region"""
        return Branch.objects.get_all_sub_branches(self)

    def __str__(self):
        return self.name

    def is_region(self):
        """Return True if this branch is a region (Kingdom, Principality, or Region type)"""
        return self.type in ['Kingdom', 'Principality', 'Region']

    def get_all_sub_branches(self):
        """Get all branches that are under this region"""
        if self.is_region():
            return self.sub_branches.all()
        return Branch.objects.none()

    class Meta:
        verbose_name = 'branch'
        verbose_name_plural = 'branches'


class Discipline(models.Model):
    """These are the combat disciplines. Include marshal authorization officer and earl marshal."""
    name = models.CharField(max_length=150)

    def __str__(self):
        return self.name


class WeaponStyle(models.Model):
    """These are the weapon styles inside a discipline. Include marshal authorization officer and earl marshal."""
    name = models.CharField(max_length=150)
    discipline = models.ForeignKey(Discipline, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'weapon style'
        verbose_name_plural = 'weapon styles'


class AuthorizationStatus(models.Model):
    """Will track the status of their authorization."""
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'authorization status'
        verbose_name_plural = 'authorization statuses'


class Title(models.Model):
    """Titles that people can have. Each title has a rank in the SCA."""
    name = models.CharField(max_length=50)
    rank = models.CharField(max_length=50, choices=TITLE_RANK_CHOICES, default='Non-Armigerous')

    def __str__(self):
        return self.name

class Person(models.Model):
    """This is the public information about a person. It is attached to the user and the authorization."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    sca_name = models.CharField(max_length=255, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True)
    title = models.ForeignKey(Title, on_delete=models.SET_NULL, null=True, blank=True)
    is_minor = models.BooleanField(default=False)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    comment = models.TextField(null=True, blank=True)

    def id(self):
        return self.user_id

    def __str__(self):
        return self.sca_name

    @property
    def minor_status(self):
        return 'Yes' if self.is_minor else 'No'

    class Meta:
        verbose_name = 'person'
        verbose_name_plural = 'people'

    def save(self, *args, **kwargs):
        # Automatically set sca_name to user first name if not provided
        if not self.sca_name:
            self.sca_name = self.user.first_name
        if not self.is_minor:
            self.parent = None

        self.full_clean()
        super().save(*args, **kwargs)

    def is_parent(self):
        return self.children.exists()

    def clean(self):
        if self.is_minor and not self.user.birthday:
            raise ValidationError('A birthday must be provided for minors.')
        super().clean()



class Authorization(models.Model):
    """These are the authorizations. They are the primary entity that the system manages."""
    person = models.ForeignKey(Person, on_delete=models.CASCADE)
    style = models.ForeignKey(WeaponStyle, on_delete=models.SET_NULL, null=True)
    status = models.ForeignKey(AuthorizationStatus, on_delete=models.SET_NULL, null=True, default=1)
    marshal = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, related_name='marshal')
    expiration = models.DateField()

    def __str__(self):
        return self.person.sca_name + ': ' + self.style.discipline.name + ' ' + self.style.name + ' authorization'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['person', 'style'], name='unique_person_style')
        ]


class BranchMarshal(models.Model):
    """These are the branch marshals. All branch marshals will get elevated privileges in the system."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True)
    discipline = models.ForeignKey(Discipline, on_delete=models.SET_NULL, null=True)
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return self.person.sca_name + ': ' + self.branch.name + ' ' + self.discipline.name + ' marshal officer'

    class Meta:
        verbose_name = 'branch marshal'
        verbose_name_plural = 'branch marshals'

