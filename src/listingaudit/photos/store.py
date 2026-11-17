"""Where a listing's photo comes from: the data folder, or Azure Blob Storage.

Locally, photos are files under data/images. A deployment without them reads each photo
from a blob container the first time it is needed, and keeps it in the data folder at
the same path, so the rest of the code always reads a file.
"""

import os
import tempfile
from functools import cache
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from listingaudit.config import settings


@cache
def container() -> ContainerClient:
    if settings.photos_connection_string is not None:
        return ContainerClient.from_connection_string(
            settings.photos_connection_string.get_secret_value(), settings.photos_container
        )
    from azure.identity import DefaultAzureCredential

    return ContainerClient(
        settings.photos_account_url, settings.photos_container, credential=DefaultAzureCredential()
    )


def in_the_cloud() -> bool:
    return bool(settings.photos_account_url or settings.photos_connection_string)


def photo_path(image_file: str) -> Path:
    """The photo's file, fetched from Blob Storage first if it isn't here yet.

    A photo that is in neither place gives a path that doesn't exist.
    """
    path = settings.data_dir / image_file
    if not path.resolve().is_relative_to(settings.data_dir.resolve()):
        raise ValueError(f"{image_file} is outside the data folder.")
    if path.exists() or not in_the_cloud():
        return path
    try:
        data = container().download_blob(image_file).readall()
    except ResourceNotFoundError:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written under a name of its own, then renamed, so no reader sees half a photo
    handle, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    os.replace(part, path)
    return path
