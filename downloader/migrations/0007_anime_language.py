from django.db import migrations, models


def set_language(apps, schema_editor):
    """Before AnimeUnity's 2026-11 redesign the Italian dub was a separate show titled "... (ITA)"."""
    Anime = apps.get_model('downloader', 'Anime')
    for anime in Anime.objects.all():
        if '(ITA)' in anime.title.upper() or anime.source_url.rstrip('/').endswith('-ita'):
            anime.language = 'ita'
            anime.save(update_fields=['language'])


class Migration(migrations.Migration):

    dependencies = [
        ('downloader', '0006_update_animeunity_domain'),
    ]

    # The data step comes last: PostgreSQL can't alter a table after rows changed in the same transaction
    operations = [
        migrations.AddField(
            model_name='anime',
            name='language',
            field=models.CharField(default='jpn', max_length=10),
        ),
        migrations.AlterField(
            model_name='anime',
            name='animeunity_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AlterUniqueTogether(
            name='anime',
            unique_together={('animeunity_id', 'language')},
        ),
        migrations.RunPython(set_language, migrations.RunPython.noop),
    ]
