import os
import csv

import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'An_Tir_Authorization.settings')
django.setup()

from authorizations.models import User, Region, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, Authorization, BranchMarshal
# Define paths to CSV files
csv_folder = 'data/'
csv_files = {
    'user': os.path.join(csv_folder, 'user.csv'),
    'region': os.path.join(csv_folder, 'region.csv'),
    'branch': os.path.join(csv_folder, 'branch.csv'),
    'discipline': os.path.join(csv_folder, 'discipline.csv'),
    'weapon_style': os.path.join(csv_folder, 'weapon_style.csv'),
    'authorization_status': os.path.join(csv_folder, 'authorization_status.csv'),
    'person': os.path.join(csv_folder, 'person.csv'),
    'authorization': os.path.join(csv_folder, 'authorization.csv'),
    'branch_marshal': os.path.join(csv_folder, 'branch_marshal.csv'),
}

def import_user(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            User.objects.create(
                id=row['id'],
                username=row['username'],
                first_name=row['first_name'],
                last_name=row['last_name'],
                email=row['email'],
                password=row['password'],
                membership=row['membership'],
                membership_expiration=row['membership_expiration'],
                address=row['address'],
                address2=row['address2'],
                city=row['city'],
                state_province=row['state_province'],
                postal_code=row['postal_code'],
                country=row['country'],
                has_logged_in=row['has_logged_in'],
            )
    print('Users imported successfully.')

    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['birthday']:
                user = User.objects.get(id=row['id'])
                user.birthday = row['birthday']
                user.save()
    print('Birthdays added successfully.')



def import_region(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            Region.objects.create(
                id=row['id'],
                name=row['name'],
            )
    print('Regions imported successfully.')


def import_branch(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            region = Region.objects.get(id=row['region_id'])
            Branch.objects.create(
                id=row['id'],
                name=row['name'],
                region_id=row['region_id'],
            )
    print('Branches imported successfully.')


def import_discipline(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            Discipline.objects.create(
                id=row['id'],
                name=row['name'],
            )
    print('Disciplines imported successfully.')


def import_weapon_style(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            discipline = Discipline.objects.get(id=row['discipline_id'])
            WeaponStyle.objects.create(
                id=row['id'],
                name=row['name'],
                discipline_id=row['discipline_id'],
            )
    print('Weapon Styles imported successfully.')


def import_authorization_status(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            AuthorizationStatus.objects.create(
                id=row['id'],
                name=row['name'],
            )
    print('Authorization Statuses imported successfully.')


def import_person(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            user = User.objects.get(id=row['user_id'])
            branch = Branch.objects.get(id=row['branch_id'])
            Person.objects.create(
                user_id=row['user_id'],
                sca_name=row['sca_name'],
                branch_id=row['branch_id'],
                is_minor=row['is_minor'],
            )
    print('People imported successfully.')

    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['parent_id']:
                person = Person.objects.get(user_id=row['user_id'])
                parent = Person.objects.get(user_id=row['parent_id'])
                person.parent = parent
                person.save()
    print('Parents imported successfully.')


def import_authorization(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            person = Person.objects.get(user_id=row['person_id'])
            style = WeaponStyle.objects.get(id=row['style_id'])
            status = AuthorizationStatus.objects.get(id=row['status_id'])
            marshal = Person.objects.get(user_id=row['marshal_id'])
            Authorization.objects.create(
                id=row['id'],
                person_id=row['person_id'],
                style_id=row['style_id'],
                status_id=row['status_id'],
                marshal_id=row['marshal_id'],
                expiration=row['expiration'],
            )
    print('Authorizations imported successfully.')


def import_branch_marshal(file_path):
    with open(file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            branch = Branch.objects.get(id=row['branch_id'])
            person = Person.objects.get(user_id=row['person_id'])
            discipline = Discipline.objects.get(id=row['discipline_id'])
            BranchMarshal.objects.create(
                id=row['id'],
                branch_id=row['branch_id'],
                person_id=row['person_id'],
                discipline_id=row['discipline_id'],
                start_date=row['start_date'],
                end_date=row['end_date'],
            )
    print('Branch Marshals imported successfully.')


def clear_data():
    # Delete all data from related tables
    print('Clearing existing data...')
    User.objects.all().delete()
    Region.objects.all().delete()
    Branch.objects.all().delete()
    Discipline.objects.all().delete()
    WeaponStyle.objects.all().delete()
    AuthorizationStatus.objects.all().delete()
    Person.objects.all().delete()
    Authorization.objects.all().delete()
    BranchMarshal.objects.all().delete()
    print('Data cleared successfully.')

# Main function to run imports
def run_imports():
    clear_data()
    import_user(csv_files['user'])
    import_region(csv_files['region'])
    import_branch(csv_files['branch'])
    import_discipline(csv_files['discipline'])
    import_weapon_style(csv_files['weapon_style'])
    import_authorization_status(csv_files['authorization_status'])
    import_person(csv_files['person'])
    import_authorization(csv_files['authorization'])
    import_branch_marshal(csv_files['branch_marshal'])

if __name__ == '__main__':
    run_imports()
