"""Listing photos from the data folder, or from Azure Blob Storage.

The last test needs Azurite, Azure's storage emulator, on 127.0.0.1:10000, and is skipped
otherwise. To start it:

    docker run -d -p 10000:10000 mcr.microsoft.com/azure-storage/azurite:3.37.0 \
        azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck
"""

from uuid import uuid4

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from pydantic import SecretStr

from listingaudit.config import settings
from listingaudit.photos import store

# Azurite's development account, the same in every installation
AZURITE = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/"
    "KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
PHOTO = "images/test/0001.jpg"


@pytest.fixture(autouse=True)
def data_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store.container.cache_clear()
    return tmp_path


class FakeContainer:
    """Stands in for a blob container holding one photo."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.downloads: list[str] = []

    def download_blob(self, name: str):
        self.downloads.append(name)
        if name not in self.blobs:
            raise ResourceNotFoundError("The specified blob does not exist.")
        data = self.blobs[name]
        return type("Download", (), {"readall": lambda self: data})()


@pytest.fixture
def blobs(monkeypatch):
    container = FakeContainer({PHOTO: b"\xff\xd8 a photo"})
    monkeypatch.setattr(settings, "photos_account_url", "https://test.blob.core.windows.net")
    monkeypatch.setattr(store, "container", lambda: container)
    return container


def test_without_blob_storage_a_photo_is_a_file_in_the_data_folder(data_folder):
    assert store.photo_path(PHOTO) == data_folder / PHOTO
    assert not (data_folder / PHOTO).exists()


def test_a_path_outside_the_data_folder_is_refused():
    with pytest.raises(ValueError, match="outside the data folder"):
        store.photo_path("../secrets.txt")


def test_a_photo_is_fetched_once_and_then_read_from_the_data_folder(blobs, data_folder):
    path = store.photo_path(PHOTO)
    assert path == data_folder / PHOTO
    assert path.read_bytes() == b"\xff\xd8 a photo"
    assert store.photo_path(PHOTO) == path
    assert blobs.downloads == [PHOTO]
    assert [p.name for p in path.parent.iterdir()] == ["0001.jpg"]


def test_a_photo_missing_from_blob_storage_stays_missing(blobs):
    assert not store.photo_path("images/test/9999.jpg").exists()


def test_photos_come_from_azurite(monkeypatch, data_folder):
    service = BlobServiceClient.from_connection_string(AZURITE, retry_total=0, connection_timeout=2)
    name = f"photos-{uuid4().hex[:8]}"
    try:
        container = service.create_container(name)
    except AzureError:
        pytest.skip("Azurite is not running on 127.0.0.1:10000")
    try:
        container.upload_blob(PHOTO, b"\xff\xd8 from the cloud")
        monkeypatch.setattr(settings, "photos_connection_string", SecretStr(AZURITE))
        monkeypatch.setattr(settings, "photos_container", name)
        assert store.photo_path(PHOTO).read_bytes() == b"\xff\xd8 from the cloud"
    finally:
        container.delete_container()
        store.container.cache_clear()
