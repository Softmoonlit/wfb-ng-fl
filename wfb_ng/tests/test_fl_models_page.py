"""Packaged model-library UI behavior via Node VM; not real-browser coverage."""
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'fl' / 'static'


def test_models_page_behavior_with_existing_node():
    node = shutil.which('node')
    assert node, 'Existing Node dependency required for page behavioral test'
    result = subprocess.run(
        [node, str(Path(__file__).with_name('models_page_test.js')),
         str(STATIC / 'models.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Models page Node VM:' in result.stdout, result.stdout + result.stderr


def test_models_page_has_accessible_controls_and_local_assets():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    page = Page()
    page.feed((STATIC / 'models.html').read_text())
    by_id = {attrs['id']: (tag, attrs) for tag, attrs in page.tags if 'id' in attrs}
    assert by_id['model-file'][0] == 'input'
    assert by_id['model-file'][1]['type'] == 'file'
    assert 'multiple' not in by_id['model-file'][1]
    assert any(tag == 'label' and attrs.get('for') == 'model-file' for tag, attrs in page.tags)
    assert by_id['upload'][1]['type'] == 'submit'
    assert by_id['refresh-models'][1]['type'] == 'button'
    for name in ('action-error', 'list-error'):
        assert by_id[name][1]['role'] == 'alert'
    assert by_id['action-status'][1]['role'] == 'status'
    assert by_id['models'][0] == 'tbody'
    assert any(tag == 'a' and attrs.get('href') == '/' for tag, attrs in page.tags)
    assets = [attrs[key] for tag, attrs in page.tags
              for key in ('src', 'href') if key in attrs and tag in ('script', 'link')]
    assert '/assets/models.js' in assets
    assert '/assets/console.css' in assets
    for asset in assets:
        assert asset.startswith('/assets/')
        assert (STATIC / asset.removeprefix('/assets/')).is_file()
    assert 'href="/models"' in (STATIC / 'index.html').read_text()
