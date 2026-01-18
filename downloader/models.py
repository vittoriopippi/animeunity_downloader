from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.conf import settings
from pathlib import Path
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
    animeunity_id = models.IntegerField(null=True, blank=True, unique=True)
    slug = models.CharField(max_length=255, null=True, blank=True)
    cover_image = models.URLField(null=True, blank=True, max_length=1024)
    plot = models.TextField(null=True, blank=True)
    year = models.CharField(max_length=10, null=True, blank=True)
    genres = models.CharField(max_length=255, null=True, blank=True)
    studio = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def __str__(self):
        return self.title

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

@receiver(pre_delete, sender=Anime)
def anime_delete_files(sender, instance, **kwargs):
    """Delete the anime folder when the Anime object is deleted."""
    if instance.directory_name:
        anime_path = Path(settings.MEDIA_ROOT) / instance.directory_name
        if anime_path.exists() and anime_path.is_dir():
            print(f"Deleting anime directory: {anime_path}")
            shutil.rmtree(anime_path)
