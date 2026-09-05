so we are trying to create a AI solution for a service which provide the transport servie to the business or organisation for that we are having two folder in the core:
data - csv files of the transportations
data-md - explainaing on the csv data


now we are having two things to implement:
- a real time alert managment
- a chatbot to provide us report on different areas


a real time alert managment:

we are having alerts_data table which you have csv, so we just need to check if we are having any alerts there or not:
table schema:

| Column | Type | Meaning | Values (observed) |
|---|---|---|---|
| `business_unit` | object | Client account / business unit the alert belongs to | 5 values: `vanta-Aus`, `catalyst-Sac`, `orbit-Slc`, `vanta-Sea`, `pinnacle-Slc` |
| `trip_id` | object | Trip the alert relates to — join key to other files | 33,474 unique, comma-formatted, e.g. `"1,097,076"` |
| `stwid` | object | Employee/rider the alert relates to; `"0"` is a placeholder for trip-level alerts not tied to a specific employee | 9,314 unique, e.g. `"0"` (placeholder, most frequent) |
| `event_id` | object | Unique identifier for the alert/event | 51,699 unique UUIDs, e.g. `"37ceae1c-7fe7-4081-a96e-da66602024a7"` |
| `event_type` | object | Category of alert raised (e.g. route deviation, SOS) | 11 values: `DEVICE_NOT_REACHABLE`, `VEHICLE_STOPPAGE`, `WOMAN_TRAVELLING_ALONE`, `PANIC_FIXED_DEVICE`, `EMPLOYEE_GEOFENCE_VIOLATION`, `PANIC_DEVICE`, `PANIC_MOBILE`, `OVER_SPEEDING`, `FIRST_MALE_NO_SHOW`, `EMPLOYEE_SIGN_OFF_TIME_VIOLATION`, `SUPPLEMENTARY_ALERT` |
| `start_time` | object | Timestamp the alert was raised | e.g. `"May 1, 2026, 12:03 AM"` |
| `acknowledge_time` | object | Timestamp the alert was acknowledged; null if unacknowledged | e.g. `"May 1, 2026, 12:10 AM"`; 54 nulls |
| `state_text` | object | Current status text of the alert | `CLOSED`, `OPEN`, `NEW` |
| `severity` | object | Severity level (`Sev-1`/`Sev-2`/`Sev-3`) | `Sev-3`, `Sev-2`, `Sev-1`; 16,348 nulls. ⚠️ also contains a stray literal `"False"` — clean it out |
| `source` | object | System/source that raised the alert | `MOBILE`, `EXTERNAL_DEVICE`, `DEVICE`, `MOBILE_APP`; 39,350 nulls |

so there would be a watcher who will keep looking at the schema entry and if there is new entry then we will be having a screen which can flag that alerts. Now here we need to prioritise the the alerts based on the data of severity and the type of alert. 

- we will create synthtic data of alerts which are not acknowledged
- we should have fixed set of action button in the UI like acknowledge, escalatte, call driver, call employee and for the actions we will be having some sort of mapping, for which type of category what action needs to be taken. (that you can decide by looking at the data also create the synthetic data for demo purpose).
  -acknowledge : it will just the time stamp for the data
  -escalte: show a pop up with the draft mail and with send button it will just update acknowledge timestamp. (user Ollama integreated already to generate the draft mail according to the trip detail)
  - call driver: show the popup with calling deails of the driver
  - call emplyee: same as driver

-alert menu in side bar in stremlit.


- a chatbot to provide us report on different areas

we should be having a chabot for querying to the database, so user can ask any question loike:
- give details of this particular trip id
- queries related to alerts
- sla not match
- ota report
- report downlaad button for specific query. etc

and with the response if any action is required then we can show the actoons

now the chat can have checkpoint and human in the loop so we need to integrate that part as well.

Note: do not remove any menu which we already have in the streamlit for now just add two more module for these purposes.

note: if you have any query or doubt then ask before generating any result
