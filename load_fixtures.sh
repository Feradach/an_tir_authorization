FIXTURE_DIR="authorizations/fixtures"

# Loop over every .json file in the fixture directory
for file in "$FIXTURE_DIR"/*.json; do
    echo "Loading fixture: $file"
    python manage.py loaddata "$file"
done

echo "All fixtures have been loaded."