# Shield Assist Preview

## How to reproduce artifacts

No build artifacts needed — the frontend runs via Vite dev server with hot reload.

## How to run the server

### Prerequisites
- Node.js and npm (for frontend)
- Python with backend dependencies installed
- `.env` file at project root (already present in main checkout)

### Steps

1. **Backend** (uvicorn on port 8000):
   ```
   python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level info
   ```
   The backend reads `.env` from the project root (loaded by `backend/app.py` via `load_dotenv()`).

2. **Frontend** (Vite dev server on port 5173):
   ```
   cd frontend && npx vite --port 5173
   ```
   The Vite config proxies `/api`, `/disputes`, `/metrics`, `/events`, `/healthz` to `http://127.0.0.1:8000`.

3. **Access**: Open `http://localhost:5173/` in a browser.

### Port assignments
- **Frontend**: 5173 (Vite dev server)
- **Backend**: 8000 (uvicorn)
- Port 3000 is occupied by another thread's preview
- Port 3001 is occupied by another thread's preview

### Windows detach recipe

**Backend:**
```powershell
powershell -NoProfile -Command "(Start-Process -FilePath 'python' -ArgumentList '-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8000','--log-level','info' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
```

**Frontend:**
```powershell
powershell -NoProfile -Command "(Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--port','5173' -WorkingDirectory '<project_root>\\frontend' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
```

**Confirm alive:**
```powershell
powershell -NoProfile -Command "Get-Process -Id <pid>"
```

### Notes
- Database: `data/shield_assist.db` (SQLite, auto-created on first run)
- Uploads: `data/uploads/` (auto-created)
- The frontend proxies API calls to the backend, so both must be running
- Port 5173 is used for frontend; port 8000 is used for backend
- The backend must be started from the project root directory for `backend.app:app` to resolve correctly
