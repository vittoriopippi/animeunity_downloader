from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.conf import settings
from pathlib import Path
import os
import shutil

class Anime(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('cancelled', 'Cancelled'),
    )

    title = models.CharField(max_length=255)
    source_url = models.URLField(unique=True, max_length=1024)
    directory_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # New metadata
    # Since AnimeUnity's 2026-11 redesign one show holds every language version,
    # so the same id can appear once per language
    animeunity_id = models.IntegerField(null=True, blank=True)
    language = models.CharField(max_length=10, default='jpn')  # "ita" = Italian dub, "jpn" = original
    slug = models.CharField(max_length=255, null=True, blank=True)
    cover_image = models.URLField(null=True, blank=True, max_length=1024)
    plot = models.TextField(null=True, blank=True)
    year = models.CharField(max_length=10, null=True, blank=True)
    genres = models.CharField(max_length=255, null=True, blank=True)
    studio = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    class Meta:
        unique_together = ('animeunity_id', 'language')

    def __str__(self):
        return self.title

    @property
    def genres_text(self):
        return ", ".join(g.strip() for g in (self.genres or "").split(",") if g.strip())

    def folder_size(self):
        """Bytes in the show's folder (episodes, poster, .nfo); 0 if there's no folder."""
        if not self.directory_name:
            return 0
        total = 0
        for root, _, files in os.walk(Path(settings.MEDIA_ROOT) / self.directory_name):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:  # deleted while counting
                    pass
        return total

    def update_status(self):
        """Update status based on episodes."""
        episodes = self.episodes.all()
        if not episodes.exists():
             self.status = 'pending'
             self.save()
             return

        statuses = [ep.status for ep in episodes]
        
        if all(s in ['completed', 'skipped', 'cancelled', 'failed'] for s in statuses):
            if all(s == 'cancelled' for s in statuses):
                self.status = 'cancelled'
            elif all(s == 'skipped' for s in statuses):
                self.status = 'skipped'
            elif any(s == 'failed' for s in statuses):
                self.status = 'failed'
            else:
                self.status = 'completed'
        elif any(s == 'downloading' for s in statuses):
            self.status = 'downloading'
        else:
            self.status = 'pending'
            
        self.save()

class Episode(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('cancelled', 'Cancelled'),
    )

    anime = models.ForeignKey(Anime, on_delete=models.CASCADE, related_name='episodes')
    number = models.CharField(max_length=10)  # String to handle "OVA", "10.5", etc.
    source_url = models.URLField(max_length=1024)  # URL to the episode page
    video_url = models.URLField(blank=True, null=True, max_length=1024) # Direct link to mp4 if known
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    progress = models.IntegerField(default=0)
    file_path = models.CharField(max_length=512, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('anime', 'number')
        ordering = ['id']

    def __str__(self):
        return f"{self.anime.title} - Episode {self.number}"

    @property
    def label(self):
        """ "EP 05" for numbered episodes, the name for specials ("OVA", "12.5")."""
        number = self.number.strip()
        return f"EP {int(number):02d}" if number.isdigit() else number.upper()

@receiver(pre_delete, sender=Anime)
def anime_delete_files(sender, instance, **kwargs):
    """Delete the anime folder when the Anime object is deleted."""
    if instance.directory_name:
        # Another anime can end up with the same folder name; never delete its files
        if Anime.objects.filter(directory_name=instance.directory_name).exclude(pk=instance.pk).exists():
            print(f"Keeping directory {instance.directory_name}: shared with another anime")
            return
        media_root = Path(settings.MEDIA_ROOT).resolve()
        anime_path = (media_root / instance.directory_name).resolve()
        # Only ever a folder directly inside the media folder (never it, or its parent for "..")
        if anime_path.parent != media_root:
            print(f"Keeping directory {instance.directory_name!r}: not a show folder")
            return
        if anime_path.exists() and anime_path.is_dir():
            print(f"Deleting anime directory: {anime_path}")
            shutil.rmtree(anime_path)
