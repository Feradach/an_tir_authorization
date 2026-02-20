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
    BranchMarshal,
    AuthorizationNote,
)

# Register your models here.
admin.site.register(User)
admin.site.register(Branch)
admin.site.register(Discipline)
admin.site.register(WeaponStyle)
admin.site.register(AuthorizationStatus)
admin.site.register(Person)
admin.site.register(Authorization)
admin.site.register(BranchMarshal)
admin.site.register(AuthorizationNote)


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
