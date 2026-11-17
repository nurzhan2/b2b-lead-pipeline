# run_pipeline.ps1 - полный цикл: поиск -> обогащение -> amoCRM.
#
# Одна команда поднимает ядро (если оно не запущено) и крутит цикл:
#
#   osm        новые компании с карт
#   zakupki    новые госзакупки
#   egrul      ФИО и должность руководителя
#   enrich     email, телефон, мессенджер, ФИО с сайта
#   site_audit PageSpeed и провалы проверок
#   amocrm     всё, что доросло до HOT, уезжает сделкой в CRM
#
# Порядок не случаен. Источники идут первыми, обогащение работает по
# компаниям с доменом и потому после них, amocrm последним - он забирает
# только тех, кто уже полностью собран и набрал HOT.
#
# Дублей в CRM не будет: ядро отдаёт только лиды без отметки в outreach,
# а отметку пишет само, после подтверждения от воркера.
#
# Запуск:
#   powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -Cycles 24 -PauseMinutes 30
#   powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipCrm

param(
    [int]$Cycles = 12,
    [int]$PauseMinutes = 25,
    [switch]$SkipCrm,
    [switch]$NoCore
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runLog = Join-Path $logDir ("pipeline_{0:yyyy-MM-dd_HHmm}.log" -f (Get-Date))

function Write-Log($message) {
    $line = "{0:HH:mm:ss}  {1}" -f (Get-Date), $message
    Write-Host $line
    Add-Content -Path $runLog -Value $line -Encoding UTF8
}

function Test-Core {
    try {
        $null = Invoke-WebRequest "http://localhost:8081/api/v1/outreach/pending?limit=1" `
            -UseBasicParsing -TimeoutSec 10
        return $true
    } catch {
        return $false
    }
}

function Start-Core {
    if (Test-Core) {
        Write-Log "ядро уже работает"
        return $true
    }

    Write-Log "поднимаю ядро..."
    $env:SERVER_PORT = "8081"
    $env:GOOGLE_CREDENTIALS_PATH = "credentials/google.json"
    $env:SHEETS_SPREADSHEET_ID = "1DvtXLy9YGg9fbA3U9Nl1jzY3E6ypkGxcqbgu5wc4zs0"
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "mvn spring-boot:run > core.log 2>&1" `
        -WorkingDirectory $root -WindowStyle Minimized

    foreach ($i in 1..40) {
        Start-Sleep -Seconds 5
        if (Test-Core) {
            Write-Log "ядро поднялось"
            return $true
        }
    }
    Write-Log "ЯДРО НЕ ПОДНЯЛОСЬ за 200 секунд - смотри core.log"
    return $false
}

function Invoke-Worker($module, $label, $timeoutMinutes) {
    Write-Log "-> $label"
    $stdout = Join-Path $logDir "$label.out.log"
    $stderr = Join-Path $logDir "$label.err.log"
    $proc = Start-Process -FilePath python `
        -ArgumentList "-u", "-m", $module `
        -WorkingDirectory $root -NoNewWindow -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    # Воркер, зависший на сетевом таймауте, не должен съесть весь цикл.
    if (-not $proc.WaitForExit($timeoutMinutes * 60 * 1000)) {
        Write-Log "   $label превысил $timeoutMinutes мин - останавливаю"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        return
    }

    $summary = Select-String -Path $stdout -Pattern "run_summary|run_finished" |
        Select-Object -Last 1
    if ($summary) {
        Write-Log ("   " + $summary.Line.Trim())
    } else {
        Write-Log "   $label завершился без сводки (смотри $stdout)"
    }
}

if (-not $NoCore) {
    if (-not (Start-Core)) { exit 1 }
} elseif (-not (Test-Core)) {
    Write-Log "ядро не отвечает, а -NoCore запрещает его поднимать"
    exit 1
}

Write-Log "цикл: $Cycles проходов, пауза $PauseMinutes мин. Лог: $runLog"

for ($i = 1; $i -le $Cycles; $i++) {
    Write-Log "=== проход $i из $Cycles ==="

    if (-not (Test-Core)) {
        # Ядро может умереть посреди суток - закрыли окно, кончилась память,
        # что угодно. Пропускать проход бессмысленно: все воркеры упрутся в
        # core_unavailable и следующие 23 прохода тоже. Поднимаем заново.
        Write-Log "ЯДРО НЕ ОТВЕЧАЕТ - поднимаю заново"
        if ($NoCore) {
            Write-Log "   -NoCore запрещает перезапуск, пропускаю проход"
            Start-Sleep -Seconds ($PauseMinutes * 60)
            continue
        }
        if (-not (Start-Core)) {
            Write-Log "   поднять не удалось, пропускаю проход"
            Start-Sleep -Seconds ($PauseMinutes * 60)
            continue
        }
    }

    Invoke-Worker "workers.osm.main"        "osm"        30
    Invoke-Worker "workers.zakupki.main"    "zakupki"    30
    Invoke-Worker "workers.egrul.main"      "egrul"      30
    Invoke-Worker "workers.enrich.main"     "enrich"     40
    Invoke-Worker "workers.site_audit.main" "site_audit" 40

    if (-not $SkipCrm) {
        # Последним: забирает только то, что уже собрано и доросло до HOT.
        Invoke-Worker "workers.amocrm.main" "amocrm" 20
    }

    if ($i -lt $Cycles) {
        Write-Log "пауза $PauseMinutes мин"
        Start-Sleep -Seconds ($PauseMinutes * 60)
    }
}

Write-Log "=== цикл завершён ==="
Write-Log "Выгрузка: python scripts\export_calllist.py --out calllist.xlsx"
