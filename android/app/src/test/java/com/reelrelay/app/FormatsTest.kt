package com.reelrelay.app

import com.fasterxml.jackson.databind.ObjectMapper
import com.yausername.youtubedl_android.mapper.VideoInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
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
          "formats": [
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
             "width": 1920, "height": 1080, "tbr": 4000, "url": "https://media.example/137.mp4"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "abr": 128, "url": "https://media.example/140.m4a"},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2",
             "width": 640, "height": 360, "tbr": 700, "url": "https://media.example/18.mp4"}
          ]
        }
    """.trimIndent()

    private fun summary(): MediaSummary = buildSummary(mapper.readValue(payload, VideoInfo::class.java))

    @Test
    fun `preview uses the smallest progressive stream`() {
        val preview = summary().preview

        assertNotNull(preview)
        assertEquals("360p MP4", preview?.label)
        assertEquals("https://media.example/18.mp4", preview?.url)
        assertTrue(preview?.headers?.containsKey("User-Agent") == true)
    }

    @Test
    fun `video choices merge video-only formats with audio`() {
        val choices = summary().choices
        val video = choices.filter { it.label.startsWith("Video") }

        assertEquals(2, video.size)
        assertEquals("137+140", video.first { it.label.contains("1080p") }.formatSelector)
        assertTrue(video.first { it.label.contains("1080p") }.mergeToMp4)
        assertEquals("18", video.first { it.label.contains("360p") }.formatSelector)
    }

    @Test
    fun `audio choices are offered after video`() {
        val choices = summary().choices

        assertTrue(choices.any { it.label == "Audio · MP3 320 kbps" && it.extractAudioFormat == "mp3" })
        assertTrue(choices.any { it.label.startsWith("Audio · original stream") })
        assertTrue(choices.last().label.startsWith("Audio"))
    }
}
