import logging
from dataclasses import dataclass, field, replace

import pytest

from aioafero import AferoDevice, AferoState
from aioafero.device import get_afero_device
from aioafero.errors import DeviceNotFound, ExceededMaximumRetries
from aioafero.v1 import AferoBridgeV1, models, v1_const
from aioafero.v1.controllers import event
from aioafero.v1.controllers.base import BaseResourcesController, update_dataclass
from aioafero.v1.models.resource import DeviceInformation

from .. import utils


@dataclass
class TestFeatureBool:
    on: bool

    @property
    def api_value(self):
        return self.on


@dataclass
class TestFeatureInstance:
    on: bool
    func_instance: str | None

    @property
    def api_value(self):
        return {
            "value": "on" if self.on else "off",
            "functionClass": "beans",
            "functionInstance": self.func_instance or "",
        }


@dataclass
class TestResource:
    id: str
    available: bool
    on: TestFeatureBool
    beans: dict[str | None, TestFeatureInstance]
    device_information: DeviceInformation = field(default_factory=DeviceInformation)


@dataclass
class TestResourcePut:
    on: TestFeatureBool | None
    beans: TestFeatureInstance | None


test_res = TestResource(
    id="cool",
    available=True,
    on=TestFeatureBool(on=True),
    beans={
        None: TestFeatureInstance(on=True, func_instance=None),
        "bean1": TestFeatureInstance(on=True, func_instance="bean1"),
        "bean2": TestFeatureInstance(on=False, func_instance="bean2"),
    },
)

test_res_update = TestResource(
    id="cool",
    available=True,
    on=TestFeatureBool(on=True),
    beans={
        None: TestFeatureInstance(on=True, func_instance=None),
        "bean1": TestFeatureInstance(on=True, func_instance="bean1"),
        "bean2": TestFeatureInstance(on=True, func_instance="bean2"),
    },
)


test_device = AferoDevice(
    id="cool",
    device_id="cool-parent",
    model="bean",
    device_class="jumping",
    default_name="bean",
    default_image="bean",
    friendly_name="bean",
    states=[
        AferoState(
            functionClass="power",
            value="on",
            lastUpdateTime=0,
            functionInstance=None,
        ),
        AferoState(
            functionClass="mapped_beans",
            value="on",
            lastUpdateTime=0,
        ),
        AferoState(
            functionClass="mapped_beans",
            value="on",
            lastUpdateTime=0,
            functionInstance="bean1",
        ),
        AferoState(
            functionClass="mapped_beans",
            value="off",
            lastUpdateTime=0,
            functionInstance="bean2",
        ),
    ],
)

test_device_update = AferoDevice(
    id="cool",
    device_id="cool-parent",
    model="bean",
    device_class="jumping",
    default_name="bean",
    default_image="bean",
    friendly_name="bean",
    states=[
        AferoState(
            functionClass="power",
            value="on",
            lastUpdateTime=0,
            functionInstance=None,
        ),
        AferoState(
            functionClass="mapped_beans",
            value="on",
            lastUpdateTime=0,
        ),
        AferoState(
            functionClass="mapped_beans",
            value="on",
            lastUpdateTime=0,
            functionInstance="bean1",
        ),
        AferoState(
            functionClass="mapped_beans",
            value="on",
            lastUpdateTime=0,
            functionInstance="bean2",
        ),
    ],
)


class Example1ResourceController(BaseResourcesController):
    ITEM_TYPE_ID: models.ResourceTypes = models.ResourceTypes.DEVICE
    ITEM_TYPES: list[models.ResourceTypes] = [models.ResourceTypes.LIGHT]
    ITEM_CLS = TestResource
    ITEM_MAPPING: dict = {"beans": "mapped_beans"}

    async def initialize_elem(self, afero_dev: AferoDevice) -> TestResource:
        """Initialize the element"""
        self._logger.info("Initializing %s", afero_dev.id)
        on: TestFeatureBool | None = None
        beans: dict[str | None, TestFeatureInstance] = {}
        for state in afero_dev.states:
            if state.functionClass == "power":
                on = TestFeatureBool(on=state.value == "on")
            elif state.functionClass == "mapped_beans":
                beans[state.functionInstance] = TestFeatureInstance(
                    on=state.value == "on", func_instance=state.functionInstance
                )
        return TestResource(
            id=afero_dev.id,
            available=True,
            on=on,
            beans=beans,
        )

    async def update_elem(self, afero_dev: AferoDevice) -> set:
        updated_keys = set()
        cur_item = self.get_device(afero_dev.id)
        for state in afero_dev.states:
            if state.functionClass == "power":
                new_val = state.value == "on"
                if cur_item.on.on != new_val:
                    updated_keys.add("on")
                    cur_item.on.on = new_val
            elif state.functionClass == "mapped_beans":
                new_val = state.value == "on"
                if cur_item.beans[state.functionInstance].on != new_val:
                    updated_keys.add("on")
                    cur_item.beans[state.functionInstance].on = state.value == "on"
        return updated_keys


@pytest.fixture
def ex1_rc(mocked_bridge_req):
    yield Example1ResourceController(mocked_bridge_req)


def test_init(ex1_rc):
    assert isinstance(ex1_rc._bridge, AferoBridgeV1)
    assert ex1_rc._items == {}
    ex1_rc._initialized = False
    assert not ex1_rc.initialized
    ex1_rc._initialized = True
    assert ex1_rc.initialized


def test_basic(ex1_rc):
    ex1_rc._items = {"cool": "beans"}
    assert ex1_rc["cool"] == "beans"
    assert "cool" in ex1_rc
    for item in ex1_rc:
        assert item == "beans"
    assert ex1_rc.items == ["beans"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_elem, evt_type, item_id, evt_data, expected_devs, expected_return",
    [
        # Device added
        (
            [],
            event.EventType.RESOURCE_ADDED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_ADDED,
                device_id=test_device.id,
                device=test_device,
                force_forward=False,
            ),
            {test_device.id},
            replace(test_res),
        ),
        # Device not found
        (
            [],
            event.EventType.RESOURCE_UPDATED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_UPDATED,
                device_id=test_device.id,
                device=test_device_update,
                force_forward=False,
            ),
            set(),
            None,
        ),
        # Device updated with changes
        (
            [test_device],
            event.EventType.RESOURCE_UPDATED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_UPDATED,
                device_id=test_device.id,
                device=test_device_update,
                force_forward=False,
            ),
            {test_device.id},
            replace(test_res_update),
        ),
        # Device updated with no changes + dont force
        (
            [test_device],
            event.EventType.RESOURCE_UPDATED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_UPDATED,
                device_id=test_device.id,
                device=test_device,
                force_forward=False,
            ),
            {test_device.id},
            None,
        ),
        # Device updated with no changes + force
        (
            [test_device],
            event.EventType.RESOURCE_UPDATED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_UPDATED,
                device_id=test_device.id,
                device=test_device,
                force_forward=True,
            ),
            {test_device.id},
            replace(test_res),
        ),
        # Device deleted
        (
            [test_device],
            event.EventType.RESOURCE_DELETED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RESOURCE_DELETED,
                device_id=test_device.id,
                force_forward=False,
            ),
            set(),
            replace(test_res),
        ),
        # Not a real event
        (
            [],
            event.EventType.RECONNECTED,
            test_device.id,
            event.AferoEvent(
                type=event.EventType.RECONNECTED,
                device_id=test_device.id,
                force_forward=False,
            ),
            set(),
            None,
        ),
    ],
)
async def test__handle_event_type(
    init_elem, evt_type, item_id, evt_data, expected_devs, expected_return, ex1_rc
):
    for elem in init_elem:
        ex1_rc._items[elem.id] = await ex1_rc.initialize_elem(elem)
        ex1_rc._bridge.add_device(elem.id, ex1_rc)
    assert (
        await ex1_rc._handle_event_type(evt_type, item_id, evt_data) == expected_return
    )
    if expected_return and evt_type != event.EventType.RESOURCE_DELETED:
        assert item_id in ex1_rc
    assert ex1_rc._bridge.tracked_devices == expected_devs


@pytest.mark.asyncio
@pytest.mark.parametrize("is_coroutine", [True, False])
@pytest.mark.parametrize(
    "id_filter, event_filter, event_type, expected",
    [
        ("beans", event.EventType.RESOURCE_ADDED, event.EventType.RESOURCE_ADDED, True),
        (
            "beans",
            event.EventType.RESOURCE_ADDED,
            event.EventType.RESOURCE_UPDATED,
            False,
        ),
        (
            "not-a-bean",
            event.EventType.RESOURCE_ADDED,
            event.EventType.RESOURCE_ADDED,
            False,
        ),
    ],
)
async def test_emit_to_subscribers(
    is_coroutine, id_filter, event_filter, event_type, expected, ex1_rc, mocker
):

    callback = mocker.AsyncMock() if is_coroutine else mocker.Mock()
    ex1_rc.subscribe(callback, id_filter=id_filter, event_filter=event_filter)
    await ex1_rc.emit_to_subscribers(event_type, "beans", test_res)
    if expected:
        callback.assert_called_once()
    else:
        callback.assert_not_called()
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evt_type, evt_data, called",
    [
        # No data
        (event.EventType.RECONNECTED, None, False),
        # data
        (
            event.EventType.RESOURCE_DELETED,
            event.AferoEvent(
                type=event.EventType.RESOURCE_DELETED,
                device_id=test_device.id,
                force_forward=False,
            ),
            True,
        ),
        # data but not an item
        (
            event.EventType.RESOURCE_UPDATED,
            event.AferoEvent(
                type=event.EventType.RESOURCE_UPDATED,
                device_id="no-thanks",
                device=test_device,
                force_forward=True,
            ),
            False,
        ),
    ],
)
async def test__handle_event(evt_type, evt_data, called, ex1_rc, mocker):
    ex1_rc._items[test_device.id] = await ex1_rc.initialize_elem(test_device)
    ex1_rc._bridge.add_device(test_device.id, ex1_rc)
    emitted = mocker.patch.object(ex1_rc, "emit_to_subscribers")
    await ex1_rc._handle_event(evt_type, evt_data)
    if called:
        emitted.assert_called_once()
    else:
        emitted.assert_not_called()


def mocked_get_filtered_devices(initial_data) -> list[AferoDevice]:
    valid = []
    for ind, element in enumerate(initial_data):
        if element["typeId"] != models.ResourceTypes.DEVICE.value:
            continue
        if ind % 2 == 0:
            valid.append(get_afero_device(element))
    return valid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_filtered_devices, expected_ids",
    [
        (
            False,
            {
                "99a03fb7-ebaa-4fc2-a7b5-df223003b127",
                "84338ebe-7ddf-4bfa-9753-3ee8cdcc8da6",
                "bc429efe-592a-4852-a18b-5b2a5e6ca5f1",
            },
        ),
        (
            mocked_get_filtered_devices,
            {
                "b16fc78d-4639-41a7-8a10-868405c412d6",
                "84338ebe-7ddf-4bfa-9753-3ee8cdcc8da6",
                "b50d9823-7ba0-44d9-b9a9-ad64dbbb225f",
            },
        ),
    ],
)
async def test__get_valid_devices(get_filtered_devices, expected_ids, ex1_rc):
    data = utils.get_raw_dump("raw_hs_data.json")
    if get_filtered_devices:
        ex1_rc.get_filtered_devices = get_filtered_devices
    devices = await ex1_rc._get_valid_devices(data)
    assert len(devices) == len(expected_ids)
    for device in devices:
        assert device.id in expected_ids


@pytest.mark.asyncio
async def test_initialize_not_needed(ex1_rc, mocker):
    check = mocker.patch.object(ex1_rc, "_get_valid_devices")
    ex1_rc._initialized = True
    await ex1_rc.initialize(utils.get_raw_dump("raw_hs_data.json"))
    check.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("item_types", [True, False])
async def test_initialize(item_types, ex1_rc, mocker):
    ex1_rc._initialized = False
    if not item_types:
        ex1_rc.ITEM_TYPES = []
    handle_event = mocker.patch.object(ex1_rc, "_handle_event")
    await ex1_rc.initialize(utils.get_raw_dump("raw_hs_data.json"))
    assert handle_event.call_count == 3
    if item_types:
        assert ex1_rc._bridge.events._subscribers == [(handle_event, None, ("light",))]
    else:
        assert ex1_rc._bridge.events._subscribers == [(handle_event, None, None)]


@pytest.mark.parametrize(
    "id_filter, event_filter, expected, expected_unsub",
    [
        # No ID filter
        (None, None, {"*": [(min, None)]}, {"*": []}),
        # ID filter
        ("beans", None, {"*": [], "beans": [(min, None)]}, {"*": [], "beans": []}),
        # ID filter as a tuple
        (
            ("beans", "double_beans"),
            (event.EventType.RESOURCE_ADDED,),
            {
                "*": [],
                "beans": [(min, (event.EventType.RESOURCE_ADDED,))],
                "double_beans": [(min, (event.EventType.RESOURCE_ADDED,))],
            },
            {"*": [], "beans": [], "double_beans": []},
        ),
    ],
)
def test_subscribe(id_filter, event_filter, expected, expected_unsub, ex1_rc):
    unsub = ex1_rc.subscribe(min, id_filter=id_filter, event_filter=event_filter)
    assert ex1_rc._subscribers == expected
    unsub()
    assert ex1_rc._subscribers == expected_unsub


def test_subscribe_with_starting(ex1_rc):
    ex1_rc.subscribe(min, id_filter="cool")
    unsub2 = ex1_rc.subscribe(min, id_filter="cool2")
    assert ex1_rc._subscribers == {
        "*": [],
        "cool": [(min, None)],
        "cool2": [(min, None)],
    }
    unsub2()
    assert ex1_rc._subscribers == {"*": [], "cool": [(min, None)], "cool2": []}


@pytest.mark.asyncio
async def test__process_state_update(ex1_rc):
    ex1_rc._items[test_res.id] = await ex1_rc.initialize_elem(test_device)
    ex1_rc._bridge.add_device(test_res.id, ex1_rc)
    await ex1_rc._process_state_update(
        ex1_rc._items[test_res.id],
        test_res.id,
        [
            {
                "functionClass": "mapped_beans",
                "value": "off",
                "lastUpdateTime": 0,
                "functionInstance": "bean2",
            }
        ],
    )
    assert ex1_rc._items[test_res.id].beans["bean2"].on is False
    update_req = await ex1_rc._bridge.events._event_queue.get()
    assert update_req["device"].id == "cool"
    assert update_req["device_id"] == "cool"
    assert update_req["force_forward"] is True
    assert update_req["type"] == event.EventType.RESOURCE_UPDATED
    state_update = update_req["device"].states[0]
    assert state_update.functionClass == "mapped_beans"
    assert state_update.functionInstance == "bean2"
    assert state_update.value == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, response_err, states, expected_call, expected, messages",
    [
        # Happy path
        (
            {"status": 200},
            None,
            [
                {
                    "functionClass": "mapped_beans",
                    "value": "off",
                    "lastUpdateTime": 0,
                    "functionInstance": "bean2",
                }
            ],
            {
                "json": {
                    "metadeviceId": "cool",
                    "values": [
                        {
                            "functionClass": "mapped_beans",
                            "value": "off",
                            "lastUpdateTime": 0,
                            "functionInstance": "bean2",
                        }
                    ],
                },
                "headers": {
                    "host": v1_const.AFERO_CLIENTS["hubspace"]["DATA_HOST"],
                    "content-type": "application/json; charset=utf-8",
                },
            },
            True,
            [],
        ),
        # Failed update
        (
            {"status": 400},
            None,
            [
                {
                    "functionClass": "mapped_beans",
                    "value": "off",
                    "lastUpdateTime": 0,
                    "functionInstance": "bean2",
                }
            ],
            {
                "json": {
                    "metadeviceId": "cool",
                    "values": [
                        {
                            "functionClass": "mapped_beans",
                            "value": "off",
                            "lastUpdateTime": 0,
                            "functionInstance": "bean2",
                        }
                    ],
                },
                "headers": {
                    "host": v1_const.AFERO_CLIENTS["hubspace"]["DATA_HOST"],
                    "content-type": "application/json; charset=utf-8",
                },
            },
            False,
            ["Invalid update provided for cool using"],
        ),
        # Retry exception
        (
            None,
            ExceededMaximumRetries,
            [
                {
                    "functionClass": "mapped_beans",
                    "value": "off",
                    "lastUpdateTime": 0,
                    "functionInstance": "bean2",
                }
            ],
            {
                "json": {
                    "metadeviceId": "cool",
                    "values": [
                        {
                            "functionClass": "mapped_beans",
                            "value": "off",
                            "lastUpdateTime": 0,
                            "functionInstance": "bean2",
                        }
                    ],
                },
                "headers": {
                    "host": v1_const.AFERO_CLIENTS["hubspace"]["DATA_HOST"],
                    "content-type": "application/json; charset=utf-8",
                },
            },
            False,
            ["Maximum retries exceeded for cool"],
        ),
    ],
)
async def test_update_afero_api(
    response,
    response_err,
    states,
    expected_call,
    expected,
    messages,
    ex1_rc,
    mock_aioresponse,
    caplog,
):
    device_id = "cool"
    url = v1_const.AFERO_CLIENTS["hubspace"]["DEVICE_STATE"].format(
        ex1_rc._bridge.account_id, str(device_id)
    )
    if response:
        mock_aioresponse.put(url, **response)
    if response_err:
        ex1_rc._bridge.request.side_effect = response_err
    assert await ex1_rc.update_afero_api(device_id, states) == expected
    if expected_call:
        ex1_rc._bridge.request.assert_called_with("put", url, **expected_call)
    else:
        ex1_rc._bridge.request.assert_not_called()
    for message in messages:
        assert message in caplog.text


@pytest.mark.asyncio
async def test_update_dev_not_found(ex1_rc, caplog):
    caplog.set_level(logging.DEBUG)
    await ex1_rc.update("not-a-device")
    assert "Unable to update device not-a-device as it does not exist" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "obj_in, states, expected_states, expected_item, successful",
    [
        # Obj in without updates
        (TestResourcePut(on=None, beans=None), None, None, test_res, True),
        # Obj in with updates
        (
            TestResourcePut(
                on=None, beans=TestFeatureInstance(on=True, func_instance="bean2")
            ),
            None,
            [
                {
                    "functionClass": "beans",
                    "functionInstance": "bean2",
                    "value": "on",
                    "lastUpdateTime": 12345,
                }
            ],
            test_res_update,
            True,
        ),
        # Obj in with unsuccessful updates
        (
            TestResourcePut(
                on=None, beans=TestFeatureInstance(on=True, func_instance="bean2")
            ),
            None,
            [
                {
                    "functionClass": "beans",
                    "functionInstance": "bean2",
                    "value": "on",
                    "lastUpdateTime": 12345,
                }
            ],
            test_res,
            False,
        ),
        # Manual states
        (
            None,
            [
                {
                    "functionClass": "mapped_beans",
                    "functionInstance": "bean2",
                    "value": "on",
                    "lastUpdateTime": 123456,
                }
            ],
            [
                {
                    "functionClass": "mapped_beans",
                    "functionInstance": "bean2",
                    "value": "on",
                    "lastUpdateTime": 123456,
                }
            ],
            test_res_update,
            True,
        ),
    ],
)
async def test_update(
    obj_in, states, expected_states, expected_item, successful, ex1_rc, mocker
):
    mocker.patch("time.time", return_value=12345)
    ex1_rc._items[test_res.id] = await ex1_rc.initialize_elem(test_device)
    ex1_rc._bridge.add_device(test_res.id, ex1_rc)
    update_afero_api = mocker.patch.object(
        ex1_rc, "update_afero_api", return_value=successful
    )
    await ex1_rc.update(test_res.id, obj_in=obj_in, states=states)
    if not expected_states:
        update_afero_api.assert_not_called()
    else:
        update_afero_api.assert_called_once_with(test_res.id, expected_states)
    assert ex1_rc._items[test_res.id] == expected_item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "starting_items,device_id,expected",
    [
        ({"cool": "beans"}, "not-a-device", None),
        ({"cool": "beans"}, "cool", "beans"),
    ],
)
async def test_get_device(ex1_rc, starting_items, device_id, expected):
    ex1_rc._items = starting_items
    if not expected:
        with pytest.raises(DeviceNotFound):
            ex1_rc.get_device(device_id)
    else:
        assert ex1_rc.get_device(device_id) == expected


@pytest.mark.parametrize(
    "resource,update,expected",
    [
        # No updates
        (replace(test_res), TestResourcePut(on=None, beans=None), replace(test_res)),
        # Test single + dict updates
        (
            replace(test_res),
            TestResourcePut(
                on=TestFeatureBool(on=False),
                beans=TestFeatureInstance(on=False, func_instance=None),
            ),
            replace(
                test_res,
                on=TestFeatureBool(on=False),
                beans={
                    None: TestFeatureInstance(on=False, func_instance=None),
                    "bean1": TestFeatureInstance(on=True, func_instance="bean1"),
                    "bean2": TestFeatureInstance(on=False, func_instance="bean2"),
                },
            ),
        ),
    ],
)
def test_update_dataclass(resource, update, expected):
    update_dataclass(resource, update)
    assert resource == expected


def test_dataclass_to_hs():
    pass
