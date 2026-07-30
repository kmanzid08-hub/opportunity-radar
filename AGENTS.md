# Coding Agent Instructions

These instructions apply to all coding agents working in this repository.

1. Work only on the current development branch. Never switch to, modify, merge into, or push directly to `main` unless the user explicitly instructs it.
2. Before making changes, run `git status` and confirm that the working tree is clean. If it is not clean, stop and report the existing changes.
3. Do not deploy to Render or any other production service.
4. Do not access, alter, migrate, reset, seed, or delete data in the production PostgreSQL database.
5. Do not modify production secrets, environment variables, API keys, GitHub secrets, or Render settings.
6. Do not run destructive commands such as `git reset --hard`, `git clean -fd`, force push, database drop, truncate, or destructive migrations.
7. Use small, reviewable changes. Do not rewrite unrelated parts of the application.
8. Preserve the current FastAPI application and its working functionality.
9. Before coding, inspect the relevant files and explain the planned changes.
10. After coding, run appropriate tests or validation commands.
11. Report all changed files, commands run, test results, warnings, and remaining risks.
12. Do not commit or push unless the user explicitly approves it.
13. Any database schema change must use a migration and must first be tested locally or against a separate staging database.
14. Maintain compatibility with both local SQLite and production PostgreSQL unless a task explicitly changes that requirement.
15. Never hardcode organization-specific, country-specific, secret, or production values.
16. New features should support the long-term goal of a secure multi-organization, multi-country SaaS platform.
17. Do not make any other file changes for this task.
