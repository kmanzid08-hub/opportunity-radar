# Opportunity Radar deployment

## 1. GitHub

Create an empty private GitHub repository, then run from this project folder:

```bash
git init
git add .
git commit -m "Prepare Opportunity Radar for production"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/opportunity-radar.git
git push -u origin main
```

Never commit `.env`, API keys, or a local SQLite database.

## 2. Render

1. In Render, choose **New > Blueprint**.
2. Connect the GitHub repository.
3. Select `render.yaml` and apply the Blueprint.
4. Enter `BRAVE_API_KEY` when requested.
5. Wait for the database, web service, and two cron jobs to deploy.
6. Open the generated `onrender.com` URL and test the dashboard.

The daily tender scan runs at 04:00 UTC (06:00 Rwanda time). The source-discovery cron starts at 04:15 UTC each day, but PostgreSQL allows the real discovery to run only once every five days.

## 3. Vercel

This application currently renders its HTML from FastAPI, so Render hosts the actual application. Vercel can provide the public-facing domain by proxying requests to Render.

1. Copy the Render web-service URL.
2. Replace `REPLACE-WITH-YOUR-RENDER-URL` in `vercel.json`.
3. Commit and push the change.
4. Import the same GitHub repository into Vercel.
5. Deploy with the repository root as the root directory.

For a future separate React/Next.js frontend, deploy that frontend directly to Vercel and keep the FastAPI API and scheduled jobs on Render.

## 4. Local test

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
set ENABLE_INTERNAL_SCHEDULER=false
uvicorn app.main:app --reload
```

On macOS/Linux, use `source venv/bin/activate` and `export` instead of `set`.
