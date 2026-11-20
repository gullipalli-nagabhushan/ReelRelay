package com.reelrelay.app

import com.fasterxml.jackson.databind.ObjectMapper
import com.yausername.youtubedl_android.mapper.VideoInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaylistTest {

    private val mapper = ObjectMapper()

    private val ytPayload = """
        {
          "title": "Sample",
          "duration": 200,
          "thumbnail": "https://i.example/sample.jpg",
          "webpage_url": "https://www.youtube.com/watch?v=abc",
          "extractor": "youtube",
          "formats": [
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
             "width": 1920, "height": 1080, "tbr": 4000, "url": "https://m/137.mp4"},
            {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none",
             "width": 1280, "height": 720, "tbr": 2500, "url": "https://m/136.mp4"},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2",
             "width": 640, "height": 360, "tbr": 700, "url": "https://m/18.mp4"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "abr": 128, "url": "https://m/140.m4a"}
          ]
        }
    """.trimIndent()

    private fun sample(): MediaSummary =
        buildSummary(mapper.readValue(ytPayload, VideoInfo::class.java))

    private val flatPlaylist = """
        {
          "_type": "playlist",
          "title": "Road trip",
          "uploader": "Someone",
          "entries": [
            {"id": "a1", "title": "First", "duration": 61, "url": "a1",
             "webpage_url": "https://www.youtube.com/watch?v=a1"},
            {"id": "a2", "title": "Second", "duration": 3725, "url": "a2",
             "webpage_url": "https://www.youtube.com/watch?v=a2"},
            {"id": "a3", "title": "Third", "url": "a3"}
          ]
        }
    """.trimIndent()

    @Test
    fun `playlist links are recognised`() {
        assertTrue(isPlaylistUrl("https://www.youtube.com/playlist?list=PL123"))
        assertTrue(isPlaylistUrl("https://www.youtube.com/watch?v=abc&list=PL123"))
        assertTrue(isPlaylistUrl("https://youtu.be/abc?list=PL123"))
        assertTrue(isPlaylistUrl("https://www.facebook.com/watch/playlist/123"))
        assertFalse(isPlaylistUrl("https://www.youtube.com/watch?v=abc"))
        assertFalse(isPlaylistUrl("https://youtu.be/abc"))
        assertFalse(isPlaylistUrl("https://www.instagram.com/reel/ABC/"))
        assertFalse(isPlaylistUrl("not a url"))
        assertFalse(isPlaylistUrl("https://www.youtube.com/watch?v=abc&list="))
    }

    @Test
    fun `flat yt-dlp output becomes ordered entries`() {
        val listing = parsePlaylist(flatPlaylist, "https://www.youtube.com/playlist?list=PL123")

        assertNotNull(listing)
        assertEquals("Road trip", listing?.title)
        assertEquals("Someone", listing?.uploader)
        assertEquals(3, listing?.entries?.size)
        assertEquals("First", listing?.entries?.first()?.title)
        assertEquals("https://www.youtube.com/watch?v=a1", listing?.entries?.first()?.url)
        assertEquals("1:01", listing?.entries?.first()?.durationLabel)
        assertEquals("1:02:05", listing?.entries?.get(1)?.durationLabel)
        assertEquals("https://www.youtube.com/watch?v=a3", listing?.entries?.last()?.url)
        assertFalse(listing!!.truncated)
    }

    @Test
    fun `playlist listings are capped but keep the true total`() {
        val listing = parsePlaylist(
            flatPlaylist,
            "https://www.youtube.com/playlist?list=PL123",
            limit = 2,
        )

        assertEquals(2, listing?.entries?.size)
        assertEquals(3, listing?.total)
        assertTrue(listing!!.truncated)
    }

    @Test
    fun `line delimited flat output is parsed`() {
        val jsonl = listOf(
            """{"_type":"url","id":"x1","title":"One","duration":61,"url":"x1",""" +
                """"webpage_url":"https://www.youtube.com/watch?v=x1",""" +
                """"playlist_title":"Mix","playlist_uploader":"Chan"}""",
            """{"_type":"url","id":"x2","title":"Two","url":"x2",""" +
                """"webpage_url":"https://www.youtube.com/watch?v=x2",""" +
                """"playlist_title":"Mix","playlist_uploader":"Chan"}""",
        ).joinToString("\n")

        val listing = parsePlaylist(jsonl, "https://www.youtube.com/playlist?list=PL1")

        assertNotNull(listing)
        assertEquals("Mix", listing?.title)
        assertEquals("Chan", listing?.uploader)
        assertEquals(listOf("x1", "x2"), listing?.entries?.map { it.url.substringAfter("v=") })
        assertEquals(2, listing?.total)
    }

    @Test
    fun `a single video payload is not a playlist`() {
        val single = """{"title": "Just a video", "formats": []}"""
        assertNull(parsePlaylist(single, "https://www.youtube.com/watch?v=abc"))
        assertNull(parsePlaylist("", "https://www.youtube.com/watch?v=abc"))
        assertNull(parsePlaylist("not json", "https://www.youtube.com/watch?v=abc"))
    }

    @Test
    fun `entries without a usable link are skipped`() {
        val json = """
            {
              "title": "Mixed",
              "entries": [
                {"id": "1", "title": "No link"},
                {"id": "2", "title": "Has link", "webpage_url": "https://example.com/v/2"},
                null
              ]
            }
        """.trimIndent()

        val listing = parsePlaylist(json, "https://example.com/playlist?list=1")

        assertEquals(1, listing?.entries?.size)
        assertEquals("https://example.com/v/2", listing?.entries?.first()?.url)
    }

    @Test
    fun `playlist rows describe one quality rule per video`() {
        val choices = buildPlaylistChoices(sample())
        val video = choices.filter { it.mergeTo != null }

        assertEquals("Best available · MP4", video.first().label)
        assertEquals("bv*+ba/b", video.first().formatSelector)
        assertEquals(listOf(1080, 720, 360), video.drop(1).map { it.height })
        assertEquals(
            listOf(
                "bv*[height<=1080]+ba/b[height<=1080]/b",
                "bv*[height<=720]+ba/b[height<=720]/b",
                "bv*[height<=360]+ba/b[height<=360]/b",
            ),
            video.drop(1).map { it.formatSelector },
        )
        assertEquals("1080p · Full HD · MP4", video[1].label)
        assertTrue(video[1].recommended)
        assertFalse(video.first().recommended)

        // Rows are rules, not resolved streams: yt-dlp handles each entry.
        assertTrue(video.all { it.video == null && it.audio == null })
    }

    @Test
    fun `playlist audio rows convert every video`() {
        val choices = buildPlaylistChoices(sample())

        val original = choices.first { it.label == "Original audio · best" }
        assertEquals("ba/b", original.formatSelector)
        assertNull(original.extractAudioFormat)

        val profiles = choices.filter { it.extractAudioFormat != null }
        assertEquals(
            listOf("MP3 · 320 kbps", "MP3 · 192 kbps", "M4A · AAC 256 kbps", "FLAC · Lossless", "WAV · PCM 16-bit"),
            profiles.map { it.label },
        )
        assertEquals(listOf(320, 192, 256, 0, 0), profiles.map { it.extractAudioBitrate })
        assertEquals(listOf("mp3", "mp3", "m4a", "flac", "wav"), profiles.map { it.extractAudioFormat })
        assertTrue(profiles.all { it.formatSelector == "ba/b" })

        // With no sample, "original" is hidden because it cannot be verified.
        val withoutSample = buildPlaylistChoices(null)
        assertTrue(withoutSample.none { it.label == "Original audio · best" })
        assertTrue(withoutSample.any { it.height == 1080 && it.recommended })
    }

    @Test
    fun `playlist summary explains the queue`() {
        val listing = parsePlaylist(flatPlaylist, "https://www.youtube.com/playlist?list=PL123")!!
        val summary = buildPlaylistSummary(listing, sample())

        assertEquals("Road trip", summary.title)
        assertEquals("3 videos · downloads one by one", summary.durationLabel)
        assertNull(summary.preview)
        assertEquals("https://www.youtube.com/playlist?list=PL123", summary.webpageUrl)
        assertNotNull(summary.thumbnail)
        assertTrue(summary.choices.isNotEmpty())
    }

    @Test
    fun `truncated playlists say how many are queued`() {
        val listing = parsePlaylist(
            flatPlaylist,
            "https://www.youtube.com/playlist?list=PL123",
            limit = 2,
        )!!

        assertEquals(
            "2 of 3 videos (first 2) · downloads one by one",
            buildPlaylistSummary(listing, null).durationLabel,
        )
    }
}
