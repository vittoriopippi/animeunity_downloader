import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Anime, Episode
from .tasks import BUSY_RETRIES, download_episode_task, retry_failed_episodes_task


def make_anime(title, statuses, minutes_ago=0):
    anime = Anime.objects.create(title=title, source_url=f'https://animeunity.cx/anime/{title}',
                                 directory_name=title, language='jpn')
    Anime.objects.filter(pk=anime.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    for n, status in enumerate(statuses, 1):
        Episode.objects.create(anime=anime, number=str(n), source_url=f'https://animeunity.cx/e/{n}', status=status)
    anime.update_status()
    return anime


class MediaTestCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.media = self.tmp / 'media'
        self.media.mkdir()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


def video_response(status_code, body=b''):
    """What `with scraper.get(...) as r` gives for a video request."""
    r = mock.MagicMock(status_code=status_code, headers={'content-length': str(len(body))})
    r.iter_content.return_value = [body]
    cm = mock.MagicMock()
    cm.__enter__.return_value = r
    return cm


@mock.patch('downloader.tasks.get_download_url', return_value='https://au-d1-01.vix-content.net/x.mp4?token=secret')
@mock.patch('downloader.tasks.cloudscraper.create_scraper')
class BusyServerTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.anime = make_anime('Show', ['pending'])
        self.episode = self.anime.episodes.get()

    def test_busy_server_is_retried_quietly(self, create_scraper, _):
        create_scraper.return_value.get.side_effect = [video_response(503), video_response(503),
                                                       video_response(200, b'video')]
        with self.assertLogs('downloader', level='INFO') as logs:
            download_episode_task.apply(args=(self.episode.id,))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, 'completed')
        self.assertEqual((self.media / 'Show' / 'Season 01' / 'Show - S01E01.mp4').read_bytes(), b'video')
        self.assertFalse([r for r in logs.records if r.levelname == 'ERROR'], 'a busy server must not log an ERROR')
        self.assertEqual(sum('video server busy' in r.getMessage() for r in logs.records), 2)

    def test_gives_up_with_a_short_message(self, create_scraper, _):
        create_scraper.return_value.get.side_effect = lambda *a, **k: video_response(503)
        with self.assertLogs('downloader', level='INFO') as logs:
            download_episode_task.apply(args=(self.episode.id,))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, 'failed')
        self.assertEqual(self.episode.error_message, f'Video server busy (503) {BUSY_RETRIES + 1} times in a row')
        errors = [r.getMessage() for r in logs.records if r.levelname == 'ERROR']
        self.assertEqual(errors, [f'Could not download Show episode 1: {self.episode.error_message}'])
        self.assertEqual(create_scraper.return_value.get.call_count, BUSY_RETRIES + 1)

    def test_cancel_during_a_busy_answer_wins(self, create_scraper, _):
        def cancel_then_busy(*a, **k):
            Episode.objects.filter(pk=self.episode.pk).update(status='cancelled')  # tapped mid-request
            return video_response(503)
        create_scraper.return_value.get.side_effect = cancel_then_busy
        download_episode_task.apply(args=(self.episode.id,))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, 'cancelled')
        self.assertEqual(create_scraper.return_value.get.call_count, 1)


class HourlyRetryTests(TestCase):
    @mock.patch('downloader.tasks.download_episode_task.delay')
    def test_retried_shows_are_waiting_again(self, delay):
        anime = make_anime('Show', ['completed', 'failed'])
        self.assertEqual(anime.status, 'failed')
        retry_failed_episodes_task()
        anime.refresh_from_db()
        self.assertEqual(anime.status, 'pending')
        delay.assert_called_once()


class QueueTests(TestCase):
    def test_finished_shows_leave_and_the_rest_follow_download_order(self):
        make_anime('Done', ['completed', 'completed'], minutes_ago=50)
        first = make_anime('First added', ['pending', 'pending'], minutes_ago=40)
        broken = make_anime('Broken', ['completed', 'failed'], minutes_ago=30)
        second = make_anime('Second added', ['pending'], minutes_ago=20)
        busy = make_anime('Busy', ['completed', 'downloading', 'pending'], minutes_ago=10)

        data = self.client.get(reverse('queue_status')).json()
        self.assertEqual([a['title'] for a in data['animes']], [busy.title, first.title, second.title, broken.title])

        page = self.client.get(reverse('queue'))
        self.assertNotContains(page, '>Done<')
        self.assertContains(page, 'id="row-tpl"')

    def test_empty_queue(self):
        make_anime('Done', ['completed'])
        page = self.client.get(reverse('queue'))
        self.assertContains(page, '<div class="void" id="queue-empty">')


class LibraryTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.anime = make_anime('Show', ['completed', 'completed', 'failed'])
        season = self.media / 'Show' / 'Season 01'
        season.mkdir(parents=True)
        (season / 'Show - S01E01.mp4').write_bytes(b'x' * 3 * 1024 * 1024)
        (season / 'Show - S01E02.mp4').write_bytes(b'x' * 2 * 1024 * 1024)
        self.anime.episodes.filter(number='3').update(error_message='Video server busy (503) 11 times in a row')

    def test_sizes(self):
        self.assertEqual(self.anime.folder_size(), 5 * 1024 * 1024)
        self.anime.episodes.filter(number='3').update(status='completed')
        self.anime.update_status()
        self.assertContains(self.client.get(reverse('downloaded')), '5.0\xa0MB', count=2)

    def test_library_lists_only_finished_shows(self):
        make_anime('Finished', ['completed', 'skipped'])  # what you skipped doesn't hold a show back
        make_anime('Halfway', ['completed', 'downloading'])
        page = self.client.get(reverse('downloaded'))
        self.assertContains(page, '>Finished<')
        self.assertNotContains(page, '>Halfway<')
        self.assertNotContains(page, '>Show<')  # has a failed episode: still in the queue

    def test_compact_episode_grid(self):
        page = self.client.get(reverse('anime_detail', args=[self.anime.id]))
        self.assertContains(page, 'class="tile st-completed"', count=2)
        self.assertContains(page, 'class="tile st-failed"', count=1)
        self.assertContains(page, '2 DONE')
        self.assertContains(page, 'Video server busy (503) 11 times in a row')
        self.assertContains(page, 'DELETE SHOW + FILES')

    def test_delete_removes_show_and_folder(self):
        response = self.client.post(reverse('delete_from_library', args=[self.anime.id]))
        self.assertRedirects(response, reverse('downloaded'))
        self.assertFalse(Anime.objects.filter(pk=self.anime.pk).exists())
        self.assertFalse((self.media / 'Show').exists())
        self.assertTrue(self.media.exists())

    def test_no_delete_while_downloading(self):
        self.anime.episodes.filter(number='3').update(status='downloading')
        response = self.client.post(reverse('delete_from_library', args=[self.anime.id]))
        self.assertRedirects(response, reverse('anime_detail', args=[self.anime.id]))
        self.assertTrue(Anime.objects.filter(pk=self.anime.pk).exists())
        self.assertTrue((self.media / 'Show').exists())

    def test_never_deletes_outside_the_media_folder(self):
        for name in ('..', '.'):
            anime = make_anime(f'Odd {name}', ['completed'])
            Anime.objects.filter(pk=anime.pk).update(directory_name=name)
            Anime.objects.get(pk=anime.pk).delete()
            self.assertTrue(self.media.exists())
            self.assertTrue((self.media / 'Show').exists())
