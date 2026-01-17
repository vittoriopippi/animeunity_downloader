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
4. **Access the Web Interface**: Go to `http://your-server-ip:8000`.

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
