"""Controls Hubspace devices on v1 API"""

__all__ = [
    "AferoBridgeV1",
    "models",
    "BaseResourcesController",
    "DeviceController",
    "FanController",
    "LightController",
    "LockController",
    "SwitchController",
    "ValveController",
]

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, Callable, Generator, Optional

import aiohttp
from aiohttp import web_exceptions

from ..device import AferoResource
from ..errors import DeviceNotFound, ExceededMaximumRetries, InvalidAuth
from . import models, v1_const
from .auth import AferoAuth
from .controllers.base import BaseResourcesController
from .controllers.device import DeviceController
from .controllers.event import EventCallBackType, EventStream, EventType
from .controllers.fan import FanController
from .controllers.light import LightController
from .controllers.lock import LockController
from .controllers.switch import SwitchController
from .controllers.valve import ValveController


class AferoBridgeV1:
    """Controls Afero IoT devices on v1 API"""

    _web_session: Optional[aiohttp.ClientSession] = None

    def __init__(
        self,
        username: str,
        password: str,
        refresh_token: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        polling_interval: int = 30,
        afero_client: Optional[str] = "hubspace",
    ):
        self._close_session: bool = session is None
        self._web_session: aiohttp.ClientSession = session
        self._account_id: Optional[str] = None
        self._afero_client: str = afero_client
        self._auth = AferoAuth(
            username, password, refresh_token=refresh_token, afero_client=afero_client
        )
        self.logger = logging.getLogger(f"{__package__}[{username}]")
        self.logger.addHandler(logging.StreamHandler())
        self._known_devs: dict[str, BaseResourcesController] = {}
        # Data Updater
        self._events: EventStream = EventStream(self, polling_interval)
        # Data Controllerse
        self._devices: DeviceController = DeviceController(
            self
        )  # Devices contain all sensors
        self._fans: FanController = FanController(self)
        self._lights: LightController = LightController(self)
        self._locks: LockController = LockController(self)
        self._switches: SwitchController = SwitchController(self)
        self._valves: ValveController = ValveController(self)

    async def __aenter__(self) -> "AferoBridgeV1":
        """Return Context manager."""
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> bool | None:
        """Exit context manager."""
        await self.close()
        if exc_val:
            raise exc_val
        return exc_type

    @property
    def devices(self) -> DeviceController:
        return self._devices

    @property
    def events(self) -> EventStream:
        return self._events

    @property
    def fans(self) -> FanController:
        return self._fans

    @property
    def lights(self) -> LightController:
        return self._lights

    @property
    def locks(self) -> LockController:
        return self._locks

    @property
    def switches(self) -> SwitchController:
        return self._switches

    @property
    def valves(self) -> ValveController:
        return self._valves

    @property
    def _controllers(self) -> list:
        dev_controllers = [
            self._devices,
            self._fans,
            self._lights,
            self._locks,
            self._switches,
            self._valves,
        ]
        return dev_controllers

    @property
    def controllers(self) -> list:
        initialized = []
        for controller in self._controllers:
            if controller and controller.initialized:
                initialized.append(controller)
        return initialized

    @property
    def tracked_devices(self) -> set:
        return set(self._known_devs.keys())

    def add_device(
        self, device_id: str, controller: BaseResourcesController[AferoResource]
    ) -> None:
        self._known_devs[device_id] = controller

    def remove_device(self, device_id: str) -> None:
        with contextlib.suppress(KeyError):
            self._known_devs.pop(device_id)

    @property
    def account_id(self) -> str:
        """Get the account ID for the Afero IoT account"""
        return self._account_id

    @property
    def afero_client(self) -> str:
        """Get identifier for Afero system"""
        return self._afero_client

    @property
    def refresh_token(self) -> Optional[str]:
        """get the refresh token for the Afero IoT account"""
        return self._auth.refresh_token

    def set_polling_interval(self, polling_interval: int) -> None:
        self._events.polling_interval = polling_interval

    async def close(self) -> None:
        """Close connection and cleanup."""
        await self.events.stop()
        if self._close_session and self._web_session:
            await self._web_session.close()
        self.logger.info("Connection to bridge closed.")

    def subscribe(
        self,
        callback: EventCallBackType,
    ) -> Callable:
        """
        Subscribe to status changes for all resources.

        Returns:
            function to unsubscribe.
        """
        unsubscribes = [
            controller.subscribe(callback) for controller in self.controllers
        ]

        def unsubscribe():
            for unsub in unsubscribes:
                unsub()

        return unsubscribe

    async def get_account_id(self) -> str:
        """Lookup the account ID associated with the login"""
        if not self._account_id:
            self.logger.debug("Querying API for account id")
            headers = {"host": v1_const.AFERO_CLIENTS[self._afero_client]["API_HOST"]}
            self.logger.debug(
                "GETURL: %s, Headers: %s",
                v1_const.AFERO_CLIENTS[self._afero_client]["ACCOUNT_ID_URL"],
                headers,
            )
            res = await self.request(
                "GET",
                v1_const.AFERO_CLIENTS[self._afero_client]["ACCOUNT_ID_URL"],
                headers=headers,
            )
            self._account_id = (
                (await res.json())
                .get("accountAccess")[0]
                .get("account")
                .get("accountId")
            )
        return self._account_id

    async def initialize(self) -> None:
        """Query Afero API for all data"""
        await self.get_account_id()
        data = await self.fetch_data()
        await asyncio.gather(
            *[
                controller.initialize(data)
                for controller in self._controllers
                if not controller.initialized
            ]
        )
        await self._events.initialize()

    async def fetch_data(self) -> list[dict[Any, str]]:
        """Query the API"""
        self.logger.debug("Querying API for all data")
        headers = {
            "host": v1_const.AFERO_CLIENTS[self._afero_client]["DATA_HOST"],
        }
        params = {"expansions": "state"}
        res = await self.request(
            "get",
            v1_const.AFERO_CLIENTS[self._afero_client]["DATA_URL"].format(
                self.account_id
            ),
            headers=headers,
            params=params,
        )
        res.raise_for_status()
        data = await res.json()
        if not isinstance(data, list):
            raise ValueError(data)
        return data

    @asynccontextmanager
    async def create_request(
        self, method: str, url: str, **kwargs
    ) -> Generator[aiohttp.ClientResponse, None, None]:
        """
        Make a request to any path with V2 request method (auth in header).

        Returns a generator with aiohttp ClientResponse.
        """
        if self._web_session is None:
            connector = aiohttp.TCPConnector(
                limit_per_host=3,
            )
            self._web_session = aiohttp.ClientSession(connector=connector)

        try:
            token = await self._auth.token(self._web_session)
        except InvalidAuth:
            self.events.emit(EventType.INVALID_AUTH)
            raise
        else:
            headers = self.get_headers(
                **{
                    "authorization": f"Bearer {token}",
                }
            )
            headers.update(kwargs.get("headers", {}))
            kwargs["headers"] = headers
            kwargs["ssl"] = True
            async with self._web_session.request(method, url, **kwargs) as res:
                yield res

    async def request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """Make request on the api and return response data."""
        retries = 0
        self.logger.info("Making request [%s] to %s with %s", method, url, kwargs)
        while retries < v1_const.MAX_RETRIES:
            retries += 1
            if retries > 1:
                retry_wait = 0.25 * retries
                await asyncio.sleep(retry_wait)
            async with self.create_request(method, url, **kwargs) as resp:
                # 503 means the service is temporarily unavailable, back off a bit.
                # 429 means the bridge is rate limiting/overloaded, we should back off a bit.
                if resp.status in [429, 503]:
                    continue
                # 403 is bad auth
                elif resp.status == 403:
                    raise web_exceptions.HTTPForbidden()
                await resp.read()
                return resp
        raise ExceededMaximumRetries("Exceeded maximum number of retries")

    async def send_service_request(self, device_id: str, states: list[dict[str, Any]]):
        """Manually send state requests to Afero IoT

        :param device_id: ID for the device
        :param states: List of states to send
        """
        controller = self._known_devs.get(device_id)
        if not controller:
            raise DeviceNotFound(f"Unable to find device {device_id}")
        await controller.update(device_id, states=states)

    def get_headers(self, **kwargs):
        headers: dict[str, str] = {
            "user-agent": v1_const.AFERO_CLIENTS[self._afero_client][
                "DEFAULT_USERAGENT"
            ],
            "accept-encoding": "gzip",
        }
        headers.update(kwargs)
        return headers
