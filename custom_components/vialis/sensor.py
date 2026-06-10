"""Vialis Linky sensors."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vialis sensors from a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[SensorEntity] = [
        VialisDailyConsumptionSensor(coordinator),
        VialisMonthlyConsumptionSensor(coordinator),
        VialisMaxPowerSensor(coordinator),
        VialisCurrentPowerSensor(coordinator),
    ]

    tariffs = coordinator.data.get("tariffs", {})
    for mnemo, tariff_data in tariffs.items():
        entities.append(VialisTariffSensor(coordinator, mnemo, tariff_data))

    async_add_entities(entities)


_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, "linky")},
    name="Linky Vialis",
    manufacturer="Vialis",
    model="Linky",
)


class VialisDailyConsumptionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for latest available day's total Vialis consumption."""

    _attr_name = "Vialis Consommation Journaliere"
    _attr_unique_id = "sensor.vialis_daily_consumption"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_info = _DEVICE_INFO

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("latest_kwh")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "date": self.coordinator.data.get("latest_date"),
            "tariffs": self.coordinator.data.get("tariffs"),
        }


class VialisMonthlyConsumptionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for current month's total Vialis consumption."""

    _attr_name = "Vialis Consommation Mensuelle"
    _attr_unique_id = "sensor.vialis_monthly_consumption"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_info = _DEVICE_INFO

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("monthly_kwh")


class VialisMaxPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for latest maximum power demand."""

    _attr_name = "Vialis Puissance Maximale"
    _attr_unique_id = "sensor.vialis_pmax"
    _attr_native_unit_of_measurement = "kVA"
    _attr_device_class = SensorDeviceClass.APPARENT_POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_info = _DEVICE_INFO

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("pmax_kva")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "date": self.coordinator.data.get("pmax_date"),
            "heure": self.coordinator.data.get("pmax_heure"),
        }


class VialisCurrentPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for latest load curve reading."""

    _attr_name = "Vialis Puissance Actuelle"
    _attr_unique_id = "sensor.vialis_current_power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_info = _DEVICE_INFO

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("current_power_w")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "date": self.coordinator.data.get("current_power_date"),
            "heure": self.coordinator.data.get("current_power_heure"),
        }


class VialisTariffSensor(CoordinatorEntity, SensorEntity):
    """Sensor for a specific tariff's latest day consumption."""

    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_info = _DEVICE_INFO

    def __init__(self, coordinator, mnemo: str, tariff_data: dict) -> None:
        super().__init__(coordinator)
        self._mnemo = mnemo
        self._attr_unique_id = f"vialis_daily_{mnemo}"
        libelle = tariff_data.get("libelle", mnemo)
        self._attr_name = f"Vialis {libelle}"

    @property
    def native_value(self) -> float | None:
        tariffs = self.coordinator.data.get("tariffs", {})
        tariff = tariffs.get(self._mnemo, {})
        return tariff.get("latest_kwh")
