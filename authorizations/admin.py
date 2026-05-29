from django.contrib import admin
from .models import (
    ReportValue,
    ReportingPeriod,
    User,
    Branch,
    Discipline,
    WeaponStyle,
    AuthorizationStatus,
    Person,
    Authorization,
    AuthorizationAuditEntry,
    BranchMarshal,
    AuthorizationNote,
    LegacyAuthorizationRecoveryEntry,
    SupportingDocument,
    SupportingDocumentPerson,
    SupportingDocumentAuthorization,
)

admin.site.register(AuthorizationNote)
admin.site.register(LegacyAuthorizationRecoveryEntry)
admin.site.register(SupportingDocument)
admin.site.register(SupportingDocumentPerson)
admin.site.register(SupportingDocumentAuthorization)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'region')
    list_filter = ('type', 'region')
    search_fields = ('name', 'region__name')
    ordering = ('name',)


@admin.register(Discipline)
class DisciplineAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(WeaponStyle)
class WeaponStyleAdmin(admin.ModelAdmin):
    list_display = ('name', 'discipline')
    list_filter = ('discipline',)
    search_fields = ('name', 'discipline__name')
    ordering = ('discipline__name', 'name')


@admin.register(AuthorizationStatus)
class AuthorizationStatusAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        'username',
        'sca_name',
        'email',
        'first_name',
        'last_name',
        'membership',
        'membership_expiration',
        'is_staff',
        'is_superuser',
        'is_active',
    )
    list_filter = ('is_staff', 'is_superuser', 'is_active')
    search_fields = (
        'username',
        'email',
        'first_name',
        'last_name',
        'membership',
        'person__sca_name',
    )
    ordering = ('username',)

    def sca_name(self, obj):
        try:
            return obj.person.sca_name
        except Person.DoesNotExist:
            return ''

    sca_name.admin_order_field = 'person__sca_name'
    sca_name.short_description = 'SCA name'


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('sca_name', 'user', 'branch', 'minor_status')
    list_filter = ('branch',)
    search_fields = (
        'sca_name',
        'user__username',
        'user__email',
        'user__first_name',
        'user__last_name',
        'user__membership',
    )
    ordering = ('sca_name',)


@admin.register(Authorization)
class AuthorizationAdmin(admin.ModelAdmin):
    list_display = ('person', 'style', 'status', 'expiration', 'marshal')
    list_filter = ('status', 'style__discipline', 'style')
    search_fields = (
        'person__sca_name',
        'person__user__username',
        'person__user__email',
        'person__user__first_name',
        'person__user__last_name',
        'person__user__membership',
        'style__name',
        'style__discipline__name',
        'marshal__sca_name',
    )
    ordering = ('person__sca_name', 'style__discipline__name', 'style__name')
    autocomplete_fields = ('person', 'style', 'status', 'marshal', 'concurring_fighter')


@admin.register(AuthorizationAuditEntry)
class AuthorizationAuditEntryAdmin(admin.ModelAdmin):
    list_display = ('authorization', 'person', 'style', 'event_type', 'changed_by', 'changed_at', 'summary')
    list_filter = ('event_type', 'style__discipline', 'style')
    search_fields = (
        'authorization__id',
        'person__sca_name',
        'person__user__username',
        'person__user__email',
        'style__name',
        'style__discipline__name',
        'changed_by__username',
        'changed_by__email',
        'changed_by__person__sca_name',
        'summary',
    )
    readonly_fields = tuple(field.name for field in AuthorizationAuditEntry._meta.fields)
    ordering = ('-changed_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BranchMarshal)
class BranchMarshalAdmin(admin.ModelAdmin):
    list_display = ('person', 'branch', 'discipline', 'start_date', 'end_date')
    list_filter = ('branch', 'discipline')
    search_fields = (
        'person__sca_name',
        'person__user__username',
        'person__user__email',
        'person__user__first_name',
        'person__user__last_name',
        'person__user__membership',
        'branch__name',
        'discipline__name',
    )
    ordering = ('person__sca_name', 'branch__name', 'discipline__name')
    autocomplete_fields = ('person', 'branch', 'discipline')


@admin.register(ReportingPeriod)
class ReportingPeriodAdmin(admin.ModelAdmin):
    list_display = ('year', 'quarter', 'authorization_officer_name')
    search_fields = ('authorization_officer_name',)
    ordering = ('-year', '-quarter')


@admin.register(ReportValue)
class ReportValueAdmin(admin.ModelAdmin):
    list_display = (
        'reporting_period',
        'report_family',
        'region_name',
        'subject_name',
        'metric_name',
        'value',
        'display_order',
    )
    list_filter = ('report_family', 'reporting_period__year', 'reporting_period__quarter')
    search_fields = ('region_name', 'subject_name', 'metric_name')
    ordering = (
        '-reporting_period__year',
        '-reporting_period__quarter',
        'report_family',
        'region_name',
        'display_order',
    )
