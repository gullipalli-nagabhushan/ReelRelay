/* Shared browser helpers for ReelRelay. */

(function () {
    'use strict';

    const YOUTUBE_HOSTS = new Set([
        'youtube.com',
        'www.youtube.com',
        'm.youtube.com',
        'music.youtube.com',
        'youtube-nocookie.com',
        'www.youtube-nocookie.com',
        'youtu.be',
        'www.youtu.be'
    ]);

    const INSTAGRAM_HOSTS = new Set([
        'instagram.com',
        'www.instagram.com',
        'm.instagram.com',
        'instagr.am',
        'www.instagr.am'
    ]);

    const FACEBOOK_HOSTS = new Set([
        'facebook.com',
        'www.facebook.com',
        'm.facebook.com',
        'web.facebook.com',
        'mbasic.facebook.com',
        'fb.watch',
        'www.fb.watch'
    ]);

    const SHORT_PREFIXES = ['/embed/', '/v/', '/shorts/', '/live/'];
    const INSTAGRAM_PREFIXES = ['/p/', '/reel/', '/reels/', '/tv/', '/stories/'];
    const FACEBOOK_HINTS = ['/watch', '/video.php', '/videos/', '/reel/', '/reels/', '/share/', '/stories/'];

    function parseUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return null;
        try {
            return new URL(/^https?:\/\//i.test(raw) ? raw : 'https://' + raw);
        } catch (error) {
            return null;
        }
    }

    function isYouTubeUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return false;
        if (/^[A-Za-z0-9_-]{11}$/.test(raw)) return true;

        const parsed = parseUrl(raw);
        if (!parsed) return false;
        if (!/^https?:$/.test(parsed.protocol)) return false;
        if (!YOUTUBE_HOSTS.has(parsed.hostname.toLowerCase())) return false;

        if (parsed.hostname.toLowerCase().endsWith('youtu.be')) {
            return /^[A-Za-z0-9_-]{11}$/.test(parsed.pathname.split('/').filter(Boolean)[0] || '');
        }

        if (parsed.pathname === '/watch' || parsed.pathname === '/watch/') {
            const id = parsed.searchParams.get('v') || '';
            return /^[A-Za-z0-9_-]{11}$/.test(id);
        }

        if (SHORT_PREFIXES.some((prefix) => parsed.pathname.startsWith(prefix))) {
            const parts = parsed.pathname.split('/').filter(Boolean);
            return /^[A-Za-z0-9_-]{11}$/.test(parts[1] || '');
        }

        return false;
    }

    function isInstagramUrl(value) {
        const parsed = parseUrl(value);
        if (!parsed) return false;
        if (!/^https?:$/.test(parsed.protocol)) return false;
        if (!INSTAGRAM_HOSTS.has(parsed.hostname.toLowerCase())) return false;
        const path = parsed.pathname.toLowerCase();
        if (!INSTAGRAM_PREFIXES.some((prefix) => path.startsWith(prefix))) return false;
        return path.split('/').filter(Boolean).length >= 2;
    }

    function isFacebookUrl(value) {
        const parsed = parseUrl(value);
        if (!parsed) return false;
        if (!/^https?:$/.test(parsed.protocol)) return false;
        const host = parsed.hostname.toLowerCase();
        if (!FACEBOOK_HOSTS.has(host)) return false;
        const path = parsed.pathname.toLowerCase();
        if (host.endsWith('fb.watch')) {
            return path.split('/').filter(Boolean).length >= 1;
        }
        if (FACEBOOK_HINTS.some((hint) => path.includes(hint))) return true;
        return Boolean(parsed.searchParams.get('v'));
    }

    function detectPlatform(value) {
        if (isYouTubeUrl(value)) return 'youtube';
        if (isInstagramUrl(value)) return 'instagram';
        if (isFacebookUrl(value)) return 'facebook';
        return null;
    }

    function isSupportedUrl(value) {
        return detectPlatform(value) !== null;
    }

    const ICONS = {
        info: 'bi-info-circle',
        success: 'bi-check-circle',
        warning: 'bi-exclamation-triangle',
        danger: 'bi-x-octagon'
    };

    function stack() {
        let container = document.getElementById('toastStack');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toastStack';
            container.className = 'toast-stack';
            container.setAttribute('aria-live', 'polite');
            document.body.appendChild(container);
        }
        return container;
    }

    function toast(message, type = 'info', timeout = 6000) {
        const container = stack();
        const item = document.createElement('div');
        item.className = 'toast-item toast-' + (ICONS[type] ? type : 'info');
        item.setAttribute('role', type === 'danger' ? 'alert' : 'status');

        const icon = document.createElement('i');
        icon.className = 'bi ' + (ICONS[type] || ICONS.info);

        const text = document.createElement('span');
        text.className = 'toast-message';
        text.textContent = message;

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.innerHTML = '&times;';

        let removed = false;
        const remove = () => {
            if (removed) return;
            removed = true;
            item.classList.add('is-leaving');
            setTimeout(() => item.remove(), 220);
        };

        close.addEventListener('click', remove);
        item.append(icon, text, close);
        container.appendChild(item);

        if (timeout > 0) {
            setTimeout(remove, timeout);
        }
        return item;
    }

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (error) {
            return false;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (window.bootstrap && window.bootstrap.Tooltip) {
            document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((element) => {
                new window.bootstrap.Tooltip(element);
            });
        }

        document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
            anchor.addEventListener('click', (event) => {
                const id = anchor.getAttribute('href');
                if (!id || id === '#') return;
                const target = document.querySelector(id);
                if (!target) return;
                event.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });

        // Never leave the loading overlay up when navigating back/forward.
        window.addEventListener('pageshow', () => {
            const loader = document.getElementById('pageLoader');
            if (loader) loader.hidden = true;
        });
    });

    window.ReelRelay = {
        isSupportedUrl,
        detectPlatform,
        isYouTubeUrl,
        toast,
        copyText
    };
})();
