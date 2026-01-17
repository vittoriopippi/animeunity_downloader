from celery import shared_task
from .models import Episode
from .utils import download_file, clean_filename, extract_download_url
from pathlib import Path
import cloudscraper
from bs4 import BeautifulSoup
from django.conf import settings

@shared_task(bind=True)
def download_episode_task(self, episode_id):
    try:
        episode = Episode.objects.get(id=episode_id)
        if episode.status != 'pending':
            return f"Task {episode.status}"

        episode.status = 'downloading'
        episode.progress = 0
        episode.save()
        episode.anime.update_status()

        # 1. Fetch the episode page to get the video URL (if not already known)
        scraper = cloudscraper.create_scraper()
        
        # If we just have the page URL, we need to extract the video URL
        if not episode.video_url:
            print(f"Fetching embed link for: {episode.source_url}")
            try:
                # Based on the old code, we should fetch the embed URL first
                # The episode ID is the last part of the source_url
                episode_id_unity = episode.source_url.rstrip('/').split('/')[-1]
                host = episode.source_url.split('//')[1].split('/')[0]
                embed_api_url = f"https://{host}/embed-url/{episode_id_unity}"
                
                # Step A: Get the actual embed URL (e.g. vixcloud)
                headers = {
                    'Referer': episode.source_url,
                    'X-Requested-With': 'XMLHttpRequest'
                }
                resp = scraper.get(embed_api_url, headers=headers)
                resp.raise_for_status()
                embed_url = resp.text.strip()
                
                if not embed_url.startswith('http'):
                    # Fallback or error
                    print(f"Invalid embed URL received: {embed_url}")
                    raise Exception("Could not get valid embed URL")

                # Step B: Fetch the embed page to get the final video URL (window.downloadUrl)
                print(f"Fetching embed page: {embed_url}")
                # Vixcloud might need referer too
                resp = scraper.get(embed_url, headers={'Referer': f"https://{host}/"})
                resp.raise_for_status()
                
                video_url = extract_download_url(resp.text)
                
                if not video_url:
                    # Try one more time with BeautifulSoup just in case regex on whole text failed
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    for script in soup.find_all('script'):
                        if script.string:
                            video_url = extract_download_url(script.string)
                            if video_url:
                                break
                
                if not video_url:
                    raise Exception("Could not extract video URL from embed page")
                
                episode.video_url = video_url
                episode.save()
            except Exception as e:
                # If fetching/extraction fails
                print(f"Extraction failed: {e}")
                episode.status = 'failed'
                episode.save()
                episode.anime.update_status()
                return f"Failed: {e}"
        
        video_url = episode.video_url

        # 2. Prepare file path
        anime_title = clean_filename(episode.anime.title)
        season_dir = "Season 01"
        if episode.number.isdigit():
             ep_str = f"S01E{int(episode.number):02d}"
        else:
             ep_str = f"S01E{episode.number}"
             
        filename = f"{anime_title} - {ep_str}.mp4"
        
        save_dir = Path(settings.MEDIA_ROOT) / anime_title / season_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = save_dir / filename

        # 3. Download with progress
        print(f"Downloading to: {file_path}")
        with scraper.get(video_url, stream=True) as r:
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
                                    print(f"Download {episode.status} for {episode.number}")
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

    except Exception as e:
        print(f"Error downloading episode {episode_id}: {e}")
        try:
             episode = Episode.objects.get(id=episode_id)
             episode.status = 'failed'
             episode.save()
             episode.anime.update_status()
        except:
            pass
        return f"Failed: {e}"
