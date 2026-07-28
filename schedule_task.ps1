# =============================================================================
#  schedule_task.ps1
#
#  Registers a Windows Scheduled Task that runs the Market Monitor every
#  weekday (Monday-Friday) at 06:30 AM *UK time* (Europe/London).
#
#  WHY THE TIME CONVERSION?
#    Windows Task Scheduler triggers always fire in the PC's LOCAL time zone.
#    This machine may not be on UK time, so we calculate what 06:30 London is
#    in local time and schedule THAT. (Both the UK and most of Europe change
#    their clocks on the same dates, so the offset stays consistent; if you
#    ever move the PC to a very different time zone, just re-run this script.)
#
#  THE REPORT IS COPIED TO THE DESKTOP AUTOMATICALLY:
#    run.py itself copies the finished report to the Desktop (and deletes the
#    previous day's copy) as its final step, so the scheduled run leaves
#    today's briefing waiting on the Desktop with no extra work here.
#
#  HOW TO USE:
#    1. Open PowerShell as Administrator (right-click > "Run as administrator").
#    2. cd "C:\Users\ALEX\Desktop\market-monitor"
#    3. If scripts are blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#    4. Run it:  .\schedule_task.ps1
#
#  To remove the task later:
#    Unregister-ScheduledTask -TaskName "MarketMonitorMorningBriefing" -Confirm:$false
# =============================================================================

# --- Configuration: absolute paths (edit these if you move the project) -----

# Full path to the Python interpreter that has the project's packages installed.
$PythonExe = "C:\Users\ALEX\AppData\Local\Python\pythoncore-3.14-64\python.exe"

# Full path to the project folder (the folder this script lives in).
$ProjectDir = "C:\Users\ALEX\Desktop\market-monitor"

# Full path to the master script we want to run each morning.
$RunScript = "$ProjectDir\run.py"

# The name the task will appear under in Windows Task Scheduler.
$TaskName = "MarketMonitorMorningBriefing"

# --- Safety check: make sure the files actually exist before continuing ------

# Test-Path returns $true if the file exists; -not flips it, so we stop early
# (exit 1 = "failed") when either the interpreter or the script is missing.
if (-not (Test-Path $PythonExe)) {
    Write-Error "Python was not found at: $PythonExe"
    exit 1
}
if (-not (Test-Path $RunScript)) {
    Write-Error "run.py was not found at: $RunScript"
    exit 1
}

# --- Work out 06:30 UK time in this PC's local time --------------------------

# Look up the UK time zone. On Windows it is called "GMT Standard Time"
# (this single ID covers both GMT in winter and British Summer Time).
$londonTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("GMT Standard Time")

# The PC's own local time zone (whatever Windows is currently set to).
$localTz = [System.TimeZoneInfo]::Local

# Build a DateTime for 06:30 *today*. We start from today's date at midnight
# and add 6 hours 30 minutes to land on 06:30:00.
$londonClock = (Get-Date).Date.AddHours(6).AddMinutes(30)

# Mark that DateTime as "Unspecified" so .NET does not assume it is already
# local; we then tell ConvertTime to read it AS London time.
$londonUnspecified = [DateTime]::SpecifyKind($londonClock, [System.DateTimeKind]::Unspecified)

# Convert "06:30 in London" into the equivalent wall-clock time on this PC.
$localEquivalent = [System.TimeZoneInfo]::ConvertTime($londonUnspecified, $londonTz, $localTz)

# Pull out just the HH:mm we will hand to the scheduler.
$triggerClock = $localEquivalent.ToString("HH:mm")

Write-Host "06:30 UK time corresponds to $triggerClock local time on this PC."

# --- Remove any existing task with the same name (so re-running is safe) -----

# -ErrorAction SilentlyContinue keeps PowerShell quiet if no such task exists.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName' so it can be recreated..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# --- Define WHAT runs (the action) -------------------------------------------

# Launch Python with run.py as its argument. -WorkingDirectory makes the task
# behave as if you had 'cd'd into the project, so relative paths inside the
# code (like "data/market_data.db") resolve to the right place.
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$RunScript`"" `
    -WorkingDirectory $ProjectDir

# --- Define WHEN it runs (the trigger) ---------------------------------------

# A weekly trigger firing on the five weekdays, at the local time we computed
# above. -At accepts a DateTime; only its time-of-day portion is used.
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At $localEquivalent

# --- Define extra behaviour (settings) ---------------------------------------

# StartWhenAvailable: if the PC was off/asleep at the trigger time, run as soon
# as it can afterwards. WakeToRun: let Windows wake the machine from sleep.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun

# --- Register (create) the task ----------------------------------------------

# Combine the action, trigger and settings into one task and save it.
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs the Market Monitor morning briefing every weekday at 06:30 UK time."

# --- Confirmation message ----------------------------------------------------

Write-Host ""
Write-Host "Scheduled task '$TaskName' created successfully." -ForegroundColor Green
Write-Host "It runs every weekday (Mon-Fri) at 06:30 UK time (= $triggerClock here)."
Write-Host "The report is copied to the Desktop automatically at the end of each run."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName `"$TaskName`""
