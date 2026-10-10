import time
import json
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger("urja_client")


class UrjaPortalClient:
    """
    Robust HTTP client adapter and scraper for the legacy Urja Meter Ops portal.
    Handles automated authentication, cookie persistence, session re-authentication,
    HMAC request signing, and HTML/JSON normalization.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.base_url = (base_url or settings.URJA_BASE_URL).rstrip("/")
        self.email = email or settings.URJA_OPERATOR_EMAIL
        self.password = password or settings.URJA_OPERATOR_PASSWORD
        self.timeout = timeout or settings.REQUEST_TIMEOUT
        self.session = requests.Session()
        self._signing_secret: Optional[str] = None
        self._cached_export: Optional[List[Dict[str, Any]]] = None
        self._cached_export_time: float = 0

    def login(self, email: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Authenticates against the Urja portal login endpoint.
        Requires Origin and Referer headers to satisfy SvelteKit CSRF validation.
        """
        target_email = email or self.email
        target_password = password or self.password

        login_url = f"{self.base_url}/login"
        payload = {"email": target_email, "password": target_password}
        headers = {
            "Origin": self.base_url,
            "Referer": login_url,
            "User-Agent": "FlockEnergy-UrjaAPI/1.0",
        }

        try:
            response = self.session.post(
                login_url,
                data=payload,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )

            if response.status_code in (200, 302, 303):
                # Verify session cookie was set
                if "__Secure-better-auth.session_token" in self.session.cookies:
                    logger.info("Successfully authenticated with Urja portal.")
                    return True
                
                # Check JSON redirect body response
                try:
                    data = response.json()
                    if data.get("type") == "redirect":
                        logger.info("Authentication succeeded with redirect response.")
                        return True
                except Exception:
                    pass

            logger.warning(
                "Login attempt failed. Status: %s, Body: %s",
                response.status_code,
                response.text[:200],
            )
            return False
        except Exception as e:
            logger.error("Exception during Urja login: %s", str(e))
            raise RuntimeError(f"Failed to connect to Urja portal login: {e}")

    def get_session_status(self) -> Dict[str, Any]:
        """
        Queries the internal better-auth session endpoint.
        """
        res = self._request("GET", "/api/auth/get-session")
        if res.status_code == 200:
            try:
                return res.json()
            except Exception:
                pass
        return {"authenticated": False}

    def _ensure_authenticated(self):
        """Ensures an active session token exists, logging in if missing."""
        if "__Secure-better-auth.session_token" not in self.session.cookies:
            logger.info("No active session cookie found; performing initial login.")
            success = self.login()
            if not success:
                raise RuntimeError("Failed to authenticate with legacy Urja portal.")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        Performs an HTTP request with automatic transparent session re-authentication
        if a 401 Unauthorized or redirect to /login is received.
        """
        self._ensure_authenticated()

        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        url = f"{self.base_url}{path}"
        res = self.session.request(method, url, **kwargs)

        # Detect session expiration
        is_expired = False
        if res.status_code == 401:
            is_expired = True
        elif res.status_code == 200 and "/login" in getattr(res, "url", "") and path != "/login":
            is_expired = True
        elif res.headers.get("content-type", "").startswith("application/json"):
            try:
                err_data = res.json()
                if err_data.get("error") == "unauthorized":
                    is_expired = True
            except Exception:
                pass

        if is_expired:
            logger.info("Session token expired or unauthorized. Performing auto-reauthentication...")
            if not self.login():
                raise RuntimeError("Auto-reauthentication against Urja portal failed.")
            # Retry original request once
            res = self.session.request(method, url, **kwargs)

        return res

    def get_signing_secret(self, force_refresh: bool = False) -> str:
        """
        Retrieves the dynamic HMAC signing secret from /portal/keys.
        """
        if self._signing_secret and not force_refresh:
            return self._signing_secret

        res = self._request("GET", "/portal/keys")
        if res.status_code == 200:
            try:
                secret = res.json().get("data", {}).get("signingSecret")
                if secret:
                    self._signing_secret = secret
                    return secret
            except Exception as e:
                logger.error("Failed to parse signing keys JSON: %s", e)
        raise RuntimeError(f"Unable to obtain signing secret: {res.status_code} {res.text}")

    def list_meters(self, query: str = "", page: int = 1) -> Dict[str, Any]:
        """
        Fetches meters list using the internal /portal/meters/search endpoint.
        Returns total count, page info, and list of normalized meter summaries.
        """
        endpoint = f"/portal/meters/search?q={requests.utils.quote(query)}&page={page}"
        res = self._request("GET", endpoint)

        if res.status_code != 200:
            raise RuntimeError(f"Failed to query meters list: {res.status_code} {res.text}")

        data = res.json()
        raw_list = data.get("data", [])
        total = data.get("total", len(raw_list))

        normalized_meters = []
        for m in raw_list:
            normalized_meters.append({
                "meter_id": m.get("meterId", ""),
                "serial_number": m.get("serialNo", ""),
                "make": m.get("make", "UNKNOWN"),
                "phase_type": m.get("phaseType", "UNKNOWN"),
                "install_status": m.get("installStatus", "UNKNOWN"),
                "dt_code": m.get("dtCode"),
            })

        page_size = 20
        total_pages = max(1, (total + page_size - 1) // page_size)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "data": normalized_meters,
        }

    def get_meter_details(self, meter_id: str) -> Dict[str, Any]:
        """
        Fetches detailed information for a specific meter, combining:
        1. Geo coordinates from /portal/meters/{id}/geo
        2. Nameplate & Hierarchy from SvelteKit SSR __data.json or HTML parsing
        Normalizes both legacy format and modern v2 format.
        """
        # 1. Fetch Geo Coordinates
        geo_res = self._request("GET", f"/portal/meters/{meter_id}/geo")
        lat = None
        lng = None
        if geo_res.status_code == 200:
            try:
                geo_obj = geo_res.json().get("data", {})
                if geo_obj.get("latitude"):
                    lat = float(geo_obj["latitude"])
                if geo_obj.get("longitude"):
                    lng = float(geo_obj["longitude"])
            except (ValueError, TypeError):
                pass

        # 2. Fetch Nameplate and Hierarchy via SvelteKit SSR JSON
        data_res = self._request("GET", f"/meters/{meter_id}/__data.json")
        nameplate: Dict[str, Any] = {}
        hierarchy_dict: Dict[str, Any] = {}
        build_type = "legacy"

        if data_res.status_code == 200:
            try:
                body = data_res.json()
                nodes = body.get("nodes", [])
                for node in nodes:
                    if not node or "data" not in node:
                        continue
                    d = node["data"]
                    if len(d) > 2 and d[1] == meter_id:
                        detail_meta = d[2]
                        if isinstance(detail_meta, dict):
                            if "classData" in detail_meta:
                                # v2 format: embedded stringified JSON
                                build_type = "v2"
                                raw_str = d[detail_meta["classData"]]
                                parsed = json.loads(raw_str).get("installed_meter", {})
                                for k, v in parsed.items():
                                    nameplate[k] = v
                            elif "data" in detail_meta:
                                # Legacy format: array of parameterName & parameterValue indices
                                build_type = "legacy"
                                indices = d[detail_meta["data"]]
                                for idx in indices:
                                    param = d[idx]
                                    pn = d[param["parameterName"]]
                                    pv = d[param["parameterValue"]]
                                    nameplate[pn] = pv

                        # Look for hierarchy elements in the node
                        for item in d:
                            if isinstance(item, dict) and "Zone" in item:
                                for hk, hv in item.items():
                                    hierarchy_dict[hk] = d[hv] if isinstance(hv, int) and hv < len(d) else hv
            except Exception as e:
                logger.warning("Error parsing __data.json for meter %s: %s", meter_id, e)

        # 3. Fallback to HTML scraping with BeautifulSoup if nameplate is missing
        if not nameplate:
            html_res = self._request("GET", f"/meters/{meter_id}")
            if html_res.status_code == 200:
                soup = BeautifulSoup(html_res.text, "html.parser")
                dl = soup.find("dl")
                if dl:
                    for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                        nameplate[dt.text.strip()] = dd.text.strip()
                navs = soup.find_all("nav")
                for nav in navs:
                    crumbs = [
                        span.text.strip()
                        for span in nav.find_all(["span", "a"])
                        if span.text.strip() and span.text.strip() not in ("›", "Meters", "Transformers")
                    ]
                    if len(crumbs) >= 3:
                        hierarchy_dict["breadcrumbs"] = crumbs

        # Normalize field names from legacy/v2 variations
        serial = (
            nameplate.get("serialNo")
            or nameplate.get("SerialNo")
            or nameplate.get("Serial No")
            or "N/A"
        )
        make = nameplate.get("make") or nameplate.get("Make") or "UNKNOWN"
        phase = nameplate.get("phaseType") or nameplate.get("PhaseType") or nameplate.get("Phase Type") or "UNKNOWN"
        status = (
            nameplate.get("installStatus")
            or nameplate.get("InstallationStatus")
            or nameplate.get("Installation Status")
            or "UNKNOWN"
        )
        install_type = (
            nameplate.get("installType")
            or nameplate.get("InstallationType")
            or nameplate.get("Installation Type")
        )
        dt_code = nameplate.get("dtCode") or nameplate.get("DT")

        return {
            "meter_id": meter_id,
            "serial_number": serial,
            "make": make,
            "phase_type": phase,
            "install_status": status,
            "install_type": install_type,
            "dt_code": dt_code,
            "build": build_type,
            "geo": {"latitude": lat, "longitude": lng},
            "hierarchy": {
                "zone": hierarchy_dict.get("Zone"),
                "circle": hierarchy_dict.get("Circle"),
                "division": hierarchy_dict.get("Division"),
                "subdivision": hierarchy_dict.get("Subdivision"),
                "substation": hierarchy_dict.get("Sub Station"),
                "feeder": hierarchy_dict.get("Feeder"),
                "dt": hierarchy_dict.get("DT"),
                "breadcrumbs": hierarchy_dict.get("breadcrumbs"),
            },
            "raw_nameplate": nameplate,
        }

    def get_meter_consumption(self, meter_id: str) -> Dict[str, Any]:
        """
        Fetches consumption readings from /portal/meters/{id}/energy.
        Cleanses whitespace, parses timestamps to ISO-8601, and normalizes
        strings to numeric floats.
        """
        res = self._request("GET", f"/portal/meters/{meter_id}/energy")
        if res.status_code != 200:
            raise RuntimeError(f"Failed to fetch consumption for meter {meter_id}: {res.status_code}")

        raw_readings = res.json().get("data", [])
        cleaned_readings = []

        def to_float(val: Any) -> Optional[float]:
            if val is None or val == "" or val == "—":
                return None
            try:
                return float(str(val).strip())
            except ValueError:
                return None

        voltages = []

        for r in raw_readings:
            raw_ts = str(r.get("timestamp", "")).strip()
            iso_ts = raw_ts

            if raw_ts:
                try:
                    dt = datetime.strptime(raw_ts, "%d/%m/%Y %H:%M")
                    iso_ts = dt.isoformat()
                except ValueError:
                    iso_ts = raw_ts

            kwh_val = to_float(r.get("kwh"))
            kvah_val = to_float(r.get("kvah"))
            volt_val = to_float(r.get("voltR"))

            if volt_val is not None:
                voltages.append(volt_val)

            cleaned_readings.append({
                "timestamp": iso_ts,
                "raw_timestamp": raw_ts,
                "kwh": kwh_val,
                "kvah": kvah_val,
                "voltage_r": volt_val,
            })

        # Generate summary metrics
        summary = None
        if cleaned_readings:
            latest = cleaned_readings[-1]
            summary = {
                "total_readings": len(cleaned_readings),
                "latest_reading": latest,
                "latest_kwh": latest.get("kwh"),
                "min_voltage_r": min(voltages) if voltages else None,
                "max_voltage_r": max(voltages) if voltages else None,
                "start_timestamp": cleaned_readings[0].get("timestamp"),
                "end_timestamp": cleaned_readings[-1].get("timestamp"),
            }

        return {
            "meter_id": meter_id,
            "total": len(cleaned_readings),
            "summary": summary,
            "readings": cleaned_readings,
        }

    def list_transformers(self, page: int = 1) -> Dict[str, Any]:
        """
        Fetches distribution transformers from /portal/dts?page={page}.
        """
        res = self._request("GET", f"/portal/dts?page={page}")
        if res.status_code != 200:
            raise RuntimeError(f"Failed to fetch transformers: {res.status_code}")

        raw = res.json()
        dts = raw.get("data", [])
        total = raw.get("total", len(dts))

        items = []
        for d in dts:
            cap = None
            if d.get("capacityKva") is not None:
                try:
                    cap = float(d["capacityKva"])
                except ValueError:
                    pass
            items.append({
                "code": d.get("code", ""),
                "name": d.get("name", ""),
                "feeder_code": d.get("feederCode", ""),
                "capacity_kva": cap,
            })

        page_size = 20
        total_pages = max(1, (total + page_size - 1) // page_size)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "data": items,
        }

    def export_all_meters(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Executes signed HMAC-SHA256 request to /portal/export?page=1.
        Caches results in memory for 5 minutes.
        """
        now = time.time()
        if not force_refresh and self._cached_export and (now - self._cached_export_time < 300):
            return self._cached_export

        secret = self.get_signing_secret()
        method = "GET"
        path = "/portal/export"
        query = "page=1"
        timestamp = str(int(now))
        msg = f"{method}\n{path}\n{query}\n{timestamp}".encode("utf-8")
        sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

        headers = {
            "x-timestamp": timestamp,
            "x-signature": sig,
        }

        res = self._request("GET", f"{path}?{query}", headers=headers)
        if res.status_code == 200:
            meters = res.json().get("data", [])
            self._cached_export = meters
            self._cached_export_time = now
            return meters

        # If secret expired or failed, retry once with fresh secret
        if res.status_code in (401, 403):
            fresh_secret = self.get_signing_secret(force_refresh=True)
            sig = hmac.new(fresh_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
            headers["x-signature"] = sig
            res2 = self._request("GET", f"{path}?{query}", headers=headers)
            if res2.status_code == 200:
                meters = res2.json().get("data", [])
                self._cached_export = meters
                self._cached_export_time = now
                return meters

        raise RuntimeError(f"Export all meters failed: {res.status_code} {res.text}")

    def get_hierarchy_tree(self) -> Dict[str, Any]:
        """
        Builds a complete hierarchical grid tree:
        Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Meters.
        """
        all_meters = self.export_all_meters()
        tree: Dict[str, Any] = {}

        for m in all_meters:
            h = m.get("hierarchy", {})
            z = h.get("zone", {}).get("name", "Unknown Zone")
            c = h.get("circle", {}).get("name", "Unknown Circle")
            d = h.get("division", {}).get("name", "Unknown Division")
            sd = h.get("subdivision", {}).get("name", "Unknown Subdivision")
            ss = h.get("substation", {}).get("name", "Unknown Substation")
            f = h.get("feeder", {}).get("name", "Unknown Feeder")
            dt = h.get("dt", {}).get("name", "Unknown DT")

            branch = tree.setdefault(z, {}).setdefault(c, {}).setdefault(d, {}).setdefault(sd, {}).setdefault(ss, {}).setdefault(f, {}).setdefault(dt, [])
            branch.append({
                "meter_id": m.get("meterId"),
                "serial_number": m.get("serialNo"),
                "status": m.get("installStatus"),
                "phase_type": m.get("phaseType"),
                "make": m.get("make"),
            })

        return {
            "total_meters": len(all_meters),
            "zones_count": len(tree),
            "tree": tree,
        }

    def health_check(self) -> bool:
        """Verifies reachability of the Urja legacy portal."""
        try:
            res = self.session.get(f"{self.base_url}/login", timeout=5.0)
            return res.status_code in (200, 302)
        except Exception:
            return False
