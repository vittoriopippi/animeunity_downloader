from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from .models import Anime, Episode
from .forms import AnimeAddForm
from .utils import (clean_filename, search_anime, get_anime_info, check_broker_status, save_anime_metadata,
                    language_from_url, SearchError)
from .tasks import download_episode_task, check_for_new_episodes_task, retry_failed_episodes_task
from django.db.models import Q, Count, Max, F
from django.contrib import messages
from django.http import JsonResponse
from django.template.defaultfilters import filesizeformat
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json
import logging

logger = logging.getLogger(__name__)


def add_to_library(result):
    """
    Add a search result (url, title, cover_image, language) to the library and
    queue the episodes that aren't downloaded yet.
    Returns (anime, number of episodes, whether the queue is up).
    Raises AnimeUnityError if AnimeUnity can't be read; then nothing is saved.
    """
    url = result['url']
    language = result.get('language') or language_from_url(url) or 'jpn'
    info = get_anime_info(url, language)
    broker_ok, _ = check_broker_status()

    title = result.get('title') or info['title']
    # Entries saved before the 2026-11 redesign have an old id and URL until the daily check
    # updates the id; the folder name still finds them, so nothing is downloaded twice
    anime = (Anime.objects.filter(animeunity_id=info['id'], language=language).first()
             or Anime.objects.filter(source_url=url).first()
             or Anime.objects.filter(directory_name=clean_filename(title), language=language).first())
    if anime is None:
        anime = Anime(source_url=url, language=language, title=title, directory_name=clean_filename(title))
    # An anime already in the library keeps its title and folder, so its files stay where they are
    anime.animeunity_id = info['id']
    anime.slug = info['slug']
    anime.cover_image = result.get('cover_image') or anime.cover_image
    anime.plot = info['plot'] or anime.plot
    anime.year = info['year'] or anime.year
    anime.studio = info['studio'] or anime.studio
    if info['genres']:
        anime.genres = ",".join(info['genres'])
    anime.save()
    save_anime_metadata(anime)

    for ep_num, ep_url in info['episodes']:
        episode, _ = Episode.objects.get_or_create(
            anime=anime,
            number=str(ep_num),
            defaults={'source_url': ep_url}
        )
        # Don't touch an episode that is currently downloading, or a second task would start on the same file
        if episode.status not in ('completed', 'downloading'):
            episode.status = 'pending'
            episode.save()
            if broker_ok:
                download_episode_task.delay(episode.id)

    anime.update_status()
    return anime, len(info['episodes']), broker_ok


class AnimeSearchView(View):
    def get(self, request):
        query = (request.GET.get('q') or '').strip()
        results = []
        search_failed = False
        if query:
            try:
                results = search_anime(query)
            except SearchError as e:
                search_failed = True
                messages.error(request, f"Could not search AnimeUnity: {e}")

        return render(request, 'downloader/search.html',
                      {'results': results, 'query': query, 'search_failed': search_failed})

    def post(self, request):
        result = {k: request.POST.get(k) for k in ('url', 'title', 'cover_image', 'language')}
        if result['url']:
            try:
                anime, count, broker_ok = add_to_library(result)
                if not broker_ok:
                    messages.warning(request, "Queue service (Redis) is offline. Episodes added to library but downloads won't start automatically.")
                messages.success(request, f"Added '{anime.title}' to queue with {count} episodes.")
                # The form posts to the current URL, so the search query is still in it
                query = request.GET.get('q')
                if query:
                    return redirect(f'/?q={query}')
                return redirect('search')
            except Exception as e:
                logger.error("Could not add %s to the library: %s", result['title'], e)
                messages.error(request, f"Error adding anime: {e}")
        return redirect('search')

def queue_status(animes):
    """What the queue page shows and refreshes every few seconds."""
    return {'animes': [{
        'id': anime.id,
        'title': anime.title,
        'cover_image': anime.cover_image,
        'status': anime.status,
        'language': anime.language,
        'episodes': [{
            'id': ep.id,
            'status': ep.status,
            'progress': ep.progress,
            'number': ep.number,
            'error_message': ep.error_message,
        } for ep in anime.episodes.all()],
    } for anime in animes]}

QUEUE_ORDER = {'downloading': 0, 'pending': 1}

def queue_animes():
    """
    The shows still in the queue: fully downloaded ones leave it (they stay in the library).
    The ones downloading come first, then the waiting ones in the order they were added,
    which is the order they download in; failed, skipped and cancelled ones go last.
    """
    animes = Anime.objects.exclude(status='completed').prefetch_related('episodes')
    return sorted(animes, key=lambda a: (QUEUE_ORDER.get(a.status, 2), a.created_at, a.id))

class QueueView(View):
    def get(self, request):
        animes = queue_animes()
        return render(request, 'downloader/queue.html', {'animes': animes, 'queue': queue_status(animes)})

class QueueStatusView(View):
    def get(self, request):
        return JsonResponse(queue_status(queue_animes()))

class DownloadedView(View):
    def get(self, request):
        # Only finished shows, latest download first. A show is in either the queue or the
        # library: it moves here when it leaves the queue (all its episodes downloaded or skipped)
        done = Q(episodes__status='completed')
        animes = list(Anime.objects
                      .filter(status='completed')
                      .annotate(done=Count('episodes', filter=done), total=Count('episodes'),
                                last_done=Max('episodes__updated_at', filter=done))
                      .order_by(F('last_done').desc(nulls_last=True)))
        for anime in animes:
            anime.size = anime.folder_size()
        return render(request, 'downloader/downloaded.html',
                      {'animes': animes, 'episode_total': sum(a.done for a in animes),
                       'size_total': sum(a.size for a in animes)})

# Episode states as the library's episode grid shows them, in the order of its legend
EPISODE_KEY = (('completed', 'DONE'), ('downloading', 'DL'), ('pending', 'WAITING'),
               ('failed', 'FAILED'), ('skipped', 'SKIPPED'), ('cancelled', 'CANCELLED'))

class AnimeDetailView(View):
    def get(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        episodes = list(anime.episodes.all().order_by('id'))
        counts = {status: sum(1 for ep in episodes if ep.status == status) for status, _ in EPISODE_KEY}
        return render(request, 'downloader/anime_detail.html', {
            'anime': anime,
            'episodes': episodes,
            'done': counts['completed'],
            'key': [(status, label, counts[status]) for status, label in EPISODE_KEY if counts[status]],
            'failed': [ep for ep in episodes if ep.status == 'failed'],
            'downloading': counts['downloading'] > 0,
            'size': anime.folder_size(),
        })

class DeleteFromLibraryView(View):
    """The library's DELETE: removes the show and its folder from the disk, then goes back to the library."""
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        if anime.episodes.filter(status='downloading').exists():
            # The worker would go on writing into the deleted folder
            messages.error(request, f"'{anime.title}' is downloading: cancel it in the queue first.")
            return redirect('anime_detail', anime_id=anime.id)
        title, size = anime.title, anime.folder_size()
        anime.delete()  # the pre_delete signal removes the folder
        messages.success(request, f"Deleted '{title}' and its files ({filesizeformat(size)} freed).")
        return redirect('downloaded')

def download_episode_view(request, episode_id):
    broker_ok, broker_err = check_broker_status()
    if broker_ok:
        download_episode_task.delay(episode_id)
    else:
        messages.error(request, f"Cannot start download: Queue service (Redis) is offline.")
    return redirect(request.META.get('HTTP_REFERER', 'queue'))

class CancelAnimeView(View):
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        # Cancel all episodes that are not completed or failed
        episodes = anime.episodes.exclude(status__in=['completed', 'failed'])
        episodes.update(status='cancelled')
        anime.update_status()
        return JsonResponse({'status': 'ok'})

class SkipAnimeView(View):
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        # Skip all episodes that are not completed or failed
        episodes = anime.episodes.exclude(status__in=['completed', 'failed'])
        episodes.update(status='skipped')
        anime.update_status()
        return JsonResponse({'status': 'ok'})

class CancelEpisodeView(View):
    def post(self, request, episode_id):
        episode = get_object_or_404(Episode, pk=episode_id)
        episode.status = 'cancelled'
        episode.save()
        episode.anime.update_status()
        return JsonResponse({'status': 'ok'})

class SkipEpisodeView(View):
    def post(self, request, episode_id):
        episode = get_object_or_404(Episode, pk=episode_id)
        episode.status = 'skipped'
        episode.save()
        episode.anime.update_status()
        return JsonResponse({'status': 'ok'})

class ResumeEpisodeView(View):
    def post(self, request, episode_id):
        episode = get_object_or_404(Episode, pk=episode_id)
        episode.status = 'pending'
        episode.progress = 0
        episode.error_message = None
        episode.save()
        episode.anime.update_status()
        
        broker_ok, _ = check_broker_status()
        if broker_ok:
            download_episode_task.delay(episode.id)
            
        return JsonResponse({'status': 'ok'})

class ResumeAnimeView(View):
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        # Find all episodes that are either cancelled, skipped or failed
        episodes_to_resume = anime.episodes.filter(status__in=['cancelled', 'skipped', 'failed'])
        
        broker_ok, _ = check_broker_status()
        
        for episode in episodes_to_resume:
            episode.status = 'pending'
            episode.progress = 0
            episode.error_message = None
            episode.save()
            if broker_ok:
                download_episode_task.delay(episode.id)
        
        anime.update_status()
        return JsonResponse({'status': 'ok'})

class ApiSearchView(View):
    def get(self, request):
        query = request.GET.get('q')
        results = []
        if query:
            try:
                results = search_anime(query)
            except SearchError as e:
                return JsonResponse({'results': [], 'error': str(e)}, status=502)
        return JsonResponse({'results': results})

@method_decorator(csrf_exempt, name='dispatch')
class ApiDownloadView(View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            title_to_match = data.get('title')
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        if not title_to_match:
            return JsonResponse({'status': 'error', 'message': 'Title is required'}, status=400)

        # 1. Search for the anime
        try:
            results = search_anime(title_to_match)
        except SearchError as e:
            return JsonResponse({'status': 'error', 'message': f'Could not search AnimeUnity: {e}'}, status=502)

        # 2. Find exact match
        match = None
        for res in results:
            if res['title'].lower() == title_to_match.lower():
                match = res
                break
        
        if not match:
            return JsonResponse({'status': 'error', 'message': f'No exact match found for "{title_to_match}"'}, status=404)

        # 3. Add to library
        try:
            anime, count, _ = add_to_library(match)
            return JsonResponse({
                'status': 'ok',
                'message': f'Added "{anime.title}" to queue with {count} episodes.',
                'anime_id': anime.id
            })
        except Exception as e:
            logger.error("Could not add %s to the library: %s", match['title'], e)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

class DeleteAnimeView(View):
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        anime.delete()
        return JsonResponse({'status': 'ok'})

class ManualCheckNewEpisodesView(View):
    def post(self, request):
        check_for_new_episodes_task.delay()
        messages.success(request, "Task to check for new episodes has been queued.")
        return redirect('search')

class ManualRetryFailedEpisodesView(View):
    def post(self, request):
        retry_failed_episodes_task.delay()
        messages.success(request, "Task to retry failed episodes has been queued.")
        return redirect('search')
