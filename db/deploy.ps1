<#
.SYNOPSIS
Deploys the api schema, the read procedures and the agent_reader account (plan U8).

.DESCRIPTION
Runs the scripts in db/ with sqlcmd and Windows authentication, in the order schema, views,
procedures, security. Every script is idempotent, so a rerun succeeds without changes.
The agent password is DC_SQL_PASSWORD, from the environment or from the .env file of the
backend, so the database account and the backend configuration cannot drift apart.

.EXAMPLE
./db/deploy.ps1
./db/deploy.ps1 -Server localhost -Database AdventureWorks2025
#>
[CmdletBinding()]
param(
    [string]$Server = 'localhost',
    [string]$Database = 'AdventureWorks2025',
    # Defaults to the .env in the repository root.
    [string]$EnvFile
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 has no $PSScriptRoot in parameter defaults of a script run with -File.
if (-not $EnvFile) {
    $EnvFile = Join-Path (Split-Path $PSScriptRoot -Parent) '.env'
}

function Get-AgentPassword {
    if ($env:DC_SQL_PASSWORD) {
        return $env:DC_SQL_PASSWORD
    }
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile | Where-Object { $_ -match '^\s*DC_SQL_PASSWORD\s*=' } |
            Select-Object -Last 1
        if ($line) {
            # Read the value as the backend's settings (python-dotenv) do: surrounding quotes
            # are not part of it, and an unquoted value ends at a " #" comment.
            $value = ($line -split '=', 2)[1].Trim()
            if ($value -match '^(["''])(.*)\1$') {
                $value = $Matches[2]
            }
            else {
                $value = ($value -replace '\s+#.*$', '').Trim()
            }
            if ($value) {
                return $value
            }
        }
    }
    throw "Set DC_SQL_PASSWORD in the environment or in $EnvFile before deploying."
}

function Invoke-Sqlcmd18 {
    # A simple function: $args passes sqlcmd flags such as -W through unparsed.
    # -E Windows authentication, -C trust the local certificate, -b fail on SQL errors,
    # -I QUOTED_IDENTIFIER ON (required by procedures over computed columns).
    $output = & sqlcmd -S $Server -E -C -b -I @args
    if ($LASTEXITCODE -ne 0) {
        throw "sqlcmd failed ($LASTEXITCODE): $($output -join [Environment]::NewLine)"
    }
    return $output
}

$mode = Invoke-Sqlcmd18 -h -1 -W -Q "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int)"
if (($mode | Select-Object -First 1).Trim() -ne '0') {
    throw ("SQL Server on '$Server' accepts only Windows authentication, so the SQL " +
        "login agent_reader cannot connect. Enable mixed mode (SQL Server and Windows " +
        "Authentication) and restart the service, see db/README.md.")
}

$password = Get-AgentPassword
if ($password.Contains("'")) {
    throw 'DC_SQL_PASSWORD must not contain an apostrophe.'
}

$scripts = foreach ($folder in 'schema', 'views', 'procedures', 'security') {
    Get-ChildItem (Join-Path $PSScriptRoot $folder) -Filter '*.sql' | Sort-Object Name
}

# sqlcmd reads environment variables as scripting variables; the password stays off the
# command line.
$env:AgentReaderPassword = $password
try {
    foreach ($script in $scripts) {
        Write-Host "Running $($script.Directory.Name)/$($script.Name)"
        Invoke-Sqlcmd18 -d $Database -v "DatabaseName=$Database" -i $script.FullName | Out-Host
    }
}
finally {
    Remove-Item Env:\AgentReaderPassword -ErrorAction SilentlyContinue
}

Write-Host "Deployed $($scripts.Count) scripts to $Server/$Database."
