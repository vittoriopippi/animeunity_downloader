from django.urls import path
from . import views

urlpatterns = [
    path('', views.AnimeSearchView.as_view(), name='search'),
    path('queue/', views.QueueView.as_view(), name='queue'),
    path('api/queue/status/', views.QueueStatusView.as_view(), name='queue_status'),
    path('downloaded/', views.DownloadedView.as_view(), name='downloaded'),
    path('anime/<int:anime_id>/', views.AnimeDetailView.as_view(), name='anime_detail'),
    path('download/<int:episode_id>/', views.download_episode_view, name='download_episode'),
    
    # Cancel/Skip API
    path('api/anime/<int:anime_id>/cancel/', views.CancelAnimeView.as_view(), name='cancel_anime'),
    path('api/anime/<int:anime_id>/skip/', views.SkipAnimeView.as_view(), name='skip_anime'),
    path('api/anime/<int:anime_id>/resume/', views.ResumeAnimeView.as_view(), name='resume_anime'),
    path('api/anime/<int:anime_id>/retry/', views.ResumeAnimeView.as_view(), name='retry_anime'),
    path('api/anime/<int:anime_id>/delete/', views.DeleteAnimeView.as_view(), name='delete_anime'),
    path('api/episode/<int:episode_id>/cancel/', views.CancelEpisodeView.as_view(), name='cancel_episode'),
    path('api/episode/<int:episode_id>/skip/', views.SkipEpisodeView.as_view(), name='skip_episode'),
    path('api/episode/<int:episode_id>/resume/', views.ResumeEpisodeView.as_view(), name='resume_episode'),
    path('api/episode/<int:episode_id>/retry/', views.ResumeEpisodeView.as_view(), name='retry_episode'),

    # New Search/Download API
    path('api/search/', views.ApiSearchView.as_view(), name='api_search'),
    path('api/download/', views.ApiDownloadView.as_view(), name='api_download'),
]
