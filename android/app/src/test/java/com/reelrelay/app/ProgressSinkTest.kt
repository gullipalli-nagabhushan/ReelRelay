package com.reelrelay.app

import org.junit.Assert.assertEquals
import org.junit.Test

class ProgressSinkTest {

    private fun sink(onValue: (Float) -> Unit = {}): ProgressSink =
        ProgressSink { value, _, _ -> onValue(value) }

    @Test
    fun `progress never walks backwards`() {
        val seen = mutableListOf<Float>()
        val progress = sink(seen::add)

        progress.report(0f)
        progress.report(25f)
        progress.report(60f)
        progress.report(40f)
        progress.report(75f)
        progress.report(100f)

        assertEquals(listOf(25f, 60f, 75f, 99f), seen)
        assertEquals(99f, progress.progress, 0.001f)
    }

    @Test
    fun `fallback continues from where the accelerated attempt stopped`() {
        val seen = mutableListOf<Float>()
        val progress = sink(seen::add)

        progress.report(84f)
        progress.beginFallback()
        progress.report(0f)
        progress.report(50f)
        progress.report(100f)

        // 84% was already shown, so the fallback resumes just below it instead
        // of restarting at zero or freezing until it catches up.
        assertEquals(listOf(84f, 89.5f, 99f), seen)
    }

    @Test
    fun `a fallback that starts immediately keeps the full range`() {
        val seen = mutableListOf<Float>()
        val progress = sink(seen::add)

        progress.beginFallback()
        progress.report(0f)
        progress.report(50f)

        assertEquals(listOf(49.5f), seen)
    }

    @Test
    fun `values outside the range are clamped`() {
        val seen = mutableListOf<Float>()
        val progress = sink(seen::add)

        progress.report(-20f)
        progress.report(250f)

        assertEquals(listOf(99f), seen)
    }

    @Test
    fun `status updates are forwarded even when the percentage holds`() {
        val seen = mutableListOf<Float>()
        val progress = ProgressSink { value, _, line ->
            if (line.isNotBlank()) seen += value
        }

        progress.report(30f, line = "Downloading video")
        progress.report(30f, line = "Downloading audio")

        assertEquals(listOf(30f, 30f), seen)
    }
}
