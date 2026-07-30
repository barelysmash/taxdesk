#requires -Version 5.1
<#
.SYNOPSIS
  Deploy taxdesk to guildenstern over Tailscale.

.DESCRIPTION
  Follows the reliable pattern: stage source on the remote, run scripts there,
  do not pass multi-line commands through SSH. Each step is idempotent — safe
  to re-run after fixing a probe miss or a config tweak.

  This script does NOT use Caddy. The API binds to the Tailscale IP and is
  reachable across the tailnet only.

.PARAMETER Host
  Remote host (Tailscale name or IP). Default: 100.113.110.44

.PARAMETER User
  Remote user with sudo. Default: barelysmash

.PARAMETER Step
  Which step to run: all, push, install, build, status. Default: all.

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 -Step push      # just rsync the source
  .\deploy.ps1 -Step install   # run install.sh (probe + backfill + services)
  .\deploy.ps1 -Step build     # build the frontend
  .\deploy.ps1 -Step status    # check service state and API health
#>

[CmdletBinding()]
param(
    [string]$RemoteHost = "100.113.110.44",
    [string]$User       = "barelysmash",
    [ValidateSet("all","push","install","build","status")]
    [string]$Step       = "all"
)

$ErrorActionPreference = "Stop"
$SrcDir = Join-Path $PSScriptRoot ".." | Resolve-Path
$Stage  = "/tmp/taxdesk-deploy"

function Say($msg) {
    Write-Host ""
    Write-Host "== $msg ==" -ForegroundColor Cyan
}

function RemoteExec($cmd) {
    Write-Host "  remote: $cmd" -ForegroundColor DarkGray
    ssh "$User@$RemoteHost" $cmd
    if ($LASTEXITCODE -ne 0) {
        throw "remote command failed (exit $LASTEXITCODE): $cmd"
    }
}

function Push {
    Say "Pushing source to ${RemoteHost}:${Stage}"

    # Clean any prior staging so deletions on local also delete on remote.
    RemoteExec "rm -rf '$Stage' && mkdir -p '$Stage'"

    # Use scp with -r since rsync isn't always present on Windows.
    # Exclude node_modules / venv / dist / pyc.
    $tarPath = Join-Path $env:TEMP "taxdesk-stage.tar.gz"
    Push-Location $SrcDir
    try {
        # tar is built into Win10+; use it to avoid copying junk.
        tar --exclude='node_modules' --exclude='.venv' `
            --exclude='__pycache__' --exclude='*.pyc' `
            --exclude='dist' --exclude='.env.local' `
            -czf $tarPath taxdesk
        if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    } finally { Pop-Location }

    scp $tarPath "${User}@${RemoteHost}:${Stage}/taxdesk.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
    Remove-Item $tarPath

    RemoteExec "cd '$Stage' && tar xzf taxdesk.tar.gz && ls -la taxdesk"
}

function Install {
    Say "Running install.sh on remote (probe + backfill + services)"
    RemoteExec "sudo bash '$Stage/taxdesk/deploy/install.sh'"
}

function Build {
    Say "Building frontend"
    RemoteExec "cd /opt/taxdesk/web && npm install --no-fund --no-audit && npm run build"
    RemoteExec "ls -la /opt/taxdesk/web/dist/"
}

function Status {
    Say "Service status"
    RemoteExec "systemctl is-active taxdesk-api taxdesk-scrape.timer || true"
    RemoteExec "systemctl status taxdesk-api --no-pager -l | head -20 || true"

    Say "API health"
    $bindHost = ssh "$User@$RemoteHost" "grep -E '^TAXDESK_BIND_HOST=' /etc/taxdesk/taxdesk.env | cut -d= -f2"
    $bindPort = ssh "$User@$RemoteHost" "grep -E '^TAXDESK_BIND_PORT=' /etc/taxdesk/taxdesk.env | cut -d= -f2"
    if (-not $bindHost) { $bindHost = "127.0.0.1" }
    if (-not $bindPort) { $bindPort = "8770" }
    RemoteExec "curl -sf http://${bindHost}:${bindPort}/api/health | head -c 500 || echo 'health check FAILED'"

    Say "Latest scrape log"
    RemoteExec "journalctl -u taxdesk-scrape --no-pager -n 30 || true"
}

# ---------------------------------------------------------------------------

switch ($Step) {
    "push"    { Push }
    "install" { Install }
    "build"   { Build }
    "status"  { Status }
    "all"     {
        Push
        Install
        Build
        Status
        Write-Host ""
        Write-Host "Done. Visit http://$(ssh "$User@$RemoteHost" 'grep -E ^TAXDESK_BIND_HOST= /etc/taxdesk/taxdesk.env | cut -d= -f2'):$(ssh "$User@$RemoteHost" 'grep -E ^TAXDESK_BIND_PORT= /etc/taxdesk/taxdesk.env | cut -d= -f2') from a tailnet device." -ForegroundColor Green
    }
}
