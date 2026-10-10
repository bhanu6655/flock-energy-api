import logging
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.config import settings
from app.client import UrjaPortalClient
from app.models import (
    LoginRequest,
    LoginResponse,
    AuthStatusResponse,
    MeterListResponse,
    MeterDetailResponse,
    MeterConsumptionResponse,
    TransformerListResponse,
    HierarchyTreeResponse,
    HealthResponse,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("urja_api")

# Shared singleton adapter instance with portal
portal_client = UrjaPortalClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    logger.info("Initializing Urja Meter Ops API wrapper...")
    try:
        portal_client.login()
        logger.info("Initial login to Urja legacy portal successful.")
    except Exception as e:
        logger.warning("Could not complete initial login on startup: %s. Will retry on request.", e)
    yield
    logger.info("Shutting down Urja Meter Ops API wrapper...")


app = FastAPI(
    title="Flock Energy - Urja Meter Ops API",
    version="1.0.0",
    description="""
# Urja Meter Ops REST API Wrapper

A modern, robust, and well-documented REST API proxy service encapsulating the legacy **Urja Meter Ops** portal.

### Key Capabilities:
- **Automated Session Lifecycle**: Transparent session authentication, cookie persistence, and auto-reauthentication upon expiration.
- **Data Cleansing & Normalization**: Transforms heterogeneous legacy responses (v1 array vs. v2 JSON stringified payloads) into standardized, typed JSON.
- **Full Hierarchy Extraction**: Decodes the entire electrical grid tree (Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Meters).
- **Cryptographic Request Signing**: Automatically retrieves signing keys and signs HMAC-SHA256 requests for restricted export endpoints.
- **Consumption Normalization**: Normalizes messy string readings and unformatted timestamps to ISO-8601 timestamps and numeric floating-point metrics.
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Enable CORS for downstream frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def root():
    """Redirect root to interactive Swagger API documentation."""
    return RedirectResponse(url="/docs")


@app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """
    Service health check verifying connectivity to the upstream Urja portal.
    """
    is_connected = portal_client.health_check()
    return {
        "status": "healthy" if is_connected else "degraded",
        "legacy_portal_connected": is_connected,
        "legacy_url": portal_client.base_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    "/api/v1/auth/login",
    response_model=LoginResponse,
    tags=["Authentication"],
    summary="Authenticate against Urja legacy portal",
)
def login(credentials: LoginRequest):
    """
    Manually authenticates or re-authenticates the service against the Urja portal
    using custom or operator credentials.
    """
    try:
        success = portal_client.login(email=credentials.email, password=credentials.password)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials for Urja portal.",
            )
        
        session_cookie = portal_client.session.cookies.get("__Secure-better-auth.session_token")
        return {
            "success": True,
            "message": "Successfully authenticated with Urja portal.",
            "session_token": session_cookie[:16] + "..." if session_cookie else None,
            "expires_at": "1 hour from issue (auto-refreshed)",
        }
    except Exception as e:
        logger.error("Authentication error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}",
        )


@app.get(
    "/api/v1/auth/status",
    response_model=AuthStatusResponse,
    tags=["Authentication"],
    summary="Check current session status",
)
def auth_status():
    """
    Queries session health against better-auth on the Urja portal.
    """
    try:
        session_info = portal_client.get_session_status()
        session_obj = session_info.get("session", {})
        user_obj = session_info.get("user", {})
        
        return {
            "authenticated": bool(session_obj),
            "user_email": user_obj.get("email"),
            "user_name": user_obj.get("name"),
            "session_expires_at": session_obj.get("expiresAt"),
        }
    except Exception as e:
        logger.error("Status check error: %s", e)
        return {"authenticated": False}


@app.get(
    "/api/v1/meters",
    response_model=MeterListResponse,
    tags=["Meters"],
    summary="Retrieve list of smart meters",
)
def list_meters(
    q: Optional[str] = Query(default="", description="Search query by meter ID or serial number"),
    page: int = Query(default=1, ge=1, description="Page number for pagination"),
):
    """
    Retrieves a paginated list of smart meters.
    Supports query filtering (`q`) across meter numbers and serials.
    """
    try:
        data = portal_client.list_meters(query=q or "", page=page)
        return data
    except Exception as e:
        logger.error("Failed to list meters: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching meters list: {str(e)}",
        )


@app.get(
    "/api/v1/meters/{id}",
    response_model=MeterDetailResponse,
    tags=["Meters"],
    summary="Retrieve specific meter details",
)
def get_meter(
    id: str,
):
    """
    Retrieves detailed specifications for an individual smart meter by ID.
    Merges geographic coordinates, electrical hierarchy, and normalized nameplate parameters.
    """
    try:
        details = portal_client.get_meter_details(meter_id=id)
        return details
    except Exception as e:
        logger.error("Failed to fetch details for meter %s: %s", id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching meter '{id}': {str(e)}",
        )


@app.get(
    "/api/v1/meters/{id}/consumption",
    response_model=MeterConsumptionResponse,
    tags=["Meters"],
    summary="Retrieve meter energy consumption history",
)
def get_meter_consumption(
    id: str,
):
    """
    Retrieves historical energy consumption readings for a meter.
    Transforms raw string measurements to floating-point numbers and formats timestamps to ISO-8601.
    Includes statistical summaries (latest reading, min/max voltage).
    """
    try:
        consumption = portal_client.get_meter_consumption(meter_id=id)
        return consumption
    except Exception as e:
        logger.error("Failed to fetch consumption for meter %s: %s", id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching consumption for meter '{id}': {str(e)}",
        )


@app.get(
    "/api/v1/transformers",
    response_model=TransformerListResponse,
    tags=["Transformers"],
    summary="Retrieve distribution transformers (DTs)",
)
def list_transformers(
    page: int = Query(default=1, ge=1, description="Page number for pagination"),
):
    """
    Retrieves paginated list of distribution transformers with capacity ratings and feeder codes.
    """
    try:
        dts = portal_client.list_transformers(page=page)
        return dts
    except Exception as e:
        logger.error("Failed to fetch transformers: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching transformers: {str(e)}",
        )


@app.get(
    "/api/v1/hierarchy",
    response_model=HierarchyTreeResponse,
    tags=["Hierarchy"],
    summary="Retrieve organized grid hierarchy tree",
)
def get_hierarchy():
    """
    Retrieves the complete electrical distribution tree:
    **Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Meters**.
    Leverages dynamic HMAC-SHA256 authenticated export for complete coverage.
    """
    try:
        tree = portal_client.get_hierarchy_tree()
        return tree
    except Exception as e:
        logger.error("Failed to generate hierarchy: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error assembling hierarchy tree: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True)
