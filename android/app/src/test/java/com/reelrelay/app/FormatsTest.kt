package com.reelrelay.app

import com.fasterxml.jackson.databind.ObjectMapper
import com.yausername.youtubedl_android.mapper.VideoInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FormatsTest {

    private val mapper = ObjectMapper()

    private val payload = """
        {
          "title": "Test video",
          "uploader": "Someone",
          "duration": 213,
          "thumbnail": "https://cdn.example/thumb.jpg",
          "webpage_url": "https://youtu.be/abc123",
          "extractor": "youtube",
          "formats": [
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
             "width": 1920, "height": 1080, "tbr": 4000, "url": "https://media.example/137.mp4"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "abr": 128, "url": "https://media.example/140.m4a"},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2",
             "width": 640, "height": 360, "tbr": 700, "url": "https://media.example/18.mp4"},
            {"format_id": "hls-720", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none",
             "width": 1280, "height": 720, "tbr": 2500,
             "url": "https://media.example/master.m3u8",
             "manifest_url": "https://media.example/master.m3u8"}
          ]
        }
    """.trimIndent()

    private fun summary(json: String = payload): MediaSummary =
        buildSummary(mapper.readValue(json, VideoInfo::class.java))

    @Test
    fun `preview uses a browser-playable progressive stream`() {
        val preview = summary().preview

        assertNotNull(preview)
        assertEquals("360p MP4", preview?.label)
        assertEquals("https://media.example/18.mp4", preview?.url)
        assertTrue(preview?.headers?.containsKey("User-Agent") == true)
    }

    @Test
    fun `1080p H264 is recommended and merges with audio`() {
        val choices = summary().choices

        assertEquals("1080p · H.264 · MP4", choices.first().label)
        assertTrue(choices.first().recommended)
        assertEquals("137+140", choices.first().formatSelector)
        assertEquals("mp4", choices.first().mergeTo)
        assertTrue(choices.first().detail.contains("Full HD"))
        assertTrue(choices.first().detail.contains("H.264 video + AAC audio"))
    }

    @Test
    fun `merged choices carry direct stream sources and sizes`() {
        val merged = summary().choices.first { it.label.startsWith("1080p") }

        assertEquals("137", merged.video?.formatId)
        assertEquals("https://media.example/137.mp4", merged.video?.url)
        assertEquals("140", merged.audio?.formatId)
        assertEquals("https://media.example/140.m4a", merged.audio?.url)
        assertTrue(merged.video?.headers?.containsKey("User-Agent") == true)
        assertTrue(merged.video!!.size > 0L)
    }

    @Test
    fun `progressive choices carry only a video source and keep their file`() {
        val progressive = summary().choices.first { it.label.startsWith("360p") }

        assertEquals("18", progressive.formatSelector)
        assertEquals("https://media.example/18.mp4", progressive.video?.url)
        assertNull(progressive.audio)
        assertNull(progressive.mergeTo)
        assertTrue(progressive.detail.contains("Single file with audio"))
    }

    @Test
    fun `manifest formats remain selectable but fall back to yt-dlp`() {
        val manifest = summary().choices.first { it.label.startsWith("720p") }

        assertEquals("hls-720+140", manifest.formatSelector)
        assertEquals("mp4", manifest.mergeTo)
        assertNull(manifest.video)
    }

    @Test
    fun `audio choices mirror the desktop profiles`() {
        val audio = summary().choices.filter { it.extractAudioFormat != null || it.label.startsWith("Original audio") }

        assertTrue(audio.any { it.label == "Original audio · M4A" })
        assertEquals(
            listOf("MP3 · 320 kbps", "MP3 · 192 kbps", "M4A · AAC 256 kbps", "FLAC · Lossless", "WAV · PCM 16-bit"),
            audio.map { it.label }.filter { it != "Original audio · M4A" },
        )
        assertEquals(listOf(320, 192, 256, 0, 0), audio.filter { it.extractAudioFormat != null }.map { it.extractAudioBitrate })
        assertEquals(listOf("mp3", "mp3", "m4a", "flac", "wav"), audio.filter { it.extractAudioFormat != null }.map { it.extractAudioFormat })
        audio.forEach { assertEquals("https://media.example/140.m4a", it.audio?.url) }
    }

    @Test
    fun `up to three codec families are offered per height`() {
        val json = """
            {
              "title": "4K",
              "duration": 60,
              "webpage_url": "https://youtu.be/abc",
              "extractor": "youtube",
              "formats": [
                {"format_id": "v1", "ext": "mp4", "vcodec": "avc1.640033", "acodec": "none", "height": 2160, "width": 3840, "tbr": 20000, "url": "https://m/1.mp4"},
                {"format_id": "v2", "ext": "mp4", "vcodec": "hev1.1.6.L150", "acodec": "none", "height": 2160, "width": 3840, "tbr": 15000, "url": "https://m/2.mp4"},
                {"format_id": "v3", "ext": "mp4", "vcodec": "av01.0.12M.08", "acodec": "none", "height": 2160, "width": 3840, "tbr": 12000, "url": "https://m/3.mp4"},
                {"format_id": "v4", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 2160, "width": 3840, "tbr": 10000, "url": "https://m/4.webm"},
                {"format_id": "a1", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "url": "https://m/a1.m4a"},
                {"format_id": "a2", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160, "url": "https://m/a2.webm"}
              ]
            }
        """.trimIndent()

        val labels = summary(json).choices.map { it.label }.filter { it.contains("p · ") }

        assertEquals(3, labels.size)
        assertTrue(labels[0].contains("H.264"))
        assertTrue(labels[1].contains("H.265"))
        assertTrue(labels[2].contains("AV1"))
        assertTrue(labels.none { it.contains("VP9") })
        assertTrue(labels.all { it.contains("4K") || it.startsWith("2160p") })
    }

    @Test
    fun `fps HDR and portrait badges describe the stream`() {
        val json = """
            {
              "title": "Short",
              "duration": 15,
              "webpage_url": "https://www.youtube.com/shorts/abc",
              "extractor": "youtube",
              "formats": [
                {"format_id": "v", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
                 "width": 1080, "height": 1920, "fps": 60, "format_note": "HDR",
                 "tbr": 6000, "url": "https://m/v.mp4"},
                {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
                 "abr": 128, "url": "https://m/a.m4a"}
              ]
            }
        """.trimIndent()

        val choice = summary(json).choices.first { it.label.startsWith("1080p") }

        assertTrue(choice.detail.contains("HDR"))
        assertTrue(choice.detail.contains("60 fps"))
        assertTrue(choice.detail.contains("Portrait"))
        assertNotNull(choice.video)
    }

    @Test
    fun `instagram reels expose a progressive video and extracted audio`() {
        val json = """
            {
              "title": "Reel",
              "duration": 30,
              "webpage_url": "https://www.instagram.com/reel/ABC/",
              "extractor": "instagram",
              "formats": [
                {"format_id": "http-720", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2",
                 "width": 720, "height": 1280, "tbr": 1800, "url": "https://scontent.example/reel.mp4",
                 "http_headers": {"Referer": "https://www.instagram.com/", "Cookie": "sessionid=1"}}
              ]
            }
        """.trimIndent()

        val summary = summary(json)
        val video = summary.choices.filter { it.video != null }
        val audio = summary.choices.filter { it.extractAudioFormat != null }

        assertEquals(1, video.size)
        assertEquals("720p · H.264 · MP4", video.first().label)
        assertTrue(video.first().recommended)
        assertNull(video.first().mergeTo)
        assertEquals("http-720", video.first().formatSelector)
        assertTrue(video.first().detail.contains("Portrait"))

        assertEquals(5, audio.size)
        assertTrue(audio.all { it.audio?.url == "https://scontent.example/reel.mp4" })
        assertTrue(audio.all { it.detail.contains("Extracted from the video") })

        val headers = video.first().video!!.headers
        assertEquals("https://www.instagram.com/", headers["Referer"])
        assertEquals("https://www.instagram.com", headers["Origin"])
        assertEquals("sessionid=1", headers["Cookie"])
    }

    @Test
    fun `facebook videos send the platform headers the CDN expects`() {
        val json = """
            {
              "title": "FB video",
              "duration": 42,
              "webpage_url": "https://www.facebook.com/watch/?v=123",
              "extractor": "facebook",
              "formats": [
                {"format_id": "sd", "ext": "mp4", "vcodec": "avc1.4d401e", "acodec": "mp4a.40.2",
                 "width": 640, "height": 360, "tbr": 800, "url": "https://video.example/fb.mp4"}
              ]
            }
        """.trimIndent()

        val summary = summary(json)
        val preview = summary.preview

        assertNotNull(preview)
        assertEquals("https://www.facebook.com/", preview?.headers?.get("Referer"))
        assertEquals("https://www.facebook.com", preview?.headers?.get("Origin"))
        assertEquals("en-US,en;q=0.9", preview?.headers?.get("Accept-Language"))
    }

    @Test
    fun `unknown extractors still get a user agent but no platform referer`() {
        val json = """
            {
              "title": "Site",
              "duration": 10,
              "webpage_url": "https://example.com/v/1",
              "extractor": "generic",
              "formats": [
                {"format_id": "1", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
                 "width": 640, "height": 360, "tbr": 500, "url": "https://example.com/v.mp4"}
              ]
            }
        """.trimIndent()

        val source = summary(json).choices.first { it.video != null }.video!!

        assertTrue(source.headers["User-Agent"]!!.contains("Mozilla"))
        assertFalse(source.headers.containsKey("Referer"))
        assertEquals("https://example.com/v.mp4", source.url)
    }

    @Test
    fun `friendly errors explain login-locked posts`() {
        val login = friendlyError("ERROR: [Instagram] ABC: Login required", hasCookies = false)
        assertTrue(login!!.contains("logged-in"))
        assertTrue(login.contains("cookies.txt"))

        val expired = friendlyError("ERROR: Requested content is not available", hasCookies = true)
        assertEquals("ERROR: Requested content is not available", expired)
    }

    @Test
    fun `friendly errors cover rate limits and private posts`() {
        assertTrue(
            friendlyError("HTTP Error 429: Too Many Requests", false)!!.contains("rate limiting"),
        )
        assertTrue(
            friendlyError("This account is private", false)!!.contains("private"),
        )
        assertTrue(
            friendlyError("ERROR: Unsupported URL: https://example.com", false)!!.contains("not supported"),
        )
        assertTrue(
            friendlyError("This live event will begin in 2 hours", false)!!.contains("Live"),
        )
        assertNull(friendlyError("   ", false))
    }
}
