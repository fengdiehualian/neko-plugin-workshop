"""共享内存遥测读取：结构对齐 SCS SDK 插件写入的映射文件。"""

from __future__ import annotations

import ctypes
from ctypes import (
    c_bool,
    c_char,
    c_double,
    c_float,
    c_int,
    c_longlong,
    c_uint,
    c_ulonglong,
)
from dataclasses import dataclass
from typing import Optional

MMF_NAMES = ["Local\\SCSTelemetry", "SCSTelemetry"]
MMF_SIZE = 32 * 1024
S = 64


class ConB(ctypes.Structure):
    _fields_ = [
        ("wheelSteerable", c_bool * 16), ("wheelSimulated", c_bool * 16),
        ("wheelPowered", c_bool * 16), ("wheelLiftable", c_bool * 16),
    ]


class ComB(ctypes.Structure):
    _fields_ = [("wheelOnGround", c_bool * 16), ("attached", c_bool)]


class ComUi(ctypes.Structure):
    _fields_ = [("wheelSubstance", c_uint * 16)]


class ConUi(ctypes.Structure):
    _fields_ = [("wheelCount", c_uint)]


class ComF(ctypes.Structure):
    _fields_ = [
        ("cargoDamage", c_float), ("wearChassis", c_float), ("wearWheels", c_float),
        ("wearBody", c_float), ("wheelSuspDeflection", c_float * 16),
        ("wheelVelocity", c_float * 16), ("wheelSteering", c_float * 16),
        ("wheelRotation", c_float * 16), ("wheelLift", c_float * 16),
        ("wheelLiftOffset", c_float * 16),
    ]


class ConF(ctypes.Structure):
    _fields_ = [("wheelRadius", c_float * 16)]


class ComFv(ctypes.Structure):
    _fields_ = [
        ("linearVelocityX", c_float), ("linearVelocityY", c_float), ("linearVelocityZ", c_float),
        ("angularVelocityX", c_float), ("angularVelocityY", c_float), ("angularVelocityZ", c_float),
        ("linearAccelerationX", c_float), ("linearAccelerationY", c_float), ("linearAccelerationZ", c_float),
        ("angularAccelerationX", c_float), ("angularAccelerationY", c_float), ("angularAccelerationZ", c_float),
    ]


class ConFv(ctypes.Structure):
    _fields_ = [
        ("hookPositionX", c_float), ("hookPositionY", c_float), ("hookPositionZ", c_float),
        ("wheelPositionX", c_float * 16), ("wheelPositionY", c_float * 16),
        ("wheelPositionZ", c_float * 16),
    ]


class ComDp(ctypes.Structure):
    _fields_ = [
        ("worldX", c_double), ("worldY", c_double), ("worldZ", c_double),
        ("rotationX", c_double), ("rotationY", c_double), ("rotationZ", c_double),
    ]


class ConS(ctypes.Structure):
    _fields_ = [
        ("id", c_char * S), ("cargoAcessoryId", c_char * S), ("bodyType", c_char * S),
        ("brandId", c_char * S), ("brand", c_char * S), ("name", c_char * S),
        ("chainType", c_char * S), ("licensePlate", c_char * S),
        ("licensePlateCountry", c_char * S), ("licensePlateCountryId", c_char * S),
    ]


class ScsTrailer(ctypes.Structure):
    _fields_ = [
        ("con_b", ConB), ("com_b", ComB), ("buffer_b", c_char * 3),
        ("com_ui", ComUi), ("con_ui", ConUi),
        ("com_f", ComF), ("con_f", ConF),
        ("com_fv", ComFv), ("con_fv", ConFv), ("buffer_fv", c_char * 4),
        ("com_dp", ComDp),
        ("con_s", ConS),
    ]


class ScsValues(ctypes.Structure):
    _fields_ = [
        ("telemetry_plugin_revision", c_uint), ("version_major", c_uint),
        ("version_minor", c_uint), ("game", c_uint),
        ("telemetry_version_game_major", c_uint), ("telemetry_version_game_minor", c_uint),
    ]


class CommonUi(ctypes.Structure):
    _fields_ = [("time_abs", c_uint)]


class ConfigUi(ctypes.Structure):
    _fields_ = [
        ("gears", c_uint), ("gears_reverse", c_uint), ("retarderStepCount", c_uint),
        ("truckWheelCount", c_uint), ("selectorCount", c_uint), ("time_abs_delivery", c_uint),
        ("maxTrailerCount", c_uint), ("unitCount", c_uint), ("plannedDistanceKm", c_uint),
    ]


class TruckUi(ctypes.Structure):
    _fields_ = [
        ("shifterSlot", c_uint), ("retarderBrake", c_uint),
        ("lightsAuxFront", c_uint), ("lightsAuxRoof", c_uint),
        ("truck_wheelSubstance", c_uint * 16),
        ("hshifterPosition", c_uint * 32), ("hshifterBitmask", c_uint * 32),
    ]


class GameplayUi(ctypes.Structure):
    _fields_ = [
        ("jobDeliveredDeliveryTime", c_uint), ("jobStartingTime", c_uint),
        ("jobFinishedTime", c_uint),
    ]


class CommonI(ctypes.Structure):
    _fields_ = [("restStop", c_int)]


class TruckI(ctypes.Structure):
    _fields_ = [("gear", c_int), ("gearDashboard", c_int), ("hshifterResulting", c_int * 32)]


class GameplayI(ctypes.Structure):
    _fields_ = [("jobDeliveredEarnedXp", c_int)]


class CommonF(ctypes.Structure):
    _fields_ = [("scale", c_float)]


class ConfigF(ctypes.Structure):
    _fields_ = [
        ("fuelCapacity", c_float), ("fuelWarningFactor", c_float), ("adblueCapacity", c_float),
        ("adblueWarningFactor", c_float), ("airPressureWarning", c_float), ("airPressurEmergency", c_float),
        ("oilPressureWarning", c_float), ("waterTemperatureWarning", c_float), ("batteryVoltageWarning", c_float),
        ("engineRpmMax", c_float), ("gearDifferential", c_float), ("cargoMass", c_float),
        ("truckWheelRadius", c_float * 16), ("gearRatiosForward", c_float * 24),
        ("gearRatiosReverse", c_float * 8), ("unitMass", c_float),
    ]


class TruckF(ctypes.Structure):
    _fields_ = [
        ("speed", c_float), ("engineRpm", c_float), ("userSteer", c_float), ("userThrottle", c_float),
        ("userBrake", c_float), ("userClutch", c_float), ("gameSteer", c_float), ("gameThrottle", c_float),
        ("gameBrake", c_float), ("gameClutch", c_float), ("cruiseControlSpeed", c_float),
        ("airPressure", c_float), ("brakeTemperature", c_float), ("fuel", c_float),
        ("fuelAvgConsumption", c_float), ("fuelRange", c_float), ("adblue", c_float),
        ("oilPressure", c_float), ("oilTemperature", c_float), ("waterTemperature", c_float),
        ("batteryVoltage", c_float), ("lightsDashboard", c_float),
        ("wearEngine", c_float), ("wearTransmission", c_float), ("wearCabin", c_float),
        ("wearChassis", c_float), ("wearWheels", c_float),
        ("truckOdometer", c_float), ("routeDistance", c_float), ("routeTime", c_float),
        ("speedLimit", c_float),
        ("truck_wheelSuspDeflection", c_float * 16), ("truck_wheelVelocity", c_float * 16),
        ("truck_wheelSteering", c_float * 16), ("truck_wheelRotation", c_float * 16),
        ("truck_wheelLift", c_float * 16), ("truck_wheelLiftOffset", c_float * 16),
    ]


class GameplayF(ctypes.Structure):
    _fields_ = [
        ("jobDeliveredCargoDamage", c_float), ("jobDeliveredDistanceKm", c_float),
        ("refuelAmount", c_float),
    ]


class JobF(ctypes.Structure):
    _fields_ = [("cargoDamage", c_float)]


class ConfigB(ctypes.Structure):
    _fields_ = [
        ("truckWheelSteerable", c_bool * 16), ("truckWheelSimulated", c_bool * 16),
        ("truckWheelPowered", c_bool * 16), ("truckWheelLiftable", c_bool * 16),
        ("isCargoLoaded", c_bool), ("specialJob", c_bool),
    ]


class TruckB(ctypes.Structure):
    _fields_ = [
        ("parkBrake", c_bool), ("motorBrake", c_bool), ("airPressureWarning", c_bool),
        ("airPressureEmergency", c_bool), ("fuelWarning", c_bool), ("adblueWarning", c_bool),
        ("oilPressureWarning", c_bool), ("waterTemperatureWarning", c_bool),
        ("batteryVoltageWarning", c_bool), ("electricEnabled", c_bool), ("engineEnabled", c_bool),
        ("wipers", c_bool), ("blinkerLeftActive", c_bool), ("blinkerRightActive", c_bool),
        ("blinkerLeftOn", c_bool), ("blinkerRightOn", c_bool), ("lightsParking", c_bool),
        ("lightsBeamLow", c_bool), ("lightsBeamHigh", c_bool), ("lightsBeacon", c_bool),
        ("lightsBrake", c_bool), ("lightsReverse", c_bool), ("lightsHazard", c_bool),
        ("cruiseControl", c_bool),
        ("truck_wheelOnGround", c_bool * 16), ("shifterToggle", c_bool * 2),
        ("differentialLock", c_bool), ("liftAxle", c_bool), ("liftAxleIndicator", c_bool),
        ("trailerLiftAxle", c_bool), ("trailerLiftAxleIndicator", c_bool),
    ]


class GameplayB(ctypes.Structure):
    _fields_ = [("jobDeliveredAutoparkUsed", c_bool), ("jobDeliveredAutoloadUsed", c_bool)]


class ConfigFv(ctypes.Structure):
    _fields_ = [
        ("cabinPositionX", c_float), ("cabinPositionY", c_float), ("cabinPositionZ", c_float),
        ("headPositionX", c_float), ("headPositionY", c_float), ("headPositionZ", c_float),
        ("truckHookPositionX", c_float), ("truckHookPositionY", c_float), ("truckHookPositionZ", c_float),
        ("truckWheelPositionX", c_float * 16), ("truckWheelPositionY", c_float * 16),
        ("truckWheelPositionZ", c_float * 16),
    ]


class TruckFv(ctypes.Structure):
    _fields_ = [
        ("lv_accelerationX", c_float), ("lv_accelerationY", c_float), ("lv_accelerationZ", c_float),
        ("av_accelerationX", c_float), ("av_accelerationY", c_float), ("av_accelerationZ", c_float),
        ("accelerationX", c_float), ("accelerationY", c_float), ("accelerationZ", c_float),
        ("aa_accelerationX", c_float), ("aa_accelerationY", c_float), ("aa_accelerationZ", c_float),
        ("cabinAVX", c_float), ("cabinAVY", c_float), ("cabinAVZ", c_float),
        ("cabinAAX", c_float), ("cabinAAY", c_float), ("cabinAAZ", c_float),
    ]


class TruckFp(ctypes.Structure):
    _fields_ = [
        ("cabinOffsetX", c_float), ("cabinOffsetY", c_float), ("cabinOffsetZ", c_float),
        ("cabinOffsetrotationX", c_float), ("cabinOffsetrotationY", c_float), ("cabinOffsetrotationZ", c_float),
        ("headOffsetX", c_float), ("headOffsetY", c_float), ("headOffsetZ", c_float),
        ("headOffsetrotationX", c_float), ("headOffsetrotationY", c_float), ("headOffsetrotationZ", c_float),
    ]


class TruckDp(ctypes.Structure):
    _fields_ = [
        ("coordinateX", c_double), ("coordinateY", c_double), ("coordinateZ", c_double),
        ("rotationX", c_double), ("rotationY", c_double), ("rotationZ", c_double),
    ]


class ConfigS(ctypes.Structure):
    _fields_ = [
        ("truckBrandId", c_char * S), ("truckBrand", c_char * S), ("truckId", c_char * S),
        ("truckName", c_char * S), ("cargoId", c_char * S), ("cargo", c_char * S),
        ("cityDstId", c_char * S), ("cityDst", c_char * S), ("compDstId", c_char * S),
        ("compDst", c_char * S), ("citySrcId", c_char * S), ("citySrc", c_char * S),
        ("compSrcId", c_char * S), ("compSrc", c_char * S), ("shifterType", c_char * 16),
        ("truckLicensePlate", c_char * S), ("truckLicensePlateCountryId", c_char * S),
        ("truckLicensePlateCountry", c_char * S), ("jobMarket", c_char * 32),
    ]


class GameplayS(ctypes.Structure):
    _fields_ = [
        ("fineOffence", c_char * 32),
        ("ferrySourceName", c_char * S), ("ferryTargetName", c_char * S),
        ("ferrySourceId", c_char * S), ("ferryTargetId", c_char * S),
        ("trainSourceName", c_char * S), ("trainTargetName", c_char * S),
        ("trainSourceId", c_char * S), ("trainTargetId", c_char * S),
    ]


class ConfigUll(ctypes.Structure):
    _fields_ = [("jobIncome", c_ulonglong)]


class GameplayLl(ctypes.Structure):
    _fields_ = [
        ("jobCancelledPenalty", c_longlong), ("jobDeliveredRevenue", c_longlong),
        ("fineAmount", c_longlong), ("tollgatePayAmount", c_longlong),
        ("ferryPayAmount", c_longlong), ("trainPayAmount", c_longlong),
    ]


class SpecialB(ctypes.Structure):
    _fields_ = [
        ("onJob", c_bool), ("jobFinished", c_bool), ("jobCancelled", c_bool),
        ("jobDelivered", c_bool), ("fined", c_bool), ("tollgate", c_bool),
        ("ferry", c_bool), ("train", c_bool), ("refuel", c_bool), ("refuelPayed", c_bool),
    ]


class Substances(ctypes.Structure):
    _fields_ = [("substance", (c_char * S) * 25)]


class Trailers(ctypes.Structure):
    _fields_ = [("trailer", ScsTrailer * 10)]


class ScsTelemetryMap(ctypes.Structure):
    _fields_ = [
        ("sdkActive", c_bool), ("placeHolder", c_char * 3),
        ("paused", c_bool), ("placeHolder2", c_char * 3),
        ("time", c_ulonglong), ("simulatedTime", c_ulonglong), ("renderTime", c_ulonglong),
        ("multiplayerTimeOffset", c_longlong),
        ("scs_values", ScsValues), ("common_ui", CommonUi), ("config_ui", ConfigUi),
        ("truck_ui", TruckUi), ("gameplay_ui", GameplayUi), ("buffer_ui", c_char * 48),
        ("common_i", CommonI), ("truck_i", TruckI), ("gameplay_i", GameplayI),
        ("buffer_i", c_char * 56),
        ("common_f", CommonF), ("config_f", ConfigF), ("truck_f", TruckF),
        ("gameplay_f", GameplayF), ("job_f", JobF), ("buffer_f", c_char * 28),
        ("config_b", ConfigB), ("truck_b", TruckB), ("gameplay_b", GameplayB),
        ("buffer_b", c_char * 25),
        ("config_fv", ConfigFv), ("truck_fv", TruckFv), ("buffer_fv", c_char * 60),
        ("truck_fp", TruckFp), ("buffer_fp", c_char * 152),
        ("truck_dp", TruckDp), ("buffer_dp", c_char * 52),
        ("config_s", ConfigS), ("gameplay_s", GameplayS), ("buffer_s", c_char * 20),
        ("config_ull", ConfigUll), ("buffer_ull", c_char * 192),
        ("gameplay_ll", GameplayLl), ("buffer_ll", c_char * 52),
        ("special_b", SpecialB), ("buffer_special", c_char * 90),
        ("substances", Substances), ("trailer", Trailers),
    ]


def _cstr(b):
    if isinstance(b, bytes):
        end = b.find(b"\x00")
        if end >= 0:
            b = b[:end]
        return b.decode("utf-8", errors="replace").strip()
    return str(b)


class TelemetryError(Exception):
    pass


@dataclass
class TruckSnapshot:
    sdk_active: bool = False
    paused: bool = False
    time_abs_min: int = 0
    time_abs_delivery_min: int = 0
    planned_distance_km: int = 0
    rest_stop_min: int = 0
    speed_mps: float = 0.0
    engine_rpm: float = 0.0
    user_steer: float = 0.0
    user_throttle: float = 0.0
    user_brake: float = 0.0
    fuel: float = 0.0
    fuel_capacity: float = 0.0
    fuel_avg_consumption: float = 0.0
    fuel_range_km: float = 0.0
    adblue: float = 0.0
    adblue_capacity: float = 0.0
    cargo_mass: float = 0.0
    wear_engine: float = 0.0
    wear_transmission: float = 0.0
    wear_cabin: float = 0.0
    wear_chassis: float = 0.0
    wear_wheels: float = 0.0
    odometer_km: float = 0.0
    route_distance_km: float = 0.0
    route_time_min: float = 0.0
    speed_limit_mps: float = 0.0
    job_cargo_damage: float = 0.0
    is_cargo_loaded: bool = False
    park_brake: bool = False
    engine_enabled: bool = False
    cruise_control: bool = False
    wipers: bool = False
    lights_high_beam: bool = False
    truck_brand: str = ""
    truck_name: str = ""
    truck_license: str = ""
    cargo: str = ""
    cargo_id: str = ""
    city_dst: str = ""
    city_src: str = ""
    fine_offence: str = ""
    job_income: int = 0
    fine_amount: int = 0
    job_cancelled_penalty: int = 0
    job_delivered_revenue: int = 0
    tollgate_amount: int = 0
    on_job: bool = False
    ev_job_finished: bool = False
    ev_job_cancelled: bool = False
    ev_job_delivered: bool = False
    ev_fined: bool = False
    ev_tollgate: bool = False
    ev_refuel: bool = False
    ev_refuel_payed: bool = False
    trailer_attached: bool = False
    trailer_cargo_damage: float = 0.0
    trailer_license: str = ""
    world_x: float = 0.0
    world_y: float = 0.0
    world_z: float = 0.0

    @property
    def speed_kmh(self) -> float:
        return self.speed_mps * 3.6

    @property
    def speed_limit_kmh(self) -> float:
        return self.speed_limit_mps * 3.6

    @property
    def is_speeding(self) -> bool:
        return self.speed_limit_mps > 1.0 and self.speed_mps > self.speed_limit_mps + 0.5

    @property
    def fuel_percent(self) -> float:
        if self.fuel_capacity <= 0:
            return 0.0
        return self.fuel / self.fuel_capacity * 100.0

    @property
    def max_damage(self) -> float:
        return max(self.wear_engine, self.wear_transmission,
                   self.wear_cabin, self.wear_chassis, self.wear_wheels)

    @property
    def damage_parts(self) -> dict:
        return {
            "engine": self.wear_engine, "transmission": self.wear_transmission,
            "cabin": self.wear_cabin, "chassis": self.wear_chassis,
            "wheels": self.wear_wheels,
        }

    @property
    def power_type(self) -> str:
        if self.fuel_capacity > 10.0 or self.fuel > 1.0:
            return "diesel"
        name = (self.truck_name + " " + self.truck_brand).lower()
        ev_hints = ("electric", "e-tech", "etech", "ev ")
        if any(h in name for h in ev_hints):
            return "electric"
        return "diesel" if self.fuel_capacity > 0.0 else "unknown"

    @property
    def delivery_remaining_min(self) -> Optional[int]:
        if not self.on_job or self.time_abs_delivery_min <= 0:
            return None
        return self.time_abs_delivery_min - self.time_abs_min

    @property
    def route_remaining_km(self) -> float:
        return self.route_distance_km / 1000.0

    @property
    def route_remaining_time_min(self) -> float:
        return self.route_time_min / 60.0

    @property
    def trip_progress_percent(self) -> Optional[float]:
        if not self.on_job or self.planned_distance_km <= 0:
            return None
        remaining = self.route_remaining_km
        p = (1.0 - remaining / self.planned_distance_km) * 100.0
        return max(0.0, min(100.0, p))

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["speed_kmh"] = self.speed_kmh
        d["speed_limit_kmh"] = self.speed_limit_kmh
        d["is_speeding"] = self.is_speeding
        d["fuel_percent"] = self.fuel_percent
        d["max_damage"] = self.max_damage
        d["damage_parts"] = self.damage_parts
        d["power_type"] = self.power_type
        d["route_remaining_km"] = self.route_remaining_km
        d["route_remaining_time_min"] = self.route_remaining_time_min
        d["trip_progress_percent"] = self.trip_progress_percent
        d["delivery_remaining_min"] = self.delivery_remaining_min
        return d


class TelemetryReader:
    """打开共享内存并解析快照。"""

    def __init__(self):
        self._view = None
        self._h = None
        self._addr = None

    def open(self) -> bool:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            fn = kernel32.OpenFileMappingW
            fn.restype = ctypes.c_void_p
            fn.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
            h = None
            for name in MMF_NAMES:
                h = fn(0x0004, False, name)
                if h:
                    break
            if not h:
                return False
            view = kernel32.MapViewOfFile
            view.restype = ctypes.c_void_p
            view.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
            addr = view(h, 0x0004, 0, 0, 0)
            if not addr:
                kernel32.CloseHandle(ctypes.c_void_p(h))
                return False
            self._h = h
            self._addr = addr
            self._view = (c_char * MMF_SIZE).from_address(addr)
            return True
        except Exception:
            return False

    def __enter__(self):
        if not self.open():
            raise TelemetryError("cannot open shared memory")
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            kernel32 = ctypes.WinDLL("kernel32")
            if self._addr:
                kernel32.UnmapViewOfFile(ctypes.c_void_p(self._addr))
            if self._h:
                kernel32.CloseHandle(ctypes.c_void_p(self._h))
        except Exception:
            pass
        self._view = None
        self._h = None
        self._addr = None

    @property
    def is_open(self) -> bool:
        return self._view is not None

    def snapshot(self) -> TruckSnapshot:
        if self._view is None:
            raise TelemetryError("not open")
        m = ScsTelemetryMap.from_buffer(self._view)
        s = TruckSnapshot()
        s.sdk_active = bool(m.sdkActive)
        s.paused = bool(m.paused)
        s.time_abs_min = m.common_ui.time_abs
        cu = m.config_ui
        s.time_abs_delivery_min = cu.time_abs_delivery
        s.planned_distance_km = cu.plannedDistanceKm
        s.rest_stop_min = m.common_i.restStop
        cf = m.config_f
        s.fuel_capacity = cf.fuelCapacity
        s.adblue_capacity = cf.adblueCapacity
        s.cargo_mass = cf.cargoMass
        tf = m.truck_f
        s.speed_mps = tf.speed
        s.engine_rpm = tf.engineRpm
        s.user_steer = tf.userSteer
        s.user_throttle = tf.userThrottle
        s.user_brake = tf.userBrake
        s.fuel = tf.fuel
        s.fuel_avg_consumption = tf.fuelAvgConsumption
        s.fuel_range_km = tf.fuelRange
        s.adblue = tf.adblue
        s.wear_engine = tf.wearEngine
        s.wear_transmission = tf.wearTransmission
        s.wear_cabin = tf.wearCabin
        s.wear_chassis = tf.wearChassis
        s.wear_wheels = tf.wearWheels
        s.odometer_km = tf.truckOdometer
        s.route_distance_km = tf.routeDistance
        s.route_time_min = tf.routeTime
        s.speed_limit_mps = tf.speedLimit
        s.job_cargo_damage = m.job_f.cargoDamage
        cb = m.config_b
        s.is_cargo_loaded = bool(cb.isCargoLoaded)
        tb = m.truck_b
        s.park_brake = bool(tb.parkBrake)
        s.engine_enabled = bool(tb.engineEnabled)
        s.cruise_control = bool(tb.cruiseControl)
        s.wipers = bool(tb.wipers)
        s.lights_high_beam = bool(tb.lightsBeamHigh)
        cs = m.config_s
        s.truck_brand = _cstr(cs.truckBrand)
        s.truck_name = _cstr(cs.truckName)
        s.truck_license = _cstr(cs.truckLicensePlate)
        s.cargo_id = _cstr(cs.cargoId)
        s.cargo = _cstr(cs.cargo)
        s.city_dst = _cstr(cs.cityDst)
        s.city_src = _cstr(cs.citySrc)
        s.fine_offence = _cstr(m.gameplay_s.fineOffence)
        dp = m.truck_dp
        s.world_x = dp.coordinateX
        s.world_y = dp.coordinateY
        s.world_z = dp.coordinateZ
        s.job_income = m.config_ull.jobIncome
        gl = m.gameplay_ll
        s.fine_amount = gl.fineAmount
        s.job_cancelled_penalty = gl.jobCancelledPenalty
        s.job_delivered_revenue = gl.jobDeliveredRevenue
        s.tollgate_amount = gl.tollgatePayAmount
        sp = m.special_b
        s.on_job = bool(sp.onJob)
        s.ev_job_finished = bool(sp.jobFinished)
        s.ev_job_cancelled = bool(sp.jobCancelled)
        s.ev_job_delivered = bool(sp.jobDelivered)
        s.ev_fined = bool(sp.fined)
        s.ev_tollgate = bool(sp.tollgate)
        s.ev_refuel = bool(sp.refuel)
        s.ev_refuel_payed = bool(sp.refuelPayed)
        tr = m.trailer.trailer[0]
        s.trailer_attached = bool(tr.com_b.attached)
        s.trailer_cargo_damage = tr.com_f.cargoDamage
        s.trailer_license = _cstr(tr.con_s.licensePlate)
        return s
