# OnlyOffice editor — setup

The translation pane embeds the **OnlyOffice Document Server** (DS), which edits the
generated `.docx` directly. The DS owns pagination + WYSIWYG; the `.docx` on the backend
is the source of truth (the DS autosaves to it via a server-side callback).

```
browser ──(api.js, iframe)──▶ Document Server (Docker) ──(fetch docx / save callback)──▶ FastAPI backend
```

## Local development (Windows)

1. **Install Docker Desktop** (one time): https://www.docker.com/products/docker-desktop/
   or `winget install Docker.DockerDesktop`. Launch it and wait until the whale icon says
   "running".
2. **Start the Document Server**:
   ```
   docker compose up -d
   ```
   Wait ~30–60s, then confirm health: open http://localhost:8080/healthcheck → `true`.
3. **Backend env** — add to `backend/.env` (the JWT secret MUST match `docker-compose.yml`):
   ```
   ONLYOFFICE_JWT_SECRET=devsecret_change_me
   ```
   The other knobs already default correctly for local Docker Desktop:
   - `ONLYOFFICE_BACKEND_URL=http://host.docker.internal:8000` — how the DS reaches the backend
   - `ONLYOFFICE_PUBLIC_URL=http://localhost:8080` — where the browser loads the editor
   - `ONLYOFFICE_DS_INTERNAL_URL=http://localhost:8080` — where the backend pulls saved files
4. Start backend (`uvicorn main:app` on :8000) and frontend (`npm run start` on :3000) as usual.
5. Open a document → the compare page right pane shows the OnlyOffice editor.

> The browser uses localhost; the **DS container** cannot — it reaches the backend via
> `host.docker.internal`. If you change the backend port, update `ONLYOFFICE_BACKEND_URL`.

## Railway (production)

1. Add a new **service from the Docker image** `onlyoffice/documentserver` (pin a tag).
   Give it ≥ 2 GB RAM. Expose its HTTP port publicly.
2. Set on that service: `JWT_ENABLED=true`, `JWT_SECRET=<strong-secret>`, `JWT_HEADER=Authorization`.
3. Set on the **backend** service:
   - `ONLYOFFICE_JWT_SECRET=<same strong-secret>`
   - `ONLYOFFICE_PUBLIC_URL=https://<ds-public-domain>` (browser-facing)
   - `ONLYOFFICE_BACKEND_URL=http://<backend-internal-domain>:$PORT` (DS → backend, private network)
   - `ONLYOFFICE_DS_INTERNAL_URL=http://<ds-internal-domain>` (backend → DS, to pull saved files)

JWT mismatch is the most common failure (the editor shows "download failed"); make sure the
secret is identical on the DS and the backend.
