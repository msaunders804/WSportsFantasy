# analysis/

Notebooks and one-off work, such as TikTok pieces and exploratory stats. **Never deployed**: the Dockerfile copies only `src/`, and `.dockerignore` excludes this folder.

- Read data through `undertow.data` (`get_session`, the models in `undertow.data.models`), or load the cached feed responses in `data/replay_cache/`. Do not call HockeyTech from here; use `undertow.ingest` if you need fresh data.
- Run notebooks against the throwaway dev database, never production: `docker compose -f docker-compose.dev.yml up -d --wait`.
- Put exports in an `output/` folder and big inputs in a `data/` folder inside the piece's own subfolder. Both are gitignored.
- When something becomes reusable (a stat helper, a simulation), move it into `src/undertow/content/` with tests and import it from the notebook.
