package com.reelrelay.app

import org.junit.Assert.assertEquals
import org.junit.Test

class CoverArtTest {

    @Test
    fun `landscape thumbnail is cropped to its centre square`() {
        // A 1280x720 YouTube thumbnail with pillarboxed vertical artwork.
        assertEquals(Triple(280, 0, 720), centreSquare(1280, 720))
    }

    @Test
    fun `portrait image is cropped to its centre square`() {
        assertEquals(Triple(0, 280, 720), centreSquare(720, 1280))
    }

    @Test
    fun `square image is left whole`() {
        assertEquals(Triple(0, 0, 720), centreSquare(720, 720))
    }
}
