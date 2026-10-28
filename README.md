# Organisation defaults

Default issue forms (Task, Bug) and the pull request template for every repository in this organisation that has no templates of its own. This repository is public because GitHub reads organisation defaults only from a public `.github` repository, so it must never hold client names or private information.

How issues and boards work: see the internal standard in `dankaro-k16/docs/ISSUES.md`.

## Supabase grants check

`.github/workflows/supabase-grants.yml` is a reusable workflow. It fails when a migration in `supabase/migrations` creates a table or view in `public` without granting it to `anon`, `authenticated` or `service_role` in the same file, or uses `alter default privileges` to grant on tables. Each Supabase repository calls it from its own `.github/workflows/supabase-grants.yml`. The script is a copy of `scripts/supabase_grants_guard.py` in claude-team; keep both identical. Background: Dankaro-projects/claude-team#21.
