# Independent local gateway tunnel

The on-demand Task Scheduler launcher keeps `cloudflared` outside the Codex tool
job. It exposes the existing authenticated gateway on `127.0.0.1:8879` and waits
for its exact owned process. PAPER backend restarts use a separate stop marker.

Run from the checkout that owns the gateway, as the signed-in Windows user:

```powershell
.\scripts\install_local_tunnel_task.ps1
```

This registers a limited interactive task with no stored credentials, logon
triggers, or automatic restarts. Repeated installation preserves a running
verified task and refuses a conflicting task with the same name.

For a reviewed cutover, first verify the old process with
`.\scripts\start_local_tunnel.ps1 -Action Status`, then stop that owned tunnel
with `-Action Stop`. Verify local `/user/health` before starting the new task:

```powershell
Start-ScheduledTask -TaskName 'NEO Local Gateway Tunnel'
Get-ScheduledTask -TaskName 'NEO Local Gateway Tunnel'
.\scripts\start_local_tunnel.ps1 -Action Status
```

Read `.runtime/tunnel/process.json` for the new temporary hostname. Verify its
public `/user/health`, set GitHub `NEO_API_URL`, deploy Pages, and check the
authenticated dashboard. The task does not publish or update that variable.
If `cloudflared` exits, the task ends and records the event in
`.runtime/tunnel/supervisor.log`; the next start must be explicit because the
hostname can change. Other Cloudflared services are left alone.

Cloudflare [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
have changing hostnames and no uptime guarantee. A stable production hostname
requires a named tunnel. This task preserves the gateway's existing API
authentication and does not add access to the shared monitor or LIVE trading.
