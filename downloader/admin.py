from django.contrib import admin
from .models import Anime, Episode

@admin.register(Anime)
class AnimeAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'created_at')
    search_fields = ('title',)
    list_filter = ('status',)

@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ('anime', 'number', 'status', 'progress', 'updated_at')
    search_fields = ('anime__title', 'number')
    list_filter = ('status', 'anime')
