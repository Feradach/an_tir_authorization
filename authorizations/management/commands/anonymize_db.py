from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from authorizations.models import User, Person

try:
    from faker import Faker  # type: ignore
except Exception:  # pragma: no cover
    Faker = None  # lazy fallback if not installed

import random
import re


def _format_phone(digits: str) -> str:
    d = re.sub(r"\D", "", digits)
    if len(d) < 10:
        d = (d + ("0" * 10))[:10]
    return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"


def _an_tir_postal(rand: random.Random) -> str:
    # Generate a postal/ZIP code that fits the app's validation rules
    # Allowed: starts with 'V' (Canada) or US ZIPs starting with 97/98/991-994/838/835
    choice = rand.choice(["CA_V", "US_97", "US_98", "US_991_994", "US_838", "US_835"])
    if choice == "CA_V":
        # Simple Canadian-like format: V1A 1A1 (not fully realistic but passes rule: starts with V)
        return f"V{rand.randint(10,99)}{rand.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')} {rand.randint(1,9)}{rand.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{rand.randint(1,9)}"
    if choice == "US_97":
        return f"97{rand.randint(000,999):03d}"
    if choice == "US_98":
        return f"98{rand.randint(000,999):03d}"
    if choice == "US_991_994":
        return f"{rand.randint(991,994)}{rand.randint(0,9)}"
    if choice == "US_838":
        return f"838{rand.randint(0,9)}{rand.randint(0,9)}"
    if choice == "US_835":
        return f"835{rand.randint(0,9)}{rand.randint(0,9)}"
    return "97000"


class Command(BaseCommand):
    help = "Anonymize sensitive fields in the database for safe sharing (deterministic)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist changes (otherwise dry-run)")
        parser.add_argument("--limit", type=int, default=None, help="Limit number of users processed (for testing)")
        parser.add_argument("--seed", type=int, default=42, help="Global seed for determinism")
        parser.add_argument(
            "--fake-names",
            action="store_true",
            help="Also pseudonymize real and SCA names (we will coordinate rules before enabling)",
        )
        parser.add_argument(
            "--email-domain",
            default="example.org",
            help="Domain for anonymized emails (use example.org/.com/.net/.test)",
        )
        parser.add_argument(
            "--sca-locales",
            default="fr_FR,de_DE,it_IT,es_ES,is_IS,sv_SE,pl_PL,cs_CZ,nl_NL,da_DK,fi_FI,ga_IE",
            help="Comma-separated Faker locale codes to rotate for SCA names",
        )
        parser.add_argument(
            "--sca-locative-rate",
            type=float,
            default=0.3,
            help="Probability to append a light locative (e.g., 'of {Branch}')",
        )
        parser.add_argument(
            "--shift-expirations",
            action="store_true",
            help="Randomly shift authorization expiration dates per redaction policy",
        )
        parser.add_argument(
            "--fake-memberships",
            action="store_true",
            help="Assign fake membership numbers/expirations to users who appear as authorizing marshals",
        )
        parser.add_argument(
            "--randomize-branches",
            action="store_true",
            help="Assign each person to a deterministic random branch id in [9,86] (one-time redaction)",
        )
        parser.add_argument(
            "--clear-comments",
            action="store_true",
            help="Clear all User.comment values to remove production notes",
        )

    def handle(self, *args, **opts):
        apply = opts["apply"]
        limit = opts["limit"]
        seed = int(opts["seed"])
        fake_names = opts["fake_names"]
        email_domain = opts["email_domain"].strip()
        sca_locales = [s.strip() for s in opts.get("sca_locales", "").split(",") if s.strip()]
        loc_rate = float(opts.get("sca_locative_rate", 0.3))
        shift_expirations = bool(opts.get("shift_expirations", False))
        fake_memberships = bool(opts.get("fake_memberships", False))
        randomize_branches = bool(opts.get("randomize_branches", False))
        clear_comments = bool(opts.get("clear_comments", False))

        if fake_names and Faker is None:
            self.stderr.write(self.style.WARNING("Faker is not installed. Install 'Faker' to enable name anonymization."))

        faker_en = Faker("en_US") if Faker else None
        if faker_en:
            faker_en.seed_instance(seed)
        faker_by_locale = {}

        # Probe locales and keep only those available in this Faker build
        available_locales = []
        skipped_locales = []
        if Faker and sca_locales:
            for loc in sca_locales:
                try:
                    _ = Faker(loc)
                    available_locales.append(loc)
                except Exception:
                    skipped_locales.append(loc)
            if skipped_locales:
                self.stderr.write(self.style.WARNING(
                    f"Skipping unavailable Faker locales: {', '.join(skipped_locales)}"
                ))

        qs = User.objects.all().order_by("id")
        if limit:
            qs = qs[:limit]

        updated_users = 0
        updated_auths = 0
        updated_memberships = 0
        updated_branches = 0
        cleared_comments = 0
        now = timezone.now()

        @transaction.atomic
        def _apply():
            nonlocal updated_users, updated_auths, updated_memberships, updated_branches, cleared_comments
            # Optionally clear all user comments up-front
            if clear_comments:
                from django.db.models import Q
                cleared_comments = User.objects.exclude(Q(comment__isnull=True) | Q(comment__exact="")).update(comment="")
            from authorizations.models import Authorization, Person  # local import to avoid circulars
            # Build marshal user id set (Authorization.marshal points to Person pk == user_id)
            marshal_user_ids = set()
            if fake_memberships:
                marshal_user_ids = set(
                    Authorization.objects.filter(marshal__isnull=False)
                    .values_list("marshal_id", flat=True)
                    .distinct()
                )

            # Track used membership numbers to avoid collisions
            used_memberships = set(
                User.objects.exclude(membership__isnull=True).values_list("membership", flat=True)
            )

            for user in qs:
                # Deterministic per-user RNG
                r = random.Random(seed + user.id)

                # Email and username must be unique
                new_email = f"user{user.id}@{email_domain}"
                new_username = f"testuser_{user.id}"

                # Phone
                phone_digits = "".join(str(r.randint(0, 9)) for _ in range(10))
                new_phone = _format_phone(phone_digits)

                # Address
                if faker_en:
                    new_address = faker_en.street_address()
                    new_address2 = ""
                    new_city = faker_en.city()
                else:
                    new_address = f"{r.randint(10,9999)} Test Ave"
                    new_address2 = ""
                    new_city = "Testville"

                # State/province & country – keep original if present; otherwise set to US
                state_province = user.state_province or "Oregon"
                country = user.country or "United States"

                # Postal code constrained to app rules
                postal = _an_tir_postal(r)

                # Membership – either null or deterministic placeholder
                new_membership = None
                new_membership_exp = None

                # Names – modern real names and distinct SCA names from rotated locales
                if fake_names:
                    # Real names (modern)
                    if faker_en:
                        user.first_name = faker_en.first_name()
                        user.last_name = faker_en.last_name()
                    else:
                        user.first_name = f"Test{user.id}"
                        user.last_name = "User"
                    # SCA names
                    try:
                        person = user.person
                        sca = f"{user.first_name} {user.last_name}"
                        if available_locales and Faker:
                            loc_idx = user.id % len(available_locales)
                            locale = available_locales[loc_idx]
                            if locale not in faker_by_locale:
                                faker_by_locale[locale] = Faker(locale)
                            f_loc = faker_by_locale[locale]
                            f_loc.seed_instance(seed + user.id)
                            given = getattr(f_loc, "first_name", faker_en.first_name if faker_en else (lambda: "A"))()
                            surname = getattr(f_loc, "last_name", faker_en.last_name if faker_en else (lambda: "Person"))()
                            sca = f"{given} {surname}"
                        # optional locative
                        if random.Random(seed + user.id + 12345).random() < loc_rate and getattr(person, "branch", None):
                            try:
                                sca = f"{sca} of {person.branch.name}"
                            except Exception:
                                pass
                        # Bypass model clean() to avoid unrelated validation errors (e.g., minor without birthday)
                        Person.objects.filter(pk=user.id).update(sca_name=sca)
                    except Person.DoesNotExist:
                        pass

                # Apply standard field anonymization (safe to run regardless of name plan)
                user.email = new_email
                user.username = new_username
                user.address = new_address
                user.address2 = new_address2
                user.city = new_city
                user.state_province = state_province
                user.postal_code = postal
                user.country = country
                user.phone_number = new_phone
                user.membership = new_membership
                user.membership_expiration = new_membership_exp
                user.save(
                    update_fields=[
                        "first_name",
                        "last_name",
                        "email",
                        "username",
                        "address",
                        "address2",
                        "city",
                        "state_province",
                        "postal_code",
                        "country",
                        "phone_number",
                        "membership",
                        "membership_expiration",
                        "comment",
                    ]
                )

                # Assign fake membership data to users who serve as authorizing marshals
                if fake_memberships and user.id in marshal_user_ids:
                    from datetime import date, timedelta
                    # Deterministic generator for membership fields
                    r_mem = random.Random(seed + user.id * 123457)
                    # 8-digit membership number
                    mem = r_mem.randint(10_000_000, 99_999_999)
                    # Ensure uniqueness among currently used numbers
                    while mem in used_memberships:
                        mem = (mem + 1) % 100_000_000
                        if mem < 10_000_000:
                            mem = 10_000_000
                    used_memberships.add(mem)
                    # Expiration between 2026-01-01 and 2030-12-31
                    start = date(2026, 1, 1)
                    end = date(2030, 12, 31)
                    delta = (end - start).days
                    mem_exp = start + timedelta(days=r_mem.randint(0, delta))
                    User.objects.filter(pk=user.id).update(membership=mem, membership_expiration=mem_exp)
                    updated_memberships += 1
                updated_users += 1

            # Optionally shift authorization expiration dates
            if shift_expirations:
                from authorizations.models import Authorization  # local import to avoid circulars at import time
                from datetime import date, timedelta
                cutoff = date(2025, 11, 1)
                for auth in Authorization.objects.all().only("id", "expiration"):
                    if not auth.expiration:
                        continue
                    r = random.Random(seed + auth.id * 9973)
                    delta_days = r.randint(10, 1000)
                    if auth.expiration >= cutoff:
                        new_exp = auth.expiration + timedelta(days=delta_days)
                    else:
                        new_exp = auth.expiration - timedelta(days=delta_days)
                    Authorization.objects.filter(pk=auth.id).update(expiration=new_exp)
                    nonlocal updated_auths
                    updated_auths += 1

            # Optionally randomize Person.branch_id across [9,86]
            if randomize_branches:
                for p in Person.objects.all().only("user_id", "branch_id"):
                    r = random.Random(seed + p.user_id * 7919)
                    new_branch = r.randint(9, 86)
                    Person.objects.filter(pk=p.user_id).update(branch_id=new_branch)
                    nonlocal updated_branches
                    updated_branches += 1

        if apply:
            _apply()
            self.stdout.write(self.style.SUCCESS(f"Anonymized users: {updated_users}"))
            if shift_expirations:
                self.stdout.write(self.style.SUCCESS(f"Shifted authorization expirations: {updated_auths}"))
            if fake_memberships:
                self.stdout.write(self.style.SUCCESS(f"Assigned fake memberships (marshals): {updated_memberships}"))
            if randomize_branches:
                self.stdout.write(self.style.SUCCESS(f"Randomized person branches: {updated_branches}"))
            if clear_comments:
                self.stdout.write(self.style.SUCCESS(f"Cleared user comments: {cleared_comments}"))
        else:
            # Dry run preview
            preview = min(limit or 5, 5)
            sample = list(User.objects.all().order_by("id")[:preview])
            for u in sample:
                r = random.Random(seed + u.id)
                if Faker:
                    f_en = Faker("en_US"); f_en.seed_instance(seed + u.id)
                    first = f_en.first_name(); last = f_en.last_name()
                else:
                    first, last = f"Test{u.id}", "User"
                sca_name = f"{first} {last}"
                if available_locales and Faker:
                    loc = available_locales[u.id % len(available_locales)]
                    f_loc = Faker(loc); f_loc.seed_instance(seed + u.id)
                    sca_name = f"{f_loc.first_name()} {f_loc.last_name()}"
                example = {
                    "id": u.id,
                    "email": f"user{u.id}@{email_domain}",
                    "username": f"testuser_{u.id}",
                    "real_name": f"{first} {last}",
                    "sca_name": sca_name,
                    "phone": _format_phone("".join(str(r.randint(0, 9)) for _ in range(10))),
                    "postal_code": _an_tir_postal(r),
                }
                self.stdout.write(f"Preview user {u.id}: {example}")
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to persist. Use --fake-names to generate names. Add --shift-expirations to randomize authorization expiration dates."))
