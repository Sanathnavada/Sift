from sift.engines.media.instagram.ytdlp_fetcher import YtdlpInstaFetcher


class _Closable:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_ytdlp_instagram_fetcher_closes_all_playwright_resources():
    page = _Closable()
    context = _Closable()
    browser = _Closable()

    YtdlpInstaFetcher._close_browser_resources(page, context, browser)

    assert page.closed is True
    assert context.closed is True
    assert browser.closed is True
