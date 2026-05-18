from datetime import date

from dateutil.relativedelta import relativedelta
from django.db import migrations


YOUTH_DISCIPLINES = ['Youth Armored', 'Youth Rapier']
YOUTH_CATEGORIES = {
    'Lion': (6, 9, 10),
    'Gryphon': (10, 13, 14),
    'Dragon': (14, 17, 18),
}
MARSHAL_STYLES = {'Junior Marshal', 'Senior Marshal'}


def calculate_age(birthday, today):
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))


def category_for_birthday(birthday, today):
    if not birthday:
        return None
    age = calculate_age(birthday, today)
    for category, (minimum, maximum, _age_out) in YOUTH_CATEGORIES.items():
        if minimum <= age <= maximum:
            return category
    return None


def category_age_out_date(birthday, category):
    if not birthday or category not in YOUTH_CATEGORIES:
        return None
    return birthday + relativedelta(years=YOUTH_CATEGORIES[category][2])


def is_prefixed_youth_style(style_name):
    return any(style_name.startswith(f'{category} - ') for category in YOUTH_CATEGORIES)


def backfill_remaining_youth_category_styles(apps, schema_editor):
    Discipline = apps.get_model('authorizations', 'Discipline')
    WeaponStyle = apps.get_model('authorizations', 'WeaponStyle')
    Authorization = apps.get_model('authorizations', 'Authorization')
    AuthorizationNote = apps.get_model('authorizations', 'AuthorizationNote')
    LegacyAuthorizationRecoveryEntry = apps.get_model('authorizations', 'LegacyAuthorizationRecoveryEntry')

    today = date.today()
    missing_birthday_rows = []

    for discipline in Discipline.objects.filter(name__in=YOUTH_DISCIPLINES):
        base_styles = [
            style for style in WeaponStyle.objects.filter(discipline=discipline).order_by('id')
            if style.name not in MARSHAL_STYLES and not is_prefixed_youth_style(style.name)
        ]

        for base_style in base_styles:
            for category in YOUTH_CATEGORIES:
                WeaponStyle.objects.get_or_create(
                    discipline=discipline,
                    name=f'{category} - {base_style.name}',
                )

        for authorization in Authorization.objects.select_related('person__user', 'status', 'style').filter(style__in=base_styles):
            birthday = authorization.person.user.birthday
            category = category_for_birthday(birthday, today)
            if not category:
                if (
                    not birthday
                    and authorization.status
                    and authorization.status.name == 'Active'
                    and authorization.expiration
                    and authorization.expiration >= today
                ):
                    missing_birthday_rows.append(
                        (
                            authorization.id,
                            authorization.person.user_id,
                            authorization.person.sca_name,
                            discipline.name,
                            authorization.style.name,
                        )
                    )
                continue

            target_style = WeaponStyle.objects.filter(
                discipline=discipline,
                name=f'{category} - {authorization.style.name}',
            ).order_by('id').first()
            if not target_style:
                continue

            age_out_date = category_age_out_date(birthday, category)
            if age_out_date and authorization.expiration and authorization.expiration > age_out_date:
                authorization.expiration = age_out_date

            existing = Authorization.objects.filter(
                person=authorization.person,
                style=target_style,
            ).exclude(pk=authorization.pk).order_by('id').first()
            if existing:
                if authorization.expiration and (
                    not existing.expiration or authorization.expiration > existing.expiration
                ):
                    existing.expiration = authorization.expiration
                existing.status = authorization.status or existing.status
                existing.marshal = authorization.marshal or existing.marshal
                existing.concurring_fighter = authorization.concurring_fighter or existing.concurring_fighter
                existing.updated_by = authorization.updated_by or existing.updated_by
                existing.save()
                AuthorizationNote.objects.filter(authorization=authorization).update(authorization=existing)
                LegacyAuthorizationRecoveryEntry.objects.filter(authorization=authorization).update(authorization=existing)
                authorization.delete()
            else:
                authorization.style = target_style
                authorization.save()

    if missing_birthday_rows:
        print(
            'Active youth combat authorizations with missing birthdays remain on legacy styles:'
        )
        for auth_id, person_id, sca_name, discipline_name, style_name in missing_birthday_rows:
            print(
                f'  authorization_id={auth_id} person_id={person_id} '
                f'sca_name="{sca_name}" style="{discipline_name} - {style_name}"'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0026_youth_age_category_weapon_styles'),
    ]

    operations = [
        migrations.RunPython(backfill_remaining_youth_category_styles, migrations.RunPython.noop),
    ]
