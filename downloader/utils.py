import requests
from bs4 import BeautifulSoup
import re
import os
import cloudscraper
import urllib.parse
import json
import socket
from django.conf import settings

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

def search_anime(query):
    """
    Search anime on AnimeUnity using cloudscraper.
    """
    scraper = cloudscraper.create_scraper()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json;charset=UTF-8',
        'Origin': 'https://www.animeunity.so',
        'Referer': 'https://www.animeunity.so/',
        'X-Requested-With': 'XMLHttpRequest',
    }

    try:
        # 1. Hit homepage for cookies/CSRF
        resp = scraper.get('https://www.animeunity.so', headers=headers)
        resp.raise_for_status()

        xsrf_cookie = scraper.cookies.get('XSRF-TOKEN')
        meta_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.text)
        meta_token = meta_match.group(1) if meta_match else None

        if xsrf_cookie:
            headers['x-xsrf-token'] = urllib.parse.unquote(xsrf_cookie)
            headers['x-csrf-token'] = headers['x-xsrf-token']
        if meta_token:
            headers['X-CSRF-TOKEN'] = meta_token

        # 2. Search
        search_url = 'https://www.animeunity.so/livesearch'
        payload = {"title": query}
        res = scraper.post(search_url, json=payload, headers=headers)
        res.raise_for_status()
        
        data = res.json()
        results = []
        for record in data.get('records', []):
            # Construct URL: https://www.animeunity.so/anime/ID-SLUG
            anime_url = f"https://www.animeunity.so/anime/{record['id']}-{record['slug']}"
            results.append({
                'title': record.get('title_eng') or record.get('title') or 'Unknown Title',
                'url': anime_url,
                'cover_image': record.get('imageurl'),
                'id': record['id'],
                'slug': record['slug'],
                'plot': record.get('plot'),
                'episodes_count': record.get('episodes_count')
            })
        return results

    except Exception as e:
        print(f"Search failed: {e}")
        return []

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

def get_episode_urls(anime_url):
    """
    Scrape the anime details page to parse the <video-player> tag for episodes.
    Returns a list of tuples: (episode_number, episode_url)
    """
    scraper = cloudscraper.create_scraper()
    print(f"Scraping episodes from: {anime_url}")
    try:
        resp = scraper.get(anime_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        episodes = []
        
        # The data is in a <video-player> tag attributes
        player = soup.find('video-player')
        if player:
            # 1. Get Anime Info for URL construction
            anime_data = {}
            if player.get('anime'):
                try:
                    anime_data = json.loads(player['anime'])
                except:
                    pass
            
            # Construct base URL if we have ID and slug, otherwise use provided URL
            if anime_data.get('id') and anime_data.get('slug'):
                base_url = f"https://www.animeunity.so/anime/{anime_data['id']}-{anime_data['slug']}"
            else:
                base_url = anime_url
            
            # 2. Get Episodes
            if player.get('episodes'):
                try:
                    episodes_data = json.loads(player['episodes'])
                    for ep in episodes_data:
                        # ep is a dict like {"number": "1", ...}
                        num = ep.get('number')
                        id = ep.get('id')
                        if num:
                            # Construct the watch link: .../ep-{number}
                            ep_url = f"{base_url}/{id}"
                            episodes.append((int(num), ep_url))
                except Exception as e:
                    print(f"Error parsing episodes JSON: {e}")

        # Sort by episode number
        def sort_key(x):
            try:
                return float(x[0])
            except:
                return 0
        
        episodes.sort(key=sort_key)
        
        print(f"Found {len(episodes)} episodes.")
        return episodes

    except Exception as e:
        print(f"Error scraping episodes: {e}")
        return []

def download_file(url, file_path):
    """
    Download file from url to file_path with streaming.
    """
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): 
                f.write(chunk)
    return file_path
