from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from .models import Anime, Episode
from .forms import AnimeAddForm
from .utils import get_anime_info_mock, clean_filename, search_anime, get_episode_urls, check_broker_status
from .tasks import download_episode_task
from django.db.models import Q
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json


class AnimeSearchView(View):
    def get(self, request):
        query = request.GET.get('q')
        results = []
        if query:
            results = search_anime(query)
        
        return render(request, 'downloader/search.html', {'results': results, 'query': query})

    def post(self, request):
        # Allow adding from the search result (simulated by passing URL)
        url = request.POST.get('url')
        title = request.POST.get('title')
        cover_image = request.POST.get('cover_image')
        plot = request.POST.get('plot')
        animeunity_id = request.POST.get('id')
        slug = request.POST.get('slug')
        episodes_count = request.POST.get('episodes_count')
        year = request.POST.get('year')
        studio = request.POST.get('studio')

        if url:
             # Mock scraping logic from previous Add view
            try:
                # 1. Check broker status first
                broker_ok, broker_err = check_broker_status()
                # broker_ok = True
                if not broker_ok:
                    messages.warning(request, f"Queue service (Redis) is offline. Episodes added to library but downloads won't start automatically.")

                # We trust the search result data for now
                defaults = {
                    'title': title,
                    'directory_name': clean_filename(title),
                    'cover_image': cover_image,
                    'plot': plot,
                    'slug': slug,
                    'year': year,
                    'studio': studio
                }
                if animeunity_id:
                    defaults['animeunity_id'] = animeunity_id
                
                # Update status based on existence
                # But initial status is pending.
                
                anime, created = Anime.objects.update_or_create(
                    source_url=url,
                    defaults=defaults
                )
                
                # Determine how many episodes to create
                num_episodes = 1
                if episodes_count and episodes_count.isdigit():
                    num_episodes = int(episodes_count)

                # From the url scrape the episode urls with bs4
                episodes_urls, genres = get_episode_urls(url)
                
                if genres:
                    anime.genres = ",".join(genres)
                    anime.save()

                # Save metadata files (nfo and poster)
                from .utils import save_anime_metadata
                save_anime_metadata(anime)

                num_episodes = len(episodes_urls)

                # Create episodes
                for ep_num, ep_url in episodes_urls:
                    episode, created = Episode.objects.get_or_create(
                        anime=anime,
                        number=str(ep_num),
                        defaults={'source_url': ep_url}
                    )

                    if episode.status != 'completed':
                        episode.status = 'pending'
                        episode.save()
                    
                for ep_num, ep_url in episodes_urls:
                    episode = Episode.objects.get(anime=anime, number=str(ep_num))
                    # Trigger download if it's new or pending AND broker is OK
                    if episode.status == 'pending' and broker_ok:
                        print(f"Triggering download for episode {episode.number}")
                        download_episode_task.delay(episode.id)
                
                # Update anime status initially
                anime.update_status()

                messages.success(request, f"Added '{title}' to queue with {num_episodes} episodes.")
                
                # Redirect back to search, preserving query if possible
                query = request.GET.get('q') # Since form action="" commonly preserves GET params in URL, we might grab it?
                # Actually request.GET might be empty if the form submit didn't include it in action URL explicitly or browser didn't keep it.
                # But let's check: <form action=""> usually submits to current URL. if current URL is /?q=foo, it submits POST to /?q=foo.
                # So request.GET['q'] should exist.
                if query:
                    return redirect(f'/?q={query}')
                return redirect('search')

            except Exception as e:
                # In a real app, handle error
                print(f"Error adding anime: {e}")
                messages.error(request, f"Error adding anime: {e}")
                pass
        return redirect('search')

class QueueView(View):
    def get(self, request):
        # Show all anime in the database with their episodes
        animes = Anime.objects.all().prefetch_related('episodes').order_by('-created_at')
        return render(request, 'downloader/queue.html', {'animes': animes})

class QueueStatusView(View):
    def get(self, request):
        animes = Anime.objects.all().prefetch_related('episodes').order_by('-created_at')
        data = []
        for anime in animes:
            episodes = []
            for ep in anime.episodes.all():
                episodes.append({
                    'id': ep.id,
                    'status': ep.status,
                    'progress': ep.progress,
                    'number': ep.number,
                    'error_message': ep.error_message
                })
            data.append({
                'id': anime.id,
                'status': anime.status,
                'episodes': episodes
            })
        return JsonResponse({'animes': data})

class DownloadedView(View):
    def get(self, request):
        # Completed episodes
        # Maybe group by Anime? For now just list them
        episodes = Episode.objects.filter(status='completed').order_by('-updated_at')
        return render(request, 'downloader/downloaded.html', {'episodes': episodes})

class AnimeDetailView(View): 
    def get(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        episodes = anime.episodes.all().order_by('id') 
        return render(request, 'downloader/anime_detail.html', {'anime': anime, 'episodes': episodes})

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
            results = search_anime(query)
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
        results = search_anime(title_to_match)
        
        # 2. Find exact match
        match = None
        for res in results:
            if res['title'].lower() == title_to_match.lower():
                match = res
                break
        
        if not match:
            return JsonResponse({'status': 'error', 'message': f'No exact match found for "{title_to_match}"'}, status=404)

        # 3. Add to library (reuse logic from AnimeSearchView)
        url = match['url']
        title = match['title']
        cover_image = match['cover_image']
        plot = match['plot']
        animeunity_id = match['id']
        slug = match['slug']
        episodes_count = match['episodes_count']
        year = match['year']
        studio = match['studio']

        try:
            broker_ok, broker_err = check_broker_status()
            
            defaults = {
                'title': title,
                'directory_name': clean_filename(title),
                'cover_image': cover_image,
                'plot': plot,
                'slug': slug,
                'year': year,
                'studio': studio
            }
            if animeunity_id:
                defaults['animeunity_id'] = animeunity_id
            
            anime, created = Anime.objects.update_or_create(
                source_url=url,
                defaults=defaults
            )
            
            episodes_urls, genres = get_episode_urls(url)
            
            if genres:
                anime.genres = ",".join(genres)
                anime.save()

            from .utils import save_anime_metadata
            save_anime_metadata(anime)

            for ep_num, ep_url in episodes_urls:
                episode, _ = Episode.objects.get_or_create(
                    anime=anime,
                    number=str(ep_num),
                    defaults={'source_url': ep_url}
                )
                if episode.status != 'completed':
                    episode.status = 'pending'
                    episode.save()
            
            if broker_ok:
                for ep_num, ep_url in episodes_urls:
                    episode = Episode.objects.get(anime=anime, number=str(ep_num))
                    if episode.status == 'pending':
                        download_episode_task.delay(episode.id)
            
            anime.update_status()
            
            return JsonResponse({
                'status': 'ok', 
                'message': f'Added "{title}" to queue with {len(episodes_urls)} episodes.',
                'anime_id': anime.id
            })

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

class DeleteAnimeView(View):
    def post(self, request, anime_id):
        anime = get_object_or_404(Anime, pk=anime_id)
        anime.delete()
        return JsonResponse({'status': 'ok'})
