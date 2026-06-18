#requires -Version 5.1
<#
.SYNOPSIS
  One-shot setup script for agile-cards-board: defensive git lock cleanup,
  init, commit, GitHub repo creation, push.

.DESCRIPTION
  Run this once after Drew approves the scaffold. Idempotent: it will
  re-stage any new files, but it won't re-create the GitHub repo if it
  already exists (gh will error and the script will exit non-zero so you
  know).

  Defensive lock cleanup before every git operation mirrors the pattern
  used elsewhere in /dev: stale .git/index.lock and refs/heads/*.lock
  files can hang around if a process crashes mid-operation, and Windows
  doesn't always free them quickly.

.NOTES
  Per Drew's working agreement, all git operations on this machine go
  through PowerShell. The Linux sandbox that scaffolded these files did
  not run any git commands.
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoDir = "C:\dev\agile-cards-board"
$RepoSlug = "Ginkobaloba/agile-cards-board"
$DefaultBranch = "main"
$Description = "Web dashboard for the agile-cards skill. React + Node.js. Drag-drop kanban, sprint planning, retros."

function Invoke-Defensive-LockCleanup {
    param([string]$GitDir)
    if (-not (Test-Path $GitDir)) { return }
    $candidates = @(
        Join-Path $GitDir "index.lock"
        Join-Path $GitDir "HEAD.lock"
        Join-Path $GitDir "config.lock"
        Join-Path $GitDir "packed-refs.lock"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            Write-Host "  removing stale $p"
            Remove-Item -Force $p -ErrorAction SilentlyContinue
        }
    }
    $refsLockDir = Join-Path $GitDir "refs"
    if (Test-Path $refsLockDir) {
        Get-ChildItem -Path $refsLockDir -Recurse -Filter "*.lock" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Write-Host "  removing stale $($_.FullName)"
                Remove-Item -Force $_.FullName -ErrorAction SilentlyContinue
            }
    }
}

function Invoke-Git {
    param(
        [Parameter(Mandatory)][string[]]$Args
    )
    Invoke-Defensive-LockCleanup -GitDir (Join-Path $RepoDir ".git")
    Push-Location $RepoDir
    try {
        & git @Args
        if ($LASTEXITCODE -ne 0) {
            throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $RepoDir)) {
    throw "Repo directory not found at $RepoDir. Did the scaffold run?"
}

Write-Host "=== agile-cards-board setup ==="
Write-Host "  repo dir : $RepoDir"
Write-Host "  remote   : $RepoSlug"
Write-Host ""

# 1) git init (idempotent)
if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    Write-Host "[1/6] git init"
    Push-Location $RepoDir
    try {
        & git init --initial-branch=$DefaultBranch | Out-Null
        if ($LASTEXITCODE -ne 0) {
            # Old git: fall back to branch rename after init.
            & git init | Out-Null
            & git symbolic-ref HEAD "refs/heads/$DefaultBranch" | Out-Null
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[1/6] git already initialized"
}

# 2) ensure user.name / user.email are set (uses ambient global config if any)
Write-Host "[2/6] checking git identity"
Push-Location $RepoDir
try {
    $name = (& git config user.name) 2>$null
    $email = (& git config user.email) 2>$null
    if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($email)) {
        Write-Host "  no local identity; falling back to global. If global is also empty, run:"
        Write-Host "    git config --global user.name  'Drew Mattick'"
        Write-Host "    git config --global user.email 'dramattick1@gmail.com'"
    }
} finally {
    Pop-Location
}

# 3) stage everything
Write-Host "[3/6] git add"
Invoke-Git -Args @("add", "-A")

# 4) commit if there's anything to commit
Write-Host "[4/6] git commit"
Push-Location $RepoDir
try {
    $hasStaged = (& git diff --cached --name-only) -ne $null
} finally {
    Pop-Location
}
if ($hasStaged) {
    Invoke-Git -Args @(
        "commit", "-m",
        "feat: scaffold dashboard v0+ (Express+TS backend, React+Vite+TS frontend, SSE, auth, kanban)"
    )
} else {
    Write-Host "  nothing to commit (already up to date)"
}

# 5) create GitHub repo if it doesn't exist, set remote
Write-Host "[5/6] gh repo create $RepoSlug"
$ghExists = $true
try {
    & gh repo view $RepoSlug 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { $ghExists = $false }
} catch {
    $ghExists = $false
}

if (-not $ghExists) {
    Push-Location $RepoDir
    try {
        & gh repo create $RepoSlug --public --source=. --remote=origin --description $Description
        if ($LASTEXITCODE -ne 0) { throw "gh repo create failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "  repo $RepoSlug already exists on GitHub; ensuring 'origin' is wired"
    Push-Location $RepoDir
    try {
        $remotes = (& git remote) -split "`r?`n"
        if ($remotes -notcontains "origin") {
            & git remote add origin "https://github.com/$RepoSlug.git"
            if ($LASTEXITCODE -ne 0) { throw "git remote add failed" }
        }
    } finally {
        Pop-Location
    }
}

# 6) push
Write-Host "[6/6] git push -u origin $DefaultBranch"
Invoke-Git -Args @("push", "-u", "origin", $DefaultBranch)

Write-Host ""
Write-Host "Done. https://github.com/$RepoSlug"
