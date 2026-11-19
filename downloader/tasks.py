from celery import shared_task
from celery.exceptions import Retry
from django.db import IntegrityError
from .models import Episode
from .utils import clean_filename, get_download_url, AnimeUnityError, DOWNLOAD_TIMEOUT
from pathlib import Path
import cloudscraper
from django.conf import settings
import logging
import random
import re

logger = logging.getLogger(__name__)

# AnimeUnity's video servers send at most 2 files at a time to one IP and answer a third
# request with "503". With 4 downloads in parallel that happens now and then, so it isn't
# an error: the episode waits about a minute and tries again with a fresh link, which
# often points to another server.
BUSY_STATUSES = (429, 503)
BUSY_RETRIES = 10
BUSY_DELAY = (45, 90)  # seconds, random so retried episodes don't all come back together

def episode_file_label(episode):
    """
    Return (season_dir, episode label) following the Jellyfin/Plex naming conventions:
    "5" -> S01E05, "1-2" -> S01E01-E02 (multi-episode file),
    anything else ("14.5", "OVA") -> a special in Season 00.
    """
    number = episode.number.strip()
    if number.isdigit():
        return "Season 01", f"S01E{int(number):02d}"

    range_match = re.fullmatch(r'(\d+)-(\d+)', number)
    if range_match:
        first, last = (int(n) for n in range_match.groups())
        return "Season 01", f"S01E{first:02d}-E{last:02d}"

    # Specials are numbered in creation order, so an index never changes once assigned
    # and a newly found special can't take the file name of one already downloaded
    special_ids = [
        ep.id for ep in episode.anime.episodes.order_by('id')
        if not ep.number.strip().isdigit() and not re.fullmatch(r'\d+-\d+', ep.number.strip())
    ]
    index = special_ids.index(episode.id) + 1
    return "Season 00", f"S00E{index:02d} - {clean_filename(number)}"

@shared_task(bind=True)
def download_episode_task(self, episode_id):
    try:
        # Atomically claim the episode so two tasks can never download it concurrently
        claimed = Episode.objects.filter(id=episode_id, status='pending').update(
            status='downloading', progress=0, error_message=None
        )
        episode = Episode.objects.get(id=episode_id)
        if not claimed:
            return f"Task {episode.status}"

        episode.anime.update_status()

        # 1. Always get a fresh video URL: the download links are signed and expire,
        # so a URL saved by a previous attempt can't be reused on retry
        try:
            video_url = get_download_url(episode.source_url)
            episode.video_url = video_url
            episode.save()
        except AnimeUnityError as e:
            logger.error("Could not download %s episode %s: %s", episode.anime.title, episode.number, e)
            episode.status = 'failed'
            episode.error_message = str(e)
            episode.save()
            episode.anime.update_status()
            return f"Failed: {e}"

        # 2. Prepare file path
        anime_title = clean_filename(episode.anime.title)
        season_dir, ep_str = episode_file_label(episode)
        filename = f"{anime_title} - {ep_str}.mp4"
        
        save_dir = Path(settings.MEDIA_ROOT) / anime_title / season_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = save_dir / filename

        # 3. Download with progress
        logger.info("Downloading %s", file_path)
        scraper = cloudscraper.create_scraper()
        # The read timeout applies per socket read, so a stalled stream fails instead of hanging the worker
        with scraper.get(video_url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            if r.status_code in BUSY_STATUSES:
                return server_busy(self, episode, r.status_code)
            r.raise_for_status()
            total_length = int(r.headers.get('content-length', 0))
            dl = 0
            
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        dl += len(chunk)
                        f.write(chunk)
                        
                        if total_length > 0:
                            progress = int(dl * 100 / total_length)
                            if progress > episode.progress + 5 or progress == 100:
                                # Refresh from DB to see if status changed (Cancel/Skip)
                                episode.refresh_from_db()
                                if episode.status in ['cancelled', 'skipped']:
                                    logger.info("Download %s for episode %s", episode.status, episode.number)
                                    f.close()
                                    if file_path.exists():
                                        file_path.unlink()
                                    episode.anime.update_status()
                                    return f"Task {episode.status}"
                                
                                episode.progress = progress
                                episode.save()
        
        rel_path = Path(anime_title) / season_dir / filename
        episode.file_path = str(Path(settings.MEDIA_URL) / rel_path).replace("\\", "/")
        episode.status = 'completed'
        episode.progress = 100
        episode.save()
        episode.anime.update_status()
        
        return f"Downloaded Episode {episode.number}"

    except Retry:
        raise
    except Exception as e:
        try:
             episode = Episode.objects.get(id=episode_id)
             logger.error("Could not download %s episode %s: %s", episode.anime.title, episode.number, e)
             episode.status = 'failed'
             episode.error_message = str(e)
             episode.save()
             episode.anime.update_status()
        except Exception:
            logger.error("Could not download episode (database id %s): %s", episode_id, e)
        return f"Failed: {e}"

def server_busy(task, episode, status_code):
    """
    The video server is busy: put the episode back in the queue for a later try
    (logged as INFO, so it raises no alert), or give up after BUSY_RETRIES tries.
    """
    tries = task.request.retries + 1
    give_up = tries > BUSY_RETRIES
    message = f"Video server busy ({status_code}) {tries} times in a row"
    # Only if it's still ours: a CANCEL or SKIP tapped meanwhile wins
    still_ours = Episode.objects.filter(pk=episode.pk, status='downloading').update(
        status='failed' if give_up else 'pending', progress=0, error_message=message if give_up else None)
    episode.anime.update_status()
    if not still_ours:
        return "Server busy; episode no longer queued"

    if not give_up:
        delay = random.randint(*BUSY_DELAY)
        logger.info("%s episode %s: video server busy, trying again in %d s (%d/%d)",
                    episode.anime.title, episode.number, delay, tries, BUSY_RETRIES)
        raise task.retry(countdown=delay, max_retries=BUSY_RETRIES)

    logger.error("Could not download %s episode %s: %s", episode.anime.title, episode.number, message)
    return f"Failed: {message}"

@shared_task
def check_for_new_episodes_task():
    from .models import Anime, Episode
    from .utils import get_anime_info

    logger.info("Checking all anime for new episodes")
    animes = Anime.objects.all()
    new_episodes_count = 0
    failed = 0

    for anime in animes:
        try:
            info = get_anime_info(anime.source_url, anime.language)
        except AnimeUnityError as e:
            logger.error("Could not check %s for new episodes: %s", anime.title, e)
            failed += 1
            continue

        # Shows got new ids in AnimeUnity's 2026-11 redesign; keep ours current
        if anime.animeunity_id != info['id']:
            anime.animeunity_id = info['id']
            try:
                anime.save(update_fields=['animeunity_id'])
            except IntegrityError:
                logger.warning("%s: another library entry already has AnimeUnity id %s", anime.title, info['id'])
                anime.refresh_from_db()

        for ep_num, ep_url in info['episodes']:
            if not Episode.objects.filter(anime=anime, number=str(ep_num)).exists():
                logger.info("New episode found for %s: %s", anime.title, ep_num)
                new_ep = Episode.objects.create(
                    anime=anime,
                    number=str(ep_num),
                    source_url=ep_url,
                    status='pending'
                )
                download_episode_task.delay(new_ep.id)
                new_episodes_count += 1

        # Update anime status in case all episodes were already completed but status was weird
        anime.update_status()

    return (f"Checked {animes.count()} anime ({failed} failed). "
            f"Found and queued {new_episodes_count} new episodes.")

@shared_task
def retry_failed_episodes_task():
    from .models import Episode
    
    failed_episodes = list(Episode.objects.filter(status='failed').select_related('anime'))
    count = len(failed_episodes)

    logger.info("Retrying %d failed episodes", count)
    for ep in failed_episodes:
        ep.status = 'pending'
        ep.save()
        download_episode_task.delay(ep.id)
    # The shows are waiting again too (the queue puts failed shows last)
    for anime in {ep.anime for ep in failed_episodes}:
        anime.update_status()

    return f"Retried {count} failed episodes."
