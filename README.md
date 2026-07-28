# Running Tracker

Aplikacja do synchronizacji i analizy treningów biegowych z Garmin Connect (dane z zegarka,
`python-garminconnect`). Delta sync do lokalnej bazy SQLite, agregaty po dowolnych zakresach
dat, wykres progresji tempo→tętno, krocząca objętość tygodniowa (reguła +10%/tydz.), eksport
przetworzonego JSON-a do wklejenia w rozmowę z Claude po interpretację.

## Structure

```
running/
├── backend/
│   ├── main.py           # FastAPI routing + serwowanie frontendu
│   ├── db.py              # SQLAlchemy engine/session
│   ├── models.py           # activities / laps / hr_zones / daily_wellness / sync_state
│   ├── garmin_sync.py       # login + delta sync z Garmin Connect
│   ├── aggregates.py        # agregaty kalendarzowe, rolling volume, pace↔HR progression
│   ├── export.py            # kompaktowy JSON export
│   ├── scheduler.py         # opcjonalny periodyczny sync w tle (APScheduler)
│   ├── dev_run.py           # lokalny dev server (uvicorn, port 8712)
│   ├── requirements.txt
│   └── Dockerfile           # build context = repo root (kopiuje też frontend/)
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── vendor/chart.umd.min.js   # Chart.js zvendorowany (bez CDN)
└── data/                    # lokalna baza SQLite (dev, gitignored)
```

Frontend jest wypalony w obraz Dockera (nie montowany jako wolumen) — żeby zaktualizować UI,
trzeba przebudować i wypchnąć nowy obraz.

## Konfiguracja sync

`SYNC_START_DATE` (opcjonalne, `YYYY-MM-DD`) w `stack.env` — sync ignoruje aktywności i dane
wellness sprzed tej daty (np. data rozpoczęcia poważnego treningu), więc stare pojedyncze
aktywności z zegarka sprzed lat nie są ściągane. Bez tej zmiennej ściągana jest cała historia.

## Autoryzacja Garmin

`GARMIN_EMAIL` / `GARMIN_PASSWORD` w `stack.env`. Biblioteka cache'uje token sesji na dysku
(`GARMIN_TOKEN_DIR`, montowany razem z bazą) — po pierwszym udanym logowaniu kolejne restarty
kontenera nie wymagają ponownego logowania.

**Jeśli na koncie Garmin włączone jest MFA**, logowanie headless w kontenerze się nie powiedzie
przy pierwszym uruchomieniu. Trzeba wtedy zalogować się raz lokalnie (interaktywny `prompt_mfa`),
żeby wygenerować token, i skopiować katalog tokenu do wolumenu NAS-a przed startem kontenera.

## Local dev

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python dev_run.py
```

Żeby przetestować sync z prawdziwym kontem, ustaw `GARMIN_EMAIL`/`GARMIN_PASSWORD` we własnej
sesji powłoki (nie w kodzie) przed uruchomieniem `dev_run.py`.

## Docker build

Budować z **root repo** jako kontekstu (Dockerfile leży w `backend/`, ale kopiuje też `frontend/`):

```
docker build -f backend/Dockerfile -t bednar1988/running:0.1.0 .
docker push bednar1988/running:0.1.0
```

## NAS deploy

Compose w `nas/running/docker-compose.yml`, port `8128`, dane w `/volume1/docker/running/data`.
