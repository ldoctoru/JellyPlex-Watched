from loguru import logger

from src.emby import Emby
from src.jellyfin import Jellyfin
from src.plex import Plex
from src.settings import (
    AppSettings,
    EmbySettings,
    JellyfinSettings,
    PlexSettings,
    _ServerBase,
)


ServerConnection = Plex | Jellyfin | Emby


def connect_server(settings: AppSettings, server: _ServerBase) -> ServerConnection:
    if isinstance(server, PlexSettings):
        plex_server = Plex(app_settings=settings, server_settings=server)
        logger.debug(f"Plex Server info: {plex_server.info()}")
        return plex_server

    if isinstance(server, JellyfinSettings):
        jellyfin_server = Jellyfin(app_settings=settings, server_settings=server)
        logger.debug(
            f"Jellyfin Server info: {jellyfin_server.server_name}: {jellyfin_server.server_version}"
        )
        return jellyfin_server

    if isinstance(server, EmbySettings):
        emby_server = Emby(app_settings=settings, server_settings=server)
        logger.debug(
            f"Emby Server info: {emby_server.server_name}: {emby_server.server_version}"
        )
        return emby_server

    msg = f"Invalid server type: {type(server)}"
    raise Exception(msg)


def generate_server_connections(settings: AppSettings) -> list[ServerConnection]:
    return [connect_server(settings, server) for server in settings.all_servers]
