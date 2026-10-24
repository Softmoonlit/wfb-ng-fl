"""Independent radio page behavior through the existing Node VM seam."""
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'fl' / 'static'


def test_radio_page_behavior_with_existing_node():
    node = shutil.which('node')
    assert node, 'Existing Node dependency required for page behavioral test'
    result = subprocess.run(
        [node, str(Path(__file__).with_name('radio_page_test.js')),
         str(STATIC / 'radio.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Radio page Node VM:' in result.stdout


def test_radio_page_has_accessible_controls_and_local_assets():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    page = Page()
    page.feed((STATIC / 'radio.html').read_text())
    by_id = {attrs['id']: (tag, attrs) for tag, attrs in page.tags if 'id' in attrs}
    for name in ('channel', 'radio_txpower_dbm', 'downlink_mcs', 'uplink_mcs', 'uftp_rate_kbps', 'confirm-risk'):
        assert by_id[name][0] in ('select', 'input')
        assert any(tag == 'label' and attrs.get('for') == name for tag, attrs in page.tags)
    assert by_id['confirm-risk'][1]['type'] == 'checkbox'
    assert 'checked' not in by_id['confirm-risk'][1]
    assert by_id['validate'][1]['type'] == 'submit'
    for name in ('apply', 'emergency-stop', 'refresh'):
        assert by_id[name][1]['type'] == 'button'
    for name in ('apply', 'validate', 'emergency-stop'):
        assert 'disabled' in by_id[name][1]
    for name in ('connection-error', 'action-error', 'rate-warning', 'stop-error'):
        assert by_id[name][1]['role'] == 'alert'
    for name in ('action-status', 'stop-status', 'confirmation-status'):
        assert by_id[name][1]['role'] == 'status'
    assert any(tag == 'a' and attrs.get('href') == '/' for tag, attrs in page.tags)
    assert any(tag == 'a' and attrs.get('href') == '/models' for tag, attrs in page.tags)
    assets = [attrs[key] for tag, attrs in page.tags
              for key in ('src', 'href') if key in attrs and tag in ('script', 'link')]
    assert '/assets/radio.js' in assets
    assert '/assets/radio.css' in assets
    assert '/assets/console.css' in assets
    for asset in assets:
        assert asset.startswith('/assets/')
        assert (STATIC / asset.removeprefix('/assets/')).is_file()
