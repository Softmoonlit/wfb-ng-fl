"""模型库通过 application service 的制品行为测试。"""
import io
import pytest

from wfb_ng.fl.model_library import ModelLibrary, ModelLibraryError

ABC_SHA256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'


def test_upload_identity_deduplication_and_restart(tmp_path):
    library = ModelLibrary(tmp_path)
    first = library.upload(io.BytesIO(b'abc'), 3, '模型.bin')
    assert first['model']['sha256'] == ABC_SHA256
    assert first['model']['filename'] == '模型.bin'
    assert first['model']['size_bytes'] == 3
    assert first['model']['referenced'] is False
    assert first['model']['created_at']
    assert first['deduplicated'] is False
    duplicate = library.upload(io.BytesIO(b'abc'), 3, 'another.bin')
    assert duplicate == {'model': first['model'], 'deduplicated': True}
    assert ModelLibrary(tmp_path).list_models() == [first['model']]
    assert library.artifact_path(ABC_SHA256).read_bytes() == b'abc'


def test_streaming_hash_across_chunks_and_list_order(tmp_path):
    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            return super().read(min(size, 2))
    library = ModelLibrary(tmp_path)
    first = library.upload(BoundedReader(b'abc'), 3, 'first.bin')
    second = library.upload(BoundedReader(b'hello'), 5, 'second.bin')
    assert first['model']['sha256'] == ABC_SHA256
    assert second['model']['sha256'] == '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    assert [model['filename'] for model in library.list_models()] == ['second.bin', 'first.bin']


def test_second_upload_is_busy_without_blocking_list_or_deletion(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    receiving, release = threading.Event(), threading.Event()
    class PausedReader(io.BytesIO):
        def read(self, size=-1):
            receiving.set()
            assert release.wait(3)
            return super().read(size)
    library = ModelLibrary(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(library.upload, PausedReader(b'abc'), 3, 'one.bin')
        try:
            assert receiving.wait(3)
            assert library.list_models() == []
            with pytest.raises(ModelLibraryError) as error:
                library.upload(io.BytesIO(b'abc'), 3, 'two.bin')
            assert error.value.code == 'MODEL_UPLOAD_BUSY'
        finally:
            release.set()
        assert first.result()['model']['sha256'] == ABC_SHA256
    assert library.upload(io.BytesIO(b'abc'), 3, 'two.bin')['deduplicated'] is True
    assert len(list(tmp_path.iterdir())) == 1


def test_storage_unavailable_does_not_prevent_service_construction(tmp_path):
    root = tmp_path / 'not-a-directory'
    root.write_bytes(b'occupied')
    library = ModelLibrary(root)
    with pytest.raises(ModelLibraryError) as error:
        library.list_models()
    assert error.value.code == 'MODEL_STORAGE_FAILED'
    root.unlink()
    assert library.list_models() == []


@pytest.mark.parametrize('size,code', [(0, 'MODEL_EMPTY'), (1024**3 + 1, 'MODEL_TOO_LARGE')])
def test_invalid_size_rejected_and_next_upload_works(tmp_path, size, code):
    library = ModelLibrary(tmp_path)
    with pytest.raises(ModelLibraryError) as error:
        library.upload(io.BytesIO(), size, 'bad.bin')
    assert error.value.code == code
    assert library.list_models() == []
    assert library.upload(io.BytesIO(b'abc'), 3, 'good.bin')['model']['sha256'] == ABC_SHA256


def test_interrupted_upload_releases_capacity_and_cleans_disk(tmp_path):
    library = ModelLibrary(tmp_path)
    library.MAX_LIBRARY_BYTES = 3
    with pytest.raises(ModelLibraryError) as error:
        library.upload(io.BytesIO(b'a'), 3, 'partial.bin')
    assert error.value.code == 'UPLOAD_INTERRUPTED'
    assert library.list_models() == []
    assert list(tmp_path.iterdir()) == []
    library.upload(io.BytesIO(b'abc'), 3, 'complete.bin')
    with pytest.raises(ModelLibraryError) as error:
        library.upload(io.BytesIO(b'x'), 1, 'overflow.bin')
    assert error.value.code == 'MODEL_CAPACITY_EXCEEDED'
    assert len(library.list_models()) == 1


def test_duplicate_succeeds_even_when_library_is_full(tmp_path):
    library = ModelLibrary(tmp_path)
    library.MAX_LIBRARY_BYTES = 3
    first = library.upload(io.BytesIO(b'abc'), 3, 'model.bin')
    assert library.upload(io.BytesIO(b'abc'), 3, 'duplicate.bin') == dict(first, deduplicated=True)
    assert len(library.list_models()) == 1


def test_application_owns_job_reference_protection(tmp_path):
    from wfb_ng.tests.test_fl_console import make_daemon
    daemon = make_daemon(model_library_dir=str(tmp_path))
    application = daemon.console
    application.upload_model(io.BytesIO(b'abc'), 3, 'model.bin')
    daemon.active_job = {'job_id':'job', 'model_sha256':ABC_SHA256}
    assert application.list_models()['models'][0]['referenced'] is True
    with pytest.raises(ModelLibraryError) as error:
        application.delete_model(ABC_SHA256)
    assert error.value.code == 'MODEL_REFERENCED'
    assert application.models.artifact_path(ABC_SHA256).read_bytes() == b'abc'
    daemon.active_job = None
    application.delete_model(ABC_SHA256)
    assert ModelLibrary(tmp_path).list_models() == []


def test_disk_write_failure_cleans_reservation_and_allows_retry(tmp_path, monkeypatch):
    from pathlib import Path
    library = ModelLibrary(tmp_path)
    library.MAX_LIBRARY_BYTES = 3
    original = Path.open
    def fail_content(path, *args, **kwargs):
        if path.name == 'content':
            raise OSError('disk full')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(Path, 'open', fail_content)
        with pytest.raises(ModelLibraryError) as error:
            library.upload(io.BytesIO(b'abc'), 3, 'model.bin')
    assert error.value.code == 'MODEL_STORAGE_FAILED'
    assert list(tmp_path.iterdir()) == []
    assert library.upload(io.BytesIO(b'abc'), 3, 'retry.bin')['model']['sha256'] == ABC_SHA256


@pytest.mark.parametrize('filename', ['', '.', '..', '../model', 'a/b', 'a\\b', 'a\x00b', '模' * 86])
def test_display_filename_is_utf8_bounded_and_path_free(tmp_path, filename):
    library = ModelLibrary(tmp_path)
    with pytest.raises(ModelLibraryError) as error:
        library.upload(io.BytesIO(b'abc'), 3, filename)
    assert error.value.code == 'INVALID_FILENAME'


def test_failed_cleanup_blocks_new_uploads_until_disk_recovers(tmp_path, monkeypatch):
    import shutil
    library = ModelLibrary(tmp_path)
    library.MAX_LIBRARY_BYTES = 3
    def fail_cleanup(path):
        raise OSError('temporary filesystem failure')
    with monkeypatch.context() as patcher:
        patcher.setattr(shutil, 'rmtree', fail_cleanup)
        with pytest.raises(ModelLibraryError):
            library.upload(io.BytesIO(b'a'), 3, 'interrupted.bin')
        with pytest.raises(ModelLibraryError) as error:
            library.upload(io.BytesIO(b'abc'), 3, 'retry.bin')
        assert error.value.code == 'MODEL_STORAGE_FAILED'
    assert library.upload(io.BytesIO(b'abc'), 3, 'retry.bin')['model']['sha256'] == ABC_SHA256
    assert len(list(tmp_path.iterdir())) == 1


def test_delete_cleanup_failure_is_explicit_and_retry_completes(tmp_path, monkeypatch):
    import shutil
    library = ModelLibrary(tmp_path)
    library.upload(io.BytesIO(b'abc'), 3, 'model.bin')
    with monkeypatch.context() as patcher:
        patcher.setattr(shutil, 'rmtree', lambda path: (_ for _ in ()).throw(OSError('disk failure')))
        with pytest.raises(ModelLibraryError) as error:
            library.delete(ABC_SHA256)
        assert error.value.code == 'MODEL_DELETE_CLEANUP_FAILED'
        assert '已删除' in str(error.value)
        with pytest.raises(ModelLibraryError) as retry_error:
            library.delete(ABC_SHA256)
        assert retry_error.value.code == 'MODEL_DELETE_CLEANUP_FAILED'
        restarted = ModelLibrary(tmp_path)
        with pytest.raises(ModelLibraryError) as restart_error:
            restarted.delete(ABC_SHA256)
        assert restart_error.value.code == 'MODEL_DELETE_CLEANUP_FAILED'
    restarted.delete(ABC_SHA256)
    library.delete(ABC_SHA256)
    assert library.list_models() == []
    assert list(tmp_path.iterdir()) == []


def test_restart_rejects_same_size_content_corruption(tmp_path):
    library = ModelLibrary(tmp_path)
    library.upload(io.BytesIO(b'abc'), 3, 'model.bin')
    library.artifact_path(ABC_SHA256).write_bytes(b'xyz')
    restarted = ModelLibrary(tmp_path)
    with pytest.raises(ModelLibraryError) as error:
        restarted.list_models()
    assert error.value.code == 'MODEL_STORAGE_FAILED'


def test_fsync_failure_prevents_publication_and_allows_retry(tmp_path, monkeypatch):
    import os
    library = ModelLibrary(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('fsync failed')))
        with pytest.raises(ModelLibraryError) as error:
            library.upload(io.BytesIO(b'abc'), 3, 'model.bin')
        assert error.value.code == 'MODEL_STORAGE_FAILED'
    assert library.list_models() == []
    assert library.upload(io.BytesIO(b'abc'), 3, 'retry.bin')['model']['sha256'] == ABC_SHA256
