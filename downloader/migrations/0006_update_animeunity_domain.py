import re

from django.conf import settings
from django.db import migrations

ANIMEUNITY_HOST = re.compile(r'^https?://(www\.)?animeunity\.[a-z]+')


def update_source_urls(apps, schema_editor):
    """Saved URLs still point at the old domain, which breaks retries and new-episode checks."""
    for model_name in ('Anime', 'Episode'):
        model = apps.get_model('downloader', model_name)
        changed = []
        for obj in model.objects.only('id', 'source_url').iterator():
            new_url = ANIMEUNITY_HOST.sub(settings.ANIMEUNITY_BASE_URL, obj.source_url)
            if new_url != obj.source_url:
                obj.source_url = new_url
                changed.append(obj)
        model.objects.bulk_update(changed, ['source_url'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('downloader', '0005_anime_genres_anime_studio_anime_year'),
    ]

    operations = [
        migrations.RunPython(update_source_urls, migrations.RunPython.noop),
    ]
