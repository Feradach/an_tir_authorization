from django.contrib import admin
from .models import (
    User,
    Branch,
    Discipline,
    WeaponStyle,
    AuthorizationStatus,
    Person,
    Authorization,
    BranchMarshal,
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