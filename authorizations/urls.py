from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('login', views.login_view, name='login'),
    path('logout', views.logout_view, name='logout'),
    path('recover_account', views.recover_account, name='recover_account'),
    path('password_reset', views.password_reset, name='password_reset'),
    path('search', views.search, name='search'),
    path('fighter/<int:person_id>', views.fighter, name='fighter'),
    path('new_fighter', views.add_fighter, name='add_fighter'),
    path('new_fighter/<int:person_id>', views.add_fighter, name='add_authorization'),
    path('sign_waiver/<int:user_id>', views.sign_waiver, name='sign_waiver'),
    path('api/styles/<int:discipline_id>/', views.get_weapon_styles, name='get_weapon_styles'),
    path('password_reset/<int:user_id>', views.password_reset, name='password_reset'),
    path('user_account/<int:user_id>', views.user_account, name='user_account'),
    path('manage_sanctions', views.manage_sanctions, name='manage_sanctions'),
    path('issue_sanctions/<int:person_id>', views.issue_sanctions, name='issue_sanctions'),
    path('branch_marshals', views.branch_marshals, name='branch_marshals')
]