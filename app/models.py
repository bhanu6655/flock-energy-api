from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    email: str = Field(
        ...,
        description="Operator email address",
        json_schema_extra={"example": "operator@urja.local"}
    )
    password: str = Field(
        ...,
        description="Operator password",
        json_schema_extra={"example": "urja-ops-2026"}
    )

class LoginResponse(BaseModel):
    success: bool
    message: str
    session_token: Optional[str] = None
    expires_at: Optional[str] = None

class AuthStatusResponse(BaseModel):
    authenticated: bool
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    session_expires_at: Optional[str] = None

class MeterSummary(BaseModel):
    meter_id: str = Field(..., description="Unique alphanumeric identifier for the meter")
    serial_number: str = Field(..., description="Physical serial number stamped on meter")
    make: str = Field(..., description="Manufacturer (e.g. HPL, L&T, Genus, Secure, Allied)")
    phase_type: str = Field(..., description="single or three phase")
    install_status: str = Field(..., description="Operational status: Installed, Decommissioned, Faulty")
    dt_code: Optional[str] = Field(None, description="Distribution Transformer code it belongs to")

class MeterListResponse(BaseModel):
    total: int = Field(..., description="Total number of meters matching query")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(default=20, description="Number of items per page")
    total_pages: int = Field(..., description="Total pages available")
    data: List[MeterSummary] = Field(..., description="List of meters")

class GeoCoordinates(BaseModel):
    latitude: Optional[float] = Field(None, description="Geographic latitude coordinate")
    longitude: Optional[float] = Field(None, description="Geographic longitude coordinate")

class MeterHierarchy(BaseModel):
    zone: Optional[str] = None
    circle: Optional[str] = None
    division: Optional[str] = None
    subdivision: Optional[str] = None
    substation: Optional[str] = None
    feeder: Optional[str] = None
    dt: Optional[str] = None
    breadcrumbs: Optional[List[str]] = None

class MeterDetailResponse(BaseModel):
    meter_id: str
    serial_number: str
    make: str
    phase_type: str
    install_status: str
    install_type: Optional[str] = None
    dt_code: Optional[str] = None
    build: Optional[str] = None
    geo: GeoCoordinates
    hierarchy: MeterHierarchy
    raw_nameplate: Dict[str, Any]

class ConsumptionReading(BaseModel):
    timestamp: str = Field(..., description="Normalized ISO-8601 formatted timestamp")
    raw_timestamp: str = Field(..., description="Original legacy timestamp string (e.g. DD/MM/YYYY HH:MM)")
    kwh: Optional[float] = Field(None, description="Active energy reading in kilowatt-hours (kWh)")
    kvah: Optional[float] = Field(None, description="Apparent energy reading in kilovolt-ampere-hours (kVAh)")
    voltage_r: Optional[float] = Field(None, description="R-phase voltage in Volts")

class ConsumptionSummary(BaseModel):
    total_readings: int
    latest_reading: Optional[ConsumptionReading] = None
    latest_kwh: Optional[float] = None
    min_voltage_r: Optional[float] = None
    max_voltage_r: Optional[float] = None
    start_timestamp: Optional[str] = None
    end_timestamp: Optional[str] = None

class MeterConsumptionResponse(BaseModel):
    meter_id: str
    total: int
    summary: Optional[ConsumptionSummary] = None
    readings: List[ConsumptionReading]

class TransformerItem(BaseModel):
    code: str = Field(..., description="DT code identifier (e.g. DT-001)")
    name: str = Field(..., description="Distribution transformer facility name")
    feeder_code: str = Field(..., description="Upstream Feeder code")
    capacity_kva: Optional[float] = Field(None, description="Transformer rated capacity in kVA")

class TransformerListResponse(BaseModel):
    total: int
    page: int
    page_size: int = Field(default=20)
    total_pages: int
    data: List[TransformerItem]

class HierarchyTreeResponse(BaseModel):
    total_meters: int
    zones_count: int
    tree: Dict[str, Any]

class HealthResponse(BaseModel):
    status: str
    legacy_portal_connected: bool
    legacy_url: str
    timestamp: str
