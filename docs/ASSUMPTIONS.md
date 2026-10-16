# Assumptions register

Ariadne's rules were ported from BI Governance Studio and from Qlik's
documentation. Neither is ground truth: the source application carries its
own assumptions, and a help page describes the platform on the day it was
written. Each entry below is a behaviour Ariadne depends on, where it came
from, how confident it is, and how to verify it on your tenant. Verify before
trusting a result that rests on it, and update this file when you do.

| Id | Assumption | Source | Confidence | How to verify |
|---|---|---|---|---|
| A1 | The Items API lists only apps in spaces the key's user is a member of; another user's Personal space is invisible. | Source application (observed on one tenant); Qlik docs on space membership | Medium | `diagnose_app_access` on an app known to be in another user's Personal space: Items listing fails, direct lookup may succeed. |
| A2 | Reading a load script needs ownership or "Can edit data in applications" in the app's space; Tenant Admin alone is refused. | Source application (observed: an admin key listed 1,908 apps and read no scripts) | Medium | `diagnose_app_access` with the intended service key on an app it does not own. |
| A3 | `POST /api/v1/apps/{id}/export?NoData=true` returns a Location pointing at the .qvf without data. | Qlik REST reference (apps, export) | Medium | `probe` is not shipped yet: run `snapshot_app` with `dry_run=false` on one test app and inspect the result. |
| A4 | `POST /api/v1/apps/import?name=&spaceId=&mode=new` places the imported app in the given space; if it lands in Personal, `PUT /apps/{id}/space` moves it. | Qlik REST reference (apps, import; apps, space) | Medium | Same as A3; the snapshot result reports whether a move was needed. |
| A5 | A managed-space (published) app can be exported by a user who can open it. | Qlik docs on managed spaces | Low | Same as A3 on an app in a managed space. If refused, the snapshot source must become the source app, which changes what "last published state" means. |
| A6 | Items report `originAppId` on published copies and `publishTime` on published apps. | Qlik Items API attributes | Medium | `list_apps` and inspect `origin_app_id` and `publish_time` on a known published copy. |
| A7 | `PUT /api/v1/apps/{id}` with `attributes.description` updates the description and keeps everything else. | Qlik REST reference (apps, update) | Medium | After a snapshot, `describe_app` on the snapshot shows the marker. |
| A8 | The lineage graph with `collapse=false` holds QVD nodes that `collapse=true` drops. | Source application (measured on one app: 150 vs 116 nodes) | Medium | `get_lineage_graph` on an app with QVD sources; count `nodes_by_kind.qvd`. |
| A9 | Qlik throttles REST at roughly 20 lineage calls in a burst (429 with Retry-After). | Source application (observed) | Medium | Watch `coverage.failed` on a tenant-wide tool; a 429 is retried and should not appear. |
| A10 | Master item properties update through `GetMeasure`/`GetDimension` + `SetProperties` with a shallow merge, and persist on `DoSave`. | Qlik Engine API reference; source application | High | `apply_master_items` with `dry_run=false` on a test app, then `get_master_items`. |
| A11 | The Items API `description` carries the app description so the snapshot marker is visible in listings. | Qlik Items API | Medium | After a snapshot, `list_apps` filtered to the archive space shows the marker in `description`. |
| A13 | `SetScript` followed by `DoSave` persists the script of the source app, and the developer-notes heading survives as a comment Qlik does not rewrite. | Qlik Engine API reference | High | `add_developer_note` with `dry_run=false` on a test app, then `get_load_script`. |
| A12 | Variables listed with `qShowReserved` and `qShowConfig` include every variable usage analysis should see. | Source application | Medium | Compare `get_master_items` variable count with the app's variable panel. |

Confidence levels: High means documented by Qlik and exercised by the source
application in production; Medium means documented or observed, not both;
Low means inferred.
