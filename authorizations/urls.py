from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('login', views.login_view, name='login'),
    path('logout', views.logout_view, name='logout'),
    path('register', views.register, name='register'),
    path('recover_account', views.recover_account, name='recover_account'),
    path('password_reset', views.password_reset, name='password_reset'),
    path('password_reset/<uidb64>/<token>', views.password_reset_token, name='password_reset_token'),
    path('search', views.search, name='search'),
    path('fighter/<int:person_id>', views.fighter, name='fighter'),
    path('sign_waiver/<int:user_id>', views.sign_waiver, name='sign_waiver'),
    path('api/styles/<int:discipline_id>/', views.get_weapon_styles, name='get_weapon_styles'),
    path('api/validate_authorization/', views.validate_authorization_rules, name='validate_authorization_rules'),
    path('api/validate_authorization_action/', views.validate_authorization_action, name='validate_authorization_action'),
    path('api/validate_sanction_action/', views.validate_sanction_action, name='validate_sanction_action'),
    path('password_reset/<int:user_id>', views.password_reset, name='password_reset'),
    path('user_account/<int:user_id>', views.user_account, name='user_account'),
    path('manage_sanctions', views.manage_sanctions, name='manage_sanctions'),
    path('issue_sanctions/<int:person_id>', views.issue_sanctions, name='issue_sanctions'),
    path('branch_marshals', views.branch_marshals, name='branch_marshals')
]
