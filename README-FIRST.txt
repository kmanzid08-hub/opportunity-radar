FILES TO REPLACE
- app/database.py
- app/main.py
- render.yaml

FILES TO ADD
- app/scheduled_discovery.py
- .github/workflows/daily-tender-scan.yml
- .github/workflows/website-discovery.yml

After copying these files into the repository, run:

git add .
git commit -m "Add free production automation"
git push

Then deploy the Render Blueprint. After Render creates the PostgreSQL database,
copy its EXTERNAL database URL and add it in GitHub as a repository Actions
secret named DATABASE_URL.

Also add BRAVE_API_KEY as a GitHub Actions secret if source discovery uses it.
