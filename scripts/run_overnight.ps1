# run_overnight.ps1 - ночной цикл сбора.
#
# Один проход: OSM (новые компании) -> enrich (контакты с их сайтов) ->
# site_audit (замер сайтов). Порядок не случаен: enrich и site_audit
# работают только по компаниям с доменом, поэтому идут ПОСЛЕ osm, иначе
# им нечего обрабатывать.
#
# Ядро должно быть уже запущено на 8081 - скрипт его не поднимает и не
# трогает. Синхронизация в Google Таблицу идёт сама, раз в 5 минут.
#
# Оба воркера помечают компанию как обработанную, так что повторные
# проходы не переделывают одно и то же - каждый цикл берёт только новое.
#
# Запуск:
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1 -Cycles 12 -PauseMinutes 20

param(
    [int]$Cycles = 10,
    [int]$PauseMinutes = 25
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runLog = Join-Path $logDir ("overnight_{0:yyyy-MM-dd_HHmm}.log" -f (Get-Date))

function Write-Log($message) {
    $line = "{0:HH:mm:ss}  {1}" -f (Get-Date), $message
    Write-Host $line
    Add-Content -Path $runLog -Value $line -Encoding UTF8
}

function Invoke-Worker($module, $label, $timeoutMinutes) {
    Write-Log "-> $label"
    $stdout = Join-Path $logDir "$label.out.log"
    $stderr = Join-Path $logDir "$label.err.log"
    $proc = Start-Process -FilePath python `
        -ArgumentList "-u", "-m", $module `
        -WorkingDirectory $root -NoNewWindow -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    # Воркер, зависший на сетевом таймауте, не должен съесть всю ночь.
    if (-not $proc.WaitForExit($timeoutMinutes * 60 * 1000)) {
        Write-Log "   $label превысил $timeoutMinutes мин - останавливаю"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        return
    }

    $summary = Select-String -Path $stdout -Pattern "run_finished|run_summary" |
        Select-Object -Last 1
    if ($summary) {
        Write-Log ("   " + $summary.Line.Trim())
    } else {
        Write-Log "   $label завершился без сводки (смотри $stdout)"
    }
}

# Ядро может упасть за ночь - без него воркеры шлют ingest в пустоту.
function Test-Core {
    try {
        $null = Invoke-WebRequest "http://localhost:8081/api/v1/companies/pending-audit?limit=1" `
            -UseBasicParsing -TimeoutSec 10
        return $true
    } catch {
        return $false
    }
}

Write-Log "Ночной цикл: $Cycles проходов, пауза $PauseMinutes мин между ними"
Write-Log "Лог: $runLog"

for ($i = 1; $i -le $Cycles; $i++) {
    Write-Log "=== проход $i из $Cycles ==="

    if (-not (Test-Core)) {
        Write-Log "ЯДРО НЕ ОТВЕЧАЕТ на 8081 - пропускаю проход"
        Start-Sleep -Seconds ($PauseMinutes * 60)
        continue
    }

    # OSM упирается в TARGET_PER_DAY и в лимиты Overpass; публичные зеркала
    # днём отдавали сплошные 429/504, ночью обычно свободнее.
    Invoke-Worker "workers.osm.main" "osm" 30
    Invoke-Worker "workers.enrich.main" "enrich" 40
    Invoke-Worker "workers.site_audit.main" "site_audit" 40

    if ($i -lt $Cycles) {
        Write-Log "пауза $PauseMinutes мин"
        Start-Sleep -Seconds ($PauseMinutes * 60)
    }
}

Write-Log "=== цикл завершён ==="
Write-Log "Сводка утром:  python scripts\export_excel.py --out leads.xlsx"
