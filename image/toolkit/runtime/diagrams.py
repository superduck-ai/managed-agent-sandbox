import json
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

from chromium import executable
from common import input_file, output_file, publish, run, staging


def render(engine, input_path, output_path):
    if engine not in {'graphviz', 'mermaid', 'd2', 'plantuml', 'typst'}:
        raise ValueError('Unknown render engine')
    source = input_file(input_path, 1 << 20)
    destination = output_file(output_path, {'.png', '.svg', '.pdf'} if engine == 'typst' else {'.png', '.svg'})
    with tempfile.TemporaryDirectory(prefix='oma-render-') as directory, staging(destination) as temporary:
        work = Path(directory)
        if engine == 'graphviz':
            run(['dot', '-T' + destination.suffix[1:], source, '-o', temporary])
        elif engine == 'mermaid':
            config = work / 'puppeteer.json'
            config.write_text(json.dumps({'executablePath': executable(), 'args': ['--no-sandbox']}))
            mermaid = work / 'mermaid.json'
            mermaid.write_text(json.dumps({'securityLevel': 'strict', 'fontFamily': 'Noto Sans CJK SC'}))
            run(['mmdc', '-i', source, '-o', temporary, '-p', config, '-c', mermaid, '-b', 'white'])
        elif engine == 'd2':
            svg = work / 'diagram.svg'
            run(['d2', '--layout=dagre', source, svg])
            if destination.suffix == '.svg':
                temporary.write_bytes(svg.read_bytes())
            else:
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(executable_path=executable(), headless=True, args=['--no-sandbox'])
                    try:
                        page = browser.new_page(viewport={'width': 1280, 'height': 800})
                        page.route('http://**/*', lambda route: route.abort())
                        page.route('https://**/*', lambda route: route.abort())
                        page.goto(svg.as_uri())
                        element = page.locator('svg').first
                        box = element.bounding_box()
                        if not box or max(box['width'], box['height']) > 16000:
                            raise ValueError('D2 diagram exceeds the render size limit')
                        element.screenshot(path=str(temporary), timeout=30000)
                    finally:
                        browser.close()
        elif engine == 'plantuml':
            local = work / 'diagram.puml'
            local.write_bytes(source.read_bytes())
            run(['java', '-DPLANTUML_SECURITY_PROFILE=SECURE', '-jar', '/usr/share/plantuml/plantuml.jar', '-charset', 'UTF-8', '-t' + destination.suffix[1:], local])
            outputs = list(work.glob('*' + destination.suffix))
            if len(outputs) != 1:
                raise ValueError('PlantUML must produce exactly one diagram')
            temporary.write_bytes(outputs[0].read_bytes())
        else:
            run(['typst', 'compile', '--root', source.parent, source, temporary])
        return publish(temporary, destination)
