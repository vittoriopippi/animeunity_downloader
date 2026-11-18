import requests
import re
import cloudscraper
import urllib.parse
import json
import html
import logging
import socket
import time
from django.conf import settings
from pathlib import Path

logger = logging.getLogger(__name__)

# Seconds; without a timeout a stalled connection blocks forever
REQUEST_TIMEOUT = 30
# (connect, read) for video streams; the read timeout applies between received chunks
DOWNLOAD_TIMEOUT = (15, 120)
# Best first; AnimeUnity answers 404 for a quality an episode doesn't have
DOWNLOAD_QUALITIES = (1080, 720, 480)
# How long to remember which domain ANIMEUNITY_BASE_URL redirects to
SITE_CACHE_SECONDS = 600
# Safety stop for the paged episode list (120 episodes a page)
MAX_EPISODE_PAGES = 50
JSON_HEADERS = {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}

class AnimeUnityError(Exception):
    """AnimeUnity couldn't be read, or answered in a way this app doesn't understand."""

class SearchError(AnimeUnityError):
    """AnimeUnity could not be searched, as opposed to a search with no results."""

def check_broker_status():
    """
    Check if the Redis broker is reachable to avoid blocking on .delay()
    Returns (True, None) if reachable, (False, "Error message") otherwise.
    """
    broker_url = getattr(settings, 'CELERY_BROKER_URL', '')
    if 'redis://' in broker_url:
        try:
            # Extract host and port using basic parsing
            # format redis://[:password@]host[:port][/db]
            url = broker_url.split('redis://')[1]
            host_port = url.split('/')[0].split('@')[-1]
            if ':' in host_port:
                host, port = host_port.split(':')
                port = int(port)
            else:
                host = host_port
                port = 6379
            
            # Fast socket check
            with socket.create_connection((host, port), timeout=1):
                return True, None
        except Exception as e:
            return False, str(e)
    return True, None # Not redis or no URL, assume OK or handled elsewhere

def clean_filename(name):
    """
    Remove illegal characters from filename.
    """
    return re.sub(r'[\\/*?:"<>|]', "", name)

def extract_download_url(html_content):
    """
    Extract the download URL from the HTML content of an episode page.
    Look for: window.downloadUrl = '...' or "..."
    """
    pattern = r'window\.downloadUrl\s*=\s*[\'"](https?:\/\/[^\'"]+)[\'"]'
    match = re.search(pattern, html_content)
    if match:
        return match.group(1)
    return None

_site = {'origin': None, 'at': 0.0}

def site_origin(scraper):
    """
    Scheme and host AnimeUnity answers on now, e.g. "https://animeunity.cx".
    ANIMEUNITY_BASE_URL may redirect to a newer domain; requests built from it would
    then be redirected too, and a redirect can turn a request into a different one.
    """
    if _site['origin'] and time.time() - _site['at'] < SITE_CACHE_SECONDS:
        return _site['origin']
    resp = scraper.get(f'{settings.ANIMEUNITY_BASE_URL}/', timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parts = urllib.parse.urlsplit(resp.url)
    _site.update(origin=f'{parts.scheme}://{parts.netloc}', at=time.time())
    return _site['origin']

def page_props(html_text):
    """AnimeUnity pages are Inertia apps: the page's data is JSON in the data-page attribute."""
    match = re.search(r'data-page="([^"]*)"', html_text)
    if not match:
        raise AnimeUnityError('the page has no data-page block (did the site layout change?)')
    return json.loads(html.unescape(match.group(1))).get('props') or {}

def read_json(resp):
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        raise AnimeUnityError(f'{resp.url} did not answer with JSON')

def display_title(title, language):
    """The original-language version keeps the plain title; dubs get " (ITA)" etc., as before the 2026-11 redesign."""
    return title if language == 'jpn' else f'{title} ({language.upper()})'

def anime_page_url(origin, anime_id, slug, language):
    # The site ignores ?lang; it keeps each language version of a show a separate library entry
    return f'{origin}/anime/{anime_id}-{slug}?lang={language}'

def language_from_url(url):
    """Language of a library URL: ?lang=..., or a "-ita" slug from before the 2026-11 redesign."""
    parts = urllib.parse.urlsplit(url or '')
    lang = urllib.parse.parse_qs(parts.query).get('lang', [None])[0]
    if lang:
        return lang
    if parts.path.rstrip('/').endswith('-ita'):
        return 'ita'
    return None

def search_anime(query):
    """
    Search AnimeUnity. Each language version of a show is its own result.
    """
    scraper = cloudscraper.create_scraper()
    try:
        origin = site_origin(scraper)
        data = read_json(scraper.get(f'{origin}/api/search', params={'q': query},
                                     headers=JSON_HEADERS, timeout=REQUEST_TIMEOUT))
        records = data.get('data')
        if not isinstance(records, list):
            raise AnimeUnityError('the search answer has no "data" list (did the API change?)')
    except Exception as e:
        logger.error("Could not search AnimeUnity for %r: %s", query, e)
        raise SearchError(str(e)) from e

    results = []
    for record in records:
        images = record.get('images') or {}
        title = record.get('title') or record.get('title_ja') or 'Unknown Title'
        variants = record.get('variants') or [{'language': 'jpn', 'episodes_count': record.get('total_episodes')}]
        for variant in variants:
            language = variant.get('language') or 'jpn'
            results.append({
                'title': display_title(title, language),
                'url': anime_page_url(origin, record['id'], record['slug'], language),
                'cover_image': images.get('poster') or images.get('cover'),
                'id': record['id'],
                'slug': record['slug'],
                'language': language,
                'plot': None,  # not in search results; read from the show's page when it's added
                'episodes_count': variant.get('episodes_count') or record.get('total_episodes'),
                'year': record.get('year'),
                'studio': None,
            })
    return results

def episode_label(number):
    """Plain integers are normalized ("01" -> 1) to match existing episode records."""
    number = str(number or '').strip()
    if number.isdigit():
        return int(number)
    return number[:10]  # Episode.number max_length; numbers can also be "12.5", "1-2" or "OVA"

def get_anime_info(anime_url, language):
    """
    Read a show and its episodes in one language ("ita", "jpn"...).
    Returns a dict with id, slug, title, plot, year, studio, genres (list) and
    episodes: a list of (episode number, episode page URL), sorted.
    Raises AnimeUnityError if AnimeUnity can't be read.
    """
    scraper = cloudscraper.create_scraper()
    try:
        # Old links (older domains, and the per-language show ids from before the
        # 2026-11 redesign) redirect to the show's current page
        resp = scraper.get(anime_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        anime = page_props(resp.text).get('anime') or {}
        if not anime.get('id') or not anime.get('slug'):
            raise AnimeUnityError('no show data on the page (did the site layout change?)')
        parts = urllib.parse.urlsplit(resp.url)
        origin = f'{parts.scheme}://{parts.netloc}'

        languages = [v.get('language') for v in anime.get('variants') or []]
        if languages and language not in languages:
            raise AnimeUnityError(f'no {language} version of {anime.get("title")} (there is: {", ".join(languages)})')

        # The episode list comes in pages of "tab_size" episodes
        records = []
        start = 1
        for _ in range(MAX_EPISODE_PAGES):
            page = read_json(scraper.get(f"{origin}/api/anime/{anime['id']}/episodes", params={'start': start},
                                         headers=JSON_HEADERS, timeout=REQUEST_TIMEOUT))
            batch = page.get('episodes') or []
            records.extend(batch)
            size = int(page.get('tab_size') or len(batch))
            start += size
            if not batch or not size or start > int(page.get('total') or 0):
                break
    except AnimeUnityError:
        raise
    except Exception as e:
        raise AnimeUnityError(str(e)) from e

    episodes = []
    seen_ids = set()
    for record in records:
        if record.get('id') in seen_ids:
            continue
        seen_ids.add(record.get('id'))
        video = next((v for v in record.get('videos') or [] if v.get('language') == language), None)
        number = episode_label(record.get('number'))
        if video and number != '':
            episodes.append((number, f"{origin}/anime/{anime['id']}-{anime['slug']}/{video['id']}"))

    # Sort by episode number, using the leading number for labels like "1-2"
    def sort_key(x):
        match = re.match(r'\d+(\.\d+)?', str(x[0]))
        return float(match.group()) if match else 0
    episodes.sort(key=sort_key)

    info = {
        'id': anime['id'],
        'slug': anime['slug'],
        'title': anime.get('title'),
        'plot': anime.get('plot'),
        'year': str(anime['year']) if anime.get('year') else None,
        'studio': ', '.join(s['name'] for s in anime.get('studios') or [] if s.get('name')) or None,
        'genres': [g['name'] for g in anime.get('genres') or [] if g.get('name')],
        'episodes': episodes,
    }
    logger.info("%s (%s): %d episodes", info['title'], language, len(episodes))
    return info

def get_download_url(episode_url):
    """
    Direct MP4 link for one episode, best quality first. The links are signed and
    expire, so get a fresh one for every download.
    Raises AnimeUnityError if there's no link.
    """
    video_id = urllib.parse.urlsplit(episode_url).path.rstrip('/').split('/')[-1]
    if not video_id.isdigit():
        raise AnimeUnityError(f'no video id at the end of {episode_url}')
    scraper = cloudscraper.create_scraper()
    try:
        origin = site_origin(scraper)
        site_host = urllib.parse.urlsplit(origin).netloc
        # What the site's DOWNLOAD button does: a redirect to the file on the video host
        for quality in DOWNLOAD_QUALITIES:
            resp = scraper.get(f'{origin}/episode-videos/{video_id}/download', params={'quality': quality},
                               allow_redirects=False, timeout=REQUEST_TIMEOUT)
            location = resp.headers.get('Location', '')
            if resp.is_redirect and urllib.parse.urlsplit(location).netloc not in ('', site_host):
                return location

        # Fallback: the video player's page also carries the link
        resp = scraper.get(episode_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        embed_url = page_props(resp.text).get('embedUrl')
        if embed_url:
            resp = scraper.get(embed_url, headers={'Referer': f'{origin}/'}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            url = extract_download_url(resp.text)
            if url:
                return url
    except AnimeUnityError:
        raise
    except Exception as e:
        raise AnimeUnityError(str(e)) from e
    raise AnimeUnityError(f'no download link for video {video_id} (tried the download button and the player)')

def get_anime_info_mock(url):
    """
    Mock function to return anime info.
    Replace with actual scraping logic.
    """
    # TODO: Implement actual scraping for AnimeUnity
    # Logic:
    # 1. Fetch url
    # 2. Parse title
    # 3. Find episode list
    
    return {
        "title": "Mock Anime",
        "episodes": [
            {"number": "1", "url": "http://example.com/ep1"},
            {"number": "2", "url": "http://example.com/ep2"},
        ]
    }

def get_video_stream_url_mock(episode_url):
    """
    Mock function to get video stream URL.
    Replace with actual logic to extract mp4/m3u8 link.
    """
    # TODO: Implement extraction logic
    return "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

def download_file(url, file_path):
    """
    Download file from url to file_path with streaming.
    """
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): 
                f.write(chunk)
    return file_path

def save_anime_metadata(anime):
    """
    Save tvshow.nfo and poster.jpg for the anime.
    """
    if not anime.directory_name:
        anime.directory_name = clean_filename(anime.title)
        anime.save()

    anime_path = Path(settings.MEDIA_ROOT) / anime.directory_name
    anime_path.mkdir(parents=True, exist_ok=True)

    # 1. Save tvshow.nfo
    nfo_path = anime_path / "tvshow.nfo"
    
    genres_xml = ""
    if anime.genres:
        for g in anime.genres.split(','):
            genres_xml += f"  <genre>{g.strip()}</genre>\n"

    nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<tvshow>
  <title>{anime.title}</title>
  <plot>{anime.plot or ""}</plot>
  <year>{anime.year or ""}</year>
{genres_xml}  <studio>{anime.studio or ""}</studio>
</tvshow>"""

    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)
    
    # 2. Save poster.jpg
    if anime.cover_image:
        poster_path = anime_path / "poster.jpg"
        try:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(anime.cover_image, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            with open(poster_path, 'wb') as f:
                f.write(resp.content)
            logger.info("Saved poster to %s", poster_path)
        except Exception as e:
            logger.warning("Could not save the poster of %s: %s", anime.title, e)
