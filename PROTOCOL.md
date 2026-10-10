# Urja Meter Ops — Protocol Discovery & Reverse-Engineering Report (`PROTOCOL.md`)

## 1. Executive Summary
This document records the protocol discovery, network inspection, authentication mechanisms, and data schemas reverse-engineered from the legacy **Urja Meter Ops** portal (`https://urja-ops.flockenergy.tech`).

The portal operates as a SvelteKit single-page application (SPA) backed by modern authentication (`better-auth`) and a suite of internal REST and SSR hydration endpoints.

---

## 2. Authentication & Session Architecture

### 2.1 Login Workflow
- **Endpoint**: `POST https://urja-ops.flockenergy.tech/login`
- **Content-Type**: `application/x-www-form-urlencoded`
- **Payload**:
  ```
  email=operator@urja.local&password=urja-ops-2026
  ```

#### Critical Finding: SvelteKit CSRF Header Enforcement
A naive HTTP client attempting a standard POST request receives:
```http
HTTP/1.1 403 Forbidden
Content-Length: 46
Cross-site POST form submissions are forbidden.
```
**Mechanism**: SvelteKit strictly validates that incoming mutation requests (such as form POSTs) include matching origin headers.
**Resolution**: The client must explicitly attach:
```http
Origin: https://urja-ops.flockenergy.tech
Referer: https://urja-ops.flockenergy.tech/login
```

#### Successful Login Response
```http
HTTP/1.1 200 OK
Content-Type: application/json
Set-Cookie: __Secure-better-auth.session_token=<TOKEN>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax

{"type":"redirect","status":303,"location":"/meters"}
```

### 2.2 Session Persistence & Token Characteristics
- **Cookie Name**: `__Secure-better-auth.session_token`
- **Lifetime**: 3600 seconds (1 hour)
- **Flags**: `HttpOnly`, `Secure`, `SameSite=Lax`
- **Session Verification**: `GET /api/auth/get-session` returns active user session metadata:
  ```json
  {
    "session": {
      "expiresAt": "2026-10-10T15:23:26.325Z",
      "token": "...",
      "createdAt": "2026-10-10T14:23:26.325Z",
      "ipAddress": "..."
    },
    "user": {
      "id": "...",
      "email": "operator@urja.local",
      "name": "Ops Desk"
    }
  }
  ```

### 2.3 Expiration & Re-Authentication Behavior
When a session expires or an unauthorized request is issued:
- API endpoints (`/portal/*`) return:
  ```http
  HTTP/1.1 401 Unauthorized
  {"error":"unauthorized","message":"A valid session is required."}
  ```
- Web page routes (`/meters`, `/transformers`) issue a 302/303 redirect or render the `/login` HTML page.
- **Auto-Reauthentication Strategy**: Our client adapter intercepts both 401 status codes and redirects to `/login`, immediately triggers `login()`, updates the session cookie jar, and replays the original request.

---

## 3. Discovered Internal Endpoints

| Endpoint | Method | Auth Required | Description / Payload |
| :--- | :--- | :--- | :--- |
| `/login` | `POST` | No | Authenticates operator (`email`, `password`) with Origin headers |
| `/api/auth/get-session` | `GET` | Cookie | Returns current session validity and user profile |
| `/portal/meters/search` | `GET` | Cookie | Query meters: `?q={search}&page={page}` (page size = 20) |
| `/portal/dts` | `GET` | Cookie | Query Distribution Transformers: `?page={page}` (40 DTs total) |
| `/portal/keys` | `GET` | Cookie | Returns `{ data: { signingSecret: "<secret>" } }` |
| `/portal/export` | `GET` | HMAC Signed | Complete export of all 403 meters with hierarchy and geo coordinates |
| `/portal/meters/{id}/geo` | `GET` | Cookie | Returns `{ data: { latitude: "...", longitude: "..." } }` |
| `/portal/meters/{id}/energy` | `GET` | Cookie | Returns time-series consumption readings (`timestamp`, `kwh`, `kvah`, `voltR`) |
| `/meters/{id}/__data.json` | `GET` | Cookie | SvelteKit SSR serialized node data containing meter nameplate and hierarchy |
| `/meters/{id}` | `GET` | Cookie | Fallback HTML page containing `<dl>` nameplate and `<nav>` breadcrumbs |

---

## 4. Cryptographic Discovery: HMAC-SHA256 Request Signing

Inspection of the client bundle `4.2Bgc2kUI.js` uncovered a protected data export endpoint `/portal/export` used by the "Export all meters" UI button.

### 4.1 Signature Algorithm
1. The client requests the current signing key from `GET /portal/keys`:
   ```json
   { "data": { "signingSecret": "I3dZPPf5CgTp7JyGNMI8i6z8LFR7TmSR" } }
   ```
2. The client captures the current Unix epoch timestamp in seconds: `timestamp = str(int(time.time()))`.
3. The message is constructed by concatenating:
   ```
   {METHOD}\n{PATH}\n{QUERY}\n{TIMESTAMP}
   ```
   Example:
   ```
   GET\n/portal/export\npage=1\n1760105700
   ```
4. An HMAC-SHA256 digest is generated using `signingSecret` and encoded as a lowercase hexadecimal string.
5. The request is dispatched with headers:
   ```http
   x-timestamp: 1760105700
   x-signature: 4a2b9e...
   ```

### 4.2 Value of this Endpoint
This endpoint yields the complete electrical network tree for all 403 meters in a single authoritative call, including nested Zone, Circle, Division, Subdivision, Substation, Feeder, and DT codes.

---

## 5. Data Structures, Disparities & Anomalies

### 5.1 Dual-Build Schema Inconsistency (`legacy` vs `v2`)
The legacy portal contains two generations of meter records:
- **Build `legacy` (e.g. Meter `J100000`)**:
  - Nameplate is stored in an array of parameter objects:
    ```json
    [
      {"parameterName": "Meter ID", "parameterValue": "J100000"},
      {"parameterName": "Serial No", "parameterValue": "SE33962"},
      {"parameterName": "Make", "parameterValue": "HPL"},
      {"parameterName": "Phase Type", "parameterValue": "single"},
      {"parameterName": "Installation Status", "parameterValue": "Decommissioned"}
    ]
    ```
- **Build `v2` (e.g. Meter `J100004`)**:
  - Nameplate is stored in an embedded stringified JSON field `classData`:
    ```json
    "classData": "{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",\"PhaseType\":\"single\",\"InstallationStatus\":\"Faulty\",\"InstallationType\":\"CT Operated\"}}"
    ```
- **Normalization Solution**: Our client adapter detects whether `classData` or `data` is present, unwraps the nested structure, and canonicalizes keys into unified camel/snake case properties (`meter_id`, `serial_number`, `make`, `phase_type`, `install_status`, `install_type`).

### 5.2 Numeric Values Formatted as Strings
In `/portal/meters/{id}/energy` and `/portal/meters/{id}/geo`:
- Voltages and energies are returned as strings (e.g. `"kwh": "48438.74"`, `"voltR": "226"`).
- Inactive or missing readings use empty strings (`""`) or dashes (`"—"`).
- Coordinates are strings (e.g. `"latitude": "26.938961002479868"`).
- **Normalization Solution**: All numeric fields are coerced to `float` or `None`.

### 5.3 Non-Standard Date-Time Formats
- Timestamps in the energy series are formatted as `DD/MM/YYYY HH:MM` (e.g. `"23/06/2026 23:30"`).
- **Normalization Solution**: Parsed with `datetime.strptime(..., "%d/%m/%Y %H:%M")` and converted to ISO-8601 (`"2026-06-23T23:30:00"`).

### 5.4 Empty Initial Server-Side Render (SSR) Tables
A direct GET on `/meters` or `/transformers` returns empty tables with `"No meters found."` or `"0 total"` because data hydration occurs purely client-side via JavaScript fetch calls. Scrapers relying purely on static HTML table parsing without evaluating JavaScript would fail. Our solution calls the underlying `/portal/` JSON endpoints directly and only parses SSR HTML when extracting nameplate dl elements.
