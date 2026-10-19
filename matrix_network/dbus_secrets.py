"""Secret-bearing NetworkManager settings operations over the system D-Bus.

Non-secret inspection and activation remain in the small ``nmcli`` adapter because
its text output is convenient and testable.  WPA passphrases belong here: D-Bus
marshalling keeps them out of the process argument list, where even a shell-free
``nmcli`` invocation could be inspected by another privileged local process.
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import NetworkOperationError


NM_SERVICE = "org.freedesktop.NetworkManager"
NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
NM_SETTINGS_IFACE = "org.freedesktop.NetworkManager.Settings"
NM_CONNECTION_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
NM_SETTINGS_ADD_TO_DISK = 0x1
NM_SETTINGS_UPDATE_TO_DISK = 0x1


class NetworkManagerDbusSecrets:
    """Create or update persistent Wi-Fi settings without secret-bearing argv.

    ``dbus-python`` is imported lazily so developer machines can import and test the
    rest of the network package without installing Linux system-bus bindings.
    """

    def __init__(self, bus: Any | None = None, dbus_module: Any | None = None):
        if dbus_module is None:
            try:
                import dbus as dbus_module  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise NetworkOperationError(
                    "dbus_unavailable",
                    "NetworkManager D-Bus bindings are unavailable",
                ) from exc
        self._dbus = dbus_module
        if bus is not None:
            self._bus = bus
        else:
            try:
                self._bus = dbus_module.SystemBus()
            except Exception as exc:
                raise NetworkOperationError(
                    "dbus_unavailable",
                    "NetworkManager system D-Bus is unavailable",
                ) from exc

    def add_wifi_candidate(
        self,
        *,
        profile_name: str,
        interface: str,
        ssid: str,
        passphrase: str,
        hidden: bool,
    ) -> str:
        """Atomically add one persistent candidate profile.

        Candidate creation and its secret are one NetworkManager transaction.  This
        avoids both argv disclosure and the orphan profile that could be left when a
        two-step ``nmcli add``/``modify`` sequence failed between commands.
        """

        dbus = self._dbus
        profile_uuid = str(uuid.uuid4())
        raw_settings: dict[str, dict[str, Any]] = {
            "connection": {
                "id": dbus.String(profile_name),
                "uuid": dbus.String(profile_uuid),
                "type": dbus.String("802-11-wireless"),
                "interface-name": dbus.String(interface),
                "autoconnect": dbus.Boolean(False),
            },
            "802-11-wireless": {
                "ssid": dbus.ByteArray(ssid.encode("utf-8")),
                "mode": dbus.String("infrastructure"),
                "hidden": dbus.Boolean(hidden),
            },
            "ipv4": {"method": dbus.String("auto")},
            "ipv6": {"method": dbus.String("auto")},
        }
        if passphrase:
            raw_settings["802-11-wireless-security"] = {
                "key-mgmt": dbus.String("wpa-psk"),
                "psk": dbus.String(passphrase),
            }
        # Explicit signatures avoid dbus-python guessing the wrong type for empty
        # option dictionaries or nested settings on different distro versions.
        settings = dbus.Dictionary(
            {
                section: dbus.Dictionary(values, signature="sv")
                for section, values in raw_settings.items()
            },
            signature="sa{sv}",
        )
        self._call_safely(
            "candidate_create_failed",
            "NetworkManager could not create the candidate profile",
            self._settings_interface().AddConnection2,
            settings,
            dbus.UInt32(NM_SETTINGS_ADD_TO_DISK),
            dbus.Dictionary({}, signature="sv"),
        )
        return profile_uuid

    def set_wifi_psk(self, profile_uuid: str, passphrase: str) -> None:
        """Persist a WPA-PSK on an existing profile via ``Update2``."""

        dbus = self._dbus
        connection = self._connection_for_uuid(profile_uuid)
        settings = self._call_safely(
            "profile_read_failed",
            "NetworkManager could not read the Wi-Fi profile",
            connection.GetSettings,
        )
        security = settings.get("802-11-wireless-security")
        if security is None:
            security = dbus.Dictionary({}, signature="sv")
            settings["802-11-wireless-security"] = security
        security["key-mgmt"] = dbus.String("wpa-psk")
        security["psk"] = dbus.String(passphrase)
        self._call_safely(
            "profile_update_failed",
            "NetworkManager could not update the Wi-Fi profile",
            connection.Update2,
            settings,
            dbus.UInt32(NM_SETTINGS_UPDATE_TO_DISK),
            dbus.Dictionary({}, signature="sv"),
        )

    def _settings_interface(self) -> Any:
        obj = self._bus.get_object(NM_SERVICE, NM_SETTINGS_PATH)
        return self._dbus.Interface(obj, NM_SETTINGS_IFACE)

    def _connection_for_uuid(self, wanted_uuid: str) -> Any:
        try:
            paths = self._settings_interface().ListConnections()
            for path in paths:
                obj = self._bus.get_object(NM_SERVICE, path)
                connection = self._dbus.Interface(obj, NM_CONNECTION_IFACE)
                settings = connection.GetSettings()
                if str(settings.get("connection", {}).get("uuid", "")) == wanted_uuid:
                    return connection
        except Exception as exc:
            # Lookup errors can include arbitrary D-Bus detail. Keep the same
            # secret-safe boundary as AddConnection2 and Update2.
            raise NetworkOperationError(
                "profile_read_failed",
                "NetworkManager could not read the Wi-Fi profile",
            ) from exc
        raise NetworkOperationError(
            "profile_missing",
            "NetworkManager Wi-Fi profile was not found",
        )

    @staticmethod
    def _call_safely(code: str, message: str, function: Any, *args: Any) -> Any:
        try:
            return function(*args)
        except Exception as exc:
            # D-Bus exceptions can contain serialized settings.  Never propagate or
            # log their text because those settings may include a passphrase.
            raise NetworkOperationError(code, message) from exc
