# AnimeDownloader Dockerized

A Django-based anime downloader for AnimeUnity, designed to be easily deployed via Docker.

## Features
- **Search**: Find any anime on AnimeUnity.
- **Queue Management**: Collapsible episode list with progress tracking.
- **Real-time Updates**: Status updates without page refreshes.
- **Reliable Networking**: PostgreSQL + Redis stack for high stability.
- **Multi-container Architecture**: Web, Worker (Celery), Redis, and database separated for performance.

## Fast Deployment (OMV / Docker Compose)

1. **Copy the `docker-compose.yml`** to your server.
2. **Adjust the Volume Paths**: Change the path `/app/media` to point to your storage disk (e.g., `/srv/dev-disk-by-uuid-.../Anime`).
3. **Run the stack**:
   ```bash
   docker-compose up -d
   ```
4. **Access the Web Interface**: Go to `http://your-server-ip:8083`.

## Automated Setup
The first time you start the stack, it will:
- Automatically run database migrations.
- Create a default superuser:
  - **Username**: `admin`
  - **Password**: `admin`

## Development
To run locally:
1. Clone the repo.
2. Run `docker-compose up --build`.

## Configuration
Check `docker-compose.yml` for environment variables like `DB_PASSWORD` or `DJANGO_SUPERUSER_PASSWORD` if you want to customize them.

If AnimeUnity moves to a new domain, set `ANIMEUNITY_BASE_URL` (default: `https://www.animeunity.co`) on the `web`, `worker` and `beat` services. A redirect from the old domain to the new one is followed by itself.

Since AnimeUnity's November 2026 redesign, one show holds all its language versions. Search lists each version separately: the Italian dub is titled "... (ITA)", as before, and each library entry remembers its language.

## Logs
Messages go to the container log (`docker logs anime_web`, `docker logs anime_worker`). Problems are logged with level `ERROR`, e.g. `[2026-11-18 01:00:00] ERROR Could not search AnimeUnity for 'naruto': ...`: a failed search, a failed download, or a show that couldn't be checked for new episodes.
