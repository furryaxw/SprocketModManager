# Log Upload

[中文](log-upload-design.zh.md) | **English**

When the user picks an entry from the sidebar's "Upload Log" menu, the client reads the log
file that entry refers to — the manager's own log, or the log of one installed runtime — and
uploads it to the Hasty Paste II Quick API at `https://paste.furryaxw.top/api/q/`. There is no
background automatic upload.

Before sending, at most the latest 8 MiB is kept and sent as UTF-8 `text/plain` verbatim. The
server returns a 2xx status and the full paste URL; the client does not persist a copy of the
log and does not retry automatically.

The manager lists uploadable logs through `ClientApi.get_log_sources()`: the manager log is
always present and always available, while a runtime log's path is supplied by its identifier
according to the detected runtime layout and counts as available only if the file exists.
`ClientApi.upload_log(source_id)` uploads one of them and, on success, displays the returned
link. Nothing is uploaded at startup, when the catalog is refreshed, or on a background timer.
