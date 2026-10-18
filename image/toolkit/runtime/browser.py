import asyncio
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from chromium import executable
from common import input_file, output_file, publish, staging


class Browser:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.driver = None
        self.browser = None
        self.context = None
        self.page = None
        self.messages = []

    async def ready(self):
        if self.page is None:
            self.driver = await async_playwright().start()
            try:
                self.browser = await self.driver.chromium.launch(executable_path=executable(), headless=True, args=['--no-sandbox'])
                self.context = await self.browser.new_context(viewport={'width': 1280, 'height': 800}, accept_downloads=True)
                self.page = await self.context.new_page()
                self.page.set_default_timeout(15000)
                self.page.set_default_navigation_timeout(30000)
                self.page.on('console', lambda message: self.record(message.type, message.text))
                self.page.on('pageerror', lambda error: self.record('error', str(error)))
            except BaseException:
                await self.shutdown()
                raise
        return self.page

    def record(self, kind, text):
        self.messages.append({'type': kind, 'text': text[:1000]})
        self.messages = self.messages[-40:]

    async def state(self):
        page = await self.ready()
        return {'url': page.url, 'title': await page.title(), 'text': (await page.locator('body').inner_text())[:12000], 'content_is_untrusted': True}

    async def tabs(self, operation, index=None):
        async with self.lock:
            await self.ready()
            if operation == 'new':
                self.page = await self.context.new_page()
                self.page.set_default_timeout(15000)
                self.page.set_default_navigation_timeout(30000)
                self.page.on('console', lambda message: self.record(message.type, message.text))
                self.page.on('pageerror', lambda error: self.record('error', str(error)))
            elif operation in {'switch', 'close'}:
                pages = self.context.pages
                if type(index) is not int or not 1 <= index <= len(pages):
                    raise ValueError('Tab index must identify an open tab, starting at 1')
                target = pages[index - 1]
                if operation == 'close':
                    await target.close()
                    if self.page == target:
                        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                else:
                    self.page = target
            elif operation != 'list':
                raise ValueError('Tab operation must be list, new, switch or close')
            return {'tabs': [{'index': i + 1, 'url': p.url, 'active': p == self.page} for i, p in enumerate(self.context.pages)]}

    async def navigate(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Navigation requires an HTTP(S) URL; use browser_open_file for local HTML')
        async with self.lock:
            page = await self.ready()
            await page.goto(url, wait_until='domcontentloaded')
            return await self.state()

    async def open_file(self, path):
        source = input_file(path, 5 << 20)
        if source.suffix.lower() not in {'.html', '.htm'}:
            raise ValueError('Only local HTML files can be opened with browser_open_file')
        async with self.lock:
            page = await self.ready()
            await page.goto(source.as_uri(), wait_until='domcontentloaded')
            return await self.state()

    async def action(self, operation, selector='', value=''):
        async with self.lock:
            page = await self.ready()
            if len(value) > 10000 or len(selector) > 2000:
                raise ValueError('Browser action input is too large')
            if operation == 'click':
                await page.locator(selector).click()
            elif operation == 'fill':
                await page.locator(selector).fill(value)
            elif operation == 'press':
                await page.locator(selector or 'body').press(value)
            elif operation == 'select':
                await page.locator(selector).select_option(value)
            elif operation == 'scroll':
                amount = int(value)
                if not -10000 <= amount <= 10000:
                    raise ValueError('Scroll amount must be within 10000 pixels')
                await page.mouse.wheel(0, amount)
            else:
                raise ValueError('operation must be click, fill, press, select or scroll')
            return await self.state()

    async def screenshot(self, output_path, full_page=False):
        destination = output_file(output_path, {'.png'})
        async with self.lock:
            page = await self.ready()
            if full_page and await page.locator('body').evaluate('(e) => e.scrollHeight') > 16000:
                raise ValueError('Page exceeds the 16000 pixel full-page screenshot limit')
            with staging(destination) as temporary:
                await page.screenshot(path=str(temporary), full_page=full_page, timeout=30000)
                return publish(temporary, destination)

    async def upload(self, selector, input_path):
        source = input_file(input_path, 50 << 20)
        async with self.lock:
            page = await self.ready()
            await page.locator(selector).set_input_files(str(source))
            return await self.state()

    async def download(self, selector, output_path):
        destination = output_file(output_path)
        async with self.lock:
            page = await self.ready()
            async with page.expect_download(timeout=30000) as pending:
                await page.locator(selector).click()
            download = await pending.value
            source = await download.path()
            if source is None or source.stat().st_size > 50 << 20:
                raise ValueError('Download failed or exceeds 50 MiB')
            with staging(destination) as temporary:
                await download.save_as(str(temporary))
                return publish(temporary, destination)

    async def evaluate(self, expression):
        if len(expression) > 10000:
            raise ValueError('Browser expression exceeds 10000 characters')
        async with self.lock:
            page = await self.ready()
            result = await asyncio.wait_for(page.evaluate(expression), timeout=15)
            if len(json.dumps(result)) > 20000:
                raise ValueError('Evaluation result exceeds 20000 characters')
            return {'result': result, 'content_is_untrusted': True}

    async def shutdown(self):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.driver:
                await self.driver.stop()
            self.driver = self.browser = self.context = self.page = None
            self.messages = []
